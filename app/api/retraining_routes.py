"""ITD-only monthly retraining export status and download endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.api.auth_dependencies import requires
from app.retraining.monthly_export import (
    MONTH_RE,
    build_current_month_demo_preview,
    csv_path_for,
    current_reporting_month,
    generate_monthly_export,
    list_manifests,
    previous_reporting_month,
    read_manifest,
    validate_export_artifact,
)
from app.security import authz


router = APIRouter(prefix="/retraining", tags=["retraining-data-export"])


def _demo_preview_enabled() -> bool:
    # Preview is a role-switcher-demo feature, not merely a non-patient-data
    # feature. Reuse the identity layer's complete guard so one stale flag can
    # never expose it under real auth, trusted proxy or credentialed research.
    from app.security.identity import azure_supervisor_demo_mode, demo_role_switcher_allowed

    return azure_supervisor_demo_mode() and demo_role_switcher_allowed()


@router.get("/exports")
def monthly_exports(
    _ctx=Depends(requires(authz.PERM_VIEW_RETRAINING_EXPORTS, "list_retraining_exports")),
) -> dict[str, Any]:
    rows = list_manifests()
    return {
        "exports": rows,
        "count": len(rows),
        "next_reporting_month": previous_reporting_month(),
        "current_demo_preview_month": (
            current_reporting_month() if _demo_preview_enabled() else None
        ),
        "demo_preview_available": _demo_preview_enabled(),
        "workflow_boundary": "prepare_notify_download_only",
        "automatic_training": False,
    }


@router.post("/exports/generate")
def generate_export(
    reporting_month: str | None = Query(default=None),
    _ctx=Depends(requires(
        authz.PERM_GENERATE_RETRAINING_EXPORTS, "generate_retraining_export"
    )),
) -> dict[str, Any]:
    month = reporting_month or previous_reporting_month()
    if not MONTH_RE.fullmatch(month):
        raise HTTPException(status_code=422, detail="reporting_month must use YYYY-MM")
    try:
        return generate_monthly_export(month)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"monthly export failed: {type(exc).__name__}") from exc


@router.get("/exports/{reporting_month}")
def export_status(
    reporting_month: str,
    _ctx=Depends(requires(authz.PERM_VIEW_RETRAINING_EXPORTS, "view_retraining_export")),
) -> dict[str, Any]:
    if not MONTH_RE.fullmatch(reporting_month):
        raise HTTPException(status_code=422, detail="reporting_month must use YYYY-MM")
    manifest = read_manifest(reporting_month)
    if manifest is None:
        raise HTTPException(status_code=404, detail="monthly export not found")
    return manifest


@router.get("/exports/{reporting_month}/download")
def download_export(
    reporting_month: str,
    _ctx=Depends(requires(authz.PERM_VIEW_RETRAINING_EXPORTS, "download_retraining_export")),
):
    if not MONTH_RE.fullmatch(reporting_month):
        raise HTTPException(status_code=422, detail="reporting_month must use YYYY-MM")
    try:
        manifest, path = validate_export_artifact(reporting_month)
    except Exception as exc:
        if read_manifest(reporting_month) is None or not csv_path_for(reporting_month).is_file():
            raise HTTPException(status_code=404, detail="monthly export not found") from exc
        raise HTTPException(status_code=503, detail="monthly export integrity check failed") from exc
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8",
        filename=str(manifest.get("filename") or path.name),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/preview/current")
def current_month_preview(
    _ctx=Depends(requires(
        authz.PERM_VIEW_RETRAINING_EXPORTS, "preview_current_retraining_data"
    )),
) -> dict[str, Any]:
    if not _demo_preview_enabled():
        raise HTTPException(status_code=404, detail="demo retraining preview is not enabled")
    manifest, _content = build_current_month_demo_preview()
    return manifest


@router.get("/preview/current/download")
def download_current_month_preview(
    _ctx=Depends(requires(
        authz.PERM_VIEW_RETRAINING_EXPORTS, "download_current_retraining_preview"
    )),
):
    if not _demo_preview_enabled():
        raise HTTPException(status_code=404, detail="demo retraining preview is not enabled")
    manifest, content = build_current_month_demo_preview()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{manifest["filename"]}"',
            "X-ALTER-Demo-Preview": "true",
        },
    )
