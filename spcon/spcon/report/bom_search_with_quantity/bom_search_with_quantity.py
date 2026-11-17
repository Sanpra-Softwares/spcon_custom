# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and Contributors and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	parents = {
		"Product Bundle Item": "Product Bundle",
		"BOM Explosion Item": "BOM",
		"BOM Item": "BOM",
	}
	# Each selected item has its own independent list of matching BOMs.
	selected_items = [(i, filters.get(f"item{i}")) for i in range(1, 6) if filters.get(f"item{i}")]
	groups = selected_items or [(1, None)]
	results = {i: [] for i, item in groups}

	for doctype in (
		"Product Bundle Item",
		"BOM Explosion Item" if filters.search_sub_assemblies else "BOM Item",
	):
		quantity_field = "stock_qty" if doctype == "BOM Explosion Item" else "qty"
		all_boms = {}
		for row in frappe.get_all(doctype, fields=["parent", "item_code", quantity_field]):
			quantities = all_boms.setdefault(row.parent, {})
			quantities[row.item_code] = quantities.get(row.item_code, 0) + flt(row.get(quantity_field))

		for i, item in groups:
			for parent, quantities in all_boms.items():
				if item and item not in quantities:
					continue
				qty = quantities.get(item, 0)
				if not item and parents[doctype] == "BOM":
					qty = frappe.db.get_value("BOM", parent, "quantity") or 0
				results[i].append((parent, parents[doctype], qty))

	columns = []
	data = []
	for i, item in groups:
		suffix = "" if i == 1 else str(i)
		label_suffix = "" if i == 1 else f" {i}"
		columns.extend([
			{
				"fieldname": f"parent{suffix}",
				"label": _("BOM") + label_suffix,
				"width": 200,
				"fieldtype": "Dynamic Link",
				"options": f"doctype{suffix}",
			},
			{
				"fieldname": f"doctype{suffix}",
				"label": _("Type") + label_suffix,
				"width": 100,
				"fieldtype": "Data",
			},
			{
				"fieldname": f"qty{suffix}",
				"label": _("Quantity") + label_suffix,
				"width": 100,
				"fieldtype": "Float",
			},
		])
		for index, (parent, doctype, qty) in enumerate(results[i]):
			if index == len(data):
				data.append({})
			data[index].update({f"parent{suffix}": parent, f"doctype{suffix}": doctype, f"qty{suffix}": qty})

	return columns, data
