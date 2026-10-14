# Copyright (c) 2026, Faris Ansari and contributors
# For license information, please see license.txt

"""The file content behind every version, so a version can be restored.

A version used to record file names only. Now each one also keeps a manifest:
`{path: sha256}` for the whole tree as it stood after that version. The bytes
live once per Prototype in a blob store, named by their hash. Two versions that
share a file share its blob, so a version costs the files it changed and not
the whole tree.

Why not store a copy of the tree per version. A tree may hold 20 MB, and a
request that edits one file would then add 20 MB. By hash it adds one file.

Blobs are never pruned: every version stays restorable. They go when the
Prototype goes (`forget`, called from `on_trash`).

Bytes, not text. A restore must give back exactly what was there, so capture
reads `rb` and never skips a file for its encoding.

Where they live. `private/files/sketch-snapshots/<name>/`, beside the source
tree and never inside it: a blob under `private/files/sketch/<name>/` would
show up in `list_files`, move `prototype_files.revision()`, and count against
the tree quotas. Nothing here is reachable over HTTP.
"""

import hashlib
import os
import re
import shutil
import tempfile
import zlib

import frappe

from sketch import prototype_files

#: `private/files/sketch-snapshots`, under the site path.
ROOT = ("private", "files", "sketch-snapshots")

_SHA = re.compile(r"^[0-9a-f]{64}$")

#: Most compressed bytes one Prototype's history may hold. Blobs are never pruned.
HISTORY_CAP_BYTES = 200 * 1024 * 1024


class HistoryFullError(frappe.ValidationError):
	"""The history store is at its cap, so no new version can be captured."""


def store_dir(name: str) -> str:
	"""Absolute path to sites/<site>/private/files/sketch-snapshots/<name>.

	Does not create the directory. The guard is the same as the thumbnails':
	a name that cannot address a tree must not address its blobs either.
	"""
	if not name or "/" in name or "\\" in name or name in (".", ".."):
		frappe.throw(frappe._("Invalid prototype id"), frappe.ValidationError)

	return os.path.abspath(frappe.get_site_path(*ROOT, name))


def manifest(name: str) -> dict[str, str]:
	"""The manifest {path: sha} of the tree on disk. Stores nothing."""
	return {rel: _sha(data) for rel, data in _scan(name)}


def capture(name: str) -> dict[str, str]:
	"""Store every file of the tree and return its manifest {path: sha}.

	A blob already stored is not written again, which is the dedup. Raises
	HistoryFullError, before writing anything, when the new blobs would push
	the store over `HISTORY_CAP_BYTES`.
	"""
	result = {}
	fresh = {}
	for rel, data in _scan(name):
		sha = _sha(data)
		result[rel] = sha
		if sha not in fresh and not os.path.exists(_blob_path(name, sha)):
			fresh[sha] = zlib.compress(data)

	added = sum(len(blob) for blob in fresh.values())
	if fresh and _store_size(name) + added > HISTORY_CAP_BYTES:
		frappe.throw(
			frappe._(
				"This prototype's version history is full ({0}). Fork it to keep saving versions with a fresh history."
			).format(f"{HISTORY_CAP_BYTES // (1024 * 1024)} MB"),
			HistoryFullError,
		)

	for sha, blob in fresh.items():
		_write_atomic(_blob_path(name, sha), blob)

	return result


def read_blob(name: str, sha: str) -> bytes:
	"""The bytes behind one hash. Raises ValidationError when the blob is gone."""
	try:
		with open(_blob_path(name, sha), "rb") as handle:
			data = zlib.decompress(handle.read())
	except (OSError, zlib.error):
		data = None

	if data is None or _sha(data) != sha:
		frappe.throw(
			frappe._("A file of this version can't be restored. It is missing or damaged in the snapshot store."),
			frappe.ValidationError,
		)

	return data


def apply(name: str, target: dict, current: dict) -> None:
	"""Make the tree on disk equal `target`. `current` is the tree now.

	Only a path that differs or is missing is written, and it gets a fresh
	mtime, so `revision()` moves and the open Viewer reloads. A path outside
	the target is deleted, then the folders that leaves empty.

	Deletes run before writes: a file that must become a folder (or the
	reverse) needs its old shape gone first.

	No `preflight`. The target tree passed the quotas when it was written.
	Every path is resolved and every blob read before the first change, so a
	bad one stops the restore with the tree untouched. If a change fails
	half way, the tree `current` is put back (its blobs are in the store,
	because the caller captured it) and the first error is raised.
	"""
	plan = _plan(name, target, current)
	try:
		_run(name, plan)
	except BaseException:
		try:
			_run(name, _plan(name, current, target))
		except Exception:
			frappe.log_error(title=f"Restore rollback failed for {name}")
		raise


def _plan(name: str, target: dict, current: dict):
	"""(paths to delete, {path: bytes} to write), all resolved up front."""
	deletes = [prototype_files.safe_join(name, path) for path in sorted(set(current) - set(target))]
	writes = {
		prototype_files.safe_join(name, path): read_blob(name, sha)
		for path, sha in target.items()
		if current.get(path) != sha
	}
	return deletes, writes


def _run(name: str, plan) -> None:
	deletes, writes = plan
	for absolute in deletes:
		if os.path.exists(absolute):
			os.remove(absolute)
			prototype_files._prune_empty_dirs(name, os.path.dirname(absolute))

	for absolute, data in writes.items():
		_write_atomic(absolute, data)


def forget(name: str) -> None:
	"""Delete the blobs of a Prototype. Called from `on_trash`."""
	shutil.rmtree(store_dir(name), ignore_errors=True)


# ------------------------------------------------------------------ internals


def _scan(name: str):
	"""Yield (relative path, bytes) for every file in the tree.

	A file that vanishes between the walk and the read is skipped.
	"""
	for rel, absolute in prototype_files._walk(name):
		try:
			with open(absolute, "rb") as handle:
				yield rel, handle.read()
		except OSError:
			continue


def _sha(data: bytes) -> str:
	return hashlib.sha256(data).hexdigest()


def _store_size(name: str) -> int:
	"""Bytes the blobs of this Prototype take on disk."""
	total = 0
	for folder, _dirs, files in os.walk(store_dir(name)):
		total += sum(os.path.getsize(os.path.join(folder, entry)) for entry in files)

	return total


def _blob_path(name: str, sha: str) -> str:
	"""Where one blob lives. A sha that is not 64 hex digits never gets a path."""
	if not isinstance(sha, str) or not _SHA.match(sha):
		frappe.throw(frappe._("Invalid snapshot hash"), frappe.ValidationError)

	return os.path.join(store_dir(name), sha[:2], sha)


def _store(name: str, sha: str, data: bytes) -> None:
	"""Write one blob unless it is already there."""
	target = _blob_path(name, sha)
	if os.path.exists(target):
		return

	_write_atomic(target, zlib.compress(data))


def _write_atomic(target: str, data: bytes) -> None:
	"""Write beside the target, then rename over it.

	A reader (the Viewer, a second capture) sees the old file or the new one,
	never half of it.
	"""
	folder = os.path.dirname(target)
	os.makedirs(folder, exist_ok=True)
	handle, temp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
	try:
		with os.fdopen(handle, "wb") as out:
			out.write(data)
		# mkstemp makes 0600. Tree files are 0644 like every other write.
		os.chmod(temp, 0o644)
		os.replace(temp, target)
	except BaseException:
		if os.path.exists(temp):
			os.remove(temp)
		raise
