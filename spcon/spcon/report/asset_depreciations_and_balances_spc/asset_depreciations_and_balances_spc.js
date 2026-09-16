// Copyright (c) 2026, Sanpra and contributors
// For license information, please see license.txt

frappe.query_reports["Asset Depreciations and Balances SPC"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[1],
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[2],
			reqd: 1,
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Asset Category", "Asset"],
			default: "Asset Category",
		},
		{
			fieldname: "asset_category",
			label: __("Asset Category"),
			fieldtype: "Link",
			options: "Asset Category",
			depends_on: "eval: doc.group_by == 'Asset Category'",
		},
		{
			fieldname: "asset",
			label: __("Asset"),
			fieldtype: "Link",
			options: "Asset",
			depends_on: "eval: doc.group_by == 'Asset'",
			get_query: function () {
				const location = frappe.query_report.get_filter_value("location");

				if (!location) {
					return {};
				}

				return {
					filters: {
						location: location,
					},
				};
			},
			on_change: function () {
				const asset = frappe.query_report.get_filter_value("asset");

				if (!asset) {
					frappe.query_report.set_filter_value("location", "");
					return;
				}

				frappe.db.get_value("Asset", asset, "location").then((r) => {
					frappe.query_report.set_filter_value("location", r.message.location || "");
				});
			},
		},
		{
			fieldname: "location",
			label: __("Location"),
			fieldtype: "Link",
			options: "Location",
		},
		{
			fieldname: "finance_book",
			label: __("Finance Book"),
			fieldtype: "Link",
			options: "Finance Book",
		},
	],
};

