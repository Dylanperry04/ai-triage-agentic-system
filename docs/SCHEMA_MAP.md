# UHL source-to-runtime schema map

The loader requires the following 28 columns in this exact order. Renamed,
missing, extra, or reordered columns fail startup. Unless stated otherwise, a
blank, non-finite, malformed, or out-of-range value fails the cache build and no
cases are served.

| Source column | Type and validation | Runtime/model consumer | Missing-data behavior |
|---|---|---|---|
| `AttendanceID` | Non-empty unique text | Combined with source identity and dataset hash to derive a 32-character `case_uid` | Rejected; raw value is never cached or returned |
| `DATE` | ISO-compatible local timestamp | API `arrival_time`; serving derives time features | Rejected |
| `year` | Integer equal to `DATE.year` | Cross-check only | Rejected |
| `month` | Integer equal to `DATE.month` | Re-derived from `arrival_time` as model input | Rejected |
| `hour` | Integer equal to `DATE.hour` | Re-derived from `arrival_time` as model input | Rejected |
| `time_bin` | Exact derived category | Re-derived from hour as model input | Rejected |
| `season` | Exact derived category | Re-derived from month as model input | Rejected |
| `EdLocationName_base` | Non-empty text | Validation only; excluded from cache, API, UI, and model | Rejected |
| `EdLocationName_token` | Text | Excluded everywhere; 348,853 blanks are explicitly allowed | Blank allowed |
| `PresentingComplaint_base` | Exact uppercase fitted category or `DOA` | API/UI `presenting_complaint`; fitted model category | Rejected if blank/unseen |
| `PresentingComplaint_token` | Non-empty text | Validation only; excluded from cache, API, UI, and model | Rejected |
| `Age` | Integer, 0 through 110 | API/UI `age`; model `age` | Rejected outside fitted support |
| `HoursinEd` | Finite number | Retrospective field; excluded everywhere at runtime | Rejected |
| `Admitted` | Integer 0 or 1 | Outcome; excluded everywhere at runtime | Rejected |
| `Over6Hours` | Integer 0 or 1 | Outcome; excluded everywhere at runtime | Rejected |
| `Over9Hours` | Integer 0 or 1 | Outcome; excluded everywhere at runtime | Rejected |
| `Over24` | Integer 0 or 1 | Outcome; excluded everywhere at runtime | Rejected |
| `Over75` | Integer 0 or 1 | Derived/outcome field; excluded everywhere at runtime | Rejected |
| `TTT_minutes` | Finite number | Retrospective timing; excluded everywhere at runtime | Rejected |
| `TTC_minutes` | Finite number | Retrospective timing; excluded everywhere at runtime | Rejected |
| `master_estimated_acuity` | Integer 1 through 5 | Training label and pinned distribution check only | Rejected; never cached or returned |
| `temperature` | Finite Fahrenheit, 80 through 110 for model rows | API/UI vital and model input | Rejected; `DOA` zero stays on non-ML pathway |
| `heartrate` | Finite bpm, 1 through 300 for model rows | API/UI vital and model input | Rejected; same `DOA` exception |
| `resprate` | Finite breaths/min, 1 through 90 for model rows | API/UI vital and model input | Rejected; same `DOA` exception |
| `o2sat` | Finite percent, 1 through 100 for model rows | API/UI vital and model input | Rejected; same `DOA` exception |
| `sbp` | Finite mmHg, 1 through 300 for model rows | API/UI vital and model input | Rejected; same `DOA` exception |
| `dbp` | Finite mmHg, 1 through 220 for model rows | API/UI vital and model input | Rejected; same `DOA` exception |
| `pain` | Finite 0 through 10 | API/UI vital and model input | Rejected; same `DOA` exception |

## Derived categories and timestamps

Timestamps are interpreted under the training policy for `Europe/Dublin`.
Source `year`, `month`, and `hour` must agree with `DATE`; source `time_bin` and
`season` must agree with these deterministic mappings:

- `LATE NIGHT`: hours 00-02
- `EARLY MORNING`: hours 03-06
- `MORNING`: hours 07-11
- `AFTERNOON`: hours 12-16
- `EVENING`: hours 17-20
- `NIGHT`: hours 21-23
- `WINTER`: December-February; `SPRING`: March-May; `SUMMER`: June-August;
  `AUTUMN`: September-November

The API accepts reassessment temperatures in Fahrenheit or Celsius and converts
Celsius to the Fahrenheit representation used during training before validating
the 80-110 F model range.

## Model input order

The artifact and serving code require exactly:

```text
age, month, hour, time_bin, season, presenting_complaint,
temperature, heartrate, resprate, o2sat, sbp, dbp, pain
```

The SHA-256 of the newline-joined order is
`fd3d1365fe744d5eb75a83b8cfb1ebf9b84695a405c802b633bd2bb78f89debd`.

## Complaint category contract

The 41 fitted values are:

```text
ABDOMINAL PAIN
BACK PAIN
BREATHING PROBLEM / SHORTNESS OF BREATH
BURN
CHEMICAL INJURY
CHEST PAIN
COLLAPSE / FAINT
DELIBERATE SELF HARM / OVERDOSE
DENTAL PROBLEM
DIARRHOEA
EAR PROBLEM
EYE PROBLEM
FIT / SEIZURE
FOREIGN BODY
INJURY
INSECT BITES / STINGS
INSECTS BITES / STINGS
LIMB PAIN
LIMB SWELLING
LIMPING CHILD
MAJOR EMERGENCY
NASAL PROBLEM
NECK PAIN
NOSE BLEED
NOT DISCLOSED
OTHER
PREGNANCY
PSYCHIATRIC PROBLEM
QUERY COVID-19
RASH
RECTAL BLEEDING
SKIN INFECTION
SORE THROAT
TESTICULAR PAIN
UNWELL ADULT
UNWELL CHILD
URINARY PROBLEM
VAGINAL BLEEDING
VOMITING
VOMITING BLOOD
WOUND
```

`DOA` occurs twice in the source but was not fitted. It is retained in the case
list with an explicit non-ML pathway; prediction requests for it return no model
estimate.

## Acuity categories

| Value | UI label | Display target |
|---|---|---|
| 1 | Immediate | 0 minutes |
| 2 | Very urgent | 10 minutes |
| 3 | Urgent | 60 minutes |
| 4 | Standard | 120 minutes |
| 5 | Non-urgent | 240 minutes |

These are research display labels. The synthetic target is not clinical ground
truth.
