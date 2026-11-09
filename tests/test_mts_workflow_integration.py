"""Real application integration; temporary clinical assertions are test fixtures only."""
import pytest
from fastapi.testclient import TestClient
from app.config import settings
from app.main import app
from app.data_pipeline.uhl_repository import get_case_repository
from app.rules.mts_catalog import VERSION, catalog_hash


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Configuration tests reload app.config, replacing its singleton. Bind the
    # current instance at execution time so full-suite runs stay isolated too.
    import sys
    from app.config import settings as current_settings
    monkeypatch.setattr(sys.modules[__name__], "settings", current_settings)
    monkeypatch.setattr("app.api.status_routes.settings", current_settings)
    monkeypatch.setattr("app.config.settings.processed_dir", tmp_path)
    monkeypatch.setenv("AUTH_MODE", "demo")
    for name in ("PATIENT_DATA_MODE", "LOCAL_CREDENTIALED_RESEARCH", "TRUSTED_AUTH_PROXY"):
        monkeypatch.setenv(name, "false")
    return TestClient(app)


def headers(role="triage_nurse"):
    return {"X-Demo-Role": role, "X-Demo-User": "test-observer"}


def uid():
    return next(c["case_uid"] for c in get_case_repository(settings).list_cases()["cases"]
                if c["triage"]["chiefcomplaint"].casefold() == "chest pain")


def checklist(client, case):
    response = client.get(f"/cases/{case}/mts-checklist", headers=headers()).json()
    return {"catalog_version": VERSION, "catalog_sha256": catalog_hash(),
            "flowchart_id": "chest_pain", "flowchart_confirmed": True,
            "expected_revision": response["revision"], "expected_observations_at": response["observations_at"],
            "observations": {"shock": {"state": "assessed", "value": True}}}


def test_nursing_checklist_is_in_exact_run_and_audit_then_can_only_raise_recommendation(client):
    case = uid()
    first = client.post(f"/cases/{case}/assessments", headers=headers()).json()
    body = checklist(client, case)
    saved = client.post(f"/cases/{case}/mts-checklist", headers=headers("ed_nurse"), json=body)
    assert saved.status_code == 200, saved.text
    stale = client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body)
    assert stale.status_code == 409
    response = client.post(f"/cases/{case}/assessments", headers=headers())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["final_acuity"] == 1
    assert result["final_acuity"] <= first["final_acuity"]
    trace = result["mts_assessment"]
    assert trace["priority"] is None and not trace["assessment_complete"]
    assert trace["candidate_priority"] == 1
    assert trace["unresolved_earlier_discriminators"] == ["airway_compromise", "inadequate_breathing"]
    assert result["mts_assessment"]["matched_discriminators"] == ["shock"]
    from app.storage.workflow_run_repository import read_workflow_run
    row = read_workflow_run(settings.processed_dir / "workflow_runs.jsonl", result["workflow_run_id"], case)
    assert row["mts_assessment"] == result["mts_assessment"]
    assert row["explanation_evidence"]["mts_assessment"] == result["mts_assessment"]
    state = client.get(f"/cases/{case}", headers=headers()).json()["workflow_state"]
    assert state["mts_checklist_requires_assessment"] is False


@pytest.mark.parametrize("role", ["researcher", "governance_auditor", "ed_doctor"])
def test_roles_without_nursing_observation_permission_cannot_change_checklists(client, role):
    case = uid()
    response = client.post(f"/cases/{case}/mts-checklist", json=checklist(client, case), headers=headers(role))
    assert response.status_code == 403


def test_explicit_unavailable_measurement_clears_previous_source_value(client):
    case = uid()
    body = checklist(client, case)
    body["observations"] = {"o2sat": {"state": "unavailable", "value": None}}
    assert client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body).status_code == 200
    current = client.get(f"/cases/{case}", headers=headers()).json()
    assert "o2sat" not in current["triage"]
    result = client.post(f"/cases/{case}/assessments?preview=true", headers=headers()).json()
    assert "o2sat" in result["critical_missing_vitals"]


