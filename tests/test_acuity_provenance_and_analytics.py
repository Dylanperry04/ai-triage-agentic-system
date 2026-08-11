"""Regression tests for the v22.1 correctness fixes.

Each test pins a defect that was live in the deployed app, so a future change
that reintroduces it fails here rather than in front of a clinician.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.agents.ml_prediction_agent import (
    _apply_artifact_decision_rule,
    _successful_mimic_prediction,
)
from app.agents.autogen_multi_agent_team import (
    MAX_EXPLANATION_SENTENCES,
    condense_explanation,
)
from app.analytics.audit_dashboard import (
    aggregate_audit_dashboard,
    canonical_acuity,
    dashboard_payload,
    normalise_audit_records,
)


class _FakeModel:
    """Stands in for the deployed sklearn pipeline: classes_ [1..5]."""

    classes_ = np.array([1, 2, 3, 4, 5])


# Probabilities from the live screenshot (Patient 10001523 / Stay 30640309).
SCREENSHOT_PROBS = np.array([[0.027, 0.224, 0.614, 0.131, 0.005]])
DEPLOYED_RULE = {
    "type": "high_acuity_threshold",
    "threshold": 0.05,
    "labels": [1, 2, 3, 4, 5],
}


class TestDecisionRuleProvenance:
    def test_deployed_rule_escalates_away_from_argmax(self):
        """The reported 'estimate 2 vs 61.4% on acuity 3' is the rule, not a bug."""
        argmax_class = int(_FakeModel.classes_[int(np.argmax(SCREENSHOT_PROBS[0]))])
        assert argmax_class == 3

        pred = _apply_artifact_decision_rule(
            _FakeModel(), argmax_class, SCREENSHOT_PROBS, DEPLOYED_RULE
        )
        # P(1)+P(2) = 0.251 >= 0.05, so the rule escalates to the better of {1,2}.
        assert pred == 2

    def test_assigned_probability_is_not_the_argmax_probability(self):
        """The confidence shown must belong to the acuity shown."""
        result = _successful_mimic_prediction(
            _FakeModel(), 2, SCREENSHOT_PROBS,
            model_name="test", decision_rule=DEPLOYED_RULE,
        )
        assert result.predicted_mimic_acuity == 2
        assert result.argmax_acuity == 3
        assert result.decision_rule_changed_prediction is True
        # The old UI paired acuity 2 with 0.614 — that is acuity 3's number.
        assert result.top_class_confidence == pytest.approx(0.614)
        assert result.assigned_acuity_probability == pytest.approx(0.224)
        assert result.assigned_acuity_probability != result.top_class_confidence

    def test_plain_argmax_reports_no_rule_change(self):
        """No spurious 'escalated' banner when the rule is a no-op."""
        result = _successful_mimic_prediction(
            _FakeModel(), 3, SCREENSHOT_PROBS,
            model_name="test", decision_rule={"type": "argmax"},
        )
        assert result.decision_rule_changed_prediction is False
        assert result.assigned_acuity_probability == pytest.approx(
            result.top_class_confidence
        )

    def test_provenance_survives_absent_decision_rule(self):
        result = _successful_mimic_prediction(
            _FakeModel(), 3, SCREENSHOT_PROBS, model_name="test", decision_rule=None,
        )
        assert result.decision_rule_type == "argmax"
        assert result.decision_rule_changed_prediction is False


class TestCanonicalAcuity:
    @pytest.mark.parametrize("value,expected", [
        (2, 2), ("2", 2), ("Very Urgent (Orange)", 2),
        ("Very Urgent (Orange) (acuity 2)", 2), ("Immediate (Red)", 1),
        ("Urgent (Yellow)", 3), ("Standard (Green)", 4), ("Non-Urgent (Blue)", 5),
        (None, None), ("", None), ("unknown", None), (0, None), (9, None),
        (True, None),
    ])
    def test_shapes_reduce_to_one_acuity(self, value, expected):
        assert canonical_acuity(value) == expected

    def test_one_acuity_is_one_bucket_across_record_kinds(self):
        """The duplicated donut slices: int 2 and 'Very Urgent (Orange)' are one acuity."""
        records = normalise_audit_records(
            workflow_runs=[{
                "timestamp_utc": "2026-08-01T10:00:00+00:00",
                "case_uid": "c1", "final_acuity": 2,
            }],
            human_reviews=[{
                "created_at_utc": "2026-08-01T10:05:00+00:00",
                "case_uid": "c1", "reviewer_role": "triage_nurse",
                "review_status": "ACCEPTED_AS_PRESENTED",
                "system_prediction": "Very Urgent (Orange)",
            }],
        )
        assert {r["acuity"] for r in records} == {2}

        agg = aggregate_audit_dashboard(records)
        labels = [row["label"] for row in agg["by_acuity"]]
        assert labels == ["Acuity 2"], labels
        # Only the human decision counts as a submitted decision.
        assert agg["by_acuity"][0]["count"] == 1


class TestRoleCountsAreDecisionsNotAuditRows:
    def test_access_events_do_not_inflate_role_workload(self):
        """1,916 'submissions' for security admin were per-request access events."""
        records = normalise_audit_records(
            access_events=[
                {"timestamp_utc": "2026-08-01T10:00:00+00:00",
                 "roles": ["security_admin"], "action": "view_audit_dashboard",
                 "decision": "ALLOWED"}
                for _ in range(500)
            ],
            human_reviews=[{
                "created_at_utc": "2026-08-01T10:05:00+00:00",
                "case_uid": "c1", "reviewer_role": "triage_nurse",
                "review_status": "ACCEPTED_AS_PRESENTED",
                "system_prediction": "2",
            }],
        )
        agg = aggregate_audit_dashboard(records)
        raw = {r["label"]: r["count"] for r in agg["by_reviewer_role"]}
        decisions = {r["label"]: r["count"] for r in agg["by_reviewer_role_decisions"]}

        assert raw["security_admin"] == 500          # audit rows, unchanged
        assert "security_admin" not in decisions      # submitted nothing clinical
        assert decisions["triage_nurse"] == 1


class TestAggregationsCoverAllMatchedRecords:
    def test_limit_bounds_rows_not_statistics(self):
        """Charts previously described only the newest `limit` records."""
        reviews = [{
            "created_at_utc": f"2026-08-01T10:{i:02d}:00+00:00",
            "case_uid": f"c{i}", "reviewer_role": "triage_nurse",
            "review_status": "ACCEPTED_AS_PRESENTED", "system_prediction": "3",
        } for i in range(50)]
        records = normalise_audit_records(human_reviews=reviews)

        payload = dashboard_payload(records, limit=10)
        assert len(payload["entries"]) == 10      # page of row detail
        assert payload["count"] == 10
        assert payload["matched"] == 50           # full filtered set
        total = sum(r["count"] for r in payload["aggregations"]["by_acuity"])
        assert total == 50, "aggregations must cover every matched record"


class TestExplanationContract:
    def test_long_markdown_output_is_condensed(self):
        raw = (
            "- **Estimated category shown**: Very Urgent (Orange). "
            "- **Main reason(s)**: Vomiting and diarrhea raise dehydration concerns. "
            "Heart rate is 115 bpm. Blood pressure is 145/94 mmHg. "
            "The model predicted acuity 2. The ruleset is provisional. "
            "- **Limitations**: No critical safety flags were noted. "
            "- **Human review required**: A clinician must review.\nTERMINATE"
        )
        out = condense_explanation(raw)
        sentences = [p for p in out.replace("!", ".").replace("?", ".").split(".") if p.strip()]
        assert len(sentences) <= MAX_EXPLANATION_SENTENCES
        assert "**" not in out and "TERMINATE" not in out
        assert not out.lstrip().startswith("-")

    def test_short_compliant_output_passes_through_unchanged(self):
        good = "Acuity 2 was shown. Heart rate was elevated. Clinician review is required."
        assert condense_explanation(good) == good

    def test_empty_stays_empty(self):
        assert condense_explanation("") == ""
        assert condense_explanation("TERMINATE") == ""


class TestSingleAcuityParser:
    """app/rules/acuity_mts_mapping.py declares itself the single source of truth
    for the acuity<->category table. Local copies had already diverged."""

    def test_all_call_sites_bind_the_same_function(self):
        from app.analytics.audit_dashboard import canonical_acuity as analytics_fn
        from app.api.case_routes import _acuity_from_text as api_fn
        from app.rules.acuity_mts_mapping import acuity_from_text as owner_fn
        assert analytics_fn is owner_fn
        assert api_fn is owner_fn

    def test_parser_is_derived_from_the_mapping_table(self):
        """Every category string in the table must parse back to its own acuity."""
        from app.rules.acuity_mts_mapping import (
            MIMIC_ACUITY_TO_MTS, acuity_from_text,
        )
        for acuity, fields in MIMIC_ACUITY_TO_MTS.items():
            assert acuity_from_text(fields["category"]) == acuity, fields["category"]
            assert acuity_from_text(fields["colour"]) == acuity, fields["colour"]

    def test_substring_collisions_resolve_to_the_longer_phrase(self):
        """'urgent' is inside both 'very urgent' and 'non-urgent'."""
        from app.rules.acuity_mts_mapping import acuity_from_text
        assert acuity_from_text("Very Urgent (Orange)") == 2
        assert acuity_from_text("Non-Urgent (Blue)") == 5
        assert acuity_from_text("Urgent (Yellow)") == 3

    def test_non_integral_float_is_not_an_acuity(self):
        """The divergence that motivated consolidation: 3.7 is not acuity 3."""
        from app.rules.acuity_mts_mapping import acuity_from_text
        assert acuity_from_text(3.7) is None
        assert acuity_from_text(2.0) == 2


class TestAcuityDrilldownFilter:
    def test_acuity_filter_matches_every_record_shape(self):
        """triage_level filtering silently dropped records stored as category text."""
        from app.analytics.audit_dashboard import AuditFilters, filter_audit_records
        records = normalise_audit_records(
            workflow_runs=[{
                "timestamp_utc": "2026-08-01T10:00:00+00:00",
                "case_uid": "c1", "final_acuity": 2,
            }],
            human_reviews=[{
                "created_at_utc": "2026-08-01T10:05:00+00:00",
                "case_uid": "c1", "reviewer_role": "triage_nurse",
                "review_status": "ACCEPTED_AS_PRESENTED",
                "system_prediction": "Very Urgent (Orange)",
            }],
        )
        legacy = filter_audit_records(records, AuditFilters(triage_level="2"))
        assert len(legacy) == 1, "legacy string filter matches only one shape"

        fixed = filter_audit_records(records, AuditFilters(acuity=2))
        assert len(fixed) == 2, "canonical filter must match both shapes"

    def test_acuity_filter_excludes_other_acuities(self):
        from app.analytics.audit_dashboard import AuditFilters, filter_audit_records
        records = normalise_audit_records(
            workflow_runs=[
                {"timestamp_utc": "2026-08-01T10:00:00+00:00", "case_uid": "a", "final_acuity": 2},
                {"timestamp_utc": "2026-08-01T10:01:00+00:00", "case_uid": "b", "final_acuity": 4},
            ],
        )
        assert len(filter_audit_records(records, AuditFilters(acuity=2))) == 1
        assert len(filter_audit_records(records, AuditFilters(acuity=4))) == 1
        assert len(filter_audit_records(records, AuditFilters(acuity=1))) == 0

    def test_no_acuity_filter_returns_everything(self):
        from app.analytics.audit_dashboard import AuditFilters, filter_audit_records
        records = normalise_audit_records(
            workflow_runs=[{"timestamp_utc": "2026-08-01T10:00:00+00:00",
                            "case_uid": "a", "final_acuity": 2}],
        )
        assert len(filter_audit_records(records, AuditFilters())) == 1
