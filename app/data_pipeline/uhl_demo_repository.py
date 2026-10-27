"""Small, immutable curated demo cohort: no full CSV scan or SQLite cache."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import threading
from datetime import datetime
from copy import deepcopy

from app.constants import (
    DATASET_SHA256, DATASET_SOURCE, DEMO_COHORT_ROWS, DEMO_COHORT_CATEGORY_COUNTS,
    DEMO_COHORT_MATCHED_ROWS, DEMO_COHORT_NOTICE, MODEL_SHA256,
    MODEL_FEATURE_RANGES, MEASUREMENT_UNITS,
)
from app.data_pipeline.uhl_repository import UhlCaseRepository, UhlDatasetContractError, _finite_float, _integer, _between, _pseudonym_cache_signature
from app.security.redaction import pseudonymous_case_uid
from app.schemas.internal import EDTriageCase
from app.rules.acuity_override import apply_acuity_override
from ml_training.uhl_synthetic.serving import UHL_PRESENTING_COMPLAINTS


class UhlDemoCaseRepository:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.RLock()
        self._signature = None
        self._cases = []
        self._index = {}
        self._manifest = {}

    def ensure_cache(self):
        with self._lock:
            path = self.settings.serving_data_path
            try:
                stat = path.stat()
                signature = (str(path), stat.st_mtime_ns, stat.st_size, _pseudonym_cache_signature())
                if self._signature == signature:
                    return
                payload = path.read_bytes()
                if hashlib.sha256(payload).hexdigest() != self.settings.expected_serving_data_sha256:
                    raise UhlDatasetContractError("Curated demo cohort hash mismatch")
                manifest = json.loads(payload)
                if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("dataset_source") != DATASET_SOURCE
                        or manifest.get("training_dataset_sha256") != DATASET_SHA256
                        or manifest.get("model_sha256") != MODEL_SHA256):
                    raise UhlDatasetContractError("Curated demo provenance is incompatible")
                rows = manifest.get("cases")
                if not isinstance(rows, list) or len(rows) != DEMO_COHORT_ROWS:
                    raise UhlDatasetContractError("Curated demo must contain exactly 100 cases")
                cases, index, selections, matched_count = [], {}, Counter(), 0
                for row in rows:
                    number = _integer(row["row_number"], "row_number", 0)
                    if number < 1:
                        raise UhlDatasetContractError("Invalid demo source-row identity")
                    age = _integer(row["age"], "age", number)
                    if not 16 <= age < 75:
                        raise UhlDatasetContractError("Case is outside the curated adult demo scope")
                    datetime.fromisoformat(row["arrival_time"])
                    if row["presenting_complaint"] not in UHL_PRESENTING_COMPLAINTS:
                        raise UhlDatasetContractError("Unknown demo presenting complaint")
                    for field in ("temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain"):
                        _between(_finite_float(row[field], field, number), field, *MODEL_FEATURE_RANGES[field], number)
                    selection = row["selection_evidence"]
                    acuity = _integer(selection["ml_acuity_before_override"], "selected acuity", number)
                    if acuity not in range(1, 6):
                        raise UhlDatasetContractError("Invalid demonstration model result")
                    _between(_finite_float(selection["assigned_class_probability"], "selected probability", number),
                             "selected probability", 0, 1, number)
                    # Keep source row numbers so existing pseudonyms/review links
                    # never get reassigned to a different patient during the swap.
                    adapted = {**row, "case_uid": pseudonymous_case_uid(DATASET_SOURCE, number),
                               "patient_display_label": f"UHL Case {number:06d}",
                               "model_eligible": True, "pathway": "ML_RESEARCH_ASSESSMENT"}
                    case = UhlCaseRepository._case_payload(adapted)
                    # Verify matched/override metadata from the actual source
                    # observations using the same serving rule evaluator.
                    override = apply_acuity_override(acuity, EDTriageCase(**case).to_triage_time_input())
                    matched = any(rule["status"] == "matched" for rule in override["evaluated_discriminators"])
                    floor = min((rule["priority"] for rule in override["evaluated_discriminators"]
                                 if rule["status"] == "matched"), default=None)
                    if (selection["final_acuity"] != override["final_acuity"]
                            or selection["source_estimated_acuity"] != override["final_acuity"]
                            or selection["has_matched_discriminator"] is not matched
                            or selection["safety_floor_acuity"] != floor
                            or selection["override_applied"] is not override["override_applied"]):
                        raise UhlDatasetContractError("Invalid demonstration safety evidence")
                    selections[override["final_acuity"]] += 1
                    matched_count += int(matched)
                    case["curated_demo"] = True
                    case["demo_data_notice"] = DEMO_COHORT_NOTICE
                    if case["case_uid"] in index:
                        raise UhlDatasetContractError("Duplicate curated case identity")
                    cases.append(case)
                    index[case["case_uid"]] = case
                if selections != Counter(DEMO_COHORT_CATEGORY_COUNTS):
                    raise UhlDatasetContractError("Curated category distribution is incompatible")
                if matched_count != DEMO_COHORT_MATCHED_ROWS:
                    raise UhlDatasetContractError("Curated matched-check count is incompatible")
                self._cases, self._index, self._manifest, self._signature = cases, index, manifest, signature
            except UhlDatasetContractError:
                raise
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise UhlDatasetContractError("Curated demo cohort is missing or invalid") from exc

    def clear_derived_cache(self):
        with self._lock:
            self._signature = None
            self._cases, self._index, self._manifest = [], {}, {}

    def status(self):
        with self._lock:
            self.ensure_cache()
            return {"dataset_source": DATASET_SOURCE, "dataset_ready": True,
                    "dataset_revision": self._manifest["cohort_id"],
                    "dataset_sha256": self.settings.expected_serving_data_sha256,
                    "training_dataset_sha256": DATASET_SHA256, "source_rows": DEMO_COHORT_ROWS,
                    "model_scope_rows": DEMO_COHORT_ROWS, "deterministic_path_rows": 0,
                    "target_counts": {}, "case_cohort": "demo_100", "cache_schema_version": 0,
                    "measurement_units": dict(MEASUREMENT_UNITS),
                    "cohort_selection": deepcopy(self._manifest["selection"])}

    def list_cases(self, *, offset=0, limit=100, query=None):
        with self._lock:
            self.ensure_cache()
            rows = self._cases
        if query and query.strip():
            term = query.strip().casefold()
            rows = [row for row in rows if term in row["case_uid"].casefold()
                    or term in row["display_name"].casefold()
                    or term in row["triage"]["chiefcomplaint"].casefold()]
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 100000))
        selected = rows[offset:offset + limit]
        next_offset = offset + len(selected) if offset + len(selected) < len(rows) else None
        return {"cases": deepcopy(selected), "pagination": {"offset": offset, "limit": limit, "returned": len(selected),
                "total": len(rows), "has_more": next_offset is not None, "next_offset": next_offset}}

    def get_case(self, case_uid):
        with self._lock:
            self.ensure_cache()
            return deepcopy(self._index.get(case_uid))