def test_reference_is_frozen_separate_from_predictions_and_unblinded_labels_are_excluded(client):
    case = uid()
    assessment = client.post(f"/cases/{case}/assessments", headers=headers()).json()
    body = {"case_uid": case, "workflow_run_id": assessment["workflow_run_id"],
        "reference_flowchart": "chest_pain", "reference_category": 2,
        "reference_discriminators": ["cardiac_pain"],
        "reference_method": "unblinded_clinician_review", "reference_provenance": "Explicit test annotation; not real clinical evidence"}
    response = client.post("/research/mts/references", json=body, headers=headers("ed_doctor"))
    assert response.status_code == 200, response.text
    report = client.get("/research/mts/evaluation", headers=headers("researcher")).json()
    assert report["reference_cases"] == 0
    assert report["flowchart_selection"]["accuracy"] is None
    body["reference_method"] = "independent_clinician_review"
    assert client.post("/research/mts/references", json=body, headers=headers("ed_doctor")).status_code == 200
    report = client.get("/research/mts/evaluation", headers=headers("researcher")).json()
    assert report["reference_cases"] == 1
    assert report["flowchart_selection"]["accuracy"] == 1
    assert report["unsupported_clinical_claims"]["explanations_with_unsupported_claims"] is None
    body["reference_discriminators"] = ["invented_discriminator"]
    assert client.post("/research/mts/references", json=body, headers=headers("ed_doctor")).status_code == 422


def test_changed_general_observations_invalidate_checklist_but_preserve_prior_trace(client):
    from app.api.case_routes import _append_workflow_state, _latest_workflow_state
    from app.storage.workflow_run_repository import read_workflow_run
    case = uid()
    assert client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=checklist(client, case)).status_code == 200
    assessment = client.post(f"/cases/{case}/assessments", headers=headers()).json()
    state = _latest_workflow_state(case)
    state.update(latest_triage_updates_at="2026-10-29T23:59:59+00:00", latest_triage_updates={"heartrate": 85})
    _append_workflow_state(state)
    current = client.get(f"/cases/{case}/mts-checklist", headers=headers()).json()
    assert current["checklist"] is None
    assert current["previous_checklist_requires_reassessment"] is True
    preview = client.post(f"/cases/{case}/assessments?preview=true", headers=headers()).json()
    assert preview["mts_assessment"]["status"] == "awaiting_nurse_checklist"
    previous = read_workflow_run(settings.processed_dir / "workflow_runs.jsonl", assessment["workflow_run_id"], case)
    assert previous["mts_assessment"]["matched_discriminators"] == ["shock"]


def test_checklist_rejects_concurrent_general_observation_change(client):
    from app.api.case_routes import _append_workflow_state, _latest_workflow_state
    case = uid()
    body = checklist(client, case)
    state = _latest_workflow_state(case)
    _append_workflow_state({**state, "case_uid": case, "latest_triage_updates_at": "2026-10-29T23:59:59+00:00"})
    assert client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body).status_code == 409


@pytest.mark.parametrize("profile", ["PATIENT_DATA_MODE", "LOCAL_CREDENTIALED_RESEARCH"])
def test_demo_research_storage_fails_closed_in_protected_profiles(client, monkeypatch, profile):
    monkeypatch.setenv(profile, "true")
    from app.storage.mts_repository import read_records, append_record
    with pytest.raises(RuntimeError, match="not enabled"):
        read_records("mts_reference")
    with pytest.raises(RuntimeError, match="not enabled"):
        append_record("mts_reference", {})


def test_changed_presenting_complaint_uses_current_proposal_when_saving(client):
    from app.api.case_routes import _append_workflow_state, _latest_workflow_state
    case = uid()
    _append_workflow_state({**_latest_workflow_state(case), "case_uid": case,
        "latest_triage_updates": {"chiefcomplaint": "shortness of breath"},
        "latest_triage_updates_at": "2026-10-29T20:00:00+00:00"})
    body = checklist(client, case)
    body.update(flowchart_id="shortness_of_breath_adults", observations={})
    response = client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body)
    assert response.status_code == 200, response.text


