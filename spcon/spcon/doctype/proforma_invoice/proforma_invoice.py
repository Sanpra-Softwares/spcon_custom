# Copyright (c) 2025, Sanpra and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from erpnext.accounts.party import _get_party_details
from erpnext.controllers.accounts_controller import get_taxes_and_charges
from erpnext.controllers.selling_controller import SellingController
from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals


def register_pricing_doctype():
    """Extend strict ERPNext pricing validation while retaining permission checks."""
    from erpnext.accounts.doctype.pricing_rule import pricing_rule

    allowed = getattr(pricing_rule, "PRICING_TRANSACTION_DOCTYPES", None)
    if allowed is not None and "Proforma Invoice" not in allowed:
        pricing_rule.PRICING_TRANSACTION_DOCTYPES = frozenset(allowed) | {"Proforma Invoice"}


class ProformaTaxesAndTotals(calculate_taxes_and_totals):
    def calculate_totals(self):
        # ERPNext's base method hard-codes standard selling DocType names.
        # Keep its selling formula here; all item/tax/discount/rounding work
        # continues to run through the standard calculation engine.
        doc = self.doc
        doc.grand_total = (
            flt(doc.taxes[-1].total) + self.grand_total_diff
            if doc.taxes else flt(doc.net_total)
        )
        doc.total_taxes_and_charges = flt(
            doc.grand_total - doc.net_total - self.grand_total_diff,
            doc.precision("total_taxes_and_charges"),
        ) if doc.taxes else 0.0
        self._set_in_company_currency(doc, ["total_taxes_and_charges"])
        doc.base_grand_total = (
            flt(doc.grand_total * doc.conversion_rate, doc.precision("base_grand_total"))
            if doc.total_taxes_and_charges else doc.base_net_total
        )
        doc.round_floats_in(doc, ["grand_total", "base_grand_total"])
        self.set_rounded_total()


class ProformaInvoice(SellingController):
    """A selling document with standard pricing/totals and no stock/GL posting."""

    def __setup__(self):
        super().__setup__()
        register_pricing_doctype()

    def get_customer_defaults(self, use_selected_addresses=True, use_tax_category=False):
        if not self.customer or not self.company:
            return frappe._dict()

        # ERPNext and India Compliance recognise Sales Order's address/GST schema.
        # Reuse that schema for defaults only; the actual document and pricing
        # permissions always remain those of Proforma Invoice.
        details = _get_party_details(
            self.customer,
            party_type="Customer",
            company=self.company,
            posting_date=self.transaction_date,
            price_list=self.selling_price_list,
            currency=self.currency,
            doctype="Sales Order",
            party_address=self.customer_address if use_selected_addresses else None,
            shipping_address=self.shipping_address_name if use_selected_addresses else None,
            company_address=self.company_address,
            ignore_permissions=self.flags.ignore_permissions,
        )
        if use_tax_category:
            from erpnext.accounts.party import get_regional_address_details
            from erpnext.utilities.regional import temporary_flag

            details.tax_category = self.tax_category
            with temporary_flag("company", self.company):
                get_regional_address_details(details, "Sales Order", self.company)

        return frappe._dict(
            {key: value for key, value in details.items() if self.meta.has_field(key)}
        )

    @frappe.whitelist()
    def get_customer_details(self):
        self.check_permission("read")
        return self.get_customer_defaults(use_selected_addresses=False)

    @frappe.whitelist()
    def get_tax_details(self, use_tax_category=False):
        self.check_permission("read")
        details = self.get_customer_defaults(use_tax_category=cint(use_tax_category))
        return {
            key: details[key]
            for key in (
                "tax_category", "taxes_and_charges", "taxes",
                "company_gstin", "billing_address_gstin",
            )
            if key in details
        }

    def set_missing_lead_customer_details(self, for_validate=False):
        # Same defaults as the client, including regional GST template and rows.
        self.update_if_missing(self.get_customer_defaults())

    def set_missing_values(self, for_validate=False):
        super().set_missing_values(for_validate)
        for item in self.items:
            if not item.delivery_date:
                item.delivery_date = self.delivery_date
            item.transaction_date = self.transaction_date

    def before_validate(self):
        self.has_unit_price_items = bool(
            frappe.db.get_single_value("Selling Settings", "allow_zero_qty_in_sales_order")
            and any(not row.qty for row in self.items if row.item_code)
        )
        self.flags.allow_zero_qty = self.has_unit_price_items

    def calculate_taxes_and_totals(self):
        ProformaTaxesAndTotals(self)

    def validate(self):
        super().validate()
        self.validate_delivery_dates()
        self.validate_uom_is_integer("stock_uom", "stock_qty")
        self.validate_uom_is_integer("uom", "qty")
        self.validate_non_invoice_documents_schedule()
        self.calculate_commission()
        self.calculate_contribution()
        self.status = "Draft" if self.docstatus == 0 else self.status
        self.billing_status = self.billing_status or "Not Billed"
        self.delivery_status = self.delivery_status or "Not Delivered"
        self.reset_default_field_value("set_warehouse", "items", "warehouse")

    def set_taxes_and_charges(self):
        # Loading a selected template must also work for API-created documents.
        if self.taxes_and_charges and not self.taxes:
            self.extend(
                "taxes",
                get_taxes_and_charges("Sales Taxes and Charges Template", self.taxes_and_charges),
            )
        super().set_taxes_and_charges()

    def validate_delivery_dates(self):
        if self.order_type != "Sales" or self.skip_delivery_note:
            return
        dates = [getdate(row.delivery_date) for row in self.items if row.delivery_date]
        if dates:
            self.delivery_date = max(dates)
        if not self.delivery_date:
            frappe.throw(_("Please enter Delivery Date"))
        for row in self.items:
            row.delivery_date = row.delivery_date or self.delivery_date
            if getdate(row.delivery_date) < getdate(self.transaction_date):
                frappe.throw(
                    _("Row {0}: Delivery Date cannot be before Transaction Date").format(row.idx)
                )

    def on_submit(self):
        self.db_set("status", "To Bill" if self.skip_delivery_note else "To Deliver and Bill")

    def on_cancel(self):
        # A proforma does not post stock, GL, reservations or payment references.
        self.db_set("status", "Cancelled")


