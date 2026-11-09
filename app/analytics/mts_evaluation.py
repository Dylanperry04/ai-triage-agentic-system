"""MTS research metrics against independently supplied clinical references.

No model prediction or estimated source label is promoted to clinical truth.
Missing annotations stay unevaluated. Priority 1 is the most urgent category.
"""
from __future__ import annotations

from collections import Counter
import math

GROUPS = ("chest_pain", "abdominal_pain", "trauma", "respiratory_symptoms")


def _fraction(numerator: int, denominator: int):
    return numerator / denominator if denominator else None


def _wilson(correct: int, total: int):
    if not total:
        return None
    z = 1.959963984540054
    p = correct / total
    scale = 1 + z * z / total
    middle = (p + z * z / (2 * total)) / scale
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / scale
    return [max(0, middle - half), min(1, middle + half)]


def _category_metrics(pairs: list[dict], field: str):
    matrix = [[0] * 5 for _ in range(5)]
    for pair in pairs:
        prediction, reference = pair.get(field), pair.get("reference_category")
        if type(prediction) is int and 1 <= prediction <= 5 and type(reference) is int and 1 <= reference <= 5:
            matrix[reference - 1][prediction - 1] += 1
    n = sum(map(sum, matrix))
    correct = sum(matrix[i][i] for i in range(5))
    under = sum(matrix[i][j] for i in range(5) for j in range(5) if j > i)
    over = sum(matrix[i][j] for i in range(5) for j in range(5) if j < i)
    expected = observed = 0.0
    if n:
        actual = [sum(row) for row in matrix]
        predicted = [sum(matrix[i][j] for i in range(5)) for j in range(5)]
        observed = sum((i - j) ** 2 * matrix[i][j] for i in range(5) for j in range(5)) / n
        expected = sum((i - j) ** 2 * actual[i] * predicted[j] for i in range(5) for j in range(5)) / (n * n)
    return {
        "reference_cases": len(pairs), "evaluated_cases": n,
        "prediction_coverage": _fraction(n, len(pairs)),
        "correct": correct, "undertriaged": under, "overtriaged": over,
        "classification_accuracy": _fraction(correct, n),
        "accuracy_ci95": _wilson(correct, n),
        "undertriage_rate": _fraction(under, n), "overtriage_rate": _fraction(over, n),
        "undertriage_ci95": _wilson(under, n), "overtriage_ci95": _wilson(over, n),
        "exact_agreement": _fraction(correct, n),
        "quadratic_weighted_kappa": 1 - observed / expected if expected else None,
        "confusion_matrix": matrix,
        "confusion_matrix_axes": {"rows": "reference 1-5", "columns": "prediction 1-5"},
    }


