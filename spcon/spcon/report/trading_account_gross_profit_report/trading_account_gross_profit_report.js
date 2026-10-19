frappe.query_reports["Trading Account Gross Profit Report"] = {
	filters: [
		{fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company")},
		{fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.year_start()},
		{fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today()},
		{fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "Warehouse", get_query: () => ({filters: {company: frappe.query_report.get_filter_value("company")}})},
		{fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group"},
		{fieldname: "cost_center", label: __("Cost Center"), fieldtype: "Link", options: "Cost Center", get_query: () => ({filters: {company: frappe.query_report.get_filter_value("company")}})},
	],
};
