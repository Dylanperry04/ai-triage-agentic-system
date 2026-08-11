"""Full-MIMIC model card generator. Reads the aggregate training/evaluation
artefacts (produced by train.py / evaluate.py) and writes a Markdown model card.
No raw rows; reads only aggregate JSON.

Run on the credentialed environment AFTER train.py and evaluate.py.
"""
import json
import sys
from datetime import date


def _load(p):
    return json.loads(p.read_text()) if p.exists() else {}


def main() -> int:
    from ml_training.full_mimic._safety import require_safe_environment, UnsafeEnvironmentError
    try:
        paths = require_safe_environment()
    except UnsafeEnvironmentError as e:
        sys.stderr.write(f"REFUSED: {e}\n")
        return 2
    out = paths["output_dir"]
    train = _load(out / "full_mimic_training_metrics.json")
    ev = _load(out / "full_mimic_evaluation.json")
    feats = _load(out / "full_mimic_feature_list.json")

    md = f"""# Model Card — MIMIC-IV-ED Full Acuity Research Model

**Generated:** {date.today().isoformat()}
**Status:** RESEARCH ONLY — not clinically validated, not for clinical use.
Clinician review is required for every output.

## Intended use
Research decision-support prototype that predicts ED acuity from triage features.
The ML model produces the acuity prediction; the LLM/agent layer only explains it
and cannot assign, alter, or override triage.

## Training data
Full credentialed MIMIC-IV-ED (read from a controlled MIMIC_FULL_ED_DIR on an
approved environment; never copied into the repo/build). Retired KTAS/demo
artefacts are not live prediction sources and are never used for full-MIMIC
prediction.

## Features
{len(feats)} engineered features (see full_mimic_feature_list.json). Leakage
columns (outcomes/labels) are excluded by the feature builder.

## Metrics (aggregate, de-identified)
- Cross-val accuracy: {train.get('cv_accuracy_mean')} ± {train.get('cv_accuracy_std')} (n={train.get('n_train')})
- High-acuity recall: {ev.get('high_acuity_recall', {}).get('recall')}
- Severe under-triage rate: {ev.get('under_over_triage', {}).get('severe_under_triage_rate')}
- Under-triage rate: {ev.get('under_over_triage', {}).get('under_triage_rate')}
- Over-triage rate: {ev.get('under_over_triage', {}).get('over_triage_rate')}
- MAE: {ev.get('ordinal_metrics', {}).get('mae')}
- Quadratic weighted kappa: {ev.get('ordinal_metrics', {}).get('quadratic_weighted_kappa')}
- Within-one-acuity-level accuracy: {ev.get('ordinal_metrics', {}).get('within_1_acuity_level_accuracy')}
- Calibration (mean Brier): {ev.get('calibration', {}).get('brier_mean')}
- sklearn version: {train.get('sklearn_version')}

## Known limitations & risks
- Not clinically validated; no prospective evaluation; no UHL ground-truth validation.
- Acuity labels in MIMIC are themselves triage assessments, not outcomes.
- Fairness: review feature importances for sensitive-attribute reliance before any use.
- Under-triage (predicting less urgent than truth) is the safety-critical error; see rates above.

## Not for
Autonomous triage; clinical deployment; any use without clinician confirmation;
claiming official Manchester Triage System assignment.
"""
    (out / "full_mimic_model_card.md").write_text(md)
    print(f"Model card written: {out/'full_mimic_model_card.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
