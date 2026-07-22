import math

from common_functions.db import get_db_cursor
from common_functions import config

from admin_functions.reports import _is_valid_uuid, _parse_date

# All bill-derived sections (status distribution, spend heatmap, pending-aging-by-unit, and the
# money KPIs) share this join base and are scoped through it by unit/account/category/date.
# The unit/account counts (incl. account-by-category) are the only date-agnostic sections --
# unit_master/account_master have no date column, so the date range cannot apply to them (they
# stay scoped by unit/account/category only).
_ALLOC_BASE = """
    FROM bill_unit_details bud
    JOIN bill ON bud.bill_id = bill.bill_id
    JOIN account_master am ON bill.account_id = am.account_id
"""

# Age buckets for still-pending charges (days since the bill's bill_date).
_PENDING_AGE_BUCKETS = [
    ("0-7d", "(CURRENT_DATE - bill.bill_date) <= 7"),
    ("8-15d", "(CURRENT_DATE - bill.bill_date) BETWEEN 8 AND 15"),
    ("16-30d", "(CURRENT_DATE - bill.bill_date) BETWEEN 16 AND 30"),
    ("31+d", "(CURRENT_DATE - bill.bill_date) >= 31"),
]

_PENDING_STATUS = "Pending Approval"

# Charge (per-unit allocation) statuses, fixed order for a stable donut.
_CHARGE_STATUSES = ("Pending Approval", "Approved", "Rejected", "Resolved")

# Bill-level statuses, fixed order for a stable distribution donut.
_BILL_STATUSES = ("Pending Approval", "Approved", "On Hold", "Resolved")

# Age buckets used by the Unit Accountant dashboard's pending-aging widget (days since bill_date).
# (lo, hi) inclusive; hi=None means open-ended.
_UA_AGE_BUCKETS = (("0-3d", 0, 3), ("4-7d", 4, 7), ("8-15d", 8, 15), ("16+d", 16, None))


def _f(value):
    """Decimal/None -> float, for a clean numeric JSON contract (response.py serializes
    Decimals via str() otherwise, which would surface amounts as strings)."""
    return float(value) if value is not None else 0.0


def _alloc_conditions(uuid_unit_id, uuid_account_id, str_utility_category_name, date_from_date, date_to_date):
    """Filter fragments over the _ALLOC_BASE join (bud/bill/am aliases)."""
    clauses = []
    values = []
    if uuid_unit_id:
        clauses.append("bud.unit_id = %s")
        values.append(uuid_unit_id)
    if uuid_account_id:
        clauses.append("bill.account_id = %s")
        values.append(uuid_account_id)
    if str_utility_category_name:
        clauses.append("am.utility_category_name = %s")
        values.append(str_utility_category_name)
    if date_from_date:
        clauses.append("bill.bill_date >= %s")
        values.append(date_from_date)
    if date_to_date:
        clauses.append("bill.bill_date <= %s")
        values.append(date_to_date)
    return clauses, values


def _where(*clause_groups):
    clauses = [c for group in clause_groups for c in group]
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


def _build_amount_distribution(rows, num_bins=12, z_threshold=2.0, max_anomalies=10):
    """From bill rows, compute a normal distribution (mean/std), a histogram, and the outliers
    beyond +/- z_threshold sigma -- powering the leadership bell-curve / anomaly view."""
    amounts = [float(r["total_amount"]) for r in rows if r["total_amount"] is not None]
    n = len(amounts)
    if n < 2:
        only = amounts[0] if n else 0.0
        return {
            "count": n, "mean": only, "std": 0.0, "min": only, "max": only,
            "lower_threshold": only, "upper_threshold": only, "bins": [], "anomalies": [],
        }

    mean = sum(amounts) / n
    std = math.sqrt(sum((a - mean) ** 2 for a in amounts) / (n - 1))  # sample std dev
    lo, hi = min(amounts), max(amounts)

    bins = []
    if hi > lo:
        width = (hi - lo) / num_bins
        counts = [0] * num_bins
        for a in amounts:
            idx = min(int((a - lo) / width), num_bins - 1)
            counts[idx] += 1
        bins = [
            {"x0": round(lo + i * width, 2), "x1": round(lo + (i + 1) * width, 2), "count": counts[i]}
            for i in range(num_bins)
        ]
    else:
        bins = [{"x0": lo, "x1": hi, "count": n}]

    anomalies = []
    if std > 0:
        for r in rows:
            amount = float(r["total_amount"]) if r["total_amount"] is not None else 0.0
            z = (amount - mean) / std
            if abs(z) >= z_threshold:
                period = r["billing_period"]
                anomalies.append({
                    "bill_id": r["bill_id"],
                    "account_id": r["account_id"],
                    "account_number": r["account_number"],
                    "account_name": r["account_name"],
                    "billing_period": period.isoformat() if hasattr(period, "isoformat") else str(period),
                    "amount": round(amount, 2),
                    "z": round(z, 2),
                })
        anomalies.sort(key=lambda x: abs(x["z"]), reverse=True)
        anomalies = anomalies[:max_anomalies]

    return {
        "count": n, "mean": round(mean, 2), "std": round(std, 2), "min": round(lo, 2), "max": round(hi, 2),
        "lower_threshold": round(mean - z_threshold * std, 2), "upper_threshold": round(mean + z_threshold * std, 2),
        "bins": bins, "anomalies": anomalies,
    }


