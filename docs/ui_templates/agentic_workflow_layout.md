# UI Template: Agentic Workflow Layout

## Target User

Clinical supervisor, researcher, ITD/security admin.

## Layout Purpose

Show the workflow stages:

1. intake and validation
2. deterministic safety review
3. ML estimate
4. explanation generation
5. clinician action
6. reassessment after additional information

## Strengths

- Clearly shows that ML assigns the estimate before LLM/AutoGen explanation.
- Helps explain that agents are read-only and cannot change acuity.
- Good for demonstrating the follow-up/reassessment loop.

## Limitations

- More complex than the clinician-first view.
- Needs strong wording to avoid implying autonomous clinical decision-making.

## Summit Use

Use this layout to explain the agentic architecture and the boundary between
model prediction, rules, explanation, and clinician review.
