import json
import unittest
from unittest.mock import patch

import frappe
from frappe.utils import flt

from erpnext.stock.report.stock_balance.stock_balance import StockBalanceReport
from spcon.spcon.report.stock_balance_with_expense.stock_balance_with_expense import (
	ExpenseQueue, ExpenseReplay, execute,
)


class TestExpenseQueue(unittest.TestCase):
	def test_fifo_partial_sale(self):
		q = ExpenseQueue()
		q.receive(10, 110, 100)
		q.receive(10, 120, 200)
		self.assertAlmostEqual(q.issue(12), 140)
		self.assertAlmostEqual(q.expense, 160)

	def test_equal_valuation_rate_merges_expenses(self):
		q = ExpenseQueue()
		q.receive(10, 100, 100)
		q.receive(10, 100, 0)
		self.assertAlmostEqual(q.issue(10), 50)
		self.assertAlmostEqual(q.expense, 50)

	def test_specific_outgoing_rate(self):
		q = ExpenseQueue()
		q.receive(10, 100, 100)
		q.receive(10, 120, 200)
		self.assertAlmostEqual(q.issue(5, 120), 100)
		self.assertAlmostEqual(q.expense, 200)

	def test_moving_average_and_lifo(self):
		for method, expected in [("Moving Average", 75), ("LIFO", 100)]:
			q = ExpenseQueue(method)
			q.receive(10, 100, 100)
			q.receive(10, 120, 200)
			self.assertAlmostEqual(q.issue(5), expected)
			self.assertAlmostEqual(q.expense, 300 - expected)

	def test_negative_stock_receipt(self):
		q = ExpenseQueue()
		q.receive(2, 100, 20)
		self.assertAlmostEqual(q.issue(3), 30)
		q.receive(4, 110, 80)
		self.assertAlmostEqual(q.expense, 60)

	def test_transfer_manufacture_and_scrap(self):
		report = StockBalanceReport(frappe._dict(from_date="2026-01-01", to_date="2026-01-31"))
		report.inventory_dimensions = []
		replay = ExpenseReplay(report)
		replay.methods, replay.default_method = {}, "FIFO"
		replay.landed, replay.receipt_qty, replay.return_against, replay.return_details = {}, {}, {}, {}
		def row(item, warehouse, qty, detail, **kwargs):
			return frappe._dict(company="Test", item_code=item, warehouse=warehouse, actual_qty=qty,
				voucher_type="Stock Entry", voucher_no=detail, voucher_detail_no=detail,
				incoming_rate=100, **kwargs)
		raw = row("Raw", "A", 10, "opening")
		replay.queue(raw).receive(10, 100, 100)
		replay.balances[report.get_group_by_key(raw)] = 100
		replay.details = {"transfer": frappe._dict(name="transfer", t_warehouse="B", s_warehouse="A",
			purpose="Material Transfer", transfer_qty=10)}
		replay.process_voucher([row("Raw", "B", 10, "transfer"), row("Raw", "A", -10, "transfer")])
		self.assertAlmostEqual(replay.queue(row("Raw", "B", 0, "x")).expense, 100)
		replay.details = {
			"raw": frappe._dict(name="raw", s_warehouse="B", purpose="Manufacture"),
			"fg": frappe._dict(name="fg", t_warehouse="C", purpose="Manufacture", is_finished_item=1,
				transfer_qty=5, basic_amount=900, additional_cost=50),
			"scrap": frappe._dict(name="scrap", t_warehouse="C", purpose="Manufacture",
				transfer_qty=1, basic_amount=100),
		}
		replay.process_voucher([row("Raw", "B", -10, "raw"), row("FG", "C", 5, "fg"),
			row("Scrap", "C", 1, "scrap")])
		self.assertAlmostEqual(replay.queue(row("FG", "C", 0, "x")).expense, 150)
		self.assertAlmostEqual(replay.queue(row("Scrap", "C", 0, "x")).expense, 0)
		self.assertAlmostEqual(sum(replay.balances.values()), 150)