def f_get_admin_dashboard_summary(
    uuid_caller_user_id,
    str_caller_role,
    uuid_unit_id,
    uuid_account_id,
    str_utility_category_name,
    date_from_date,
    date_to_date,
):
    try:
        # 1. Validate input
        if uuid_unit_id and not _is_valid_uuid(uuid_unit_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if uuid_account_id and not _is_valid_uuid(uuid_account_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        try:
            date_from_date = _parse_date(date_from_date)
            date_to_date = _parse_date(date_to_date)
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        if date_from_date and date_to_date and date_from_date > date_to_date:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Administrator":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        # Note: unit_id/account_id are NOT existence-checked -- a stale/nonexistent id simply
        # narrows the affected section(s) to zero rather than failing the whole dashboard.
        alloc_clauses, alloc_values = _alloc_conditions(
            uuid_unit_id, uuid_account_id, str_utility_category_name, date_from_date, date_to_date
        )

        with get_db_cursor() as cursor:
            # ---- Section: unit count (date-agnostic) ----
            unit_clauses = []
            unit_values = []
            unit_join = "FROM unit_master"
            if uuid_unit_id:
                unit_clauses.append("unit_master.unit_id = %s")
                unit_values.append(uuid_unit_id)
            if uuid_account_id or str_utility_category_name:
                unit_join += """
                    JOIN account_unit_details
                        ON account_unit_details.unit_id = unit_master.unit_id
                        AND account_unit_details.mapping_status = 'Active'
                    JOIN account_master
                        ON account_master.account_id = account_unit_details.account_id
                """
                if uuid_account_id:
                    unit_clauses.append("account_unit_details.account_id = %s")
                    unit_values.append(uuid_account_id)
                if str_utility_category_name:
                    unit_clauses.append("account_master.utility_category_name = %s")
                    unit_values.append(str_utility_category_name)
            cursor.execute(
                f"SELECT COUNT(DISTINCT unit_master.unit_id) AS n {unit_join} {_where(unit_clauses)}",
                tuple(unit_values),
            )
            total_units = cursor.fetchone()["n"]

            # ---- Section: account count + account-by-category breakdown (date-agnostic) ----
            acct_clauses = []
            acct_values = []
            acct_join = "FROM account_master"
            if uuid_unit_id:
                acct_join += """
                    JOIN account_unit_details
                        ON account_unit_details.account_id = account_master.account_id
                        AND account_unit_details.mapping_status = 'Active'
                """
                acct_clauses.append("account_unit_details.unit_id = %s")
                acct_values.append(uuid_unit_id)
            if uuid_account_id:
                acct_clauses.append("account_master.account_id = %s")
                acct_values.append(uuid_account_id)
            if str_utility_category_name:
                acct_clauses.append("account_master.utility_category_name = %s")
                acct_values.append(str_utility_category_name)
            cursor.execute(
                f"SELECT COUNT(DISTINCT account_master.account_id) AS n {acct_join} {_where(acct_clauses)}",
                tuple(acct_values),
            )
            total_accounts = cursor.fetchone()["n"]

            cursor.execute(
                f"""
                SELECT account_master.utility_category_name AS utility_category_name,
                       COUNT(DISTINCT account_master.account_id) AS account_count
                {acct_join}
                {_where(acct_clauses)}
                GROUP BY account_master.utility_category_name
                ORDER BY account_count DESC, account_master.utility_category_name
                """,
                tuple(acct_values),
            )
            account_by_category = [
                {"utility_category_name": r["utility_category_name"], "account_count": r["account_count"]}
                for r in cursor.fetchall()
            ]

            # ---- KPIs: total billed + pending (money via allocations, count via distinct bill) ----
            cursor.execute(
                f"""
                SELECT COALESCE(SUM(bud.allocated_amount), 0) AS total_billed,
                       COUNT(DISTINCT bill.bill_id) AS total_bills
                {_ALLOC_BASE}
                {_where(alloc_clauses)}
                """,
                tuple(alloc_values),
            )
            billed_row = cursor.fetchone()

            cursor.execute(
                f"""
                SELECT COALESCE(SUM(bud.allocated_amount), 0) AS pending_amount,
                       COUNT(DISTINCT bill.bill_id) AS pending_bills
                {_ALLOC_BASE}
                {_where(["bill.bill_status = %s"], alloc_clauses)}
                """,
                tuple([_PENDING_STATUS] + alloc_values),
            )
            pending_row = cursor.fetchone()

            # ---- Bill status distribution ----
            cursor.execute(
                f"""
                SELECT bill.bill_status AS bill_status, COUNT(DISTINCT bill.bill_id) AS bill_count
                {_ALLOC_BASE}
                {_where(alloc_clauses)}
                GROUP BY bill.bill_status
                """,
                tuple(alloc_values),
            )
            status_distribution = [
                {"bill_status": r["bill_status"], "bill_count": r["bill_count"]} for r in cursor.fetchall()
            ]

            # ---- Category x month spend heatmap ----
            cursor.execute(
                f"""
                SELECT am.utility_category_name AS utility_category_name,
                       bill.billing_period AS billing_period,
                       COALESCE(SUM(bud.allocated_amount), 0) AS amount
                {_ALLOC_BASE}
                {_where(alloc_clauses)}
                GROUP BY am.utility_category_name, bill.billing_period
                """,
                tuple(alloc_values),
            )
            cells = []
            periods = set()
            categories = set()
            for r in cursor.fetchall():
                period = r["billing_period"]
                period_str = period.isoformat() if hasattr(period, "isoformat") else str(period)
                category = r["utility_category_name"]
                cells.append(
                    {
                        "utility_category_name": category,
                        "billing_period": period_str,
                        "amount": _f(r["amount"]),
                    }
                )
                periods.add(period_str)
                categories.add(category)

            # ---- Unit pending aging: per-unit pending charges bucketed by age (from bill_date) ----
            bucket_selects = ",\n".join(
                f"COUNT(*) FILTER (WHERE {cond}) AS bucket_{i}" for i, (_, cond) in enumerate(_PENDING_AGE_BUCKETS)
            )
            cursor.execute(
                f"""
                SELECT bud.unit_id AS unit_id,
                       um.unit_code AS unit_code,
                       um.unit_name AS unit_name,
                       COUNT(*) AS total_pending,
                       COALESCE(SUM(bud.allocated_amount), 0) AS pending_amount,
                       MAX(CURRENT_DATE - bill.bill_date) AS oldest_days,
                       {bucket_selects}
                {_ALLOC_BASE}
                JOIN unit_master um ON bud.unit_id = um.unit_id
                {_where(["bill.bill_status = %s"], alloc_clauses)}
                GROUP BY bud.unit_id, um.unit_code, um.unit_name
                ORDER BY oldest_days DESC NULLS LAST
                """,
                tuple([_PENDING_STATUS] + alloc_values),
            )
            pending_aging_by_unit = [
                {
                    "unit_id": r["unit_id"],
                    "unit_code": r["unit_code"],
                    "unit_name": r["unit_name"],
                    "total_pending": r["total_pending"],
                    "pending_amount": _f(r["pending_amount"]),
                    "oldest_days": r["oldest_days"] if r["oldest_days"] is not None else 0,
                    "buckets": [
                        {"label": label, "count": r[f"bucket_{i}"]}
                        for i, (label, _) in enumerate(_PENDING_AGE_BUCKETS)
                    ],
                }
                for r in cursor.fetchall()
            ]

        # No STATUS_NO_DATA_FOUND: zero counts / empty sections are a valid 200 dashboard,
        # unlike the paginated report functions in reports.py. Do not add a "not records" 404 here.
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {
                "kpis": {
                    "total_units": total_units,
                    "total_accounts": total_accounts,
                    "total_bills": billed_row["total_bills"],
                    "total_billed": _f(billed_row["total_billed"]),
                    "pending_bills": pending_row["pending_bills"],
                    "pending_amount": _f(pending_row["pending_amount"]),
                },
                "account_by_category": account_by_category,
                "status_distribution": status_distribution,
                "category_month_heatmap": {
                    "periods": sorted(periods),
                    "categories": sorted(categories),
                    "cells": cells,
                },
                "pending_aging_by_unit": pending_aging_by_unit,
            },
        )

    except Exception as ex:
        # System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_get_leadership_dashboard_summary(
    uuid_caller_user_id,
    str_caller_role,
    str_utility_category_name,
    date_from_date,
    date_to_date,
):
    """Server-computed org-wide analytics for the Leadership dashboard (read-only oversight).

    Leadership sees the whole organisation (no unit scoping). Returns money KPIs, the bill-status
    mix, a monthly spend-by-category trend, and the top units/accounts by spend. Scoped by an
    optional utility category and a bill_date range. Never returns 404 -- zeros are valid.
    """
    try:
        # 1. Validate input
        try:
            date_from_date = _parse_date(date_from_date)
            date_to_date = _parse_date(date_to_date)
        except ValueError:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None
        if date_from_date and date_to_date and date_from_date > date_to_date:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check
        if str_caller_role != "Leadership":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        # unit/account are always None for Leadership (org-wide); category/date scope everything.
        alloc_clauses, alloc_values = _alloc_conditions(None, None, str_utility_category_name, date_from_date, date_to_date)

        with get_db_cursor() as cursor:
            # Money totals
            cursor.execute(
                f"""
                SELECT COALESCE(SUM(bud.allocated_amount), 0) AS total_billed,
                       COUNT(DISTINCT bill.bill_id) AS total_bills
                {_ALLOC_BASE}
                {_where(alloc_clauses)}
                """,
                tuple(alloc_values),
            )
            billed_row = cursor.fetchone()

            # Bill counts by status (drives KPIs + the status distribution)
            cursor.execute(
                f"""
                SELECT bill.bill_status AS bill_status, COUNT(DISTINCT bill.bill_id) AS bill_count
                {_ALLOC_BASE}
                {_where(alloc_clauses)}
                GROUP BY bill.bill_status
                """,
                tuple(alloc_values),
            )
            counts_by_status = {r["bill_status"]: r["bill_count"] for r in cursor.fetchall()}

            # Pending amount (money awaiting approval)
            cursor.execute(
                f"""
                SELECT COALESCE(SUM(bud.allocated_amount), 0) AS pending_amount
                {_ALLOC_BASE}
                {_where(["bill.bill_status = %s"], alloc_clauses)}
                """,
                tuple([_PENDING_STATUS] + alloc_values),
            )
            pending_amount = cursor.fetchone()["pending_amount"]

            # Monthly spend by category (stacked-bar trend over >= 1 year)
            cursor.execute(
                f"""
                SELECT bill.billing_period AS billing_period,
                       am.utility_category_name AS utility_category_name,
                       COALESCE(SUM(bud.allocated_amount), 0) AS amount
                {_ALLOC_BASE}
                {_where(alloc_clauses)}
                GROUP BY bill.billing_period, am.utility_category_name
                ORDER BY bill.billing_period
                """,
                tuple(alloc_values),
            )
            mbc_cells, mbc_periods, mbc_categories = [], set(), set()
            for r in cursor.fetchall():
                p = r["billing_period"]
                period_str = p.isoformat() if hasattr(p, "isoformat") else str(p)
                cat = r["utility_category_name"]
                mbc_cells.append({"billing_period": period_str, "utility_category_name": cat, "amount": _f(r["amount"])})
                mbc_periods.add(period_str)
                mbc_categories.add(cat)

            # Top units by spend
            cursor.execute(
                f"""
                SELECT bud.unit_id AS unit_id, um.unit_code AS unit_code, um.unit_name AS unit_name,
                       COALESCE(SUM(bud.allocated_amount), 0) AS amount
                {_ALLOC_BASE}
                JOIN unit_master um ON bud.unit_id = um.unit_id
                {_where(alloc_clauses)}
                GROUP BY bud.unit_id, um.unit_code, um.unit_name
                ORDER BY amount DESC
                LIMIT 8
                """,
                tuple(alloc_values),
            )
            top_units = [
                {"unit_id": r["unit_id"], "unit_code": r["unit_code"], "unit_name": r["unit_name"], "amount": _f(r["amount"])}
                for r in cursor.fetchall()
            ]

            # Per-category bill-amount distributions for the anomaly bell curve. Each bill is
            # scored against its OWN category's mean/std (category-wise anomaly), so a large
            # electricity bill isn't flagged against small telecom bills. Bill-level; the
            # category/date filters reference bill/am only.
            cursor.execute(
                f"""
                SELECT bill.bill_id AS bill_id, bill.account_id AS account_id,
                       bill.total_amount AS total_amount, bill.billing_period AS billing_period,
                       am.account_number AS account_number, am.account_name AS account_name,
                       am.utility_category_name AS utility_category_name
                FROM bill
                JOIN account_master am ON bill.account_id = am.account_id
                {_where(alloc_clauses)}
                """,
                tuple(alloc_values),
            )
            dist_rows = cursor.fetchall()

            # Active accounts for the Account Bifurcation Breakdown drill-down picker --
            # date/category-agnostic by design, since a picker shouldn't shrink just because the
            # current filter has no bills yet.
            cursor.execute(
                "SELECT account_id, account_number, account_name, utility_category_name "
                "FROM account_master WHERE account_status = 'Active' ORDER BY account_number"
            )
            all_accounts = [
                {
                    "account_id": r["account_id"],
                    "account_number": r["account_number"],
                    "account_name": r["account_name"],
                    "utility_category_name": r["utility_category_name"],
                }
                for r in cursor.fetchall()
            ]

        rows_by_category = {}
        for r in dist_rows:
            rows_by_category.setdefault(r["utility_category_name"], []).append(r)
        category_distributions = []
        for cat, rows in rows_by_category.items():
            dist = _build_amount_distribution(rows)
            dist["utility_category_name"] = cat
            category_distributions.append(dist)
        category_distributions.sort(key=lambda d: d["count"], reverse=True)

        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {
                "kpis": {
                    "total_bills": billed_row["total_bills"],
                    "total_billed": _f(billed_row["total_billed"]),
                    "pending_bills": counts_by_status.get("Pending Approval", 0),
                    "pending_amount": _f(pending_amount),
                    "approved_bills": counts_by_status.get("Approved", 0),
                    "on_hold_bills": counts_by_status.get("On Hold", 0),
                },
                "status_distribution": [
                    {"bill_status": s, "bill_count": counts_by_status.get(s, 0)} for s in _BILL_STATUSES
                ],
                "top_units": top_units,
                "monthly_by_category": {
                    "periods": sorted(mbc_periods),
                    "categories": sorted(mbc_categories),
                    "cells": mbc_cells,
                },
                "category_distributions": category_distributions,
                "accounts": all_accounts,
            },
        )

    except Exception as ex:
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None


