# Copyright (c) 2026, Faris Ansari and contributors
# For license information, please see license.txt

"""Any version can be restored, and a restore is itself a version.

A version keeps a manifest `{path: sha256}` of the whole tree after it, and
`sketch/snapshots.py` keeps the bytes once per hash. The traps:

- A version must cost only the files it changed. Two commits that change one
  of three files may add one blob, not three.
- The tree must come back byte for byte, including a file that is not UTF-8.
  Capture reads bytes and never skips a file for its encoding.
- A restore adds, rewrites and deletes. A file the target never had must go,
  or the tree is a mix of two versions.
- History only grows. A restore is recorded as a new version of kind Restore,
  so undoing one is restoring the version before it. Work the agent had not
  committed yet is committed first, or the restore would destroy it.
- A restore that changes nothing and a dry run record nothing.
- A row from before manifests cannot be restored, and must say so rather
  than empty the tree.
- The permission check is `prototype.resolve_owned`, the same as every read.
- The blobs go with the Prototype.

The tool calls run through `tools.do_write_files` and friends with real
argument dicts, the path an agent takes.
"""

import os
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, set_user

from sketch import api, prototype_files, snapshots, versions
from sketch.mcp import tools
from sketch.tests import utils

SLUG = "d2t-restore-work"

#: Written straight to disk, so they carry no pending change and no version.
SEED = {
	"src/App.vue": "<template><div>hi</div></template>\n",
	"src/one.txt": "one\n",
	"src/two.txt": "two\n",
}

#: Not UTF-8: a PNG signature, a NUL, and bytes no decoder accepts.
BINARY = b"\x89PNG\r\n\x1a\n\x00\xff\xfe\xfd\x80"


