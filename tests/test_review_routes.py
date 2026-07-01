"""
Tests for the human review API routes (app/api/review_routes.py).

This is the first dedicated test coverage for this route file -- before
this, it was only exercised indirectly through the Streamlit frontend's
own tests, never at the API/route level directly.

The central coverage here is source_dataset threading: a real fix from a
later review pass. stay_id alone is not a safe identifier once multiple
datasets exist (nothing in the schema prevents two different datasets
from someday having genuinely overlapping integer stay_ids -- KTAS and
MIMIC demo do not overlap today only by coincidence of their current
ranges, not by any guarantee), so HumanReviewRequest/HumanReviewRecord
gained an optional source_dataset field, populated server-side from the
real, authoritative processed-cases file rather than trusted blindly
from client input.

ISOLATION: every write-path test here patches
app.config.settings.processed_dir to a tmp_path and writes its own small
triage_cases_sample.jsonl fixture there, so NOTHING in this file ever
writes to the real, production data/processed/human_reviews.jsonl -- the
same safe isolation pattern used throughout tests/test_frontend.py (see
isolated_processed_dir there).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.review_routes import valid_stay_id_to_dataset
import os as _os
_os.environ["ALLOW_LEGACY_RAW_ID_ROUTES"] = "true"  # these test the legacy compat layer
_os.environ.pop("PATIENT_DATA_MODE", None)
import importlib
import app.main as _appmain
importlib.reload(_appmain)
app = _appmain.app

# The review routes now enforce RBAC server-side. These tests exercise the route
# logic (validation, source_dataset threading), so the client carries a verified
# clinical_supervisor identity (can submit/view reviews) and runs behind a
# trusted proxy. A separate suite (test_api_auth_boundary.py) covers the
# auth/RBAC behaviour itself.
import base64 as _b64
import json as _json


def _supervisor_principal():
    claims = [
        {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "test-supervisor"},
        {"typ": "name", "val": "Test Supervisor"},
        {"typ": "groups", "val": "clinical-supervisors"},
    ]
    return _b64.b64encode(_json.dumps({"claims": claims}).encode()).decode()


client = TestClient(app, headers={"X-MS-CLIENT-PRINCIPAL": _supervisor_principal()})


@pytest.fixture(autouse=True)
def _trusted_proxy_for_review_tests(monkeypatch):
    """The supervisor header is only trusted behind a trusted proxy."""
    monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")


@pytest.fixture
def isolated_review_storage(tmp_path, monkeypatch):
    """
    Patches settings.processed_dir to an empty tmp_path and writes one
    small, known case to triage_cases_sample.jsonl there -- the file
    valid_stay_id_to_dataset() reads to validate a submitted stay_id and
    resolve its source_dataset. Returns the patched directory.

    BUG FOUND AND FIXED while building this fixture: patching via the
    string path "app.config.settings.processed_dir" (the pattern used
    safely throughout tests/test_frontend.py) silently does NOT affect
    app/api/review_routes.py's actual behaviour, because that module
    imports settings via "from app.config import settings" -- a
    name-binding import that keeps review_routes.settings pointing at
    whatever object existed at import time. If anything in the same
    pytest process later reloads app.config via importlib.reload()
    (confirmed: tests/test_main_cors.py does exactly this, to pick up a
    different CORS_ALLOWED_ORIGINS environment variable per test),
    app.config.settings becomes a genuinely different object in memory
    than review_routes.settings, and a monkeypatch targeting the former
    has zero effect on the latter -- confirmed directly by checking
    id(app.config.settings) vs id(review_routes.settings) before and
    after a reload. This would have meant every test in this file
    silently wrote to the REAL, production data/processed/
    human_reviews.jsonl file instead of the intended tmp_path, whenever
    test_main_cors.py happened to run first in the same pytest process
    -- reproduced exactly that way before this fix (a FileNotFoundError
    at the tmp_path, since the real write went to the real file instead).

    Fixed by patching the attribute directly on the
    review_routes.settings object itself, which is robust to this reload
    scenario regardless of which name review_routes.py used to import
    settings.
    """
    from app.api import review_routes as _review_routes_module
    import app.api.case_resolver as _cr

    processed = tmp_path / "processed"
    processed.mkdir()
    fixture_case = {
        "source_dataset": "MIMIC-IV-ED-Full-v2.2",
        "stay_id": 1,
        "subject_id": 900001,
        "edstay": {"subject_id": 900001, "stay_id": 1, "gender": "F",
                   "arrival_transport": "AMBULANCE", "disposition": "HOME"},
        "triage": {
            "subject_id": 900001, "stay_id": 1, "chiefcomplaint": "test complaint",
            "temperature_unit": "C", "acuity": 3,
        },
        "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
    }
    # valid_stay_id_to_dataset() reads via the full-MIMIC resolver, whose source is
    # frontend_cases_override.jsonl in tests.
    (processed / "frontend_cases_override.jsonl").write_text(
        json.dumps(fixture_case) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(_review_routes_module.settings, "processed_dir", processed)
    monkeypatch.setattr(_cr.settings, "processed_dir", processed)
    return processed


class TestSourceDatasetThreading:
    """
    The core regression guard for this file: confirms source_dataset is
    genuinely populated on every saved review record, from the real,
    authoritative server-side lookup, not from unchecked client input.
    """

    def test_valid_stay_id_to_dataset_is_full_mimic_only(self, tmp_path, monkeypatch):
        """valid_stay_id_to_dataset() is full-MIMIC-only: it maps stay_ids to the
        full-MIMIC label from the resolver, never a demo/KTAS label. Seeded with a
        synthetic MIMIC-shaped case (no credentialed data)."""
        import json as _json
        proc = tmp_path / "processed"; proc.mkdir()
        case = {
            "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 30000001,
            "subject_id": 10000001,
            "edstay": {"subject_id": 10000001, "stay_id": 30000001, "gender": "F",
                       "arrival_transport": "AMBULANCE", "disposition": "HOME"},
            "triage": {"subject_id": 10000001, "stay_id": 30000001,
                       "chiefcomplaint": "CHEST PAIN", "acuity": 2},
            "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
        }
        (proc / "frontend_cases_override.jsonl").write_text(_json.dumps(case))
        monkeypatch.setattr("app.config.settings.processed_dir", proc)
        import app.api.case_resolver as _cr
        monkeypatch.setattr(_cr.settings, "processed_dir", proc)
        mapping = valid_stay_id_to_dataset()
        assert mapping.get(30000001) == "MIMIC-IV-ED-Full-v2.2"
        # never a demo/KTAS label
        assert all(v == "MIMIC-IV-ED-Full-v2.2" for v in mapping.values())

    def test_valid_stay_id_to_dataset_empty_without_full_mimic(self, tmp_path, monkeypatch):
        """Fail-closed: with no full-MIMIC cases, the mapping is empty (the legacy
        endpoint then rejects the stay_id) — it never falls back to demo/KTAS."""
        proc = tmp_path / "processed"; proc.mkdir()
        monkeypatch.setattr("app.config.settings.processed_dir", proc)
        import app.api.case_resolver as _cr
        monkeypatch.setattr(_cr.settings, "processed_dir", proc)
        assert valid_stay_id_to_dataset() == {}

    def test_legacy_submit_review_is_gone(self, isolated_review_storage):
        response = client.post(
            "/review/submit",
            json={
                "stay_id": 1,
                "reviewer_role": "triage_nurse",
                "review_status": "REVIEWED",
                "review_comment": "Test review for source_dataset coverage.",
            },
        )
        assert response.status_code == 410
        assert "/cases/{case_uid}/reviews" in response.json()["detail"]

    def test_client_supplied_source_dataset_is_not_blindly_trusted(
        self, isolated_review_storage
    ):
        """
        Confirms the server-side lookup is authoritative: a client
        claiming a known stay_id belongs to a different dataset than it
        actually does must NOT have that claim accepted.
        """
        response = client.post(
            "/review/submit",
            json={
                "stay_id": 1,
                "source_dataset": "MIMIC-IV-ED-Demo-v2.2",
                "reviewer_role": "triage_nurse",
                "review_status": "REVIEWED",
                "review_comment": "Test: attempting to spoof source_dataset.",
            },
        )
        assert response.status_code == 410
        assert "/cases/{case_uid}/reviews" in response.json()["detail"]

    def test_source_dataset_is_optional_for_backward_compatibility(
        self, isolated_review_storage
    ):
        """
        Confirms a request with no source_dataset field at all (e.g. an
        older client written before this field existed) still succeeds,
        rather than failing validation.
        """
        response = client.post(
            "/review/submit",
            json={
                "stay_id": 1,
                "reviewer_role": "researcher",
                "review_status": "REVIEWED",
                "review_comment": "Test: omitting source_dataset entirely.",
            },
        )
        assert response.status_code == 410
        assert "/cases/{case_uid}/reviews" in response.json()["detail"]


class TestSubmitReviewValidation:
    def test_invalid_stay_id_returns_400(self, isolated_review_storage):
        response = client.post(
            "/review/submit",
            json={
                "stay_id": 999999999,
                "reviewer_role": "triage_nurse",
                "review_status": "REVIEWED",
                "review_comment": "This stay_id does not exist.",
            },
        )
        assert response.status_code == 410
        assert "/cases/{case_uid}/reviews" in response.json()["detail"]


class TestReviewByStay:
    def test_stay_with_no_reviews_returns_empty_list(self, isolated_review_storage):
        response = client.get("/review/by-stay/123456789")
        assert response.status_code == 410
        assert "/cases/{case_uid}/reviews" in response.json()["detail"]


def teardown_module(module):
    """Restore the default (legacy-disabled) app state for subsequent test modules."""
    import os, importlib
    os.environ.pop("ALLOW_LEGACY_RAW_ID_ROUTES", None)
    import app.main as _am
    importlib.reload(_am)
