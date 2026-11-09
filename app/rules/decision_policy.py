"""Identity of the executed hybrid policy, independently of prediction success.

Source digests bind executable behavior as well as the declarative catalog.
They identify a software revision, not clinical validation or approval.
"""
from functools import lru_cache
from copy import deepcopy
from pathlib import Path
import hashlib
import json

SERVING_POLICY = {"id": "modal_with_individual_urgent_threshold_v1", "threshold": .25,
                  "description": "Modal class; choose the most urgent higher class with individual probability >= 0.25"}


@lru_cache(maxsize=1)
def _identity():
    from app.config import settings
    from app.rules.mts_catalog import catalog_hash
    root = Path(__file__).resolve().parents[2]
    groups = {
        "input_processing": ["app/api/case_routes.py", "app/api/mts_routes.py", "app/services/observation_update.py", "app/schemas/internal.py"],
        "serving": ["app/agents/ml_prediction_agent.py", "app/ml/trusted_artifact.py", "ml_training/uhl_synthetic/serving.py", "ml_training/uhl_synthetic/features.py", "ml_training/uhl_synthetic/dataset.py"],
        "safety": ["app/rules/discriminators.py", "app/rules/acuity_override.py", "app/rules/acuity_mts_mapping.py", "app/rules/vitals.py"],
        "mts_execution": ["app/rules/mts_assessment.py", "app/rules/mts_catalog.py", "app/schemas/mts.py"],
        "hybrid": ["app/agents/orchestrator.py", "app/rules/decision_policy.py"],
    }
    digests = {name + "_sha256": hashlib.sha256(b"\n".join(
        rel.encode() + b":" + (root / rel).read_bytes().replace(b"\r\n", b"\n") for rel in paths)).hexdigest()
        for name, paths in groups.items()}
    identity = {"model_sha256": settings.expected_model_sha256, "catalog_sha256": catalog_hash(),
                "serving_policy": SERVING_POLICY, **digests}
    identity["system_policy_sha256"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    application_files = sorted([*(root / "app").rglob("*.py"), *(root / "ml_training").rglob("*.py"),
                                *(root / "frontend-react" / "src").rglob("*.jsx"), *(root / "frontend-react" / "src").rglob("*.js")])
    identity["application_revision"] = hashlib.sha256(b"\n".join(
        p.relative_to(root).as_posix().encode() + b":" + p.read_bytes().replace(b"\r\n", b"\n")
        for p in application_files)).hexdigest()
    return identity


def system_identity():
    """Return an isolated copy of the identity frozen for this process."""
    return deepcopy(_identity())
