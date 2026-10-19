# Stock Balance with Expense

This standard Script Report belongs to `spcon`. The original Stock Balance
report and ERPNext source files are unchanged. Python calls `StockBalanceReport.run()`
and appends two currency columns. JavaScript mirrors the installed standard
Stock Balance filters, formatter, buttons and inventory-dimension setup; keep
that file in sync when upgrading ERPNext.

Both columns describe closing inventory at **To Date**, including expense in
opening stock, rather than expenses incurred only between From Date and To Date.
Stock Value Without Additional Expenses = standard Balance Value minus Expense
Included in Valuation. Original values, UOM conversions, filters, stock ageing,
reserved quantities and total-row behavior remain controlled by ERPNext.

## Expense provenance

* Replay uncancelled SLEs from the start of ledger history through To Date. Do
  not restrict this replay to selected items/warehouses: their stock can contain
  costs transferred from other items/warehouses. Output still uses standard filters.
* Submitted Landed Cost Item `applicable_charges` are grouped by receipt detail
  and distributed over that detail's stock quantity. Do not additionally count
  the receipt's `landed_cost_voucher_amount` or the LCV header total.
* ERPNext reposts LCV charges at the original receipt date. Historical report
  dates therefore reflect the currently reposted ledger, including LCVs submitted
  later. Cancelled LCVs and cancelled ledger entries are excluded.
* Tag each valuation bin with expense per stock unit. Match ERPNext FIFO order,
  outgoing-rate selection and equal-valuation-rate merging. Equal total rates
  combine expense by quantity-weighted averaging, matching the cost pool ERPNext
  actually maintains. Moving Average and LIFO queues are also implemented.
* Issues consume expense with stock. Transfers move the consumed expense to
  their destination without creating new expense. Manufacture/Repack propagate
  consumed expense to outputs whose rates derive from consumption, using the
  installed ERPNext finished-quantity rules. Fixed scrap and manually entered
  output rates do not inherit consumed expense.
* Manufacture adds each receiving detail's allocated `additional_cost` once in
  company currency. Do not add the Stock Entry's additional-cost header total.
  Repack propagates existing expense; its own additional costs are outside the
  requested manufacturing/LCV scope.
* Material Consumption for Manufacture expense is held by Work Order and passed
  once to Manufacture when no source rows occur in that manufacturing voucher.
* Sales returns restore expense from the referenced original issue detail,
  falling back to the original voucher/item weighted rate for legacy unlinked rows.
* Reconciliation retains expense on surviving units and resets valuation bins
  to the SLE's new rate. Newly counted stock and opening balances without source
  expense documents have zero attributable expense. A manual valuation change
  is not treated as a new LCV/manufacturing charge.

The current site has no serial/batch stock. The report explicitly rejects
serial/batch ledger history rather than estimating expense for those separately
valued units. Serial/batch support needs a separate provenance implementation.

## Validation

Read-only validation (does not create or submit stock transactions):

```sh
bench --site spcon.sanpra execute spcon.spcon.report.stock_balance_with_expense.test_stock_balance_with_expense.validate_site
```

Checks synthetic FIFO issues, equal-rate merging, rate-selected issues, moving
average, LIFO, negative stock, transfers and manufacturing with fixed scrap.
Audits reconstructed cost queues against every posted SLE's actual queue,
compares all original report fields and columns, and checks historical dates,
item/warehouse selection, variants, ageing and From Date independence.
