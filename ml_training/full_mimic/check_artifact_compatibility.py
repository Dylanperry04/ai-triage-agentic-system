"""Model-artefact compatibility check. Verifies a saved model artefact was trained
with an sklearn version compatible with the runtime, so the app fails safe rather
than silently mispredicting on a version mismatch.

Usable for the demo model too:
  python ml_training/full_mimic/check_artifact_compatibility.py <path-to-joblib>
"""
import sys
from pathlib import Path


def check_artifact(path: Path) -> dict:
    import joblib
    import sklearn
    from ml_training.full_mimic.raw_triage_pipeline import (
        RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION,
        RAW_TRIAGE_INPUT_COLUMNS,
        RAW_TRIAGE_INPUT_TYPE,
    )

    obj = joblib.load(path)
    trained_with = None
    contract_version = 1
    input_type = "structured_feature_matrix"
    if isinstance(obj, dict):
        trained_with = obj.get("sklearn_version")
        contract_version = int(obj.get("artifact_contract_version", 1))
        input_type = obj.get("input_type") or input_type
    runtime = sklearn.__version__

    def major_minor(v):
        try:
            return tuple(int(x) for x in v.split(".")[:2])
        except Exception:
            return None

    compatible = True
    reason = "ok"
    if trained_with is None:
        compatible = False
        reason = "artefact does not record its sklearn version"
    elif major_minor(trained_with) != major_minor(runtime):
        compatible = False
        reason = f"sklearn minor-version mismatch: trained={trained_with} runtime={runtime}"
    elif contract_version == RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION:
        raw_cols = list(obj.get("raw_input_columns", [])) if isinstance(obj, dict) else []
        if input_type != RAW_TRIAGE_INPUT_TYPE:
            compatible = False
            reason = (
                "v2 artefact input_type mismatch: "
                f"expected {RAW_TRIAGE_INPUT_TYPE}, got {input_type!r}"
            )
        elif raw_cols != list(RAW_TRIAGE_INPUT_COLUMNS):
            compatible = False
            reason = "v2 artefact raw_input_columns do not match serving contract"
        elif "model" not in obj:
            compatible = False
            reason = "v2 artefact does not contain a model"
    elif contract_version != 1:
        compatible = False
        reason = f"unsupported artefact contract version: {contract_version}"
    return {
        "artifact": str(path),
        "trained_with_sklearn": trained_with,
        "runtime_sklearn": runtime,
        "compatible": compatible,
        "reason": reason,
        "artifact_contract_version": contract_version,
        "input_type": input_type,
        "feature_count": len(obj.get("feature_names", [])) if isinstance(obj, dict) else None,
        "raw_input_count": (
            len(obj.get("raw_input_columns", [])) if isinstance(obj, dict) else None
        ),
    }


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: check_artifact_compatibility.py <path-to-joblib>\n")
        return 2
    path = Path(sys.argv[1]).expanduser()
    if not path.exists():
        sys.stderr.write(f"artefact not found: {path}\n")
        return 2
    result = check_artifact(path)
    import json
    print(json.dumps(result, indent=2))
    return 0 if result["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