def evaluate_reference_pairs(pairs: list[dict], *, explanation_pairs: list[dict] | None = None) -> dict:
    """Evaluate frozen run/reference pairs; callers provide one latest review per case.

    Primary results use the independently reviewed method declared by the
    reviewer. The prototype does not verify reviewer blinding or enrollment.
    """
    eligible = [p for p in pairs if p.get("reference_method") == "independent_clinician_review"]
    groups = {}
    for group in (*GROUPS, "all"):
        rows = eligible if group == "all" else [p for p in eligible if p.get("complaint_group") == group]
        groups[group] = {field: _category_metrics(rows, field) for field in
                         ("ml_category", "ml_safety_category", "final_category", "mts_category")}
    chart_rows = [p for p in eligible if p.get("reference_flowchart") and p.get("output_eligibility", {}).get("flowchart", True)]
    correct_charts = sum(p.get("proposed_flowchart") in {p["reference_flowchart"], *p.get("acceptable_flowcharts", [])} for p in chart_rows)
    chart_abstentions = sum(not p.get("proposed_flowchart") for p in chart_rows)
    discriminator_rows = [p for p in eligible if p.get("reference_discriminators") is not None
                          and p.get("output_eligibility", {}).get("mts_category", True)]
    conditional_rows = [p for p in discriminator_rows if p.get("identified_discriminators") is not None]
    def detection_metrics(rows):
        tp = fp = fn = 0
        for pair in rows:
            reference, predicted = set(pair["reference_discriminators"]), set(pair.get("identified_discriminators") or [])
            tp += len(reference & predicted)
            fp += len(predicted - reference)
            fn += len(reference - predicted)
        return {"evaluated_cases": len(rows), "true_positives": tp, "false_positives": fp, "false_negatives": fn,
                "precision": _fraction(tp, tp + fp), "recall": _fraction(tp, tp + fn),
                "f1": _fraction(2 * tp, 2 * tp + fp + fn)}
    detection = detection_metrics(discriminator_rows)
    adherence = [p for p in eligible if type(p.get("guideline_consistent")) is bool]
    mts_adherence = [p for p in eligible if type(p.get("mts_guideline_consistent")) is bool]
    # Claim review necessarily sees the explanation. Its evidence does not
    # depend on whether the category reference was collected before seeing AI.
    explanation_source = pairs if explanation_pairs is None else explanation_pairs
    explanations = [p for p in explanation_source if p.get("explanation_reviewed") is True]
    unsupported = sum(bool(p.get("unsupported_claims")) for p in explanations)
    return {
        "status": "available" if eligible else "awaiting_independent_clinical_references",
        "reference_cases": len(eligible), "excluded_reference_methods": dict(Counter(
            p.get("reference_method", "missing") for p in pairs if p.get("reference_method") != "independent_clinician_review")),
        "unit": "latest independently reviewed assessment per case",
        "rates_are_fractions": True, "curated_demo_is_unbiased_evaluation": False,
        "reference_scope": {
            "chart_catalog": "implemented_charts_only",
            "review_independence": "reviewer_attested",
            "whole_population_coverage_available": False,
        },
        "flowchart_selection": {"evaluated_cases": len(chart_rows), "correct": correct_charts,
            "abstentions": chart_abstentions,
            "proposal_coverage": _fraction(len(chart_rows) - chart_abstentions, len(chart_rows)),
            "accuracy": _fraction(correct_charts, len(chart_rows))},
        "discriminator_identification": {"evaluated_cases": len(discriminator_rows),
            "reference_cases": sum(p.get("reference_discriminators") is not None for p in eligible),
            "annotation_coverage": _fraction(len(conditional_rows), len(discriminator_rows)),
            **detection, "conditional_on_identification": detection_metrics(conditional_rows),
            "target": "all applicable discriminators", "missing_identification_policy": "empty prediction set; reference findings count as missed",
            "output_origin": "structured checklist findings and evaluated measurement predicates",
            "averaging": "end-to-end micro, set of discriminator IDs per independently referenced case"},
        "guideline_adherence": {"reviewed_decisions": len(adherence),
            "consistent_decisions": sum(p["guideline_consistent"] for p in adherence),
            "rate": _fraction(sum(p["guideline_consistent"] for p in adherence), len(adherence)),
            "target": "final_category: displayed hybrid recommendation for the exact run",
            "reference": "independent clinician adjudication, not engine self-agreement"},
        "mts_guideline_adherence": {"reviewed_decisions": len(mts_adherence),
            "consistent_decisions": sum(p["mts_guideline_consistent"] for p in mts_adherence),
            "rate": _fraction(sum(p["mts_guideline_consistent"] for p in mts_adherence), len(mts_adherence)),
            "target": "mts_category: completed structured assessment"},
        "unsupported_clinical_claims": {"reviewed_explanations": len(explanations),
            "explanations_with_unsupported_claims": unsupported if explanations else None,
            "unsupported_claim_count": sum(len(p.get("unsupported_claims") or []) for p in explanations) if explanations else None,
            "unreviewed_explanations": sum(p.get("has_explanation") is True and not p.get("explanation_reviewed") for p in eligible),
            "method": "explicit clinician review of the saved explanation"},
        "by_presenting_complaint": groups,
        "paired_stage_comparison": {g: {
            field: _category_metrics([p for p in eligible if (g == "all" or p.get("complaint_group") == g)
                and all(p.get(k) is not None for k in ("ml_category", "ml_safety_category", "mts_category", "final_category"))], field)
            for field in ("ml_category", "ml_safety_category", "mts_category", "final_category")}
            for g in (*GROUPS, "all")},
        "limitations": ["No clinical metric is inferred from estimated training labels.",
            "The selected 100-case cohort is not a random or independent evaluation sample.",
            "Chart references are limited to the implemented catalog, so these results cannot measure whole-ED coverage.",
            "The reviewer attests to independence. The prototype cannot verify blinded collection or eligible-case enrollment.",
            "Complaint groups describe presenting symptoms, not retrospective diagnoses.",
            "Small samples and case selection limit generalisation; no significance claim is made."],
    }
