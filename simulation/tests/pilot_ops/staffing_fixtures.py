"""Closed staffing fixtures shared by the staffing advisory domain tests."""

from __future__ import annotations

from copy import deepcopy


def roster_import_request() -> dict[str, object]:
    """Return a fresh valid weekly roster import request."""

    return deepcopy(
        {
            "schema": "nxt-staffing-roster-import/v1",
            "request_id": "roster-001",
            "expected_roster_revision": 0,
            "site_id": "pilot-course-a",
            "deployment_id": "pilot-a-edge-task-sim-v0",
            "site_timezone": "Asia/Shanghai",
            "effective_from_local_date": "2026-10-05",
            "effective_until_local_date": None,
            "operator": "course-manager",
            "source_ref": "weekly-roster-2026-10.csv",
            "workers": [
                {
                    "staff_id": "staff-001",
                    "display_name": "本地员工甲",
                    "skill_codes": ["BALL_PICKING"],
                    "eligibility": [
                        {"role_code": "RANGE_ATTENDANT", "area_code": "RANGE_A"}
                    ],
                    "max_daily_minutes": 480,
                }
            ],
            "availability": [
                {
                    "staff_id": "staff-001",
                    "weekday": 0,
                    "start_local": "08:00",
                    "end_local": "17:00",
                }
            ],
            "regular_assignments": [
                {
                    "staff_id": "staff-001",
                    "weekday": 0,
                    "role_code": "RANGE_ATTENDANT",
                    "area_code": "RANGE_A",
                    "start_local": "09:00",
                    "end_local": "17:00",
                }
            ],
            "assignment_rules": [
                {
                    "role_code": "RANGE_ATTENDANT",
                    "area_code": "RANGE_A",
                    "required_skill_codes": ["BALL_PICKING"],
                }
            ],
            "coverage": [
                {
                    "weekday": 0,
                    "role_code": "RANGE_ATTENDANT",
                    "area_code": "RANGE_A",
                    "start_local": "09:00",
                    "end_local": "17:00",
                    "minimum_staff": 1,
                }
            ],
        }
    )