def test_source_recorded_measurements_cannot_be_fabricated(client):
    case = uid()
    body = checklist(client, case)
    body["observations"] = {"heartrate": {"state": "source_recorded", "value": 1}}
    assert client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body).status_code == 422
    source = client.get(f"/cases/{case}/mts-checklist", headers=headers()).json()["source_triage"]
    body["observations"]["heartrate"]["value"] = source["heartrate"]
    body["observations"]["temperature"] = {"state": "source_recorded", "value": (source["temperature"] - 32) * 5 / 9}
    assert client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body).status_code == 200


def test_explanation_review_binds_exact_saved_text_and_counts_each_explanation(client):
    from app.storage.mts_repository import save_explanation
    case = uid()
    result = client.post(f"/cases/{case}/assessments", headers=headers()).json()
    run = result["workflow_run_id"]
    first = save_explanation(case, run, {"final_explanation": "First test explanation.", "status": "test_fixture"})
    second = save_explanation(case, run, {"final_explanation": "Second test explanation.", "status": "test_fixture"})
    body = {"case_uid": case, "workflow_run_id": run, "reference_flowchart": "chest_pain",
            "reference_category": 2, "reference_method": "independent_clinician_review",
            "reference_provenance": "Test reference only; no clinical evaluation asserted",
            "explanation_reviewed": True, "explanation_id": "missing-id"}
    assert client.post("/research/mts/references", headers=headers(), json=body).status_code == 409
    for explanation_id, claims in ((first, ["Unsupported test symptom"]), (second, [])):
        body.update(explanation_id=explanation_id, unsupported_claims=claims)
        assert client.post("/research/mts/references", headers=headers(), json=body).status_code == 200
    report = client.get("/research/mts/evaluation", headers=headers("researcher")).json()
    assert report["reference_cases"] == 1
    assert report["unsupported_clinical_claims"]["reviewed_explanations"] == 2
    assert report["unsupported_clinical_claims"]["explanations_with_unsupported_claims"] == 1
    assert report["unsupported_clinical_claims"]["unreviewed_explanations"] == 0


def test_mts_audit_metadata_survives_without_bypassing_identifier_redaction():
    from app.security.redaction import redact_for_log
    from uuid import uuid4
    revision = str(uuid4())
    digest = catalog_hash()
    row = redact_for_log({"checklist_revision": revision, "record_id": revision,
                          "explanation_id": revision, "catalog_sha256": digest,
                          "notes": "Contact nurse@example.org", "name": "Patient Name"})
    assert row["checklist_revision"] == row["record_id"] == row["explanation_id"] == revision
    assert row["catalog_sha256"] == digest
    assert "name" not in row and "nurse@example.org" not in row["notes"]
    unsafe = redact_for_log({"explanation_id": "nurse@example.org", "catalog_sha256": "9" * 64})
    assert "nurse@example.org" not in unsafe["explanation_id"]
    assert "9" * 64 not in unsafe["catalog_sha256"]
    # A valid UUID can contain no hexadecimal letters; it is still opaque
    # metadata rather than a raw numeric identifier and must round-trip.
    numeric_uuid = "12345678-1234-4123-8123-123456789012"
    assert redact_for_log({"record_id": numeric_uuid})["record_id"] == numeric_uuid


def test_missing_ml_preserves_red_finding_and_mts_evaluation(client):
    case = uid()
    body = checklist(client, case)
    body['observations']['airway_compromise'] = {'state': 'assessed', 'value': False}
    body['observations']['inadequate_breathing'] = {'state': 'assessed', 'value': False}
    body['observations']['o2sat'] = {'state': 'unavailable', 'value': None}
    assert client.post(f'/cases/{case}/mts-checklist', headers=headers(), json=body).status_code == 200
    result = client.post(f'/cases/{case}/assessments', headers=headers()).json()
    assert result['final_acuity'] == 1
    assert result['mts_assessment']['recommendation_basis'] == 'documented_safety_or_mts_floor_without_ml'
    assert result['mts_assessment']['priority'] == 1
    reference = {'case_uid': case, 'workflow_run_id': result['workflow_run_id'],
        'reference_flowchart': 'chest_pain', 'reference_category': 1, 'reference_discriminators': ['shock'],
        'reference_method': 'independent_clinician_review', 'reference_provenance': 'Unit test reference only'}
    saved = client.post('/research/mts/references', headers=headers('ed_doctor'), json=reference)
    assert saved.status_code == 200, saved.text
    metrics = client.get('/research/mts/evaluation', headers=headers('researcher')).json()['by_presenting_complaint']['all']
    assert metrics['mts_category']['evaluated_cases'] == metrics['final_category']['evaluated_cases'] == 1
    assert metrics['ml_category']['evaluated_cases'] == 0


