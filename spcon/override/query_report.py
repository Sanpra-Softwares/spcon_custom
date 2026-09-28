import inspect
import json
from contextlib import contextmanager

import frappe
import frappe.query_builder.utils
from frappe.database.query import Engine
from frappe.desk.query_report import _run as original_run, get_report_doc

from spcon.permissions.permissions import get_sales_person_customer_names


RESTRICTED_RESULT_REPORTS = {"Sales Analytics", "Sales Report", "Sales Order Analysis", "Sales Order Analysis SPC"}
REPORT_ALIASES = {"Work Order Consumed Materials": "Work Order Consumed Materials SPC"}


@frappe.whitelist()
@frappe.read_only()
def run(
	report_name,
	filters=None,
	user=None,
	ignore_prepared_report=False,
	custom_columns=None,
	is_tree=False,
	parent_field=None,
	are_default_filters=True,
):
	filters = parse_filters(filters)
	report_name = REPORT_ALIASES.get(report_name, report_name)

	run_as_user = user
	if report_name in RESTRICTED_RESULT_REPORTS:
		get_report_doc(report_name)
		run_as_user = "Administrator"

	with ignore_unsupported_get_query_permissions():
		result = original_run(
			report_name=report_name,
			filters=filters,
			user=run_as_user,
			ignore_prepared_report=ignore_prepared_report,
			custom_columns=custom_columns,
			is_tree=is_tree,
			parent_field=parent_field,
			are_default_filters=are_default_filters,
		)

	if report_name in RESTRICTED_RESULT_REPORTS:
		filter_report_result_by_customer(result)

	if report_name == "Asset Depreciations and Balances":
		add_opening_net_value_column(result)

	return result


def parse_filters(filters):
	if isinstance(filters, str):
		return frappe._dict(json.loads(filters or "{}"))

	return frappe._dict(filters or {})


@contextmanager
def ignore_unsupported_get_query_permissions():
	if supports_get_query_permissions():
		yield
		return

	original_frappe_get_query = frappe.get_query
	original_utils_get_query = frappe.query_builder.utils.get_query

	def get_query(*args, **kwargs):
		kwargs.pop("ignore_permissions", None)
		return original_frappe_get_query(*args, **kwargs)

	frappe.get_query = get_query
	frappe.query_builder.utils.get_query = get_query

	try:
		yield
	finally:
		frappe.get_query = original_frappe_get_query
		frappe.query_builder.utils.get_query = original_utils_get_query


def supports_get_query_permissions():
	return "ignore_permissions" in inspect.signature(Engine.get_query).parameters


def filter_report_result_by_customer(result):
	allowed_customers = get_sales_person_customer_names()

	if allowed_customers is None or "result" not in result:
		return

	allowed_customers = set(allowed_customers)
	if not allowed_customers:
		result["result"] = []
		return

	result["result"] = [row for row in result.get("result", []) if get_row_customer(row) in allowed_customers]


def get_row_customer(row):
	if not isinstance(row, dict):
		return None

	for fieldname in ("customer", "party", "entity"):
		if row.get(fieldname) and is_customer(row.get(fieldname)):
			return row.get(fieldname)

	for fieldname, doctype in (
		("sales_invoice", "Sales Invoice"),
		("invoice", "Sales Invoice"),
		("sales_order", "Sales Order"),
		("delivery_note", "Delivery Note"),
		("voucher_no", row.get("voucher_type")),
		("against_voucher", row.get("against_voucher_type")),
	):
		if row.get(fieldname) and doctype in {"Sales Invoice", "Sales Order", "Delivery Note"}:
			customer = frappe.db.get_value(doctype, row.get(fieldname), "customer")
			if customer:
				return customer

	return None


def is_customer(name):
	return bool(name and frappe.db.exists("Customer", name))


def add_opening_net_value_column(result):
	columns = result.get("columns") or []
	rows = result.get("result") or []

	if any(get_column_fieldname(column) == "net_value_as_on_from_date" for column in columns):
		return

	value_column_index = next(
		(
			idx
			for idx, column in enumerate(columns)
			if get_column_fieldname(column) == "value_as_on_from_date"
		),
		None,
	)
	if value_column_index is None:
		return

	accumulated_depreciation_index = next(
		(
			idx
			for idx, column in enumerate(columns)
			if get_column_fieldname(column) == "accumulated_depreciation_as_on_from_date"
		),
		None,
	)
	value_column = columns[value_column_index]
	label = get_column_label(value_column).replace("Value as on", "Net Value as on", 1)
	columns.insert(
		value_column_index + 1,
		{
			"label": label,
			"fieldname": "net_value_as_on_from_date",
			"fieldtype": "Currency",
			"width": 170,
		},
	)

	for row in rows:
		if isinstance(row, dict):
			row["net_value_as_on_from_date"] = (row.get("value_as_on_from_date") or 0) - (
				row.get("accumulated_depreciation_as_on_from_date") or 0
			)
		elif isinstance(row, list):
			value = row[value_column_index] or 0
			accumulated_depreciation = (
				row[accumulated_depreciation_index] if accumulated_depreciation_index is not None else 0
			) or 0
			row.insert(value_column_index + 1, value - accumulated_depreciation)


def get_column_fieldname(column):
	if isinstance(column, dict):
		return column.get("fieldname")

	return None


def get_column_label(column):
	if isinstance(column, dict):
		return column.get("label", "Net Value")

	return "Net Value"
