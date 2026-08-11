"""
Retired raw-stay_id follow-up API routes.

The canonical follow-up surface is POST /cases/{case_uid}/followups. These
legacy routes remain only as explicit 410 tombstones when the compatibility
router is deliberately enabled.
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth_dependencies import requires
from app.security import authz
from app.schemas.followup import FollowUpLinkRequest

router = APIRouter()


def _gone() -> None:
    raise HTTPException(
        status_code=410,
        detail=(
            "Legacy raw-stay_id follow-up routes are retired. Use "
            "POST /cases/{case_uid}/followups instead."
        ),
    )


@router.post("/followup/link", dependencies=[Depends(requires(authz.PERM_RUN_ASSESSMENT, "followup_link"))])
def link_followup(link: FollowUpLinkRequest):
    _gone()


@router.get("/followup/history/{stay_id}", dependencies=[Depends(requires(authz.PERM_VIEW_CASE, "followup_history"))])
def followup_history(stay_id: int):
    _gone()