@pytest.mark.parametrize(("ml", "shock", "o2sat", "before", "after", "mts_effect", "vital_effect"), [
    (4, True, 97, 4, 1, True, False),
    (4, False, 89, 2, 2, False, True),
    (1, True, 97, 1, 1, False, False),
    (4, False, 97, 4, 4, False, False),
    (None, True, None, None, 1, True, False),
])
def test_recorded_contributions_agree_across_outputs(
    client, monkeypatch, ml, shock, o2sat, before, after, mts_effect, vital_effect
):
    from app.schemas.workflow import MLPredictionResult
    from app.storage.workflow_run_repository import read_workflow_run

    monkeypatch.setattr("app.agents.orchestrator.run_ml_prediction", lambda _: MLPredictionResult(
        prediction_available=ml is not None, predicted_mimic_acuity=ml,
    ))
    case = uid()
    body = checklist(client, case)
    body["observations"]["shock"]["value"] = shock
    body["observations"]["o2sat"] = (
        {"state": "assessed", "value": o2sat} if o2sat is not None
        else {"state": "unavailable", "value": None}
    )
    saved = client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body)
    assert saved.status_code == 200, saved.text
    result = client.post(f"/cases/{case}/assessments", headers=headers()).json()
    trace = result["mts_assessment"]
    safety = result["discriminator_evidence"]
    assert result["final_acuity"] == after
    assert trace["escalation_applied"] is mts_effect
    assert trace["advisory_before_mts"] == before
    assert safety["override_applied"] is vital_effect
    pathway = safety["pathway"]
    assert pathway["kind"] == "structured_mts"
    assert pathway["affects_displayed_acuity"] is mts_effect
    assert pathway["advisory_before"] == before
    assert pathway["advisory_after"] == after
    run = read_workflow_run(settings.processed_dir / "workflow_runs.jsonl", result["workflow_run_id"], case)
    assert run["discriminator_evidence"] == safety
    assert run["explanation_evidence"]["category_reasoning"]["rules_summary"]["contribution"] == pathway


def test_pinned_explanation_uses_saved_evidence_without_generation(client, monkeypatch):
    from app.storage.mts_repository import read_records

    async def unexpected_cloud_call(*args, **kwargs):
        pytest.fail("Rendering a recorded decision must never invoke AutoGen")

    monkeypatch.setattr("app.agents.autogen_multi_agent_team.run_case_uid_team_explanation", unexpected_cloud_call)
    monkeypatch.setenv("ALTER_DISABLE_MULTIAGENT_EXPLANATION", "true")
    case = uid()
    body = checklist(client, case)
    body["observations"]["shock"]["note"] = "Invented unsupported symptom"
    body["observations"]["o2sat"] = {"state": "unavailable", "value": None}
    assert client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body).status_code == 200
    run = client.post(f"/cases/{case}/assessments", headers=headers()).json()["workflow_run_id"]
    body = checklist(client, case)
    body["observations"]["shock"]["value"] = False
    assert client.post(f"/cases/{case}/mts-checklist", headers=headers(), json=body).status_code == 200
    response = client.post(f"/cases/{case}/multiagent-explanations", headers=headers(),
                           json={"workflow_run_id": run})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["explanation_source"] == "recorded_evidence"
    assert "Acuity 1 (shock)" in result["final_explanation"]
    assert "Invented unsupported symptom" not in result["final_explanation"]
    assert "no available model estimate" in result["final_explanation"]
    record = next(r for r in read_records("mts_explanation") if r["record_id"] == result["explanation_id"])
    assert record["workflow_run_id"] == run
    assert record["explanation"] == result["final_explanation"]
    assert record["evidence_links"] == result["evidence_links"]
    assert any(link["claim"] == "mts_finding" for link in result["evidence_links"])