@frappe.whitelist()
def make_sales_order(source_name, target_doc=None):
    """Create an editable Sales Order; never pass a PI name to SO-only APIs."""
    from frappe.model.mapper import get_mapped_doc

    def set_missing_values(source, target):
        target.status = "Draft"
        target.reserve_stock = 0
        target.set_missing_values()
        target.calculate_taxes_and_totals()

    return get_mapped_doc(
        "Proforma Invoice",
        source_name,
        {
            "Proforma Invoice": {
                "doctype": "Sales Order",
                "validation": {"docstatus": ["=", 1]},
                "field_no_map": [
                    "amended_from", "status", "per_billed", "per_delivered", "per_picked",
                    "advance_paid", "billing_status", "delivery_status", "inter_company_order_reference",
                ],
            },
            "Proforma Invoice Item": {
                "doctype": "Sales Order Item",
                "field_no_map": [
                    "delivered_qty", "billed_amt", "picked_qty", "work_order_qty", "stock_reserved_qty",
                    "reserve_stock", "purchase_order", "purchase_order_item",
                    "material_request", "material_request_item",
                ],
            },
        },
        target_doc,
        set_missing_values,
    )


@frappe.whitelist()
def get_proforma_defaults(doc, taxes_only=False, use_tax_category=False):
    """Return defaults without syncing an old request's document over newer edits."""
    values = frappe.parse_json(doc)
    if not isinstance(values, dict) or values.get("doctype") != "Proforma Invoice":
        frappe.throw(_("Invalid doctype"), frappe.PermissionError)
    values.pop("flags", None)
    invoice = frappe.get_doc(values)
    invoice.check_permission("read")
    if not invoice.is_new():
        frappe.get_doc("Proforma Invoice", invoice.name).check_permission("read")
    if cint(taxes_only):
        return invoice.get_tax_details(use_tax_category=use_tax_category)
    return invoice.get_customer_details()


@frappe.whitelist()
def make_proforma_invoice(source_name, target_doc=None):
    """Use the standard mapper to fetch submitted Quotation items."""
    from frappe.model.mapper import get_mapped_doc

    def set_missing_values(source, target):
        target.set_missing_values()
        target.calculate_taxes_and_totals()

    return get_mapped_doc(
        "Quotation",
        source_name,
        {
            "Quotation": {
                "doctype": "Proforma Invoice",
                "validation": {"docstatus": ["=", 1], "quotation_to": ["=", "Customer"]},
                "field_map": {"party_name": "customer"},
                "field_no_map": ["amended_from", "status"],
            },
            "Quotation Item": {
                "doctype": "Proforma Invoice Item",
                "field_map": {"parent": "prevdoc_docname", "name": "quotation_item"},
                "condition": lambda row: not row.get("is_alternative"),
            },
        },
        target_doc,
        set_missing_values,
    )
