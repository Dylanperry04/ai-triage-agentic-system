"""Controlled software fixtures; none are independent clinical references."""
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.test_mts_workflow_integration import client, headers, uid, checklist


@pytest.mark.parametrize("value", [32, 35, 37, 38.5, 41])
def test_temperature_preserves_units_across_checklist_and_followup(client, value):
    case = uid()
    form = checklist(client, case)
    form["observations"] = {"temperature": {"state": "assessed", "value": value}}
    assert client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=form).status_code == 200
    triage = client.get(f"/cases/{case}", headers=headers()).json()["triage"]
    assert triage["temperature"] == value and triage["temperature_unit"] == "C"
    from app.rules.vitals import temperature_c
    from types import SimpleNamespace
    assert temperature_c(SimpleNamespace(**triage)) == pytest.approx(value)
    run = client.post(f"/cases/{case}/assessments", headers=headers()).json()
    assert run["ml_prediction_available"] is True
    response = client.post(f"/cases/{case}/followups", headers=headers(), json={"updated_vitals": {"temperature": 98.6}})
    assert response.status_code == 200, response.text
    change = next(v for v in response.json()["changed_vitals"] if v["field"] == "temperature")
    assert change["previous"] == pytest.approx(value * 9 / 5 + 32)
    assert change["new"] == 98.6 and change["unit"] == "F"
    triage = client.get(f"/cases/{case}", headers=headers()).json()["triage"]
    assert triage["temperature"] == 98.6 and triage["temperature_unit"] == "F"
    assert temperature_c(SimpleNamespace(**triage)) == pytest.approx(37)
    # The previous decision retains its original Celsius observation snapshot.
    from app.config import settings
    from app.storage.workflow_run_repository import read_workflow_run
    frozen = read_workflow_run(settings.processed_dir / "workflow_runs.jsonl", run["workflow_run_id"], case)
    assert frozen["mts_assessment"]["observations"]["temperature"]["value"] == value


@pytest.mark.parametrize("field", ["pain", "temperature", "heartrate", "resprate", "sbp", "dbp", "o2sat"])
@pytest.mark.parametrize("value", [True, False])
def test_boolean_is_never_a_numeric_followup(client, field, value):
    assert client.post(f"/cases/{uid()}/followups", headers=headers(), json={"updated_vitals": {field: value}}).status_code == 422


def test_noop_and_explicit_repeat_have_different_history(client):
    from app.api.case_routes import _latest_workflow_state
    case = uid(); body = checklist(client, case)
    body["observations"] = {"heartrate": {"state": "assessed", "value": 80}}
    first = client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body).json()
    before = _latest_workflow_state(case)
    noop = client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=first["checklist"]).json()
    assert noop["status"] == "unchanged"
    assert _latest_workflow_state(case) == before
    repeated = {**first["checklist"], "reassessed_measurements": ["heartrate"]}
    result = client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=repeated)
    assert result.status_code == 200, result.text
    after = _latest_workflow_state(case)
    assert after["last_vitals_updated_at"] != before["last_vitals_updated_at"]
    assert after["last_vitals_assessed_fields"] == ["heartrate"]


@pytest.mark.parametrize("payload", [b'{"broken"\n', b'[]\n', b'{"record_id":"incomplete"}\n'])
def test_damaged_evidence_cannot_be_reported_as_zero(client, payload):
    from app.config import settings
    (settings.processed_dir / "mts_references.jsonl").write_bytes(payload)
    response = client.get("/research/mts/evaluation", headers=headers("researcher"))
    assert response.status_code == 503
    assert response.json()["code"] == "research_evidence_integrity_error"


def test_strict_reads_share_the_append_lock(tmp_path):
    from app.storage.jsonl_io import append_jsonl, read_jsonl_dicts
    path = tmp_path / "evidence.jsonl"
    def worker(i):
        append_jsonl(path, {"i": i, "value": "x" * 2000})
        return read_jsonl_dicts(path, strict=True)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(worker, range(80)))
    assert len(read_jsonl_dicts(path, strict=True)) == 80


def test_trusted_loader_never_loads_bad_pin_and_loads_same_verified_bytes(monkeypatch, tmp_path):
    from app.ml.trusted_artifact import load_verified_model
    import joblib
    path = tmp_path / "model.joblib"; path.write_bytes(b"verified test bytes")
    seen = []
    def loader(stream):
        assert isinstance(stream, io.BytesIO)
        path.write_bytes(b"replaced after verification")
        seen.append(stream.read())
        return {"ok": True}
    monkeypatch.setattr(joblib, "load", loader)
    with pytest.raises(ValueError): load_verified_model(path, "0" * 64)
    assert seen == []
    assert load_verified_model(path, hashlib.sha256(b"verified test bytes").hexdigest()) == {"ok": True}
    assert seen == [b"verified test bytes"]


