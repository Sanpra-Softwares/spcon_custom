// Copyright (c) 2025, Sanpra and contributors
// For license information, please see license.txt

cur_frm.cscript.tax_table = "Sales Taxes and Charges";
erpnext.accounts.taxes.setup_tax_filters("Sales Taxes and Charges");
erpnext.accounts.taxes.setup_tax_validations("Proforma Invoice");
erpnext.sales_common.setup_selling_controller();

frappe.ui.form.on("Proforma Invoice", {
    setup(frm) {
        frm.add_fetch("customer", "tax_id", "tax_id");
        frm.set_query("sales_person", "sales_team", () => ({filters: {is_group: 0, enabled: 1}}));
        frm.set_df_property("packed_items", "cannot_add_rows", true);
        frm.set_df_property("packed_items", "cannot_delete_rows", true);
    },
    onload(frm) {
        if (!frm.doc.transaction_date) {
            frm.set_value("transaction_date", frappe.datetime.get_today());
        }
    },
    refresh(frm) {
        if (frm.doc.docstatus === 0 && frappe.model.can_read("Quotation")) {
            frm.add_custom_button(__("Quotation"), () => {
                erpnext.utils.map_current_doc({
                    method: "spcon.spcon.doctype.proforma_invoice.proforma_invoice.make_proforma_invoice",
                    source_doctype: "Quotation",
                    target: frm,
                    setters: {party_name: frm.doc.customer || undefined},
                    get_query_filters: {docstatus: 1, quotation_to: "Customer", company: frm.doc.company},
                });
            }, __("Get Items From"));
        }
        if (frm.doc.docstatus === 1 && frappe.model.can_create("Sales Order")) {
            frm.add_custom_button(__("Sales Order"), () => {
                frappe.model.open_mapped_doc({
                    method: "spcon.spcon.doctype.proforma_invoice.proforma_invoice.make_sales_order",
                    frm,
                });
            }, __("Create"));
        }
    },
    delivery_date(frm) {
        return Promise.all((frm.doc.items || []).map(row =>
            frappe.model.set_value(row.doctype, row.name, "delivery_date", frm.doc.delivery_date)
        ));
    },
});

erpnext.selling.ProformaInvoiceController = class ProformaInvoiceController extends erpnext.selling.SellingController {
    calculate_totals() {
        // Use the standard selling formula for this custom selling DocType.
        const doc = this.frm.doc;
        doc.grand_total = flt(doc.taxes?.length
            ? doc.taxes[doc.taxes.length - 1].total + this.grand_total_diff : doc.net_total);
        doc.total_taxes_and_charges = flt(
            doc.grand_total - doc.net_total - this.grand_total_diff,
            precision("total_taxes_and_charges")
        );
        this.set_in_company_currency(doc, ["total_taxes_and_charges"]);
        doc.base_grand_total = doc.total_taxes_and_charges
            ? flt(doc.grand_total * doc.conversion_rate) : doc.base_net_total;
        frappe.model.round_floats_in(doc, ["grand_total", "base_grand_total"]);
        this.set_rounded_total();
    }

    async calculate_taxes_and_totals(...args) {
        await super.calculate_taxes_and_totals(...args);
        this.calculate_commission();
        this.calculate_contribution();
    }

    context_key() {
        return JSON.stringify(["company", "customer", "customer_address", "shipping_address_name",
            "company_address", "tax_category", "taxes_and_charges", "transaction_date", "currency", "selling_price_list"].map(field => this.frm.doc[field] || ""));
    }

    async company() {
        super.company();
        await frappe.after_ajax();
        return this.customer();
    }

    async transaction_date() {
        super.transaction_date();
        await frappe.after_ajax();
        return this.update_proforma_taxes();
    }

    async customer() {
        if (!this.frm.doc.customer) {
            this.defaults_request = (this.defaults_request || 0) + 1;
            return this.frm.set_value({taxes_and_charges: "", taxes: []});
        }
        if (!this.frm.doc.company) return;
        const context = this.context_key();
        const request = this.defaults_request = (this.defaults_request || 0) + 1;
        const {message} = await frappe.call({
            method: "spcon.spcon.doctype.proforma_invoice.proforma_invoice.get_proforma_defaults",
            args: {doc: this.frm.doc},
        });
        if (request !== this.defaults_request || context !== this.context_key() || !message) return;
        this.frm.updating_party_details = true;
        try {
            await this.frm.set_value(message);
        } finally {
            this.frm.updating_party_details = false;
        }
        await this.update_item_tax_map();
        await this.apply_price_list();
        return this.calculate_taxes_and_totals();
    }

    async update_proforma_taxes(use_tax_category = false) {
        if (this.frm.updating_party_details || this.frm.doc.docstatus !== 0 ||
            !this.frm.doc.customer || !this.frm.doc.company) return;
        const context = this.context_key();
        const request = this.defaults_request = (this.defaults_request || 0) + 1;
        const {message} = await frappe.call({
            method: "spcon.spcon.doctype.proforma_invoice.proforma_invoice.get_proforma_defaults",
            args: {doc: this.frm.doc, taxes_only: true, use_tax_category},
        });
        if (request !== this.defaults_request || context !== this.context_key() || !message) return;
        this.frm.updating_party_details = true;
        try {
            // Setting the standard template triggers its normal row loader.
            await this.frm.set_value(message);
        } finally {
            this.frm.updating_party_details = false;
        }
        await this.update_item_tax_map();
        return this.calculate_taxes_and_totals();
    }

    customer_address() {
        erpnext.utils.get_address_display(this.frm, "customer_address");
        return this.update_proforma_taxes();
    }

    shipping_address_name() {
        erpnext.utils.get_address_display(this.frm, "shipping_address_name", "shipping_address");
        return this.update_proforma_taxes();
    }

    company_address() {
        super.company_address();
        return this.update_proforma_taxes();
    }

    tax_category() {
        return this.update_proforma_taxes(true);
    }
};

frappe.ui.form.on("Proforma Invoice Item", {
    item_code(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.delivery_date && frm.doc.delivery_date) {
            return frappe.model.set_value(cdt, cdn, "delivery_date", frm.doc.delivery_date);
        }
    },
});

extend_cscript(cur_frm.cscript, new erpnext.selling.ProformaInvoiceController({frm: cur_frm}));
