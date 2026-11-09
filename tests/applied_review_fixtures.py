"""Storage context for existing analytics/export tests of completed decisions.

Those tests previously supplied only review journals. R4 requires independent
application evidence. Keep their clinical values/assertions unchanged and add
matching state evidence explicitly; pending/corrupt proof is tested separately
in test_nov08_review_outcomes.py against the production functions directly.
"""
from app.analytics.audit_dashboard import normalise_audit_records


def applied_review_states(reviews):
    states = []
    for index, review in enumerate(reviews):
        review.setdefault("case_uid", f"fixture-case-{index}")
        review.setdefault("workflow_run_id", f"fixture-run-{index}")
        review.setdefault("review_id", f"fixture-review-{index}")
        review.setdefault("base_state_revision", index)
        states.append({"case_uid": review["case_uid"],
            "workflow_run_id": review["workflow_run_id"],
            "latest_workflow_run_id": review["workflow_run_id"],
            "last_review_id": review["review_id"],
            "state_revision": review["base_state_revision"] + 1})
    return states


def normalise_applied_review_fixtures(**streams):
    reviews = streams.get("human_reviews", [])
    return normalise_audit_records(**streams,
        review_application_states=applied_review_states(reviews))
