from common_functions.db import get_db_cursor
from common_functions import config


def f_get_current_user(uuid_caller_user_id, str_caller_role):
    """Return the authenticated caller's own profile, role, and active unit assignments.

    Role-agnostic: any valid session may read its own identity, so there is NO RBAC check
    here. This is the only way a non-Administrator (e.g. a Unit Accountant) can learn its
    role and unit scope on the client -- role is never embedded in the JWT (resolved fresh
    per request, see common_functions/auth.py) and filter_user_master / filter_user_unit_details
    are Administrator-only.
    """
    try:
        # 1. Validate input
        if not uuid_caller_user_id:
            return config.STATUS_INVALID_INPUT, config.DESC_INVALID_INPUT, "", None

        # 2. No RBAC check -- a session is always allowed to read its own identity.
        with get_db_cursor() as cursor:
            # 3. Resolve the caller's profile + role
            cursor.execute(
                """
                SELECT u.user_id, u.full_name, u.email, r.role_name
                FROM user_master u
                JOIN role_master r ON r.role_id = u.role_id
                WHERE u.user_id = %s
                """,
                (uuid_caller_user_id,),
            )
            user_row = cursor.fetchone()
            if not user_row:
                return config.STATUS_NO_DATA_FOUND, config.DESC_NO_DATA_FOUND, config.ERR_NO_DATA_FOUND, None

            # 4. Resolve active unit assignments (empty for Administrator/Leadership)
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
            assigned_units = [
                {"unit_id": r["unit_id"], "unit_code": r["unit_code"], "unit_name": r["unit_name"]}
                for r in cursor.fetchall()
            ]

        # 5. Success
        return (
            config.STATUS_SUCCESS,
            config.DESC_SUCCESS,
            "",
            {
                "user_id": user_row["user_id"],
                "full_name": user_row["full_name"],
                "email": user_row["email"],
                "role": user_row["role_name"],
                "assigned_units": assigned_units,
            },
        )

    except Exception as ex:
        # 6. System exception
        return config.STATUS_INTERNAL_SYSTEM_EXCEPTION, config.DESC_SYSTEM_EXCEPTION, str(ex), None
