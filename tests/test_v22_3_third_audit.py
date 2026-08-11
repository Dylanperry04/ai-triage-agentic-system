"""Regression guards for the third external audit's confirmed counterexamples.

Every case here is one the auditor actually reproduced. They are pinned as
tests because three of them were regressions introduced by the previous round
of fixes, which passing suites did not catch.
"""
from __future__ import annotations

import pytest

from app.agents.autogen_multi_agent_team import (
    MAX_EXPLANATION_CHARS,
    condense_explanation,
)
from app.analytics.audit_dashboard import (
    AuditFilters,
    aggregate_audit_dashboard,
    filter_audit_records,
    normalise_audit_records,
)
from app.analytics.itd_evidence import build_itd_evidence
from app.rules.llm_safety_filter import check_forbidden_phrases


def _review(rid, status, **kw):
    base = {"review_id": rid, "created_at_utc": "2026-08-07T10:00:00+00:00",
            "case_uid": rid, "reviewer_role": "triage_nurse",
            "review_status": status, "review_comment": "x",
            "system_prediction": "Urgent (Yellow)"}
    base.update(kw)
    return base


class TestSafetyFilterDirectives:
    @pytest.mark.parametrize("text", [
        "Start aspirin immediately.",
        "Call cardiology urgently.",
        "Stop warfarin now.",
        "The patient requires admission.",
        "Consider giving aspirin.",
        "Give aspirin immediately.",
        "The patient should be discharged.",
        "Administer 500mg paracetamol.",
        "Diagnose stroke.",
        "The diagnosis is appendicitis.",
        "It is safe to go home now.",
        "I recommend discharging the patient.",
        "Refer to surgery and start antibiotics.",
    ])
    def test_directive_advice_is_blocked(self, text):
        assert check_forbidden_phrases(text), f"bypass: {text!r}"

    @pytest.mark.parametrize("text", [
        "Fluids were administered before arrival.",
        "Aspirin was prescribed by the GP yesterday.",
        "This system does not diagnose or recommend treatment.",
        "No diagnosis is assigned by this system.",
        "The patient was discharged at 14:00 according to the record.",
        "The patient had been started on warfarin previously.",
        "Heart rate is 115 bpm and blood pressure is 145/94 mmHg.",
        "Clinician review is required before any action.",
        "The provisional rules engine flagged the chief complaint.",
    ])
    def test_retrospective_and_disclaimer_text_is_not_blocked(self, text):
        """Over-blocking trains people to ignore the flag."""
        assert check_forbidden_phrases(text) == [], f"false positive: {text!r}"

    def test_directive_still_caught_alongside_a_disclaimer_sentence(self):
        text = "Heart rate is elevated. Start aspirin immediately."
        assert check_forbidden_phrases(text)


class TestTruncationPreservesMandatoryReviewStatement:
    def test_review_sentence_survives_the_character_cap(self):
        body = ("The recorded observations show an elevated heart rate and raised "
                "blood pressure consistent with physiological stress. ") * 8
        out = condense_explanation(body + "Clinician review is required before any action.")
        assert "clinician review is required" in out.lower()
        assert len(out) <= MAX_EXPLANATION_CHARS + 1

    def test_review_sentence_survives_the_sentence_cap(self):
        body = " ".join(f"Observation {i} is recorded." for i in range(1, 12))
        out = condense_explanation(body + " A clinician must review this before any action.")
        assert "clinician must review" in out.lower()

    def test_review_sentence_wins_when_nothing_else_fits(self):
        huge = "The patient is " + "very " * 600 + "unwell. Clinician review is required."
        out = condense_explanation(huge)
        assert "clinician review is required" in out.lower()

    def test_text_without_a_review_sentence_is_still_capped(self):
        out = condense_explanation("The patient is " + "very " * 600 + "unwell.")
        assert len(out) <= MAX_EXPLANATION_CHARS + 1


