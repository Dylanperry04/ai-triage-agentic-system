# Data folder

Live dataset:

```text
Full MIMIC-IV-ED v2.2, read from MIMIC_FULL_ED_DIR outside this repo.
```

Processed files are written to:

```text
data/processed/
```

Trained research models are written to:

```text
data/models/
```

Do not copy credentialed MIMIC tables into this repository or into Docker images.
For local research, set LOCAL_CREDENTIALED_RESEARCH=true and point
MIMIC_FULL_ED_DIR at the extracted `ed` folder.
