# Copyright (c) 2026, Faris Ansari and contributors
# For license information, please see license.txt

"""Give the latest version of every Prototype a manifest.

Versions recorded before restore existed hold no manifest, so none can be
restored. Their trees are gone, but the latest one still has a tree: the one on
disk. This captures it and files it as that version's manifest, so each
Prototype can at least return to its newest version.

A Prototype with pending changes is skipped, because its disk no longer
matches its last version and the capture would be a lie. So is one whose tree
directory is missing. A latest version that already has a manifest is left
alone, which makes a second run a no-op.
"""

import json
import os

import frappe

from sketch import prototype_files, snapshots, versions


def execute():
	for name in frappe.get_all("Sketch Prototype", pluck="name"):
		if versions.pending(name) or not os.path.isdir(prototype_files.prototype_dir(name)):
			continue

		latest = frappe.db.get_value(
			"Sketch Prototype Version",
			{"prototype": name},
			["name", "manifest"],
			order_by="sequence desc",
		)
		if not latest or latest[1]:
			continue

		manifest = snapshots.capture(name)
		frappe.db.set_value(
			"Sketch Prototype Version", latest[0], "manifest", json.dumps(manifest), update_modified=False
		)
