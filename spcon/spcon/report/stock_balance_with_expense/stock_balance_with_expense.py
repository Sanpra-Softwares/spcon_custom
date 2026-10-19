"""Stock Balance plus the expense component of the closing inventory.

LCV charges are read once from submitted Landed Cost Items, at the original
receipt date, just as ERPNext reposts them. Manufacturing additional_cost is
already the allocated company-currency amount; never add its header total too.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.stock.report.stock_balance.stock_balance import StockBalanceReport


class ExpenseQueue:
	"""Mirror ERPNext's valuation bins, tagging each with an expense unit rate.

	Equal *valuation* rates merge in ERPNext. Merge expense rates by weight as
	well, rather than running an independent FIFO queue of expense rates.
	"""

	def __init__(self, method="FIFO"):
		self.method = method
		self.bins = []  # quantity, valuation rate, expense rate

	@property
	def qty(self):
		return sum(row[0] for row in self.bins)

	@property
	def expense(self):
		return sum(row[0] * row[2] for row in self.bins)

	def receive(self, qty, rate, expense):
		if not qty:
			return
		expense_rate = expense / qty
		if self.method == "Moving Average":
			old_qty = self.qty
			new_qty = old_qty + qty
			new_expense = self.expense + expense if old_qty > 0 else new_qty * expense_rate
			self.bins = [[new_qty, rate, new_expense / new_qty if new_qty else 0]]
		elif self.bins and self.bins[-1][0] <= 0:
			new_qty = self.bins[-1][0] + qty
			if new_qty > 0:
				self.bins[-1] = [new_qty, rate, expense_rate]
			else:
				self.bins[-1][0] = new_qty
		elif self.bins and self.bins[-1][1] == rate:
			row = self.bins[-1]
			row[2] = (row[0] * row[2] + expense) / (row[0] + qty)
			row[0] += qty
		else:
			self.bins.append([qty, rate, expense_rate])

	def issue(self, qty, outgoing_rate=0):
		if self.method == "Moving Average":
			rate = self.expense / self.qty if self.qty else 0
			self.bins = [[self.qty - qty, 0, rate]]
			return qty * rate
		expense = 0.0
		last_rate = 0.0
		while qty > 1e-9:
			if not self.bins or self.bins[0][0] <= 0:
				if self.bins:
					last_rate = self.bins[0][2]
					self.bins[0][0] -= qty
				else:
					self.bins.append([-qty, outgoing_rate, last_rate])
				expense += qty * last_rate
				break
			index = -1 if self.method == "LIFO" else 0
			if outgoing_rate > 0 and self.method == "FIFO":
				index = next((i for i, row in enumerate(self.bins) if row[1] == outgoing_rate), index)
			row = self.bins[index]
			last_rate = row[2]
			take = min(qty, row[0])
			expense += take * row[2]
			row[0] -= take
			qty -= take
			if abs(row[0]) < 1e-9:
				self.bins.pop(index)
		return expense


def execute(filters=None):
	filters = frappe._dict(filters or {})
	report = StockBalanceReport(filters)
	result = list(report.run())
	columns, data = result[:2]
	if data:
		balances = ExpenseReplay(report).run()
		for row in data:
			expense = balances.get(report.get_group_by_key(row), 0.0)
			row["expense_included_in_valuation"] = expense
			row["stock_value_without_additional_expenses"] = flt(row.bal_val) - expense
	columns.extend([
		{"label": _("Expense Included in Valuation"), "fieldname": "expense_included_in_valuation",
		 "fieldtype": "Currency", "options": "Company:company:default_currency", "width": 210},
		{"label": _("Stock Value Without Additional Expenses"),
		 "fieldname": "stock_value_without_additional_expenses", "fieldtype": "Currency",
		 "options": "Company:company:default_currency", "width": 250},
	])
	return tuple(result)


class ExpenseReplay:
	def __init__(self, report):
		self.report = report
		self.queues = {}
		self.balances = defaultdict(float)
		self.issued = defaultdict(lambda: [0.0, 0.0])
		self.issued_details = defaultdict(lambda: [0.0, 0.0])
		self.consumed = defaultdict(float)

	def run(self):
		# Do not apply item/warehouse/from-date filters here: a filtered finished
		# item may contain expenses from another item, warehouse or earlier year.
		conditions = "posting_date <= %(to_date)s and is_cancelled=0 and docstatus<2"
		params = {"to_date": self.report.to_date}
		if self.report.filters.company:
			conditions += " and company=%(company)s"
			params["company"] = self.report.filters.company
		entries = frappe.db.sql(
			f"select * from `tabStock Ledger Entry` where {conditions} order by posting_datetime, creation, name",
			params, as_dict=True,
		)
		if any(d.serial_no or d.batch_no or d.serial_and_batch_bundle for d in entries):
			frappe.throw(_("Expense attribution for serial/batch stock is not supported by this report."))
		self.methods = dict(frappe.get_all("Item", fields=["name", "valuation_method"], as_list=True))
		self.default_method = frappe.db.get_single_value("Stock Settings", "valuation_method") or "FIFO"
		self.details = {d.name: d for d in frappe.db.sql("""
			select d.name, d.parent, d.transfer_qty, d.basic_amount, d.additional_cost,
			 d.s_warehouse, d.t_warehouse, d.is_finished_item, d.set_basic_rate_manually,
			 d.allow_zero_valuation_rate, d.item_code, s.purpose, s.work_order
			from `tabStock Entry Detail` d join `tabStock Entry` s on s.name=d.parent
			where s.docstatus=1""", as_dict=True)}
		self.landed = dict(frappe.db.sql("""
			select purchase_receipt_item, sum(applicable_charges)
			from `tabLanded Cost Item` where docstatus=1 group by purchase_receipt_item"""))
		self.receipt_qty = {}
		for doctype in ("Purchase Receipt Item", "Purchase Invoice Item"):
			self.receipt_qty.update(dict(frappe.db.sql(
				f"select name, stock_qty from `tab{doctype}` where docstatus=1")))
		self.return_against = {}
		self.return_details = {}
		for doctype in ("Sales Invoice", "Delivery Note", "Purchase Receipt", "Purchase Invoice"):
			for name, original in frappe.get_all(doctype, filters={"docstatus": 1, "is_return": 1},
				fields=["name", "return_against"], as_list=True):
				self.return_against[(doctype, name)] = original
		for doctype, field in (("Sales Invoice Item", "sales_invoice_item"), ("Delivery Note Item", "dn_detail")):
			self.return_details.update(dict(frappe.db.sql(
				f"select name, `{field}` from `tab{doctype}` where docstatus=1 and coalesce(`{field}`, '') != ''")))
		# Process all source rows before targets of the same stock voucher.
		groups = {}
		for entry in entries:
			key = (entry.posting_datetime, entry.voucher_type, entry.voucher_no)
			groups.setdefault(key, []).append(entry)
		for rows in groups.values():
			self.process_voucher(rows)
		return self.balances

	def queue(self, row):
		key = (row.company, row.item_code, row.warehouse)
		if key not in self.queues:
			self.queues[key] = ExpenseQueue(self.methods.get(row.item_code) or self.default_method)
		return self.queues[key]

	def process_voucher(self, rows):
		transferred = defaultdict(float)
		manufacturing_expense = 0.0
		incoming = []
		for row in rows:
			queue = self.queue(row)
			qty = flt(row.actual_qty)
			if row.voucher_type == "Stock Reconciliation":
				# Reconciliation replaces ERPNext's entire cost queue. Preserve
				# the expense of retained units; newly counted units have no
				# attributable LCV/manufacturing expense.
				before = queue.expense
				new_qty = flt(row.qty_after_transaction)
				if new_qty < queue.qty:
					queue.issue(queue.qty - new_qty)
				retained = queue.expense
				queue.bins = [[new_qty, flt(row.valuation_rate), retained / new_qty if new_qty else 0]]
				self.balances[self.report.get_group_by_key(row)] += queue.expense - before
				continue
			if qty >= 0:
				incoming.append((row, qty))
				continue
			expense = queue.issue(-qty, flt(row.outgoing_rate))
			self.balances[self.report.get_group_by_key(row)] -= expense
			issued = self.issued[(row.voucher_type, row.voucher_no, row.item_code)]
			issued[0] -= qty
			issued[1] += expense
			issued_detail = self.issued_details[row.voucher_detail_no]
			issued_detail[0] -= qty
			issued_detail[1] += expense
			if row.voucher_type == "Stock Entry":
				detail = self.details.get(row.voucher_detail_no)
				if detail:
					if detail.t_warehouse:
						transferred[detail.name] += expense
					else:
						manufacturing_expense += expense
					if detail.purpose == "Material Consumption for Manufacture" and detail.work_order:
						self.consumed[detail.work_order] += expense
		manufactured = [self.details.get(row.voucher_detail_no) for row, qty in incoming
			if row.voucher_type == "Stock Entry"]
		manufactured = [d for d in manufactured if d and d.purpose in ("Manufacture", "Repack")
			and d.is_finished_item and not d.set_basic_rate_manually and not d.allow_zero_valuation_rate]
		if manufactured and not manufacturing_expense and not any(
			self.details.get(r.voucher_detail_no) and self.details[r.voucher_detail_no].s_warehouse
			for r in rows if r.voucher_type == "Stock Entry"
		):
			work_order = manufactured[0].work_order
			manufacturing_expense = self.consumed.pop(work_order, 0.0)
		# Scrap/manual rates do not derive from consumed material cost.
		# ERPNext assigns consumed cost (less fixed scrap value) to finished
		# products, so inherited expenses belong to those finished products.
		output_qty = sum(flt(self.details[row.voucher_detail_no].transfer_qty) for row, qty in incoming
			if row.voucher_type == "Stock Entry" and self.details.get(row.voucher_detail_no)
			and self.details[row.voucher_detail_no].is_finished_item)
		manufactured_names = {d.name for d in manufactured}
		for row, qty in incoming:
			expense = 0.0
			if row.voucher_type in ("Purchase Receipt", "Purchase Invoice"):
				stock_qty = flt(self.receipt_qty.get(row.voucher_detail_no))
				if stock_qty:
					expense = flt(self.landed.get(row.voucher_detail_no)) * qty / stock_qty
			elif row.voucher_type == "Stock Entry":
				detail = self.details.get(row.voucher_detail_no)
				if detail:
					expense = transferred[detail.name]
					if detail.name in manufactured_names and output_qty:
						denominator = output_qty
						if detail.purpose == "Repack" and len({d.item_code for d in manufactured}) == 1:
							denominator = flt(detail.transfer_qty)
						expense += manufacturing_expense * qty / denominator
					if detail.purpose == "Manufacture" and detail.transfer_qty:
						expense += flt(detail.additional_cost) * qty / flt(detail.transfer_qty)
			elif row.voucher_type in ("Sales Invoice", "Delivery Note"):
				original = self.return_against.get((row.voucher_type, row.voucher_no))
				issued_qty, issued_expense = self.issued[(row.voucher_type, original, row.item_code)]
				reference = self.return_details.get(row.voucher_detail_no)
				if reference and self.issued_details[reference][0]:
					issued_qty, issued_expense = self.issued_details[reference]
				if issued_qty:
					expense = qty * issued_expense / issued_qty
			queue = self.queue(row)
			before = queue.expense
			queue.receive(qty, flt(row.incoming_rate), expense)
			self.balances[self.report.get_group_by_key(row)] += queue.expense - before