def test_input_revision_affects_each_output_but_ui_revision_does_not():
    from app.rules.decision_policy import system_identity
    from app.api.mts_routes import _eligibility
    identity = system_identity()
    row = {"system_identity": identity, "catalog_sha256": identity["catalog_sha256"],
           "model_sha256": identity["model_sha256"], "reference_method": "independent_clinician_review"}
    assert all(_eligibility(row)[0].values())
    row["system_identity"] = {**identity, "application_revision": "different UI"}
    assert all(_eligibility(row)[0].values())
    row["system_identity"] = {**identity, "input_processing_sha256": "old"}
    flags, reasons = _eligibility(row)
    assert not any(flags.values())
    assert "input_processing_version_mismatch" in reasons


def test_untrusted_readiness_does_not_deserialize(client, monkeypatch):
    from app.api import health_routes
    import joblib
    from unittest.mock import Mock
    status = health_routes._uhl_status_payload()
    monkeypatch.setattr(health_routes, "_uhl_status_payload", lambda: {**status, "model_hash_verified": False})
    loader = Mock(); monkeypatch.setattr(joblib, "load", loader)
    assert client.get("/ready").status_code == 503
    loader.assert_not_called()


def test_invalid_combined_reference_cannot_write_partial_annotation(client):
    from app.storage.mts_repository import read_records
    case = uid(); body = checklist(client, case)
    body["observations"] = {k: {"state": "unavailable"} for k in ("temperature", "o2sat", "heartrate", "resprate", "sbp")}
    assert client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body).status_code == 200
    run = client.post(f"/cases/{case}/assessments", headers=headers()).json()
    assert run["final_acuity"] is None
    response = client.post("/research/mts/references", headers=headers(), json={"case_uid": case,
        "workflow_run_id": run["workflow_run_id"], "reference_flowchart": "chest_pain", "reference_category": 3,
        "reference_method": "independent_clinician_review", "reference_provenance": "Independent software fixture only",
        "guideline_consistent": True})
    assert response.status_code == 409
    assert read_records("mts_reference") == []


def test_combined_review_io_failure_reports_committed_reference_and_retry_is_idempotent(client, monkeypatch):
    from app.api import mts_routes
    from app.storage.mts_repository import read_records
    case = uid()
    run = client.post(f"/cases/{case}/assessments", headers=headers()).json()
    payload = {"case_uid": case, "workflow_run_id": run["workflow_run_id"], "reference_flowchart": "chest_pain",
        "reference_category": 3, "reference_method": "independent_clinician_review",
        "reference_provenance": "Independent software fixture only", "guideline_consistent": True}
    original = mts_routes.append_record
    def fail_review(kind, values):
        if kind == "mts_guideline_review": raise OSError("Injected disk failure")
        return original(kind, values)
    monkeypatch.setattr(mts_routes, "append_record", fail_review)
    failed = client.post("/research/mts/references", headers=headers(), json=payload)
    assert failed.status_code == 503
    record_id = failed.json()["detail"]["saved_reference_id"]
    assert failed.json()["detail"]["retry_safe"] is True
    assert len(read_records("mts_reference")) == 1
    monkeypatch.setattr(mts_routes, "append_record", original)
    for _ in range(2):
        response = client.post("/research/mts/references", headers=headers(), json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["record_id"] == record_id
    assert len(read_records("mts_reference")) == len(read_records("mts_guideline_review")) == 1


@pytest.mark.parametrize("source,stub,expected", [("azure_trusted_header", False, True), ("local_stub", True, False), ("azure_supervisor_demo_stub", True, False), ("local_fixed_role", False, False)])
def test_platform_logout_only_for_real_trusted_azure_identity(monkeypatch, source, stub, expected):
    from app.api.session_routes import auth_session
    from app.security.identity import AuthContext
    ctx = AuthContext(authenticated=True, user_id="test-user", roles=["triage_nurse"], source=source, is_demo_stub=stub)
    assert bool(auth_session(ctx)["logout_url"]) is expected


@pytest.mark.parametrize("labels", ["shock", [True], ["invented"], ["shock", "shock"]])
def test_semantically_damaged_reference_blocks_metrics(client, labels):
    from app.storage.mts_repository import append_record
    append_record("mts_reference", {"case_uid": uid(), "workflow_run_id": "software-fixture",
        "reference_flowchart": "chest_pain", "reference_category": 3,
        "reference_method": "independent_clinician_review", "reference_discriminators": labels})
    assert client.get("/research/mts/evaluation", headers=headers("researcher")).status_code == 503


@pytest.mark.parametrize("field,value", [("reference_flowchart", []), ("case_uid", ["invalid"])])
def test_wrong_evidence_types_have_explicit_integrity_response(client, field, value):
    from app.storage.mts_repository import append_record
    row = {"case_uid": uid(), "workflow_run_id": "software-fixture", "reference_flowchart": "chest_pain",
        "reference_category": 3, "reference_method": "independent_clinician_review"}
    row[field] = value
    append_record("mts_reference", row)
    response = client.get("/research/mts/evaluation", headers=headers("researcher"))
    assert response.status_code == 503
    assert response.json()["code"] == "research_evidence_integrity_error"
