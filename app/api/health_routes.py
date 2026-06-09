from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    return {
        "status": "ok",
        "clinical_use": "not_for_clinical_use",
        "rules_status": "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED",
    }
