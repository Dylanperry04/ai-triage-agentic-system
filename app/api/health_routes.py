from fastapi import APIRouter

from app.rules.manchester_engine import get_approved_ruleset

router = APIRouter()


@router.get("/health")
def health():
    ruleset = get_approved_ruleset()
    provisional_active = bool(ruleset) and ruleset.get(
        "validation_status"
    ) != "CLINICALLY_APPROVED"
    return {
        "status": "ok",
        "clinical_use": "not_for_clinical_use",
        "default_dataset": "MIMIC-IV-ED-Demo-v2.2",
        "datasets_available": ["MIMIC-IV-ED-Demo-v2.2", "Kaggle-KTAS"],
        "ktas_model_status": "research_only_ktas_cases_only",
        "official_manchester_triage": "not_implemented",
        "provisional_mts_mode": "enabled" if provisional_active else "disabled",
        "official_mts_ruleset": False,
        "clinically_approved_ruleset": False,
        "rules_status": (
            "PROVISIONAL_MTS_RESEARCH_RULESET_ACTIVE"
            if provisional_active
            else "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED"
        ),
        "human_review_required": True,
    }
