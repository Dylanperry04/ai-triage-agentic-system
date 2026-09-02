"""Validated natural-language query planning over aggregate ITD audit evidence.

The language model may choose *which approved measure* a question refers to. It
never receives patient content, audit rows, staff identifiers or numeric values,
and it never calculates the answer. The backend validates the plan and renders
only counts it computed from the recorded audit evidence. A deterministic parser
provides the same core demo capability when Azure OpenAI is unavailable.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping


METRICS: dict[str, dict[str, Any]] = {
    "audit_records": {"field": "records_in_window", "label": "audit record", "breakdown": None},
    "access_events": {"field": "access_events", "label": "access event", "breakdown": None},
    "access_allowed": {"field": "access_allowed", "label": "allowed access event", "breakdown": None},
    "access_denied": {"field": "access_denied", "label": "access denial", "breakdown": "denied_by_role", "role_breakdown": "denied_by_role"},
    "model_assessments": {"field": "model_assessments_run", "label": "model assessment", "breakdown": None},
    "clinical_actions": {"field": "clinical_decisions_submitted", "label": "clinical workflow action", "breakdown": "decisions_by_person", "role_breakdown": "top_roles_decisions_only"},
    "acceptances": {"field": "accepts_submitted", "label": "model-recommendation acceptance", "breakdown": "accepts_by_person", "role_breakdown": "accepts_by_role"},
    "overrides": {"field": "overrides_submitted", "label": "clinician change to a model recommendation", "plural": "clinician changes to model recommendations", "breakdown": "overrides_by_person", "role_breakdown": "overrides_by_role"},
    "escalation_actions": {"field": "escalations_submitted", "label": "escalation action", "breakdown": "escalations_by_person", "role_breakdown": "escalations_by_role"},
    "escalated_patients": {"field": "escalated_patients", "label": "escalated patient", "breakdown": "escalated_patients_by_person"},
    "escalations_resolved": {"field": "escalations_resolved", "label": "escalation resolution", "breakdown": None},
    "open_escalations": {"field": "open_escalations", "label": "currently open escalation", "breakdown": None, "current": True},
    "information_requests": {"field": "information_requests_submitted", "label": "request for additional information", "plural": "requests for additional information", "breakdown": "information_requests_by_person", "role_breakdown": "information_requests_by_role"},
    "discharged_patients": {"field": "patients_discharged", "label": "discharged patient", "breakdown": "discharges_by_person", "role_breakdown": "discharges_by_role"},
    "admitted_patients": {"field": "patients_admitted", "label": "admitted/closed patient", "breakdown": "admissions_by_person", "role_breakdown": "admissions_by_role"},
    "observation_updates": {"field": "observation_updates", "label": "observation update", "breakdown": "observation_updates_by_person", "role_breakdown": "observation_updates_by_role"},
    "active_overdue_observations": {"field": "overdue_vitals_alerts_active", "label": "active overdue-observations alert", "breakdown": None, "current": True},
}


def _local_plan(question: str) -> dict[str, Any] | None:
    q = question.lower()
    metrics: list[str] = []

    if any(x in q for x in ("overdue", "recheck due")):
        metrics.append("active_overdue_observations")
    if any(x in q for x in ("escalat",)):
        if any(x in q for x in ("currently open", "current open", "still open", "awaiting", "open escalation")) or "open" in q:
            metrics.append("open_escalations")
        elif any(x in q for x in ("resolved", "resolution", "closed escalation")):
            metrics.append("escalations_resolved")
        elif any(x in q for x in ("patient", "patients", "case", "cases")):
            metrics.append("escalated_patients")
        else:
            metrics.append("escalation_actions")
    if any(x in q for x in ("assessment", "assessments", "model run", "model runs", "prediction run")):
        metrics.append("model_assessments")
    if any(x in q for x in ("request for additional", "requests for additional", "request more information", "requested more information")):
        metrics.append("information_requests")
    if "discharg" in q:
        metrics.append("discharged_patients")
    if any(x in q for x in ("admitted", "admission", "closed as admitted")):
        metrics.append("admitted_patients")
    if any(x in q for x in ("observation", "observations", "vitals recorded", "vital signs recorded")) and "overdue" not in q:
        metrics.append("observation_updates")
    if any(x in q for x in ("override", "overrode", "changed the model", "changes to the model")):
        metrics.append("overrides")
    if any(x in q for x in ("acceptance", "acceptances", "accepted")):
        metrics.append("acceptances")
    if any(x in q for x in ("access denial", "access denials", "denied", "blocked access")):
        metrics.append("access_denied")
    elif any(x in q for x in ("access event", "access events", "sign in", "signin", "login")):
        metrics.append("access_events")
    if any(x in q for x in ("clinical action", "clinical actions", "decision", "decisions")) and not metrics:
        metrics.append("clinical_actions")
    if any(x in q for x in ("audit record", "audit records", "audit log", "activity", "usage")) and not metrics:
        metrics.append("audit_records")

    metrics = list(dict.fromkeys(metrics))[:4]
    if not metrics:
        return None
    percentage = any(x in q for x in ("percentage", "percent", "rate", "proportion"))
    denominator = None
    if percentage:
        if "acceptances" in metrics:
            denominator = "model_assessments"
            metrics = ["acceptances"]
        elif "overrides" in metrics:
            denominator = "model_assessments"
            metrics = ["overrides"]
        else:
            percentage = False
    return {
        "metrics": metrics,
        "calculation": "percentage" if percentage else "count",
        "denominator_metric": denominator,
        "group_by": (
            "role" if any(x in q for x in ("which role", "by role", "per role", "each role"))
            else "person" if any(x in q for x in (
                "who", "staff", "member", "each nurse", "each ed nurse",
                "by nurse", "per nurse", "by person",
            ))
            else "none"
        ),
        "rank": "most" if any(x in q for x in ("most", "highest", "top")) else "all",
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (TypeError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", str(text or ""), flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def _azure_plan(question: str) -> dict[str, Any] | None:
    """Ask Azure OpenAI for a schema-only plan; fail quietly to local parsing."""
    try:
        enabled = os.environ.get("ITD_ASSISTANT_LLM_PLANNER_ENABLED")
        if enabled is not None and enabled.lower() != "true":
            return None
        if enabled is None and os.environ.get("AZURE_SUPERVISOR_DEMO_MODE", "").lower() != "true":
            return None
        from app.agents.autogen_team import load_azure_config
        config = load_azure_config()
        if not config:
            return None
        from openai import AzureOpenAI

        descriptions = ", ".join(
            f"{name} ({spec['label']})" for name, spec in METRICS.items()
        )
        client = AzureOpenAI(
            azure_endpoint=config["endpoint"], api_key=config["api_key"],
            api_version=config["api_version"],
        )
        completion = client.chat.completions.create(
            model=config["deployment"], temperature=0, max_tokens=220,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Map an IT administrator's aggregate audit question to a JSON query plan. "
                        "Never answer the question and never invent a metric. Return only JSON with: "
                        "metrics (1-4 strings), calculation (count or percentage), denominator_metric "
                        "(metric or null), group_by (none, person, or role), rank (all or most). Approved metrics: "
                        + descriptions
                    ),
                },
                {"role": "user", "content": question},
            ],
        )
        return _extract_json(completion.choices[0].message.content or "")
    except Exception:
        return None


def _validated_plan(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    metrics = [str(item) for item in (raw.get("metrics") or []) if str(item) in METRICS]
    metrics = list(dict.fromkeys(metrics))[:4]
    if not metrics:
        return None
    calculation = str(raw.get("calculation") or "count")
    if calculation not in {"count", "percentage"}:
        calculation = "count"
    denominator = str(raw.get("denominator_metric") or "") or None
    if denominator not in METRICS:
        denominator = None
    if calculation == "percentage" and denominator is None:
        calculation = "count"
    return {
        "metrics": metrics,
        "calculation": calculation,
        "denominator_metric": denominator,
        "group_by": (
            str(raw.get("group_by"))
            if raw.get("group_by") in {"person", "role"}
            else "none"
        ),
        "rank": "most" if raw.get("rank") == "most" else "all",
    }


def _count(evidence: Mapping[str, Any], metric: str) -> int:
    return int(evidence.get(METRICS[metric]["field"]) or 0)


def _count_sentence(metric: str, count: int, window_label: str) -> str:
    spec = METRICS[metric]
    noun = spec["label"] if count == 1 else spec.get("plural", spec["label"] + "s")
    if metric == "open_escalations":
        return f"{count} escalation{' is' if count == 1 else 's are'} currently open."
    if metric == "active_overdue_observations":
        return f"{count} overdue-observation alert{' is' if count == 1 else 's are'} currently active."
    if spec.get("current"):
        return f"{count} {noun} {'is' if count == 1 else 'are'} currently recorded."
    return f"{count} {noun} {'was' if count == 1 else 'were'} recorded {window_label}."


def answer_audit_question(
    question: str, evidence: Mapping[str, Any], window_label: str,
) -> str | None:
    """Return an evidence-grounded answer, or None for a non-audit question."""
    # Use the LLM where configured for flexible phrasing, then validate every
    # selected field. Local parsing is the no-network/demo-test fallback.
    plan = _validated_plan(_azure_plan(question)) or _validated_plan(_local_plan(question))
    if plan is None:
        return None

    metrics = plan["metrics"]
    if plan["calculation"] == "percentage":
        denominator_metric = plan["denominator_metric"]
        numerator_metric = next(
            (metric for metric in metrics if metric != denominator_metric), metrics[0]
        )
        numerator = _count(evidence, numerator_metric)
        denominator = _count(evidence, denominator_metric)
        if denominator == 0:
            return (
                f"A percentage cannot be calculated for {window_label} because "
                f"0 {METRICS[denominator_metric]['label']}s were recorded."
            )
        percentage = numerator * 100.0 / denominator
        outcome = (
            "accepted without change" if numerator_metric == "acceptances"
            else "changed by a clinician" if numerator_metric == "overrides"
            else f"counted as {METRICS[numerator_metric]['label']}s"
        )
        return (
            f"{percentage:.1f}% ({numerator} of {denominator}) of the recorded "
            f"{METRICS[denominator_metric]['label']}s were {outcome} {window_label}."
        )

    sentences: list[str] = []
    for metric in metrics:
        count = _count(evidence, metric)
        sentence = _count_sentence(metric, count, window_label)
        breakdown_field = (
            METRICS[metric].get("role_breakdown")
            if plan["group_by"] == "role"
            else METRICS[metric].get("breakdown")
        )
        if plan["group_by"] != "none" and breakdown_field:
            people = list(evidence.get(breakdown_field) or [])
            if people:
                if plan["rank"] == "most":
                    highest = int(people[0].get("count") or 0)
                    leaders = [str(p.get("label")) for p in people if int(p.get("count") or 0) == highest]
                    sentence += (
                        f" {' and '.join(leaders)} recorded the most, with {highest}."
                    )
                else:
                    prefix = "By role: " if plan["group_by"] == "role" else "By staff member: "
                    sentence += f" {prefix}" + ", ".join(
                        f"{p.get('label')} ({int(p.get('count') or 0)})" for p in people[:8]
                    ) + "."
            else:
                sentence += " No staff-attributed record was available for that measure."
        sentences.append(sentence)
    return " ".join(sentences)
