"""
Optional Weights & Biases (W&B) logging for governance evidence.

This is an OPT-IN integration: it is a no-op unless W&B is both installed AND
configured (WANDB_API_KEY set, or wandb already logged in). It never raises into
the caller and never blocks the app — if W&B is unavailable, log_* functions
return a status dict saying so. This makes the W&B RAI-style workflow genuinely
available (model metrics + policy-check results logged to a W&B run) without
making the demo depend on it.

Honest scope: this logs metrics and governance/policy-check results to W&B. It is
not a full W&B Weave trace pipeline or hosted evaluation suite; those remain a
larger, separate integration.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional


_REPO_ROOT = Path(__file__).resolve().parents[2]


def wandb_available() -> bool:
    try:
        import wandb  # noqa: F401
    except Exception:
        return False
    return True


def wandb_configured() -> bool:
    """Configured if the package is importable AND an API key/login is present AND
    cloud egress is allowed for the active profile. In LOCAL_CREDENTIALED_RESEARCH,
    cloud egress (and therefore W&B) is OFF by default so no patient-derived data
    leaves the box."""
    from app.security.identity import cloud_egress_allowed
    if not cloud_egress_allowed():
        return False
    if not wandb_available():
        return False
    return bool(os.environ.get("WANDB_API_KEY"))


def log_governance_run(
    project: str,
    policy_results: Dict[str, Any],
    red_team_results: Optional[Dict[str, Any]] = None,
    model_metrics: Optional[Dict[str, Any]] = None,
    run_name: Optional[str] = None,
    mode: str = "online",
) -> Dict[str, Any]:
    """Log governance evidence to a W&B run. No-op (with a status) if W&B is not
    configured. Returns a status dict; never raises into the caller."""
    from app.security.identity import local_credentialed_research_mode
    if local_credentialed_research_mode():
        if os.environ.get("ALLOW_WANDB_OFFLINE_IN_LOCAL_RESEARCH", "").lower() != "true":
            return {
                "status": "SKIPPED",
                "reason": (
                    "W&B logging is disabled by default in LOCAL_CREDENTIALED_RESEARCH, "
                    "including offline mode."
                ),
            }
        wandb_dir = os.environ.get("WANDB_DIR", "").strip()
        if not wandb_dir:
            return {
                "status": "SKIPPED",
                "reason": (
                    "Set WANDB_DIR to an approved path outside the repository before "
                    "allowing W&B offline logs in LOCAL_CREDENTIALED_RESEARCH."
                ),
            }
        try:
            Path(wandb_dir).expanduser().resolve().relative_to(_REPO_ROOT)
            return {
                "status": "SKIPPED",
                "reason": "WANDB_DIR must be outside the repository.",
            }
        except ValueError:
            pass
    if not wandb_available():
        return {"status": "SKIPPED", "reason": "wandb not installed"}
    if not wandb_configured() and mode == "online":
        return {"status": "SKIPPED", "reason": "WANDB_API_KEY not set (offline mode available)"}
    try:
        import wandb
        run = wandb.init(project=project, name=run_name, mode=mode, reinit=True,
                         config={"component": "governance_policy_checks"})
        payload: Dict[str, Any] = {
            "policy/overall_pass": policy_results.get("overall_status") == "PASS",
            "policy/passed": policy_results.get("passed"),
            "policy/total": policy_results.get("total"),
        }
        for c in policy_results.get("checks", []):
            payload[f"policy/{c['policy']}"] = 1.0 if c["status"] == "PASS" else 0.0
        if red_team_results:
            payload["red_team/overall_pass"] = red_team_results.get("overall_status") == "PASS"
            payload["red_team/passed"] = red_team_results.get("passed")
        if model_metrics:
            for k, v in model_metrics.items():
                if isinstance(v, (int, float)):
                    payload[f"model/{k}"] = v
        run.log(payload)
        run_url = getattr(run, "url", None)
        run.finish()
        return {"status": "LOGGED", "run_url": run_url, "metrics_logged": len(payload)}
    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}
