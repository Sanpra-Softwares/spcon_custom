import frappe
from frappe import _
from frappe.utils import getdate, nowdate

@frappe.whitelist()
def set_leave_type_absent(doc,method):
    leave_without_pay = frappe.get_value("Leave Type", doc.leave_type, "is_lwp")
    if leave_without_pay==1 and doc.half_day == 0:
        attendance = frappe.get_all("Attendance", {"leave_application": doc.name}, pluck="name")
        # frappe.throw(str(attendance))
        for att in attendance:
            # pass 
            frappe.set_value("Attendance", att, "status", "Absent")

    # if leave_without_pay==1 and doc.half_day == 1:
    #     attendance = frappe.get_all("Attendance", {"leave_application": doc.name}, pluck="name")
    #     for att in attendance:
    #         frappe.set_value("Attendance", att, "status", "Half Day")
    #         frappe.set_value("Attendance", att, "half_day_status", "Absent") 



# def validate_backdated_leave(doc, method=None):
    # Backdated leave restriction disabled.
    # # Skip for Leave Approvers
    # if "Leave Approver" in frappe.get_roles():
    #     return
    #
    # today = getdate(nowdate())
    # from_date = getdate(doc.from_date)
    #
    # days_difference = (today - from_date).days
    #
    # # Allow today, future dates, and only 1 day backdated
    # if days_difference > 1:
    #     frappe.throw(
    #         _("You can apply leave only for yesterday or a future date. Leave applications older than 1 day are not allowed.")
    #     )