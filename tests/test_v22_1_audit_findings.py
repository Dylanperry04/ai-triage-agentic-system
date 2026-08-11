"""Regression guards for the defects found in the v22.1 external audit.

Each test pins one confirmed defect. Several of these were bugs in the v22.1
fixes themselves, which is why they are pinned rather than trusted.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.analytics.audit_dashboard import (
    aggregate_audit_dashboard,
    normalise_audit_records,
)
from app.api.case_routes import _assigned_acuity_for_row
from app.agents.ml_prediction_agent import (
    _apply_artifact_decision_rule,
    _effective_decision_rule,
)


class _M:
    classes_ = np.array([1, 2, 3, 4, 5])


ARTEFACT_RULE = {"type": "high_acuity_threshold", "threshold": 0.05, "labels": [1, 2, 3, 4, 5]}


def _review(**kw):
    base = {
        "review_id": kw.pop("rid", "h1"),
        "created_at_utc": "2026-08-07T10:00:00+00:00",
        "case_uid": "c1",
        "reviewer_role": "triage_nurse",
        "review_comment": "x",
    }
    base.update(kw)
    return base


class TestThresholdOverrideProvenance:
    def test_override_changes_the_operating_point(self):
        rule, artefact_tau = _effective_decision_rule(ARTEFACT_RULE)
        assert artefact_tau == 0.05
        assert rule["threshold"] == 0.25
        # Rule type and labels must be untouched — only the value moves.
        assert rule["type"] == ARTEFACT_RULE["type"]
        assert rule["labels"] == ARTEFACT_RULE["labels"]

    def test_cases_between_the_thresholds_are_no_longer_escalated(self):
        probs = np.array([[0.01, 0.15, 0.60, 0.20, 0.04]])   # P(1)+P(2) = 0.16
        rule, _ = _effective_decision_rule(ARTEFACT_RULE)
        assert _apply_artifact_decision_rule(_M(), 3, probs, ARTEFACT_RULE) == 2
        assert _apply_artifact_decision_rule(_M(), 3, probs, rule) == 3

    def test_strong_high_acuity_signal_still_escalates(self):
        """Raising the threshold must not disable the safety rule entirely."""
        probs = np.array([[0.05, 0.45, 0.40, 0.08, 0.02]])   # P(1)+P(2) = 0.50
        rule, _ = _effective_decision_rule(ARTEFACT_RULE)
        assert _apply_artifact_decision_rule(_M(), 3, probs, rule) == 2

    def test_argmax_rule_is_never_touched(self):
        rule, artefact_tau = _effective_decision_rule({"type": "argmax"})
        assert artefact_tau is None and rule == {"type": "argmax"}


class TestLegacyReviewQueueColours:
    def test_new_records_use_the_persisted_value(self):
        assert _assigned_acuity_for_row({"assigned_acuity": 2}) == 2

    def test_legacy_override_recovers_the_clinician_acuity(self):
        row = {"review_status": "OVERRIDDEN",
               "clinician_override_decision": "Very Urgent (Orange) (acuity 2)"}
        assert _assigned_acuity_for_row(row) == 2

    def test_legacy_accept_recovers_the_system_prediction(self):
        row = {"review_status": "ACCEPTED_AS_PRESENTED", "system_prediction": "Urgent (Yellow)"}
        assert _assigned_acuity_for_row(row) == 3

    def test_override_takes_precedence_over_system_prediction(self):
        row = {"review_status": "OVERRIDDEN", "system_prediction": "Urgent (Yellow)",
               "clinician_override_decision": "Immediate (Red)"}
        assert _assigned_acuity_for_row(row) == 1

    def test_no_acuity_stays_none_rather_than_being_invented(self):
        assert _assigned_acuity_for_row({"review_status": "DISCHARGED"}) is None


class TestOverrideDirectionAndSettledAcuity:
    def test_direction_fields_are_not_reversible(self):
        """The panel rendered clinician -> model under a model -> clinician label."""
        recs = normalise_audit_records(human_reviews=[_review(
            review_status="OVERRIDDEN", system_prediction="Urgent (Yellow)",
            clinician_override="Very Urgent (Orange)")])
        rec = recs[0]
        assert rec["system_acuity"] == 3       # what the system estimated
        assert rec["clinician_acuity"] == 2    # what the clinician chose

    def test_decided_acuity_follows_the_clinician_on_an_override(self):
        recs = normalise_audit_records(human_reviews=[_review(
            review_status="OVERRIDDEN", system_prediction="Urgent (Yellow)",
            clinician_override="Very Urgent (Orange)")])
        assert recs[0]["decided_acuity"] == 2, "must not count the rejected acuity"
        agg = aggregate_audit_dashboard(recs)
        assert [(r["label"], r["count"]) for r in agg["by_acuity"]] == [("Acuity 2", 1)]

    def test_decided_acuity_follows_the_system_on_an_accept(self):
        recs = normalise_audit_records(human_reviews=[_review(
            review_status="ACCEPTED_AS_PRESENTED", system_prediction="Urgent (Yellow)")])
        assert recs[0]["decided_acuity"] == 3


class TestOnlyTerminalDecisionsSettleAnAcuity:
    @pytest.mark.parametrize("status", [
        "REQUEST_MORE_INFORMATION", "ESCALATION_REQUIRED", "ESCALATION_CONFIRMED",
        "DISCHARGED", "REJECTED",
    ])
    def test_workflow_actions_are_excluded_from_the_acuity_chart(self, status):
        recs = normalise_audit_records(human_reviews=[_review(
            review_status=status, system_prediction="Urgent (Yellow)")])
        agg = aggregate_audit_dashboard(recs)
        assert agg["by_acuity"] == [], f"{status} does not settle an acuity"

    @pytest.mark.parametrize("status", ["ACCEPTED_AS_PRESENTED", "OVERRIDDEN"])
    def test_terminal_decisions_are_included(self, status):
        recs = normalise_audit_records(human_reviews=[_review(
            review_status=status, system_prediction="Urgent (Yellow)",
            clinician_override="Urgent (Yellow)")])
        assert aggregate_audit_dashboard(recs)["by_acuity"] == [
            {"acuity": 3, "label": "Acuity 3", "count": 1}
        ]


class TestPersonCountsReconcileWithRoleCounts:
    def test_one_submission_counts_once_per_person(self):
        """State rows multiply per decision; review rows do not."""
        recs = normalise_audit_records(
            human_reviews=[_review(
                rid="h1", review_status="ACCEPTED_AS_PRESENTED",
                system_prediction="Urgent (Yellow)",
                reviewer_user_id="demo-aoibhinn",
                reviewer_display_name="Aoibhinn Costelloe")],
            workflow_states=[
                {"case_uid": "c1", "updated_at_utc": "2026-08-07T10:00:00+00:00",
                 "reviewer_role": "triage_nurse", "actor_user_id": "demo-aoibhinn"},
                {"case_uid": "c1", "updated_at_utc": "2026-08-07T10:01:00+00:00",
                 "reviewer_role": "triage_nurse", "actor_user_id": "demo-aoibhinn"},
                {"case_uid": "c1", "updated_at_utc": "2026-08-07T10:02:00+00:00",
                 "reviewer_role": "triage_nurse", "actor_user_id": "demo-aoibhinn"},
            ],
        )
        agg = aggregate_audit_dashboard(recs)
        people = {a["label"]: a["count"] for a in agg["by_actor"]}
        roles = {r["label"]: r["count"] for r in agg["by_reviewer_role_decisions"]}
        assert people["Aoibhinn Costelloe"] == 1, "3 state rows, 1 submitted decision"
        assert sum(people.values()) == sum(roles.values()), "person and role totals must reconcile"

    def test_demo_identity_is_marked_unverified(self):
        recs = normalise_audit_records(human_reviews=[_review(
            review_status="OVERRIDDEN", system_prediction="Urgent (Yellow)",
            clinician_override="Very Urgent (Orange)",
            reviewer_user_id="demo-x", reviewer_display_name="Demo X",
            reviewer_identity_verified=False)])
        actor = aggregate_audit_dashboard(recs)["by_actor"][0]
        assert actor["identity_verified"] is False
