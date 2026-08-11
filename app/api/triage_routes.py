"""
Retired raw-stay_id triage API routes.

These compatibility routes are not part of the public surface. app.main only
registers them when ALLOW_LEGACY_RAW_ID_ROUTES=true and never in patient-data or
local credentialed research mode. The canonical API is /cases/{case_uid}, where
case_uid is pseudonymous and full-MIMIC-only.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth_dependencies import requires
from app.security import authz

router = APIRouter()


def _gone() -> None:
    raise HTTPException(
        status_code=410,
        detail=(
            "Legacy raw-stay_id triage routes are retired. Use the "
            "case_uid-keyed /cases API instead."
        ),
    )


@router.get("/triage/cases", dependencies=[Depends(requires(authz.PERM_VIEW_CASE, "list_cases"))])
def list_cases():
    _gone()


@router.get("/triage/run/{stay_id}", dependencies=[Depends(requires(authz.PERM_RUN_ASSESSMENT, "run_assessment"))])
def run_case(stay_id: int):
    _gone()


@router.post("/triage/run/{stay_id}/explain", dependencies=[Depends(requires(authz.PERM_ASK_CHATBOT, "run_explanation"))])
def run_case_with_explanation(stay_id: int):
    _gone()
