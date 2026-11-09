# Data folder

The local review launcher uses `uhl_demo_balanced_100.json`. This manifest contains 100 unchanged source rows with synthetic vital signs and estimated acuity labels. It exercises chart proposals and abstentions. The selection uses the frozen training partition only. The app's older `demo_100` profile uses `uhl_demo_100.json` for the previous 60-matched-check demonstration.

To regenerate the local review manifest, run this command from the project folder with the immutable training CSV and legacy manifest available.

```text
python scripts/build_balanced_demo_cohort.py --output <temporary-file>
```

The builder uses `uhl_dataset_final.csv.gz` and verifies the archived split before selecting. The manifest records source provenance, exact one-based source row numbers and declared strata. Review case labels are for workflow demonstration. Clinical accuracy requires separate, independently annotated cases.

The local launcher writes generated data to `.local-review-data`. The historical credentialed MIMIC workflow uses `LOCAL_CREDENTIALED_RESEARCH=true` and an external `MIMIC_FULL_ED_DIR`. Keep credentialed MIMIC tables outside the repository and runtime images.
