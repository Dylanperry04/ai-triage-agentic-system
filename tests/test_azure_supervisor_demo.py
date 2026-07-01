"""Azure supervisor demo guardrails and synthetic case source."""

from starlette.testclient import TestClient

from app.main import app


def test_azure_supervisor_demo_serves_labelled_synthetic_cases(monkeypatch, tmp_path):
    for name in (
        "PATIENT_DATA_MODE",
        "LOCAL_CREDENTIALED_RESEARCH",
        "AUTH_REQUIRED",
        "TRUSTED_AUTH_PROXY",
        "REAL_PATIENT_DATA",
        "MIMIC_FULL_ED_DIR",
        "ALLOW_FULL_MIMIC_IN_AZURE_DEMO",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AZURE_SUPERVISOR_DEMO_MODE", "true")
    monkeypatch.setenv("ALLOW_DEMO_ROLE_SWITCHER", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "demo")
    monkeypatch.setattr("app.config.settings.processed_dir", tmp_path)

    client = TestClient(app)
    response = client.get("/cases", headers={"X-Demo-Role": "triage_nurse"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 10
    first = payload["cases"][0]
    assert first["is_synthetic_demo"] is True
    assert "not real patient data" in first["demo_data_notice"].lower()
    assert "triage" in first
    assert "stay_id" not in str(first)
    assert "subject_id" not in str(first)
    assert "hadm_id" not in str(first)
    scenarios = {c.get("triage", {}).get("chiefcomplaint") for c in payload["cases"]}
    assert "chest pain radiating to left arm" in scenarios
    assert "dizziness" in scenarios

    assessment = client.post(
        f"/cases/{first['case_uid']}/assessments",
        headers={"X-Demo-Role": "triage_nurse"},
    )
    assert assessment.status_code == 200
    assessment_blob = str(assessment.json())
    assert "stay_id" not in assessment_blob
    assert "subject_id" not in assessment_blob
    assert "hadm_id" not in assessment_blob

    status = client.get("/status/full-mimic").json()
    assert status["active_profile"] == "azure_supervisor_demo"
    llm_status = client.get("/status/llm").json()
    assert llm_status["active_profile"] == "azure_supervisor_demo"


def test_azure_supervisor_demo_does_not_override_explicit_full_mimic_approval(
    monkeypatch,
    tmp_path,
):
    for name in (
        "PATIENT_DATA_MODE",
        "LOCAL_CREDENTIALED_RESEARCH",
        "AUTH_REQUIRED",
        "TRUSTED_AUTH_PROXY",
        "REAL_PATIENT_DATA",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AZURE_SUPERVISOR_DEMO_MODE", "true")
    monkeypatch.setenv("ALLOW_DEMO_ROLE_SWITCHER", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "demo")
    monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(tmp_path / "missing-ed-dir"))
    monkeypatch.setenv("ALLOW_FULL_MIMIC_IN_AZURE_DEMO", "true")
    monkeypatch.setattr("app.config.settings.processed_dir", tmp_path)

    client = TestClient(app)
    response = client.get("/cases", headers={"X-Demo-Role": "triage_nurse"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["cases"] == []
    assert payload["pagination"]["total"] == 0
    status = client.get("/status/full-mimic").json()
    assert status["full_mimic_requested_for_azure_demo"] is True
    assert status["real_mimic_demo_acknowledged"] is False
    assert "Full MIMIC requested for Azure demo" in status["reason"]
    assert "MIMIC_FULL_ED_DIR is not readable by the backend" in status["reason"]


def test_full_mimic_azure_demo_requires_acknowledgement(monkeypatch):
    from app.security.security_status import unsafe_combinations

    for name in (
        "PATIENT_DATA_MODE",
        "LOCAL_CREDENTIALED_RESEARCH",
        "AUTH_REQUIRED",
        "TRUSTED_AUTH_PROXY",
        "REAL_PATIENT_DATA",
        "REAL_MIMIC_DEMO_ACKNOWLEDGED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AZURE_SUPERVISOR_DEMO_MODE", "true")
    monkeypatch.setenv("ALLOW_DEMO_ROLE_SWITCHER", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "demo")
    monkeypatch.setenv("ALLOW_FULL_MIMIC_IN_AZURE_DEMO", "true")

    problems = unsafe_combinations(run_probes=False)
    assert any("REAL_MIMIC_DEMO_ACKNOWLEDGED" in p for p in problems)