def test_pinned_explanation_rejects_conflicting_stages(client, monkeypatch):
    case = uid()
    result = client.post(f"/cases/{case}/assessments", headers=headers()).json()
    run_id = result["workflow_run_id"]
    from app.storage import workflow_run_repository
    run = workflow_run_repository.read_workflow_run(settings.processed_dir / "workflow_runs.jsonl", run_id, case)
    evidence = run["explanation_evidence"]
    evidence["final_acuity_assessment"]["final_acuity"] = None
    evidence["ml_research_estimate"].update(available=True, predicted_mimic_acuity=2)
    monkeypatch.setattr(workflow_run_repository, "read_workflow_run", lambda *_: run)
    response = client.post(f"/cases/{case}/multiagent-explanations", headers=headers(),
                           json={"workflow_run_id": run_id})
    assert response.status_code == 409
    assert "inconsistent" in response.json()["detail"]


def test_incomplete_acceptance_requires_explicit_exception_and_rejects_stale_run(client):
    case = uid()
    result = client.post(f'/cases/{case}/assessments', headers=headers()).json()
    review = {'workflow_run_id': result['workflow_run_id'], 'review_status': 'ACCEPTED_AS_PRESENTED'}
    denied = client.post(f'/cases/{case}/reviews', headers=headers(), json=review)
    assert denied.status_code == 422, denied.text
    review['assessment_exception_reason'] = 'Test exception: unavailable observations acknowledged'
    accepted = client.post(f'/cases/{case}/reviews', headers=headers(), json=review)
    assert accepted.status_code == 200, accepted.text
    state = client.get(f'/cases/{case}', headers=headers()).json()['workflow_state']
    assert state['assessment_exception_reason'] == review['assessment_exception_reason']
    assert client.post(f'/cases/{case}/mts-checklist', headers=headers(), json=checklist(client, case)).status_code == 200
    assert client.post(f'/cases/{case}/reviews', headers=headers(), json=review).status_code == 409


def test_checklist_measurements_share_clock_provenance_and_requested_information_handoff(client):
    from app.api.case_routes import _append_workflow_state, _latest_workflow_state
    case = uid()
    _append_workflow_state({'case_uid': case, 'case_status': 'request_more_info', 'requesting_role': 'ed_doctor',
        'request_timestamp': '2026-10-01T10:00:00+00:00', 'last_vitals_updated_at': '2026-10-01T09:00:00+00:00',
        'overdue_vitals_alert_active': True})
    body = checklist(client, case)
    body['observations']['heartrate'] = {'state': 'assessed', 'value': 90}
    response = client.post(f'/cases/{case}/mts-checklist', headers=headers('ed_nurse'), json=body)
    assert response.status_code == 200, response.text
    state = _latest_workflow_state(case)
    measured_at = state['last_vitals_updated_at']
    assert measured_at != '2026-10-01T09:00:00+00:00'
    assert state['last_vitals_assessed_fields'] == ['heartrate']
    assert state['last_vitals_update_scope'] == 'partial'
    assert state['reassessment_target_role'] == 'ed_doctor'
    assert state['information_response_to_request_at'] == '2026-10-01T10:00:00+00:00'
    assert state['case_status'] == 'request_more_info'
    assert state['observation_provenance']['heartrate']['recorded_by_role'] == 'ed_nurse'
    body = response.json()['checklist']; body['notes'] = 'Notes-only save does not repeat vitals'
    assert client.post(f'/cases/{case}/mts-checklist', headers=headers(), json=body).status_code == 200
    assert _latest_workflow_state(case)['last_vitals_updated_at'] == measured_at
    current = client.get(f'/cases/{case}/mts-checklist', headers=headers()).json()
    assert current['recorded_triage']['heartrate'] == 90
    assert current['observation_provenance']['heartrate']['observed_at'] == measured_at


