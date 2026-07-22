# Model Overview Slide Notes

These notes are plain-English content for presentation slides. They should be
reviewed alongside the live dashboard outputs from the final retraining run.

## Slide 1: Use Case

Emergency Department triage decision support research.

The system estimates MIMIC-IV-ED-style acuity from triage-time information and
presents the result for clinician review. It does not replace triage staff and
does not implement official Manchester Triage rules.

## Slide 2: Dataset and Split

MIMIC-IV-ED acuity labels are imbalanced. Acuity 3 is the majority class and
acuity 5 is rare.

The training split is stratified patient-grouped 70/15/15. This keeps repeat
visits from the same patient out of multiple splits while preserving the class
distribution.

## Slide 3: Model Families

The comparison covers:

- linear and regression-style baselines
- tree and boosted ensemble models
- raw chief-complaint TF-IDF text models
- TF-IDF/SVD plus boosted models
- class-weight and SMOTE imbalance strategies

## Slide 4: Why Text Matters

Chief complaint text contains clinically relevant triage-time signal, including
short ED abbreviations such as CP, SOB, SI, AMS, MVC, and ETOH.

The raw text models are included because broad keyword flags can lose important
distinctions.

## Slide 5: Imbalance Strategy

The project compares:

- no weighting
- class weighting
- SMOTE after split, training-only

Validation and test data remain untouched and naturally imbalanced.

## Slide 6: Safety Trade-Off

The final model is not selected on recall alone.

The balanced-safety profile considers high-acuity recall, specificity,
predicted urgent rate, over-triage, under-triage, and severe under-triage.

## Slide 7: Explainability

Supported models report feature importances or coefficients. Unsupported models
state that feature importance is unavailable. TF-IDF/SVD models do not claim
direct raw-token importance when the SVD mapping prevents it.

## Slide 8: Deployment Boundary

The current deployment is a research/demo Azure App Service with Streamlit and
FastAPI. Clinical use requires separate validation, approval, monitoring,
governance sign-off, and deployment approval.