def f_get_unit_accountant_dashboard_summary(uuid_caller_user_id, str_caller_role):
    """Server-side summary for the Unit Accountant dashboard -- the counterpart to
    f_get_admin_dashboard_summary. Everything is scoped to the caller's OWN active unit
    assignments (their "my dashboard"); there are no client-supplied filters.

    Returns kpis (units assigned, pending charges + amount, blocked), the charge-status
    distribution, per-unit pending workload (all assigned units, incl. zero-pending), and the
    pending-aging buckets. Like the admin summary it never returns 404 -- an accountant with no
    assignments or no pending work is a valid, all-zero 200.
    """
    try:
        # 1. Validate input
        if not uuid_caller_user_id or not _is_valid_uuid(uuid_caller_user_id):
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. RBAC check -- this is the Unit Accountant's own dashboard.
        if str_caller_role != "Unit Accountant":
            return config.STATUS_AUTHORIZATION_FAILURE, config.DESC_AUTHORIZATION_FAILURE, config.ERR_ROLE_NOT_PERMITTED, None

        with get_db_cursor() as cursor:
            # 3. Resolve the caller's active unit assignments (their scope).
            cursor.execute(
                """
                SELECT uud.unit_id, um.unit_code, um.unit_name
                FROM user_unit_details uud
                JOIN unit_master um ON um.unit_id = uud.unit_id
                WHERE uud.user_id = %s AND uud.assignment_status = 'Active'
                ORDER BY um.unit_code
                """,
                (uuid_caller_user_id,),
            )
            assigned = cursor.fetchall()
            unit_ids = [r["unit_id"] for r in assigned]

            # 4. Fetch the caller's charges (per-unit allocations) with a server-derived
            # current_status and age. Aggregation below happens in this function (server-side),
            # not the browser. Empty assignment list -> no charges (skip the query).
            charges = []
            if unit_ids:
                cursor.execute(
                    """
                    SELECT bud.unit_id AS unit_id,
                           bud.allocated_amount AS allocated_amount,
                           b.billing_period AS billing_period,
                           (CURRENT_DATE - b.bill_date) AS age_days,
                           COALESCE(
                               (SELECT ahl.history_status FROM approval_history_log ahl
                                WHERE ahl.allocation_id = bud.allocation_id
                                ORDER BY ahl.performed_on DESC LIMIT 1),
                               %s
                           ) AS current_status
                    FROM bill_unit_details bud
                    JOIN bill b ON bud.bill_id = b.bill_id
                    WHERE bud.unit_id = ANY(%s::uuid[])
                    """,
                    (_PENDING_STATUS, unit_ids),
                )
                charges = cursor.fetchall()

        # 5. Aggregate (server-side)
        status_counts = {s: 0 for s in _CHARGE_STATUSES}
        per_unit = {
            r["unit_id"]: {
                "unit_id": r["unit_id"],
                "unit_code": r["unit_code"],
                "unit_name": r["unit_name"],
                "pending_count": 0,
                "pending_amount": 0.0,
            }
            for r in assigned
        }
        aging = {label: 0 for (label, _, _) in _UA_AGE_BUCKETS}
        pending_count = 0
        pending_amount = 0.0
        blocked = 0

        # Unit x month spend heatmap (total allocated spend, all statuses) -- the Unit Accountant
        # counterpart to the admin's category x month heatmap. Rows = the caller's units (by code),
        # columns = billing periods; derived from the same charge rows to avoid a second query.
        heatmap_acc = {}  # (unit_code, period_str) -> amount
        heatmap_periods = set()
        heatmap_units = set()

        for ch in charges:
            status = ch["current_status"]
            status_counts[status] = status_counts.get(status, 0) + 1
            if status == "Rejected":
                blocked += 1

            amount_all = float(ch["allocated_amount"]) if ch["allocated_amount"] is not None else 0.0
            period = ch["billing_period"]
            period_str = period.isoformat() if hasattr(period, "isoformat") else str(period)
            unit_row_ref = per_unit.get(ch["unit_id"])
            if unit_row_ref is not None:
                code = unit_row_ref["unit_code"]
                key = (code, period_str)
                heatmap_acc[key] = heatmap_acc.get(key, 0.0) + amount_all
                heatmap_periods.add(period_str)
                heatmap_units.add(code)
            if status == _PENDING_STATUS:
                amount = float(ch["allocated_amount"]) if ch["allocated_amount"] is not None else 0.0
                pending_count += 1
                pending_amount += amount
                unit_row = per_unit.get(ch["unit_id"])
                if unit_row is not None:
                    unit_row["pending_count"] += 1
                    unit_row["pending_amount"] += amount
                days = max(0, int(ch["age_days"])) if ch["age_days"] is not None else 0
                for label, lo, hi in _UA_AGE_BUCKETS:
                    if days >= lo and (hi is None or days <= hi):
                        aging[label] += 1
                        break

        my_units = []
        for u in per_unit.values():
            u["pending_amount"] = round(u["pending_amount"], 2)
            my_units.append(u)

        # 6. Success (never 404 -- zeros are a valid dashboard, matching the admin summary)
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {
                "kpis": {
                    "units_assigned": len(assigned),
                    "pending_charges": pending_count,
                    "pending_amount": round(pending_amount, 2),
                    "blocked": blocked,
                },
                "charge_status_distribution": [
                    {"status": s, "count": status_counts[s]} for s in _CHARGE_STATUSES
                ],
                "my_units": my_units,
                "pending_aging": [{"label": label, "count": aging[label]} for (label, _, _) in _UA_AGE_BUCKETS],
                "unit_month_heatmap": {
                    "periods": sorted(heatmap_periods),
                    "units": sorted(heatmap_units),
                    "cells": [
                        {"unit_code": code, "billing_period": period_str, "amount": round(amt, 2)}
                        for (code, period_str), amt in heatmap_acc.items()
                    ],
                },
            },
        )

    except Exception as ex:
        # System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
