"""Keep the expense report discoverable beside standard Stock Balance links."""

import frappe


def ensure_workspace_links():
	report_name = "Stock Balance with Expense"
	if not frappe.db.exists("Report", report_name):
		return
	ensure_report_roles(report_name)

	updated = []
	for parent in frappe.get_all("Workspace Link", filters={"link_type": "Report", "link_to": "Stock Balance"},
		pluck="parent"):
		workspace = frappe.get_doc("Workspace", parent)
		if any(link.link_to == report_name for link in workspace.links):
			continue
		position = next(i for i, link in enumerate(workspace.links) if link.link_to == "Stock Balance")
		link = workspace.append("links", {
			"type": "Link", "label": report_name, "link_type": "Report", "link_to": report_name,
			"is_query_report": 1, "report_ref_doctype": "Stock Ledger Entry", "hidden": 0,
		})
		workspace.links.remove(link)
		workspace.links.insert(position + 1, link)
		# Write only child rows to avoid exporting core workspace JSON in
		# developer mode. Existing links and workspace content stay intact.
		for idx, row in enumerate(workspace.links, 1):
			row.idx = idx
			if row is link:
				row.db_insert()
			else:
				frappe.db.set_value("Workspace Link", row.name, "idx", idx, update_modified=False)
		card = next((row for row in reversed(workspace.links[:position + 1]) if row.type == "Card Break"), None)
		if card and card.link_count:
			frappe.db.set_value("Workspace Link", card.name, "link_count", card.link_count + 1, update_modified=False)
		frappe.clear_document_cache("Workspace", parent)
		updated.append(parent)
	frappe.clear_cache()
	return updated


def ensure_report_roles(report_name):
	# This site grants Sales User access to Stock Balance through Custom Role,
	# overriding its shipped roles. Match that effective policy on the new
	# report rather than relying only on ERPNext's default roles.
	source_name = frappe.db.get_value("Custom Role", {"report": "Stock Balance"}, "name")
	if not source_name:
		return
	source = frappe.get_doc("Custom Role", source_name)
	target_name = frappe.db.get_value("Custom Role", {"report": report_name}, "name")
	target = frappe.get_doc("Custom Role", target_name) if target_name else frappe.new_doc("Custom Role")
	wanted = [row.role for row in source.roles]
	if target_name and [row.role for row in target.roles] == wanted:
		return
	target.report = report_name
	target.ref_doctype = source.ref_doctype or "Stock Ledger Entry"
	target.set("roles", [{"role": role} for role in wanted])
	target.save(ignore_permissions=True)


def audit_report_visibility():
	from frappe.boot import get_allowed_reports
	from frappe.desk.desktop import Workspace
	previous_user = frappe.session.user
	checked = 0
	missing = []
	workspace_missing = []
	try:
		users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, pluck="name")
		for user in users:
			frappe.set_user(user)
			reports = get_allowed_reports(cache=False)
			if "Stock Balance" not in reports:
				continue
			checked += 1
			if "Stock Balance with Expense" not in reports:
				missing.append(user)
			workspace = Workspace(frappe._dict(name="Stock", title="Stock", public=1))
			links = [link.link_to for card in workspace.get_links() for link in card["links"]]
			if "Stock Balance" in links and "Stock Balance with Expense" not in links:
				workspace_missing.append(user)
	finally:
		frappe.set_user(previous_user)
	return {"users_with_standard_report": checked, "missing_new_report": missing,
		"missing_workspace_link": workspace_missing}
