"""Append-only, redacted research references and exact explanation snapshots."""
import os
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

FILES = {"mts_reference": "mts_references.jsonl", "mts_explanation": "mts_explanations.jsonl",
         "mts_guideline_review": "mts_guideline_reviews.jsonl", "mts_explanation_review": "mts_explanation_reviews.jsonl"}


def require_demo_research():
    if any(os.environ.get(k, "").lower() == "true" for k in ("PATIENT_DATA_MODE", "LOCAL_CREDENTIALED_RESEARCH")):
        raise RuntimeError("MTS research collection is not enabled for patient-data/credentialed deployments")


def read_records(kind):
    require_demo_research()
    from app.config import settings
    from app.storage.jsonl_io import read_jsonl_dicts
    from app.security.local_paths import credentialed_artifact_path
    path = credentialed_artifact_path(settings.processed_dir / FILES[kind], purpose="MTS research evidence")
    rows = read_jsonl_dicts(path, strict=True) if path.exists() else []
    from app.storage.jsonl_io import EvidenceIntegrityError
    from app.rules.mts_catalog import CHARTS
    import hashlib
    for number, row in enumerate(rows, 1):
        required = ("record_id", "case_uid", "workflow_run_id", "timestamp_utc")
        if row.get("record_kind") != kind or any(not isinstance(row.get(k), str) or not row[k].strip() for k in required):
            raise EvidenceIntegrityError(f"Incomplete research evidence in {path.name}, record {number}")
        if kind == "mts_reference" and (
                not isinstance(row.get("reference_flowchart"), str) or row["reference_flowchart"] not in CHARTS
                or type(row.get("reference_category")) is not int or not 1 <= row["reference_category"] <= 5
                or row.get("reference_method") not in ("independent_clinician_review", "unblinded_clinician_review")):
            raise EvidenceIntegrityError(f"Invalid reference annotation in {path.name}, record {number}")
        if kind == "mts_reference":
            labels = row.get("reference_discriminators")
            allowed = {rule["id"] for rule in CHARTS[row["reference_flowchart"]]["rules"]}
            if labels is not None and (not isinstance(labels, list) or any(not isinstance(x, str) for x in labels)
                                       or len(set(labels)) != len(labels) or not set(labels) <= allowed):
                raise EvidenceIntegrityError(f"Invalid discriminator annotation in {path.name}, record {number}")
        if kind == "mts_guideline_review" and (not isinstance(row.get("reference_id"), str) or not row["reference_id"]
                or row.get("target", "final_category") not in ("final_category", "mts_category")
                or type(row.get("guideline_consistent")) is not bool):
            raise EvidenceIntegrityError(f"Invalid guideline review in {path.name}, record {number}")
        if kind == "mts_explanation_review" and (not row.get("explanation_id") or not isinstance(row.get("unsupported_claims"), list)):
            raise EvidenceIntegrityError(f"Invalid explanation review in {path.name}, record {number}")
        if kind == "mts_explanation_review" and any(not isinstance(claim, str) or not claim.strip() for claim in row["unsupported_claims"]):
            raise EvidenceIntegrityError(f"Invalid unsupported-claim evidence in {path.name}, record {number}")
        if kind == "mts_explanation" and (not isinstance(row.get("explanation"), str) or hashlib.sha256(row["explanation"].encode()).hexdigest() != row.get("explanation_sha256")):
            raise EvidenceIntegrityError(f"Explanation integrity failure in {path.name}, record {number}")
    return rows


def append_record(kind, values):
    require_demo_research()
    from app.config import settings
    from app.storage.guarded_writer import write_record
    from app.constants import DATASET_SOURCE
    record = {"source_dataset": DATASET_SOURCE, **values, "record_kind": kind, "record_id": str(uuid4()),
              "timestamp_utc": datetime.now(timezone.utc).isoformat()}
    write_record(settings.processed_dir / FILES[kind], record, record_kind=kind)
    return record


def save_explanation(case_uid, workflow_run_id, response):
    import hashlib
    from app.constants import MODEL_SHA256
    from app.rules.mts_catalog import catalog_hash
    text = str(response.get("final_explanation") or response.get("explanation") or "")
    from app.security.redaction import redact_text
    text = redact_text(text)
    if not text.strip():
        return None
    from app.config import settings
    from app.storage.workflow_run_repository import read_workflow_run
    run = read_workflow_run(settings.processed_dir / "workflow_runs.jsonl", workflow_run_id, case_uid) or {}
    record = append_record("mts_explanation", {"case_uid": case_uid,
        "workflow_run_id": workflow_run_id, "explanation": text,
        "explanation_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "catalog_sha256": (run.get("mts_assessment") or {}).get("catalog_sha256") or catalog_hash(),
        "model_sha256": run.get("model_sha256") or MODEL_SHA256,
        "system_identity": run.get("system_identity") or {},
        "generation_status": response.get("status"),
        "explanation_source": response.get("explanation_source"),
        "explanation_policy": response.get("explanation_policy"),
        "evidence_links": response.get("evidence_links") or []})
    return record["record_id"]
