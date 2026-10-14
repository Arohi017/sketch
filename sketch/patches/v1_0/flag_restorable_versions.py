# Copyright (c) 2026, Faris Ansari and contributors
# For license information, please see license.txt

"""Set `restorable` on every version that already holds a manifest.

The flag is new. A row recorded before it has the default, 0, even when its
manifest is stored, so the history list would call it not restorable.
"""

import frappe


def execute():
	frappe.db.sql(
		"""update `tabSketch Prototype Version` set restorable = 1
		where ifnull(manifest, '') != '' and restorable = 0"""
	)