class TestRestore(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		utils.require_runtime()
		cls.owner = utils.make_user("rstown", "d2trstown")
		cls.other = utils.make_user("rstoth", "d2trstoth")

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for email in (cls.owner, cls.other):
			utils.drop_user(email)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.doc = utils.make_prototype(self.owner, SLUG, files=SEED)
		self.addCleanup(utils.drop_prototype, self.doc.name)
		frappe.set_user(self.owner)
		# A restore queues a card refresh. These tests are about files.
		refresh = patch("sketch.thumbnails.request_refresh")
		self.refresh = refresh.start()
		self.addCleanup(refresh.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	# ---------------------------------------------------------------- drivers

	def save(self, prompt, write=None, delete=(), edit=None):
		"""One agent request: write, edit and delete, then `commit`."""
		if write:
			tools.do_write_files(
				{
					"prototype": SLUG,
					"files": [{"path": path, "content": body} for path, body in write.items()],
				}
			)
		for path, (old, new) in (edit or {}).items():
			tools.do_edit_file({"prototype": SLUG, "path": path, "old_string": old, "new_string": new})
		for path in delete:
			tools.do_delete_file({"prototype": SLUG, "path": path})

		tools.do_commit({"prototype": SLUG, "prompt": prompt})
		return self.history()[0]["sequence"]

	def restore(self, sequence, dry_run=False):
		return versions.restore(self.doc, sequence, dry_run)

	def history(self):
		return versions.history(self.doc.name)

	def tree(self) -> dict[str, bytes]:
		"""The tree on disk as bytes."""
		return dict(snapshots._scan(self.doc.name))

	def blobs(self) -> int:
		count = 0
		for _folder, _dirs, names in os.walk(snapshots.store_dir(self.doc.name)):
			count += len(names)

		return count

	# ----------------------------------------------------------------- store

	def test_two_commits_that_change_one_of_three_files_add_one_blob(self):
		self.save("add a file", write={"src/new.txt": "new\n"})
		before = self.blobs()
		self.assertEqual(before, 4)

		self.save("change one file", edit={"src/one.txt": ("one", "uno")})

		self.assertEqual(self.blobs(), before + 1)

	def test_capture_is_byte_safe(self):
		absolute = prototype_files.safe_join(self.doc.name, "src/logo.bin")
		with open(absolute, "wb") as handle:
			handle.write(BINARY)

		manifest = snapshots.capture(self.doc.name)

		self.assertIn("src/logo.bin", manifest)
		self.assertEqual(snapshots.read_blob(self.doc.name, manifest["src/logo.bin"]), BINARY)

	def test_a_missing_blob_is_a_clear_error(self):
		with self.assertRaises(frappe.ValidationError):
			snapshots.read_blob(self.doc.name, "0" * 64)

	def test_a_manifest_cannot_point_outside_the_store(self):
		with self.assertRaises(frappe.ValidationError):
			snapshots.read_blob(self.doc.name, "../../../etc/passwd")

	def test_diff_names_every_kind_of_change_sorted(self):
		old = {"a": "1", "b": "2", "c": "3"}
		new = {"a": "1", "b": "9", "d": "4"}

		self.assertEqual(
			versions.diff(old, new),
			[
				{"path": "b", "action": versions.MODIFIED},
				{"path": "c", "action": versions.DELETED},
				{"path": "d", "action": versions.ADDED},
			],
		)

	# --------------------------------------------------------------- restore

	def test_restore_rewrites_adds_and_deletes_to_match_byte_for_byte(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		absolute = prototype_files.safe_join(self.doc.name, "src/logo.bin")
		with open(absolute, "wb") as handle:
			handle.write(BINARY)
		versions.note(self.doc.name, "src/logo.bin", versions.ADDED)
		self.save("second")
		self.save(
			"change everything",
			write={"src/deep/er/page.vue": "<template>p</template>\n"},
			edit={"src/one.txt": ("one", "uno")},
			delete=["src/two.txt", "src/new.txt"],
		)
		self.assertTrue(self.restore(first)["recorded"])

		tree = self.tree()
		self.assertEqual(sorted(tree), ["src/App.vue", "src/new.txt", "src/one.txt", "src/two.txt"])
		self.assertEqual(tree["src/one.txt"], b"one\n")
		self.assertEqual(tree["src/two.txt"], b"two\n")
		self.assertFalse(os.path.exists(os.path.join(prototype_files.prototype_dir(self.doc.name), "src", "deep")))

	def test_restore_gives_back_a_binary_file(self):
		absolute = prototype_files.safe_join(self.doc.name, "src/logo.bin")
		with open(absolute, "wb") as handle:
			handle.write(BINARY)
		versions.note(self.doc.name, "src/logo.bin", versions.ADDED)
		first = self.save("with the logo")
		self.save("drop it", delete=["src/logo.bin"])

		self.restore(first)

		self.assertEqual(self.tree()["src/logo.bin"], BINARY)

	def test_restore_records_a_restore_version_with_the_counts(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save(
			"second",
			write={"src/extra.txt": "x\n"},
			edit={"src/one.txt": ("one", "uno")},
			delete=["src/two.txt"],
		)

		result = self.restore(first)

		self.assertEqual(result["sequence"], 3)
		self.assertEqual(result["restored_from"], first)
		self.assertEqual((result["files_added"], result["files_modified"], result["files_deleted"]), (1, 1, 1))
		row = self.history()[0]
		self.assertEqual((row["sequence"], row["kind"], row["restored_from"]), (3, "Restore", first))
		self.assertEqual(row["prompt"], next(r["prompt"] for r in self.history() if r["sequence"] == first))
		self.assertEqual((row["files_added"], row["files_modified"], row["files_deleted"]), (1, 1, 1))
		self.assertTrue(row["restorable"])
		self.refresh.assert_called_with(self.doc.name)

	def test_dry_run_writes_nothing(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save("second", edit={"src/one.txt": ("one", "uno")})
		with open(prototype_files.safe_join(self.doc.name, "src/one.txt"), "w") as handle:
			handle.write("unsaved edit\n")
		tree = self.tree()
		rows = len(self.history())
		blobs = self.blobs()

		result = self.restore(first, dry_run=True)

		self.assertEqual(result["changes"], [{"path": "src/one.txt", "action": versions.MODIFIED}])
		self.assertEqual(result["files_modified"], 1)
		self.assertNotIn("recorded", result)
		self.assertEqual(self.tree(), tree)
		self.assertEqual(self.blobs(), blobs)
		self.assertEqual(len(self.history()), rows)

	def test_a_failed_restore_puts_the_old_tree_back(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save(
			"second", write={"src/extra.txt": "x\n"}, edit={"src/one.txt": ("one", "uno")}, delete=["src/two.txt"]
		)
		before = self.tree()
		real = snapshots._write_atomic
		calls = []

		def flaky(target, data):
			calls.append(target)
			if len(calls) == 2:
				raise OSError("disk full")
			return real(target, data)

		with patch("sketch.snapshots._write_atomic", side_effect=flaky):
			with self.assertRaises(OSError):
				self.restore(first)

		self.assertEqual(self.tree(), before)

	def test_a_file_can_become_a_folder_and_back(self):
		as_file = self.save("file", write={"src/utils": "export {}\n"})
		tools.do_delete_file({"prototype": SLUG, "path": "src/utils"})
		tools.do_write_files(
			{"prototype": SLUG, "files": [{"path": "src/utils/index.ts", "content": "export {}\n"}]}
		)
		tools.do_commit({"prototype": SLUG, "prompt": "folder"})
		as_folder = self.history()[0]["sequence"]

		self.restore(as_file)
		self.assertEqual(self.tree()["src/utils"], b"export {}\n")
		self.assertNotIn("src/utils/index.ts", self.tree())

		self.restore(as_folder)
		self.assertEqual(self.tree()["src/utils/index.ts"], b"export {}\n")
		self.assertNotIn("src/utils", self.tree())

	def test_a_full_history_refuses_a_new_version_and_stores_nothing(self):
		blobs = self.blobs()
		tools.do_write_files({"prototype": SLUG, "files": [{"path": "src/big.txt", "content": "big\n"}]})

		with patch.object(snapshots, "HISTORY_CAP_BYTES", 1):
			with self.assertRaises(snapshots.HistoryFullError):
				tools.do_commit({"prototype": SLUG, "prompt": "too much"})

		self.assertEqual(self.blobs(), blobs)

	def test_a_restore_that_changes_nothing_records_nothing(self):
		first = self.save("first", write={"src/new.txt": "new\n"})

		result = self.restore(first)

		self.assertFalse(result["recorded"])
		self.assertEqual(result["changes"], [])
		self.assertEqual(len(self.history()), 1)

	def test_pending_changes_are_committed_before_the_restore(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		tools.do_write_files({"prototype": SLUG, "files": [{"path": "src/draft.txt", "content": "draft\n"}]})
		self.assertEqual(len(versions.pending(self.doc.name)), 1)

		result = self.restore(first)

		rows = self.history()
		self.assertEqual([row["kind"] for row in rows], ["Restore", "Request", "Request"])
		self.assertEqual(rows[1]["prompt"], f"Unsaved changes, kept before restoring version {first}")
		self.assertEqual(result["sequence"], 3)
		self.assertEqual(versions.pending(self.doc.name), [])
		self.assertNotIn("src/draft.txt", self.tree())
		# The kept work is itself restorable.
		self.restore(2)
		self.assertEqual(self.tree()["src/draft.txt"], b"draft\n")

	def test_undo_is_a_restore_of_the_version_before(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		original = self.tree()
		self.save("second", edit={"src/one.txt": ("one", "uno")}, delete=["src/two.txt"])
		changed = self.tree()

		restored = self.restore(first)
		self.assertEqual(self.tree(), original)

		self.restore(restored["sequence"] - 1)
		self.assertEqual(self.tree(), changed)

	def test_a_version_from_before_manifests_cannot_be_restored(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		frappe.db.set_value(versions.VERSION, {"prototype": self.doc.name, "sequence": first}, "manifest", "")
		self.save("second", edit={"src/one.txt": ("one", "uno")})
		tree = self.tree()

		with self.assertRaises(frappe.ValidationError) as caught:
			self.restore(first)

		self.assertIn("recorded before restore existed", str(caught.exception))
		self.assertEqual(self.tree(), tree)

	def test_a_version_that_does_not_exist_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.restore(99)

		self.assertIn("No version 99", str(caught.exception))

	# --------------------------------------------------------------- history

	def test_history_says_restorable_and_never_returns_the_manifest(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save("second", edit={"src/one.txt": ("one", "uno")})
		frappe.db.set_value(
			versions.VERSION,
			{"prototype": self.doc.name, "sequence": first},
			{"manifest": "", "restorable": 0},
		)

		rows = self.history()

		self.assertEqual([row["restorable"] for row in rows], [True, False])
		for row in rows:
			self.assertNotIn("manifest", row)

	def test_the_api_list_carries_kind_restored_from_and_restorable(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save("second", edit={"src/one.txt": ("one", "uno")})
		self.restore(first)

		rows = api.list_versions(SLUG)

		self.assertEqual([row["kind"] for row in rows], ["restore", "request", "request"])
		self.assertEqual([row["restored_from"] for row in rows], [first, None, None])
		self.assertEqual([row["restorable"] for row in rows], [True, True, True])

	# ------------------------------------------------------------------- api

	def test_the_api_restores_and_supports_a_dry_run(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save("second", edit={"src/one.txt": ("one", "uno")})

		dry = api.restore_version(SLUG, first, 1)
		self.assertEqual(self.tree()["src/one.txt"], b"uno\n")
		real = api.restore_version(SLUG, str(first), 0)

		self.assertEqual(dry["changes"], real["changes"])
		self.assertTrue(real["recorded"])
		self.assertEqual(self.tree()["src/one.txt"], b"one\n")

	def test_an_api_restore_records_the_app_as_its_source(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save("second", edit={"src/one.txt": ("one", "uno")})

		api.restore_version(SLUG, first)

		rows = api.list_versions(SLUG)
		self.assertEqual(rows[0]["source"], "App")
		self.assertIsNone(rows[1]["source"])

	def test_a_non_owner_cannot_restore(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save("second", edit={"src/one.txt": ("one", "uno")})

		with set_user(self.other):
			with self.assertRaises(frappe.DoesNotExistError):
				api.restore_version(SLUG, first)

		self.assertEqual(self.tree()["src/one.txt"], b"uno\n")

	def test_a_non_owner_cannot_restore_through_mcp(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save("second", edit={"src/one.txt": ("one", "uno")})

		with set_user(self.other):
			reply = tools.call_tool("restore_version", {"prototype": SLUG, "sequence": first})

		self.assertTrue(reply["isError"])
		self.assertEqual(self.tree()["src/one.txt"], b"uno\n")

	# ----------------------------------------------------------------- prune

	def three_versions(self):
		first = self.save("first", write={"src/a.txt": "a\n"})
		second = self.save("second", write={"src/b.txt": "b\n"}, delete=["src/a.txt"])
		third = self.save("third", edit={"src/one.txt": ("one", "uno")})
		return first, second, third

	def test_prune_deletes_older_rows_and_keeps_newer_ones(self):
		_first, second, third = self.three_versions()

		result = api.delete_versions(SLUG, second)

		self.assertEqual(result["deleted"], 2)
		self.assertEqual([row["sequence"] for row in self.history()], [third])

	def test_a_restore_row_keeps_its_name_after_its_target_is_pruned(self):
		first, _second, third = self.three_versions()
		back = self.restore(first)["sequence"]
		self.restore(third)
		again = self.restore(back)["sequence"]

		api.delete_versions(SLUG, again - 1)

		self.assertEqual([row["prompt"] for row in self.history()], ["first"])

	def test_prune_refuses_the_current_version(self):
		_first, _second, third = self.three_versions()

		for sequence in (third, third + 1, 0):
			with self.assertRaises(frappe.ValidationError):
				api.delete_versions(SLUG, sequence)

		self.assertEqual(len(self.history()), 3)

	def test_prune_keeps_blobs_that_kept_versions_or_the_disk_use(self):
		_first, second, third = self.three_versions()
		tree = self.tree()

		api.delete_versions(SLUG, second)

		# Only the third version's blobs are left, and it still restores.
		self.assertEqual(self.blobs(), len(set(snapshots._sha(data) for data in tree.values())))
		self.assertFalse(self.restore(third)["recorded"])
		self.assertEqual(self.tree(), tree)
		fourth = self.save("fourth", delete=["src/one.txt"])
		self.restore(third)
		self.assertEqual(self.tree(), tree)
		self.assertGreater(fourth, third)

	def test_prune_dry_run_deletes_nothing_and_reports_what_the_real_run_frees(self):
		_first, second, _third = self.three_versions()
		blobs = self.blobs()

		dry = api.delete_versions(SLUG, second, 1)

		self.assertEqual(self.blobs(), blobs)
		self.assertEqual(len(self.history()), 3)
		self.assertGreater(dry["freed_bytes"], 0)
		before = snapshots.usage(self.doc.name)
		real = api.delete_versions(SLUG, second)
		self.assertEqual(real, dry)
		self.assertEqual(before - snapshots.usage(self.doc.name), real["freed_bytes"])

	def test_a_non_owner_cannot_prune(self):
		_first, second, _third = self.three_versions()

		with set_user(self.other):
			with self.assertRaises(frappe.DoesNotExistError):
				api.delete_versions(SLUG, second)
			with self.assertRaises(frappe.DoesNotExistError):
				api.history_usage(SLUG)

		self.assertEqual(len(self.history()), 3)

	def test_history_usage_reports_the_store_size_and_cap(self):
		usage = api.history_usage(SLUG)

		self.assertEqual(usage, {"used_bytes": snapshots.usage(self.doc.name), "cap_bytes": snapshots.HISTORY_CAP_BYTES})

	def test_restore_version_is_marked_destructive(self):
		self.assertIn("restore_version", tools.DESTRUCTIVE)

	# ------------------------------------------------------------------- mcp

	def test_the_mcp_tools_list_and_restore(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save("second", edit={"src/one.txt": ("one", "uno")})

		listed = tools.call_tool("list_versions", {"prototype": SLUG})
		self.assertFalse(listed["isError"])
		self.assertEqual([row["sequence"] for row in listed["structuredContent"]["versions"]], [2, 1])
		self.assertIn("v2 [request] second", listed["content"][0]["text"])

		dry = tools.call_tool("restore_version", {"prototype": SLUG, "sequence": first, "dry_run": True})
		self.assertFalse(dry["isError"])
		self.assertEqual(self.tree()["src/one.txt"], b"uno\n")

		done = tools.call_tool("restore_version", {"prototype": SLUG, "sequence": first})
		self.assertEqual(done["structuredContent"]["restored_from"], first)
		self.assertEqual(self.tree()["src/one.txt"], b"one\n")

	def test_an_mcp_restore_records_the_agent_as_its_source(self):
		first = self.save("first", write={"src/new.txt": "new\n"})
		self.save("second", edit={"src/one.txt": ("one", "uno")})

		tools.call_tool("restore_version", {"prototype": SLUG, "sequence": first})

		rows = api.list_versions(SLUG)
		self.assertEqual(rows[0]["source"], "Agent")
		self.assertIsNone(rows[1]["source"])

	def test_the_mcp_list_text_is_capped_at_thirty_rows(self):
		for index in range(32):
			self.save(f"step {index}", write={f"src/s{index}.txt": "x\n"})

		listed = tools.call_tool("list_versions", {"prototype": SLUG})

		self.assertEqual(len(listed["structuredContent"]["versions"]), 32)
		lines = listed["content"][0]["text"].splitlines()
		self.assertEqual(len(lines), 31)
		self.assertIn("2 older version(s)", lines[-1])

	def test_restore_version_needs_a_sequence(self):
		reply = tools.call_tool("restore_version", {"prototype": SLUG})

		self.assertTrue(reply["isError"])
		self.assertIn("sequence", reply["content"][0]["text"])


class TestCreatedVersions(IntegrationTestCase):
	"""Create and fork each record version 1, so a Prototype can go back to its start."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		utils.require_runtime()
		cls.author = utils.make_user("rstcre", "d2trstcre")
		cls.addClassCleanup(utils.drop_user, cls.author)
		cls.reader = utils.make_user("rstfork", "d2trstfork")
		cls.addClassCleanup(utils.drop_user, cls.reader)

	def test_create_records_a_created_version_with_every_file_added(self):
		with set_user(self.author):
			row = api.create_prototype("D2t Restore Create", "blank")
		self.addCleanup(utils.drop_prototype, row["name"])

		versions_ = versions.history(row["name"])
		self.assertEqual(len(versions_), 1)
		first = versions_[0]
		files = prototype_files.list_files(row["name"])
		self.assertEqual((first["sequence"], first["kind"]), (1, "Created"))
		self.assertEqual(first["prompt"], "Created from the Blank recipe")
		self.assertEqual(first["files_added"], len(files))
		self.assertEqual((first["files_modified"], first["files_deleted"]), (0, 0))
		self.assertTrue(first["restorable"])
		self.assertEqual(versions.pending(row["name"]), [])

	def test_fork_records_a_created_version(self):
		source = utils.make_prototype(
			self.author, "d2t-restore-src", files=SEED, is_public=True, title="D2t Restore Src"
		)
		self.addCleanup(utils.drop_prototype, source.name)

		with set_user(self.reader):
			row = api.fork_prototype(utils.username_of(self.author), source.slug)
		self.addCleanup(utils.drop_prototype, row["name"])

		first = versions.history(row["name"])[0]
		self.assertEqual((first["sequence"], first["kind"]), (1, "Created"))
		self.assertEqual(first["prompt"], f"Forked from @d2trstcre/{source.slug}")
		self.assertEqual(first["files_added"], len(SEED))

	def test_deleting_a_prototype_removes_its_snapshots(self):
		with set_user(self.author):
			row = api.create_prototype("D2t Restore Delete", "blank")
			store = snapshots.store_dir(row["name"])
			self.assertTrue(os.path.isdir(store))

			api.delete_prototype(row["slug"])

		# The blobs go once the delete commits, so a rolled back delete keeps them.
		frappe.db.commit()

		self.assertFalse(os.path.exists(store))
		self.assertEqual(frappe.db.count(versions.VERSION, {"prototype": row["name"]}), 0)


class TestSnapshotBackfill(IntegrationTestCase):
	"""The patch gives the latest version of each Prototype a manifest."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		utils.require_runtime()
		cls.owner = utils.make_user("rstbkf", "d2trstbkf")
		cls.addClassCleanup(utils.drop_user, cls.owner)

	def make(self, slug):
		doc = utils.make_prototype(self.owner, slug, files=SEED)
		self.addCleanup(utils.drop_prototype, doc.name)
		row = versions.record(doc, versions.REQUEST, "old", [], {})
		frappe.db.set_value(versions.VERSION, row.name, {"manifest": "", "restorable": 0})
		return doc

	def latest(self, doc):
		return versions.history(doc.name)[0]

	def test_the_latest_version_becomes_restorable_and_a_second_run_is_a_no_op(self):
		from sketch.patches.v1_0 import snapshot_latest_versions

		doc = self.make("d2t-backfill")
		self.assertFalse(self.latest(doc)["restorable"])

		snapshot_latest_versions.execute()
		self.assertTrue(self.latest(doc)["restorable"])
		stamp = frappe.db.get_value(versions.VERSION, {"prototype": doc.name}, "manifest")

		snapshot_latest_versions.execute()
		self.assertEqual(frappe.db.get_value(versions.VERSION, {"prototype": doc.name}, "manifest"), stamp)

	def test_a_prototype_with_pending_changes_is_skipped(self):
		from sketch.patches.v1_0 import snapshot_latest_versions

		doc = self.make("d2t-backfill-pending")
		versions.note(doc.name, "src/one.txt", versions.MODIFIED)

		snapshot_latest_versions.execute()

		self.assertFalse(self.latest(doc)["restorable"])