def test_separate_explanation_reviews_do_not_displace_reference_and_corrections_are_explicit(client):
    from app.storage.mts_repository import save_explanation
    case = uid()
    run = client.post(f'/cases/{case}/assessments', headers=headers()).json()['workflow_run_id']
    body = {'case_uid': case, 'workflow_run_id': run, 'reference_flowchart': 'chest_pain',
        'reference_category': 2, 'reference_discriminators': ['cardiac_pain'],
        'reference_method': 'independent_clinician_review', 'reference_provenance': 'Unit test independently supplied reference'}
    first = client.post('/research/mts/references', headers=headers(), json=body).json()
    explanation = save_explanation(case, run, {'final_explanation': 'Test explanation only.', 'status': 'test'})
    reviewed = client.post('/research/mts/explanation-reviews', headers=headers(), json={
        'case_uid': case, 'workflow_run_id': run, 'explanation_id': explanation,
        'unsupported_claims': [], 'review_note': 'Test review found no unsupported claims'})
    assert reviewed.status_code == 200, reviewed.text
    history = client.get('/research/mts/references', params={'case_uid': case}, headers=headers()).json()['references']
    assert len(history) == 1 and history[0]['reference_discriminators'] == ['cardiac_pain']
    body['reference_category'] = 3
    assert client.post('/research/mts/references', headers=headers(), json=body).status_code == 409
    body.update(supersedes_record_id=first['record_id'], correction_reason='Explicit test correction')
    assert client.post('/research/mts/references', headers=headers(), json=body).status_code == 200
    body['reference_category'] = 4
    assert client.post('/research/mts/references', headers=headers(), json=body).status_code == 409
    report = client.get('/research/mts/evaluation', headers=headers('researcher')).json()
    assert report['reference_cases'] == 1 and report['unsupported_clinical_claims']['reviewed_explanations'] == 1


@pytest.mark.parametrize("damage", ["orphan", "wrong_case", "wrong_run", "wrong_digest", "missing_digest"])
def test_evaluation_rejects_explanation_reviews_without_exact_saved_evidence(client, damage):
    import hashlib
    from app.rules.decision_policy import system_identity
    from app.storage.mts_repository import append_record

    context = {"case_uid": "test-case", "workflow_run_id": "test-run", "system_identity": system_identity()}
    text = "Exact saved test explanation."
    digest = hashlib.sha256(text.encode()).hexdigest()
    explanation_id = "orphaned-test-id"
    if damage != "orphan":
        explanation_id = append_record("mts_explanation", {**context, "explanation": text,
                                        "explanation_sha256": digest})["record_id"]
    review = {**context, "explanation_id": explanation_id, "explanation_reviewed": True,
              "explanation_sha256": digest, "unsupported_claims": ["Test claim"]}
    if damage == "wrong_case":
        review["case_uid"] = "other-case"
    elif damage == "wrong_run":
        review["workflow_run_id"] = "other-run"
    elif damage == "wrong_digest":
        review["explanation_sha256"] = "a" * 64
    elif damage == "missing_digest":
        review.pop("explanation_sha256")
    append_record("mts_explanation_review", review)
    response = client.get("/research/mts/evaluation", headers=headers("researcher"))
    assert response.status_code == 503
    assert response.json()["code"] == "research_evidence_integrity_error"


def test_evaluation_rejects_orphaned_guideline_reference(client):
    from app.storage.mts_repository import append_record

    append_record("mts_guideline_review", {"case_uid": "test-case", "workflow_run_id": "test-run",
                  "reference_id": "missing-reference", "target": "final_category", "guideline_consistent": True})
    response = client.get("/research/mts/evaluation", headers=headers("researcher"))
    assert response.status_code == 503
    assert response.json()["code"] == "research_evidence_integrity_error"