class TestOverridesKpiCountsOnlyRealOverrides:
    def test_rules_only_adjustment_is_not_a_clinician_override(self):
        recs = normalise_audit_records(workflow_runs=[{
            "workflow_run_id": "r1", "timestamp_utc": "2026-08-07T10:00:00+00:00",
            "case_uid": "c1", "final_acuity": 2, "override_applied": True}])
        assert aggregate_audit_dashboard(recs)["summary"]["overrides"] == 0

    def test_uncertain_with_a_reason_is_not_an_override(self):
        recs = normalise_audit_records(human_reviews=[
            _review("h1", "UNCERTAIN", override_reason="unsure")])
        assert aggregate_audit_dashboard(recs)["summary"]["overrides"] == 0

    def test_genuine_override_still_counts(self):
        recs = normalise_audit_records(human_reviews=[
            _review("h1", "OVERRIDDEN", clinician_override="Very Urgent (Orange)")])
        assert aggregate_audit_dashboard(recs)["summary"]["overrides"] == 1


class TestKpiPopulationsAreDistinguishable:
    def test_submitted_actions_and_acuity_decisions_are_separate_numbers(self):
        recs = normalise_audit_records(human_reviews=[
            _review("h1", "ACCEPTED_AS_PRESENTED"),
            _review("h2", "REQUEST_MORE_INFORMATION"),
        ])
        summary = aggregate_audit_dashboard(recs)["summary"]
        donut_total = sum(r["count"] for r in aggregate_audit_dashboard(recs)["by_acuity"])
        assert summary["total_reviews"] == 2, "all submitted actions"
        assert summary["acuity_decisions"] == 1, "accept + override only"
        assert summary["acuity_decisions"] == donut_total, "KPI must match the donut"


class TestRoleBarAndRoleFilterAgree:
    def test_secondary_role_does_not_return_records_the_bar_never_counted(self):
        recs = normalise_audit_records(human_reviews=[_review(
            "h1", "ACCEPTED_AS_PRESENTED",
            reviewer_roles=["triage_nurse", "ed_doctor"])])
        bar = {r["label"]: r["count"]
               for r in aggregate_audit_dashboard(recs)["by_reviewer_role_decisions"]}
        assert bar == {"triage_nurse": 1}
        assert len(filter_audit_records(recs, AuditFilters(reviewer_role="ed_doctor"))) == 0
        assert len(filter_audit_records(recs, AuditFilters(reviewer_role="triage_nurse"))) == 1


class TestItdAnswersTheActionAsked:
    def _evidence(self):
        return build_itd_evidence([
            {"record_kind": "human_review", "timestamp_utc": "2026-08-07T10:00:00+00:00",
             "case_uid": "c1", "decision_type": "ACCEPTED_AS_PRESENTED",
             "actor_user_id": "alice", "actor_display_name": "Alice"},
            {"record_kind": "human_review", "timestamp_utc": "2026-08-07T10:01:00+00:00",
             "case_uid": "c2", "decision_type": "OVERRIDDEN",
             "actor_user_id": "bob", "actor_display_name": "Bob"},
        ], window_days=None)

    def test_override_count_is_not_the_total_decision_count(self):
        ev = self._evidence()
        assert ev["overrides_submitted"] == 1
        assert ev["clinical_decisions_submitted"] == 2

    def test_who_overrode_lists_only_the_overrider(self):
        people = [p["label"] for p in self._evidence()["overrides_by_person"]]
        assert people == ["Bob"], "Alice accepted; she did not override anything"

    def test_version_question_is_answered_not_deflected(self):
        from app.api.status_routes import _compose_itd_answer
        cfg = {"app_version": "22.3.0", "package_checkpoint": "vX",
               "model_path_configured": True, "model_file_exists": True,
               "report_dir_exists": True, "security_mode": "demo",
               "auth_provider": "demo", "is_safe_configuration": True,
               "audit_sink": "local", "warnings": [],
               "overdue_vitals_sweeper_enabled": True}
        answer = _compose_itd_answer("what version is running?", cfg, {}, None, 7)
        assert "22.3.0" in answer


class TestBlockedAgentTurnChangesStatus:
    def test_status_is_not_pass_when_a_turn_is_withheld(self):
        from app.api.safe_dto import safe_multiagent_explanation_response
        dto = safe_multiagent_explanation_response(
            case_uid="c1", source_dataset="MIMIC-IV-ED-Full-v2.2",
            team_result={"status": "PASS",
                         "final_explanation": "The estimate reflects recorded vitals.",
                         "agent_turns": [{"agent": "Intake", "text": "Start aspirin immediately."}],
                         "safety_failures": []},
        )
        assert dto["status"] == "SAFETY_FAIL", "a withheld turn must not report PASS"
        assert dto["safety_failures"]