def validate_site():
	"""Read-only regression and queue audit against this site's posted ledger."""
	suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestExpenseQueue)
	result = unittest.TextTestRunner(verbosity=2).run(suite)
	assert result.wasSuccessful()
	latest = frappe.db.sql("select max(posting_date) from `tabStock Ledger Entry` where is_cancelled=0")[0][0]
	filters = frappe._dict(from_date="2026-09-01", to_date=str(latest))
	report = StockBalanceReport(filters.copy())
	standard_columns, standard_rows = report.run()
	queue_errors = []
	processed = [0]
	original = ExpenseReplay.process_voucher
	def audit(self, rows):
		original(self, rows)
		processed[0] += len(rows)
		last = {(r.company, r.item_code, r.warehouse): r for r in rows}
		for key, row in last.items():
			q = self.queues[key]
			actual = json.loads(row.stock_queue or "[]")
			if q.method != "Moving Average" and actual:
				actual_value = sum(flt(qty) * flt(rate) for qty, rate in actual)
				value = sum(qty * rate for qty, rate, expense in q.bins)
				if abs(q.qty - flt(row.qty_after_transaction)) > 1e-6 or abs(value - actual_value) > 0.1:
					queue_errors.append((row.name, row.voucher_type, value, actual_value))
	with patch.object(ExpenseReplay, "process_voucher", audit):
		columns, rows = execute(filters.copy())
	assert columns[:-2] == standard_columns
	assert len(rows) == len(standard_rows)
	for standard, custom in zip(standard_rows, rows):
		assert all(custom.get(k) == v for k, v in standard.items()), standard.item_code
		assert abs(custom.bal_val - custom.expense_included_in_valuation
			- custom.stock_value_without_additional_expenses) < 1e-7
	assert not queue_errors, queue_errors[:10]
	selected = next(r for r in rows if r.expense_included_in_valuation > 0 and r.bal_qty > 0)
	cases = [
		frappe._dict(company=selected.company, from_date="2025-04-01", to_date=str(latest),
			item_code=[selected.item_code], warehouse=[selected.warehouse], show_variant_attributes=1),
		frappe._dict(company=selected.company, from_date="2025-07-01", to_date="2025-09-30",
			show_stock_ageing_data=1),
	]
	for case in cases:
		base_columns, base_rows = StockBalanceReport(case.copy()).run()
		new_columns, new_rows = execute(case.copy())
		assert new_columns[:-2] == base_columns
		assert len(base_rows) == len(new_rows)
		for base, new in zip(base_rows, new_rows):
			assert all(new.get(k) == v for k, v in base.items())
		if case.item_code:
			matched = next(r for r in new_rows if r.item_code == selected.item_code and r.warehouse == selected.warehouse)
			assert matched.expense_included_in_valuation == selected.expense_included_in_valuation
	return {"unit_tests": result.testsRun, "standard_rows_unchanged": len(rows),
		"ledger_entries_audited": processed[0], "queue_mismatches": len(queue_errors),
		"filter_and_date_regressions": len(cases),
		"mismatch_examples": queue_errors[:10],
		"expense_total": sum(r.expense_included_in_valuation for r in rows)}


def validate_report_loading():
	from pathlib import Path
	from frappe.desk.query_report import get_script, run
	name = "Stock Balance with Expense"
	script = get_script(name)
	assert f'frappe.query_reports["{name}"]' in script["script"]
	standard_path = Path(frappe.get_app_path("erpnext", "stock", "report", "stock_balance", "stock_balance.js"))
	custom_path = Path(__file__).with_name("stock_balance_with_expense.js")
	assert custom_path.read_text().split("\n", 1)[1] == standard_path.read_text().replace('"Stock Balance"', f'"{name}"')
	result = run(name, filters={"from_date": "2026-09-01", "to_date": "2026-10-15"}, ignore_prepared_report=True)
	fields = [c["fieldname"] for c in result["columns"]]
	assert fields[-2:] == ["expense_included_in_valuation", "stock_value_without_additional_expenses"]
	rows = result["result"]
	assert rows
	# Frappe adds the report's total row after execute().
	for field in fields[-2:]:
		index = fields.index(field)
		def value(row):
			return flt(row.get(field)) if isinstance(row, dict) else flt(row[index])
		assert abs(value(rows[-1]) - sum(value(r) for r in rows[:-1])) < 0.01
	return {"report_registered": name, "standard_filter_script_matches": True,
		"rows_including_total": len(rows), "total_row_verified": True}