def test_legacy_combined_review_requires_a_saved_exact_link_to_count(client):
    import hashlib
    from app.rules.decision_policy import system_identity
    from app.storage.mts_repository import append_record

    context = {"case_uid": "test-case", "workflow_run_id": "test-run", "system_identity": system_identity()}
    text = "Exact saved test explanation."
    explanation = append_record("mts_explanation", {**context, "explanation": text,
                                "explanation_sha256": hashlib.sha256(text.encode()).hexdigest()})
    reference = {**context, "reference_flowchart": "chest_pain", "reference_category": 2,
                 "reference_method": "unblinded_clinician_review", "explanation_reviewed": True,
                 "unsupported_claims": ["Test claim"]}
    append_record("mts_reference", {**reference, "explanation_id": explanation["record_id"]})
    append_record("mts_reference", {**reference, "case_uid": "other-case"})
    response = client.get("/research/mts/evaluation", headers=headers("researcher"))
    assert response.status_code == 200, response.text
    result = response.json()["unsupported_clinical_claims"]
    assert result["generated_explanations"] == result["reviewed_explanations"] == 1
    assert result["explanations_with_unsupported_claims"] == 1
    assert result["legacy_reviews_without_exact_link"] == 1


def test_doctor_reads_frozen_trace_and_researcher_cannot_read_clinical_evidence(client):
    case = uid()
    client.post(f'/cases/{case}/mts-checklist', headers=headers(), json=checklist(client, case))
    run = client.post(f'/cases/{case}/assessments', headers=headers()).json()
    url = f'/cases/{case}/mts-assessment?workflow_run_id=' + run['workflow_run_id']
    doctor = client.get(url, headers=headers('ed_doctor'))
    assert doctor.status_code == 200, doctor.text
    assert doctor.json()['mts_assessment'] == run['mts_assessment']
    assert client.get(url, headers=headers('researcher')).status_code == 403
    assert client.get(f'/cases/{case}/mts-assessment?workflow_run_id=wrong', headers=headers()).status_code == 409


def test_journey_export_contains_reference_explanation_and_separate_review_links(client):
    from app.storage.mts_repository import save_explanation
    from app.api.status_routes import _normalised_audit_records
    from app.analytics.audit_journey_export import build_audit_journey_rows
    case = uid()
    run = client.post(f'/cases/{case}/assessments', headers=headers()).json()['workflow_run_id']
    reference = client.post('/research/mts/references', headers=headers(), json={
        'case_uid': case, 'workflow_run_id': run, 'reference_flowchart': 'chest_pain',
        'reference_category': 2, 'reference_method': 'independent_clinician_review',
        'reference_provenance': 'Test reference for export linkage'}).json()['record_id']
    explanation = save_explanation(case, run, {'final_explanation': 'Exact saved test explanation.', 'status': 'test'})
    response = client.post('/research/mts/explanation-reviews', headers=headers(), json={
        'case_uid': case, 'workflow_run_id': run, 'explanation_id': explanation,
        'reference_id': reference, 'unsupported_claims': [], 'review_note': 'Test export judgement'})
    assert response.status_code == 200
    records = _normalised_audit_records()
    events = [r for r in records if r.get('case_uid') == case]
    kinds = {r['record_kind'] for r in events}
    assert {'mts_reference', 'mts_explanation', 'mts_explanation_review'} <= kinds
    rows, columns = build_audit_journey_rows(events)
    saved = next(r for r in rows if r['record_kind'] == 'mts_explanation_review')
    assert saved['workflow_run_id'] == run
    assert saved['source_dataset'] == 'UHL_SYNTHETIC_TRIAGE_VITALS_ACUITY_FINAL_20260402'
    assert saved['source.reference_id'] == reference
    assert saved['source.explanation_id'] == explanation


def test_adherence_cannot_claim_a_completed_mts_decision_that_does_not_exist(client):
    case = uid()
    run = client.post(f'/cases/{case}/assessments', headers=headers()).json()['workflow_run_id']
    reference = client.post('/research/mts/references', headers=headers(), json={
        'case_uid': case, 'workflow_run_id': run, 'reference_flowchart': 'chest_pain',
        'reference_category': 2, 'reference_method': 'independent_clinician_review',
        'reference_provenance': 'Test reference: assessment intentionally incomplete'}).json()['record_id']
    body = {'case_uid': case, 'workflow_run_id': run, 'reference_id': reference,
        'target': 'mts_category', 'guideline_consistent': True, 'review_note': 'Deliberate invalid target test'}
    assert client.post('/research/mts/guideline-reviews', headers=headers(), json=body).status_code == 409
    body['target'] = 'final_category'
    assert client.post('/research/mts/guideline-reviews', headers=headers(), json=body).status_code == 200
