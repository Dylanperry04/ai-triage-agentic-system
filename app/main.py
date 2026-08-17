"""
AI Triage Research System — FastAPI Backend

NOT FOR CLINICAL USE. Research prototype only. Clinician review required for every
output. The only prediction/training source is the packaged UHL synthetic
triage-vitals cohort; raw identifiers never appear in the API.

ARCHITECTURE (v18): this FastAPI service is the SERVER-SIDE ENFORCEMENT
BOUNDARY and the ONLY service. It serves the built React UI
(frontend-react/dist) at "/" and every protected action is a same-origin HTTP
call back into this service. The retired Streamlit frontend (frontend/app.py,
SERVICE_ROLE=frontend) is kept in-repo for reference only. Every protected route enforces authentication + app-level RBAC via
app/api/auth_dependencies.py (reads the Entra-injected X-MS-CLIENT-PRINCIPAL,
fails closed in patient-data mode, audits every decision). On startup the service
refuses to run in patient-data mode unless the security preconditions hold
(AUTH_REQUIRED, non-demo AUTH_PROVIDER, TRUSTED_AUTH_PROXY, keyvault secrets,
durable audit). See docs/SECURITY_ARCHITECTURE.md and
docs/DEPLOYMENT_SECURITY_CHECKLIST.md.
"""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os as _os

from app.config import settings
from app.version import APP_VERSION, PACKAGE_CHECKPOINT
from app.rules.provisional_mts_ruleset import register_provisional_ruleset
from app.api.health_routes import router as health_router
from app.api.governance_routes import router as governance_router


_notification_logger = logging.getLogger("alter.notifications.runtime")


