
import frappe
from frappe.utils import getdate, add_days
from frappe.utils import get_first_day, get_last_day, nowdate
from frappe.utils import time_diff_in_hours, get_time
from frappe.utils import flt

# def apply_sandwich_rule_on_attendance_save(doc, method):
#     if doc.shift and doc.working_hours is not None:
#         # Fetch custom_work_hrs from the linked Shift Type
#         shift_type = frappe.get_value("Shift Type",{"name":doc.shift},"custom_working_hrs")
#         custom_work_hrs = shift_type # Ensure float type for calculations

#         # Calculate overtime
#         if doc.working_hours > custom_work_hrs:
#             doc.custom_over_time = doc.working_hours - custom_work_hrs
#         else:
#             doc.custom_over_time = 0

#     # Late Entry Update Status Half Day
#     if doc.status == "Present":
#         first_day, last_day = get_first_day(nowdate()), get_last_day(nowdate())
#         late_marks_count = frappe.db.get_all(
#             "Attendance",
#             filters={
#                 "employee": doc.employee,
#                 "late_entry": 1,
#                 "custom_late_mark_flag": 0,
#                 "attendance_date": ["between", [first_day, last_day]],
#             },
#             fields=["name"]
#         )co
#         # if len(late_marks_count) >= 4:
#         if len(late_marks_count) >= 3:
#             frappe.set_value("Attendance", doc.name, "status", "Half Day")
#             # frappe.set_value("Attendance", doc.name, "half_day_status", "Present")
#             # frappe.set_value("Attendance", doc.name, "leave_type", "Allocated Leave")
#             frappe.set_value("Attendance", doc.name, "leave_type", "Leave Without Pay")
#             for att in late_marks_count:
#                 frappe.set_value("Attendance", att["name"], "custom_late_mark_flag", 1)


from datetime import datetime, time, timedelta
from frappe.utils import get_first_day, get_last_day, today, getdate

def _get_shift_time_limits(shift):
    if not shift:
        return None

    settings = frappe.get_value(
        "Shift Type",
        shift,
        ["start_time", "end_time", "late_entry_grace_period", "early_exit_grace_period"],
        as_dict=True,
    )
    if not settings or settings.start_time is None or settings.end_time is None:
        return None

    base_date = datetime.min.date()
    late_in = datetime.combine(base_date, get_time(settings.start_time)) + timedelta(
        minutes=flt(settings.late_entry_grace_period)
    )
    early_out = datetime.combine(base_date, get_time(settings.end_time)) - timedelta(
        minutes=flt(settings.early_exit_grace_period)
    )
    return late_in.time(), early_out.time()


def _get_working_hours(first_in, last_out):
    if not first_in or not last_out:
        return 0

    return flt(time_diff_in_hours(last_out, first_in))


def _is_actual_half_day(working_hours, shift):
    if not shift or not working_hours:
        return False

    absent_threshold, half_day_threshold = frappe.get_value(
        "Shift Type",
        shift,
        ["working_hours_threshold_for_absent", "working_hours_threshold_for_half_day"],
    ) or (None, None)

    absent_threshold = flt(absent_threshold)
    half_day_threshold = flt(half_day_threshold)

    if not absent_threshold or not half_day_threshold:
        return False

    return absent_threshold < flt(working_hours) < half_day_threshold


def _get_late_days_upto(employee, month_start, att_date):
    start_dt = datetime.combine(month_start, time.min)
    end_dt = datetime.combine(att_date, time.max)

    rows = frappe.db.sql(
        """
        select
            date(`time`) as day,
            max(nullif(shift, '')) as shift,
            min(case when log_type='IN' then `time` end) as first_in,
            max(case when log_type='OUT' then `time` end) as last_out
        from `tabEmployee Checkin`
        where employee = %s and `time` between %s and %s
        group by date(`time`)
        """,
        (employee, start_dt, end_dt),
        as_dict=True,
    )

    late_days = []
    neglect_shift_cache = {}
    shift_time_cache = {}
    for row in rows:
        day = getdate(row.day)
        if day.weekday() == 6:
            continue

        shift = row.get("shift")
        if shift not in neglect_shift_cache:
            neglect_shift_cache[shift] = bool(
                shift
                and frappe.get_value(
                    "Shift Type",
                    shift,
                    "custom_neglect_late_entry_for_half_day_condition",
                )
            )

        # If enabled on Shift Type, don't count this day for half-day due to late/early
        if neglect_shift_cache.get(shift):
            continue

        if shift not in shift_time_cache:
            shift_time_cache[shift] = _get_shift_time_limits(shift)
        limits = shift_time_cache[shift]
        if limits is None:
            continue
        late_in_time, early_out_time = limits

        working_hours = _get_working_hours(row.first_in, row.last_out)
        if _is_actual_half_day(working_hours, shift):
            continue

        in_time = get_time(row.first_in) if row.first_in else None
        out_time = get_time(row.last_out) if row.last_out else None
        late = in_time is not None and in_time > late_in_time
        early = out_time is not None and out_time < early_out_time
        if late or early:
            late_days.append(day)
    return sorted(set(late_days))
@frappe.whitelist()
def apply_sandwich_rule_on_attendance_save(doc, method=None):
    if not doc.leave_application and not doc.custom_negligent_late_entry:
        emp = doc.employee
        att_date = getdate(doc.attendance_date)

        if doc.status == "Half Day" or _is_actual_half_day(doc.working_hours, doc.shift):
            return

        # ⛔ Skip Sunday
        if att_date.weekday() == 6:
            return

        # Month start
        start_date = get_first_day(att_date)

        late_days = _get_late_days_upto(emp, start_date, att_date)

        if att_date not in late_days:
            return

        count = late_days.index(att_date) + 1

        # Apply late entry / early exit rule only for full-day attendance.
        if count <= 3:
            return

        doc.status = "Half Day"
        if doc.meta.has_field("half_day_status"):
            doc.half_day_status = "Absent"
        doc.leave_type = "Leave Without Pay"
        if doc.meta.has_field("half_day_date"):
            doc.half_day_date = att_date
        if doc.meta.has_field("half_day"):
            doc.half_day = 1

        if doc.docstatus == 1:
            updates = {
                "status": doc.status,
                "leave_type": doc.leave_type,
                "custom_late_entry_early_exit": 1,
            }
            if doc.meta.has_field("half_day_status"):
                updates["half_day_status"] = doc.half_day_status
            if doc.meta.has_field("half_day_date"):
                updates["half_day_date"] = doc.half_day_date
            if doc.meta.has_field("half_day"):
                updates["half_day"] = doc.half_day

            frappe.db.set_value("Attendance", doc.name, updates)
