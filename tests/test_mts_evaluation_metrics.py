from app.analytics.mts_evaluation import evaluate_reference_pairs
import pytest


def pair(**values):
    return {"reference_method": "independent_clinician_review", "reference_category": 2,
            "complaint_group": "chest_pain", **values}


def test_missing_references_are_unavailable_instead_of_perfect_or_zero_accuracy():
    report = evaluate_reference_pairs([])
    assert report["status"] == "awaiting_independent_clinical_references"
    assert report["flowchart_selection"]["accuracy"] is None
    assert report["flowchart_selection"]["proposal_coverage"] is None
    assert report["reference_scope"] == {"chart_catalog": "implemented_charts_only",
                                         "review_independence": "reviewer_attested",
                                         "whole_population_coverage_available": False}
    assert report["discriminator_identification"]["f1"] is None
    assert report["guideline_adherence"]["rate"] is None
    assert report["unsupported_clinical_claims"]["explanations_with_unsupported_claims"] is None
    assert all(g["ml_category"]["classification_accuracy"] is None for g in report["by_presenting_complaint"].values())


def test_urgency_direction_coverage_and_independent_reference_eligibility():
    report = evaluate_reference_pairs([pair(ml_category=n) for n in (1, 2, 3, None)] +
        [pair(ml_category=2, reference_method="estimated_source_label")])
    metric = report["by_presenting_complaint"]["chest_pain"]["ml_category"]
    assert metric["evaluated_cases"] == 3
    assert metric["reference_cases"] == 4
    assert metric["prediction_coverage"] == .75
    assert metric["undertriaged"] == metric["overtriaged"] == metric["correct"] == 1
    assert metric["undertriage_rate"] == pytest.approx(1/3)
    assert report["excluded_reference_methods"] == {"estimated_source_label": 1}
    assert report["by_presenting_complaint"]["trauma"]["ml_category"]["evaluated_cases"] == 0


def test_process_metrics_use_reviewed_annotations_and_not_self_generated_rules():
    report = evaluate_reference_pairs([
        pair(reference_flowchart="chest_pain", proposed_flowchart="chest_pain",
             reference_discriminators=["cardiac_pain", "shock"], identified_discriminators=["cardiac_pain", "cardiac_pain", "severe_pain"],
             guideline_consistent=False, explanation_reviewed=True, unsupported_claims=["Invented symptom"], has_explanation=True),
        pair(reference_flowchart="chest_pain", proposed_flowchart=None, reference_discriminators=[],
             identified_discriminators=[], guideline_consistent=True, has_explanation=True),
    ])
    assert report["flowchart_selection"]["accuracy"] == .5
    assert report["flowchart_selection"]["abstentions"] == 1
    assert report["flowchart_selection"]["proposal_coverage"] == .5
    d = report["discriminator_identification"]
    assert (d["true_positives"], d["false_positives"], d["false_negatives"]) == (1, 1, 1)
    assert d["precision"] == d["recall"] == d["f1"] == .5
    assert report["guideline_adherence"]["rate"] == .5
    claims = report["unsupported_clinical_claims"]
    assert claims["reviewed_explanations"] == claims["unreviewed_explanations"] == 1
    assert claims["explanations_with_unsupported_claims"] == 1


def test_weighted_agreement_matches_independent_sklearn_implementation():
    from sklearn.metrics import cohen_kappa_score
    actual, predicted = [1, 2, 2, 3, 4, 5], [1, 1, 4, 3, 5, 5]
    rows = [pair(reference_category=a, ml_category=p) for a, p in zip(actual, predicted)]
    metric = evaluate_reference_pairs(rows)["by_presenting_complaint"]["all"]["ml_category"]
    assert metric["quadratic_weighted_kappa"] == pytest.approx(cohen_kappa_score(actual, predicted, weights="quadratic"))
    assert metric["accuracy_ci95"][0] < .5 < metric["accuracy_ci95"][1]
    constant = evaluate_reference_pairs([pair(ml_category=2)])["by_presenting_complaint"]["all"]["ml_category"]
    assert constant["quadratic_weighted_kappa"] is None


def test_explanation_claim_review_does_not_require_a_blinded_category_reference():
    report = evaluate_reference_pairs([pair(reference_method="unblinded_clinician_review",
        explanation_reviewed=True, unsupported_claims=["Unobserved symptom"])])
    assert report["reference_cases"] == 0
    assert report["by_presenting_complaint"]["all"]["ml_category"]["classification_accuracy"] is None
    assert report["unsupported_clinical_claims"]["reviewed_explanations"] == 1
    assert report["unsupported_clinical_claims"]["explanations_with_unsupported_claims"] == 1


def test_missing_identification_is_counted_as_missed_not_removed_from_f1():
    report = evaluate_reference_pairs([pair(reference_discriminators=['shock'], identified_discriminators=None),
        pair(reference_discriminators=['shock'], identified_discriminators=['shock'])])
    d = report['discriminator_identification']
    assert d['false_negatives'] == 1 and d['recall'] == .5
    assert d['f1'] == pytest.approx(2/3)
    assert d['conditional_on_identification']['f1'] == 1
    assert d['annotation_coverage'] == .5