def _overdue_vitals_sweeper_enabled() -> bool:
    raw = _os.environ.get("ENABLE_OVERDUE_VITALS_SWEEPER")
    if raw is not None:
        return raw.lower() == "true"
    # Patient-data deployments need server-side notification creation. Local
    # tests/demo runs keep it off unless explicitly enabled to avoid background
    # mutations during deterministic test execution.
    return _os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    task = None
    notification_task = None
    notification_backfill_task = None
    if _overdue_vitals_sweeper_enabled():
        try:
            interval = max(60, int(_os.environ.get("OVERDUE_VITALS_SWEEP_INTERVAL_SECONDS", "300")))
        except ValueError:
            interval = 300
        try:
            limit = max(1, min(int(_os.environ.get("OVERDUE_VITALS_SWEEP_LIMIT", "50000")), 50000))
        except ValueError:
            limit = 50000

        async def _loop() -> None:
            while True:
                try:
                    from app.api.case_routes import sweep_overdue_vitals_once
                    result = await asyncio.to_thread(sweep_overdue_vitals_once, limit=limit)
                    app.state.overdue_vitals_sweeper_status = {
                        "state": "complete", "last_success_at": datetime.now(timezone.utc).isoformat(),
                        "errors": len(result.get("errors") or []),
                    }
                except Exception as exc:
                    # The explicit sweep route and health/preflight checks surface
                    # durable-store failures. The background loop keeps the service
                    # alive and retries on the next interval.
                    app.state.overdue_vitals_sweeper_status = {
                        "state": "failed", "last_error_at": datetime.now(timezone.utc).isoformat(),
                        "error": exc.__class__.__name__,
                    }
                    _notification_logger.error(
                        "overdue-vitals background sweep failed error=%s",
                        exc.__class__.__name__,
                    )
                await asyncio.sleep(interval)

        task = asyncio.create_task(_loop())
        app.state.overdue_vitals_sweeper_task = task
    try:
        from app.notifications.config import validate_sms_startup
        from app.notifications.repository import get_notification_repository

        notification_settings = validate_sms_startup()
        notification_repository = get_notification_repository(notification_settings)
        active_rollout_policy = notification_repository.upsert_rollout_policy(
            notification_settings.rollout_policy()
        )
        app.state.notification_rollout_policy_version = active_rollout_policy.version
    except Exception:
        # A live-SMS configuration error is unsafe and must prevent startup.
        # With SMS disabled, validate_sms_startup accepts an unprovisioned Azure
        # environment so the existing in-app workflow remains available.
        raise
    try:
        notification_reconcile_interval = max(
            30, min(int(_os.environ.get("NOTIFICATION_RECONCILE_INTERVAL_SECONDS", "60")), 3600)
        )
    except ValueError:
        notification_reconcile_interval = 60

    async def _backfill_notifications() -> None:
        """Continuously repair the workflow-to-notification reliability boundary."""
        previous: dict = {}
        while True:
            app.state.notification_backfill_status = {
                **previous, "state": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                from app.notifications.models import utc_iso
                from app.notifications.service import reconcile_current_workflow_states

                result = await asyncio.to_thread(reconcile_current_workflow_states, limit=50000)
                unresolved = int(result.get("failures") or 0) + int(
                    result.get("publication_failures") or 0
                )
                observed_at = utc_iso()
                previous = {
                    "state": "complete" if unresolved == 0 else "degraded",
                    "complete": unresolved == 0,
                    "unresolved_event_count": unresolved,
                    "last_attempted_reconciliation_at": observed_at,
                    "last_successful_reconciliation_at": (
                        observed_at if unresolved == 0 else
                        str(previous.get("last_successful_reconciliation_at") or "")
                    ),
                    **result,
                }
                app.state.notification_backfill_status = previous
            except Exception as exc:
                from app.notifications.models import utc_iso

                previous = {
                    **previous, "state": "failed", "complete": False,
                    "unresolved_event_count": max(
                        1, int(previous.get("unresolved_event_count") or 0)
                    ),
                    "last_error_at": utc_iso(), "error": exc.__class__.__name__,
                }
                app.state.notification_backfill_status = previous
                _notification_logger.error(
                    "notification reconciliation failed error=%s", exc.__class__.__name__
                )
            await asyncio.sleep(notification_reconcile_interval)

    notification_backfill_task = asyncio.create_task(_backfill_notifications())
    app.state.notification_backfill_task = notification_backfill_task
    if notification_settings.sms_publish_enabled:
        async def _notification_loop() -> None:
            while True:
                try:
                    from app.notifications.publisher import reconcile_outbox

                    result = await asyncio.to_thread(
                        reconcile_outbox,
                        notification_repository,
                        notification_settings,
                        limit=500,
                    )
                    app.state.notification_outbox_status = {"state": "complete", **result}
                except Exception as exc:
                    # The Azure Function has its own timer reconciliation. This
                    # loop is a latency optimisation and retries every minute.
                    app.state.notification_outbox_status = {
                        "state": "failed", "error": exc.__class__.__name__,
                    }
                    _notification_logger.error(
                        "notification outbox loop failed error=%s", exc.__class__.__name__
                    )
                await asyncio.sleep(60)

        notification_task = asyncio.create_task(_notification_loop())
        app.state.notification_outbox_task = notification_task
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            # asyncio.CancelledError inherits from BaseException (3.8+), so
            # suppress(Exception) does NOT catch it and a normal shutdown
            # cancellation escapes into the lifespan teardown.
            with suppress(asyncio.CancelledError, Exception):
                await task
        if notification_task is not None:
            notification_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await notification_task
        if notification_backfill_task is not None:
            notification_backfill_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await notification_backfill_task


app = FastAPI(
    title="AI Triage Research System",
    version=APP_VERSION,
    lifespan=_app_lifespan,
    description=(
        "Research decision-support API for ED acuity prediction. The ML model "
        "predicts acuity; the LLM layer only explains. NOT FOR CLINICAL USE — "
        "clinician review is required for every output. The only prediction/"
        "training source is the packaged UHL synthetic triage-vitals cohort and "
        "its pinned CatBoost serving artifact. The synthetic data is for controlled "
        "research demonstration only and is never a clinical or patient-data "
        "source. The external API uses a "
        "pseudonymous case_uid; raw identifiers never appear in the public API."
    ),
)

# CORS origins come from settings.cors_allowed_origins, which defaults to
# local-development-only values (localhost:8501) and must be set via the
# CORS_ALLOWED_ORIGINS environment variable for a real deployment -- this
# project never defaults to a wildcard. See infrastructure/azure_deploy.md.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _security_headers(request, call_next):
    """Baseline browser-hardening headers on every response.

    The UI is same-origin and uses inline styles, so a strict CSP is not
    imposed here; these are safe, non-breaking defaults. Patient-data
    deployments additionally sit behind the Entra trusted proxy.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    return response

# Register the provisional MTS research ruleset at startup (default-on; set
# PROVISIONAL_MTS_MODE=off to disable). This makes the engine assign
# provisional, clinician-review-required Manchester categories instead of
# staying gated. The categories are NOT the official Manchester Triage System
# and are NOT clinically approved -- see app/rules/provisional_mts_ruleset.py.
if settings.provisional_mts_mode:
    register_provisional_ruleset()


# ── Startup security guard (fail closed on unsafe patient-data config) ───────
# If the service is started in patient-data mode, the security preconditions must
# hold; otherwise we refuse to start rather than serve patient data unsafely.
def _enforce_patient_data_security_invariants() -> None:
    """Refuse to start in patient-data mode with an unsafe configuration.

    Startup checks are allowed to run readiness probes that have side effects
    (for example, a harmless durable-audit write/read probe). The status endpoint
    reads the cached probe result instead of performing those writes on GET.
    """
    from app.security.security_status import unsafe_combinations
    problems = unsafe_combinations()
    # CORS wildcard is unsafe in any mode; the patient-data ones only fire in
    # patient-data mode (encoded in unsafe_combinations()).
    if problems:
        raise RuntimeError(
            "Refusing to start the API with unsafe security config:\n  - "
            + "\n  - ".join(problems)
            + "\nSee docs/DEPLOYMENT_SECURITY_CHECKLIST.md."
        )


def _unused_old_guard() -> None:
    return


def _enforce_local_research_invariants() -> None:
    """In the LOCAL_CREDENTIALED_RESEARCH profile, refuse to start unless the
    backend is bound to a loopback interface (BACKEND_BIND_HOST). This profile
    handles credentialed data on an approved local machine and must never be
    exposed on a network interface."""
    from app.security.identity import assert_local_research_bind_is_loopback
    assert_local_research_bind_is_loopback()


_enforce_patient_data_security_invariants()
_enforce_local_research_invariants()

def _system_meta_payload():
    from app.agents.autogen_team import load_azure_config
    from app.rules.manchester_engine import get_approved_ruleset

    ruleset = get_approved_ruleset()
    provisional_active = bool(ruleset) and ruleset.get(
        "validation_status"
    ) != "CLINICALLY_APPROVED"

    from app.api.health_routes import _uhl_status_payload
    _uhl = _uhl_status_payload()

    presentation_ui = _os.environ.get("PRESENTATION_UI_MODE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    return {
        "status": "running",
        "version": APP_VERSION,
        "presentation_ui_mode": presentation_ui,
        "package_checkpoint": PACKAGE_CHECKPOINT,
        "default_dataset": _uhl["dataset_source"],
        **_uhl,
        "datasets_available": [_uhl["dataset_source"]],
        "uhl_data_configured": _uhl["dataset_ready"],
        "uhl_model_configured": _uhl["model_ready"],
        "prediction_model_source": "UHL_MODEL_PATH (packaged default)",
        "patient_data_ready": False,
        "patient_data_ready_requires": [
            "PATIENT_DATA_MODE=true",
            "real Entra/SSO and trusted auth proxy",
            "Key Vault secret read and Managed Identity",
            "durable audit write/read",
            "private ingress/network controls",
            "non-wildcard CORS",
            "model artefact hash verification",
            "governance, DPIA, and security approval evidence",
        ],
        "clinical_use": "NOT_FOR_CLINICAL_USE",
        "official_manchester_triage": "NOT_IMPLEMENTED",
        "provisional_mts_research_ruleset": "ENABLED" if provisional_active else "DISABLED",
        "official_mts_ruleset": False,
        "clinically_approved_ruleset": False,
        "rules_status": (
            "PROVISIONAL_MTS_RESEARCH_RULESET_ACTIVE"
            if provisional_active
            else "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED"
        ),
        "human_review_required": True,
        "chat_agent_orchestration_framework": "autogen-agentchat",
        "chat_agent_status": "configured" if load_azure_config() else "not_configured",
        "docs": "/docs",
    }

app.include_router(health_router)
# Canonical case_uid-keyed API (the public surface) + status endpoints.
from app.api.case_routes import router as case_router
from app.api.status_routes import router as status_router
from app.api.session_routes import router as session_router
from app.api.notification_routes import router as notification_router
app.include_router(case_router)
app.include_router(status_router)
app.include_router(session_router)
app.include_router(notification_router)
app.include_router(governance_router)

# ── Legacy raw-ID routers (triage/review/explanation/chat/followup) ──────────
# These expose stay_id in URLs and are NOT part of the final public API. They are
# registered ONLY when explicitly enabled for backward-compatibility AND never in
# patient-data mode. Default: disabled. Streamlit protected actions never use them.
_allow_legacy = _os.environ.get("ALLOW_LEGACY_RAW_ID_ROUTES", "false").lower() == "true"
_patient_mode = _os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
_local_research_mode = (
    _os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
    and not _patient_mode
)
if _allow_legacy and not _patient_mode and not _local_research_mode:
    from app.api.triage_routes import router as triage_router
    from app.api.review_routes import router as review_router
    from app.api.explanation_routes import router as explanation_router
    from app.api.chat_routes import router as chat_router
    from app.api.followup_routes import router as followup_router

    app.include_router(triage_router)
    app.include_router(review_router)
    app.include_router(explanation_router)
    app.include_router(chat_router)
    app.include_router(followup_router)


# ── React UI (frontend-react/dist) served by this backend ────────────────────
# Single-service architecture: the React frontend is built into
# frontend-react/dist and served by this FastAPI app, replacing the retired
# Streamlit presentation service. The API surface is unchanged; the UI calls it
# same-origin (no CORS needed for the built-in UI). Set SERVE_REACT_UI=false to
# disable (e.g. pure-API deployments). The JSON status formerly at "/" remains
# available: unchanged for non-browser clients at "/" (content negotiation) and
# always at "/system/meta".
from pathlib import Path as _Path
from fastapi import Request as _Request
from fastapi.responses import FileResponse as _FileResponse, JSONResponse as _JSONResponse
from fastapi.staticfiles import StaticFiles as _StaticFiles

_REACT_DIST = _Path(__file__).resolve().parent.parent / "frontend-react" / "dist"
_SERVE_UI = (
    _os.environ.get("SERVE_REACT_UI", "true").lower() != "false"
    and (_REACT_DIST / "index.html").exists()
)


@app.get("/system/meta", include_in_schema=False)
def system_meta():
    """The system status JSON formerly served only at '/'."""
    return _system_meta_payload()


@app.get("/", include_in_schema=False)
def root(request: _Request):
    """Browsers (Accept: text/html) get the React UI when it is built; API
    clients and tests keep receiving the status JSON."""
    accepts_html = "text/html" in (request.headers.get("accept") or "")
    if _SERVE_UI and accepts_html:
        return _FileResponse(_REACT_DIST / "index.html")
    return _JSONResponse(_system_meta_payload())


if _SERVE_UI:
    app.mount("/assets", _StaticFiles(directory=str(_REACT_DIST / "assets")), name="ui-assets")

    @app.get("/favicon.svg", include_in_schema=False)
    def _favicon():
        icon = _REACT_DIST / "favicon.svg"
        if icon.exists():
            return _FileResponse(icon)
        return _JSONResponse({"detail": "Not Found"}, status_code=404)
