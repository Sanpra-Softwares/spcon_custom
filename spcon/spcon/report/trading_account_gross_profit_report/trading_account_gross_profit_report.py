import frappe
from frappe import _
from frappe.utils import flt, getdate
from frappe.utils.nestedset import get_descendants_of
from erpnext.stock.report.stock_balance.stock_balance import execute as stock_balance_execute

ACCOUNT_GROUPS = {
    "sales": ("Income", ["Sales", "Sales Account", "Sales Accounts"]),
    "direct_income": ("Income", ["Direct Income", "Direct Incomes"]),
    "purchases": ("Expense", ["Purchase", "Purchases", "Purchase Account", "Purchase Accounts"]),
    "direct_expenses": ("Expense", ["Direct Expense", "Direct Expenses"]),
}

def execute(filters=None):
    filters = frappe._dict(filters or {})
    validate_filters(filters)
    currency = frappe.get_cached_value("Company", filters.company, "default_currency")
    opening_stock, closing_stock = get_stock_values(filters)
    sales = get_bucket_amount(filters, "sales")
    direct_income = get_bucket_amount(filters, "direct_income")
    purchases = get_bucket_amount(filters, "purchases")
    direct_expenses = get_bucket_amount(filters, "direct_expenses")
    total_debit = opening_stock + purchases + direct_expenses
    total_credit = sales + direct_income + closing_stock
    gross_profit = total_credit - total_debit
    data = [
        row(_("Opening Stock"), opening_stock, None, "Opening Stock"),
        row(_("Purchases"), purchases, None, "Purchases"),
        row(_("Direct Expenses"), direct_expenses, None, "Direct Expenses"),
        row(_("Total Debit"), total_debit, None, "Total Debit", True),
        row("", None, None, ""),
        row(_("Sales"), None, sales, "Sales"),
        row(_("Direct Income"), None, direct_income, "Direct Income"),
        row(_("Closing Stock"), None, closing_stock, "Closing Stock"),
        row(_("Total Credit"), None, total_credit, "Total Credit", True),
        row("", None, None, ""),
    ]
    if gross_profit >= 0:
        data.append(row(_("Gross Profit"), None, gross_profit, "Gross Profit", True))
    else:
        data.append(row(_("Gross Loss"), abs(gross_profit), None, "Gross Loss", True))
    summary = [
        {"value": total_debit, "label": _("Total Debit"), "datatype": "Currency", "currency": currency},
        {"value": total_credit, "label": _("Total Credit"), "datatype": "Currency", "currency": currency},
        {"value": abs(gross_profit), "indicator": "Green" if gross_profit >= 0 else "Red", "label": _("Gross Profit") if gross_profit >= 0 else _("Gross Loss"), "datatype": "Currency", "currency": currency},
    ]
    return get_columns(currency), data, None, None, summary

def validate_filters(filters):
    for fieldname in ("company", "from_date", "to_date"):
        if not filters.get(fieldname):
            frappe.throw(_("{0} is mandatory").format(frappe.bold(_(frappe.unscrub(fieldname)))))
    if getdate(filters.from_date) > getdate(filters.to_date):
        frappe.throw(_("From Date cannot be after To Date"))

def get_columns(currency):
    return [
        {"label": _("Particulars"), "fieldname": "particulars", "fieldtype": "Data", "width": 280},
        {"label": _("Debit"), "fieldname": "debit", "fieldtype": "Currency", "options": "currency", "width": 180},
        {"label": _("Credit"), "fieldname": "credit", "fieldtype": "Currency", "options": "currency", "width": 180},
        {"label": _("Section"), "fieldname": "section", "fieldtype": "Data", "width": 160},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "hidden": 1, "default": currency},
    ]

def row(particulars, debit, credit, section, is_total=False):
    return {"particulars": particulars, "debit": flt(debit) if debit is not None else None, "credit": flt(credit) if credit is not None else None, "section": section, "bold": 1 if is_total else 0, "is_total_row": 1 if is_total else 0}

def get_stock_values(filters):
    stock_filters = frappe._dict({
        "company": filters.company,
        "from_date": filters.from_date,
        "to_date": filters.to_date,
        "warehouse": filters.get("warehouse"),
        "item_group": filters.get("item_group"),
        "include_zero_stock_items": 1,
        "ignore_closing_balance": 0,
    })
    _columns, data = stock_balance_execute(stock_filters)
    opening_stock = sum(flt(d.get("opening_val")) for d in data)
    closing_stock = sum(flt(d.get("bal_val")) for d in data)
    return opening_stock, closing_stock

def get_bucket_amount(filters, bucket):
    root_type, account_names = ACCOUNT_GROUPS[bucket]
    accounts = get_accounts(filters.company, root_type, account_names)
    if not accounts and bucket == "sales":
        accounts = frappe.get_all("Account", filters={"company": filters.company, "account_type": "Income Account", "is_group": 0}, pluck="name")
    if not accounts and bucket == "purchases":
        accounts = frappe.get_all("Account", filters={"company": filters.company, "account_type": "Cost of Goods Sold", "is_group": 0}, pluck="name")
    return get_gl_amount(filters, root_type, accounts)

def get_accounts(company, root_type, account_names):
    groups = frappe.get_all(
        "Account",
        filters={"company": company, "root_type": root_type, "account_name": ["in", account_names], "is_group": 1},
        fields=["lft", "rgt"],
    )
    accounts = []
    for group in groups:
        accounts.extend(
            frappe.get_all(
                "Account",
                filters={"company": company, "is_group": 0, "lft": [">=", group.lft], "rgt": ["<=", group.rgt]},
                pluck="name",
            )
        )
    return list(set(accounts))

def get_gl_amount(filters, root_type, accounts):
    if not accounts:
        return 0
    conditions = [
        "company = %(company)s",
        "posting_date between %(from_date)s and %(to_date)s",
        "is_cancelled = 0",
        "account in %(accounts)s",
    ]
    values = {"company": filters.company, "from_date": filters.from_date, "to_date": filters.to_date, "accounts": tuple(accounts)}
    if filters.get("cost_center"):
        cost_centers = [filters.cost_center] + get_descendants_of("Cost Center", filters.cost_center, ignore_permissions=True)
        conditions.append("cost_center in %(cost_centers)s")
        values["cost_centers"] = tuple(cost_centers)
    amount_field = "sum(credit - debit)" if root_type == "Income" else "sum(debit - credit)"
    table = chr(96) + "tabGL Entry" + chr(96)
    sql = "select {0} from {1} where {2}".format(amount_field, table, " and ".join(conditions))
    return flt(frappe.db.sql(sql, values)[0][0])
