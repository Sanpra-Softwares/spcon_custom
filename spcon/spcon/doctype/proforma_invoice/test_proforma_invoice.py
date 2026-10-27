# Copyright (c) 2025, Sanpra and Contributors
# See license.txt

import unittest
from unittest.mock import patch

import frappe

from spcon.spcon.doctype.proforma_invoice import proforma_invoice as controller


class TestProformaInvoice(unittest.TestCase):
    def setUp(self):
        self.doc = frappe.new_doc("Proforma Invoice")
        self.doc.customer = "Test Customer"
        self.doc.company = "Test Company"
        self.doc.customer_address = "Previous Customer Address"
        self.doc.shipping_address_name = "Previous Shipping Address"
        self.doc.company_address = "Company Address"

    def test_customer_change_uses_new_customer_primary_addresses(self):
        defaults = frappe._dict(customer_address="New Customer Address", taxes_and_charges="GST")
        with patch.object(controller, "_get_party_details", return_value=defaults) as get_details:
            result = self.doc.get_customer_defaults(use_selected_addresses=False)
        self.assertIsNone(get_details.call_args.kwargs["party_address"])
        self.assertIsNone(get_details.call_args.kwargs["shipping_address"])
        self.assertEqual(result.customer_address, "New Customer Address")

    def test_address_change_keeps_selected_addresses(self):
        with patch.object(controller, "_get_party_details", return_value={}) as get_details:
            self.doc.get_customer_defaults()
        self.assertEqual(get_details.call_args.kwargs["party_address"], self.doc.customer_address)
        self.assertEqual(get_details.call_args.kwargs["shipping_address"], self.doc.shipping_address_name)

    def test_unsupported_regional_fields_are_not_added_to_document(self):
        defaults = {"taxes_and_charges": "GST", "taxes": [], "unsupported_field": "value"}
        with patch.object(controller, "_get_party_details", return_value=defaults):
            result = self.doc.get_customer_defaults()
        self.assertNotIn("unsupported_field", result)
        self.assertIn("taxes", result)

    def test_defaults_endpoint_rejects_other_doctypes(self):
        with self.assertRaises(frappe.PermissionError):
            controller.get_proforma_defaults({"doctype": "Sales Order"})

    def test_defaults_require_document_permission(self):
        with patch.object(controller.ProformaInvoice, "check_permission", side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                controller.get_proforma_defaults(self.doc.as_dict())

    def test_strict_pricing_registration_preserves_existing_allowlist(self):
        from erpnext.accounts.doctype.pricing_rule import pricing_rule

        with patch.object(pricing_rule, "PRICING_TRANSACTION_DOCTYPES", frozenset({"Sales Order"}), create=True):
            controller.register_pricing_doctype()
            controller.register_pricing_doctype()
            self.assertEqual(pricing_rule.PRICING_TRANSACTION_DOCTYPES, {"Sales Order", "Proforma Invoice"})
