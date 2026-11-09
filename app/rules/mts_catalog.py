"""Versioned research transcription of eight charts in the supplied fourth edition.

Page numbers are printed book pages. Clinical findings require an explicit
nursing assessment; a normal/missing vital never proves their absence.
The source PDF is intentionally not distributed with the application.
"""
from copy import deepcopy
import hashlib
import json
import re

SOURCE = {
    "title": "Emergency Triage, Manchester Triage Group", "edition": 4,
    "printed_year": 2027, "isbn": "9781394266654",
    "publisher_url": "https://www.wiley.com/en-gb/emergency-triage-4th-edition-p-9781394266654",
    "validation_status": "source_transcribed_research_requires_clinical_validation",
    "scope": "Eight initial presentation charts; no claim of complete MTS coverage or clinical approval",
}
VERSION = "mts_fourth_edition_research_v2"
CORE = "Airway compromise|Inadequate breathing|Shock"
BLEED_RED = "Airway compromise|Inadequate breathing|Exsanguinating haemorrhage|Shock"
RECENT = "Recent mild pain|Recent problem"


def discriminator_id(label):
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")


MODEL_MEASUREMENT_FIELDS = ("heartrate", "resprate", "o2sat", "sbp", "dbp", "temperature", "pain")


def _chart(name, group, page, bands, *, fallback=5, extra_measurements=()):
    rules = []
    for priority, labels in enumerate(bands, 1):
        for label in filter(None, labels.split("|")):
            identifier = discriminator_id(label)
            rules.append({"id": identifier, "label": label, "priority": priority,
                          "source_page": page, "kind": "measurement_predicate" if identifier in
                          ("low_spo2", "very_low_spo2", "low_pefr", "very_low_pefr") else "clinical_finding"})
    context = ["on_supplemental_oxygen"] if any(r["id"] == "low_spo2" for r in rules) else []
    return {"name": name, "complaint_group": group, "source_page": page,
            "rules": rules, "fallback_priority": fallback,
            "measurement_fields": [*MODEL_MEASUREMENT_FIELDS, *extra_measurements, *context]}


CHARTS = {
    "chest_pain": _chart("Chest pain", "chest_pain", 94, [CORE,
        "Very low SpO2|Acutely short of breath|Massive haemoptysis|New abnormal pulse|Aortic pain|Cardiac pain|Severe pain",
        "Low SpO2|Persistent vomiting|Significant cardiac history|Pleuritic pain|Moderate pain",
        "Vomiting|" + RECENT]),
    "abdominal_pain_adults": _chart("Abdominal pain in adults", "abdominal_pain", 68, [CORE,
        "Vomiting blood|Passing fresh or altered blood PR|PV blood loss and 20 weeks pregnant or more|Very hot|Possible sepsis|Aortic pain|Severe pain",
        "Persistent vomiting|Black or redcurrant stools|Hot|History of acutely vomiting blood|Possibly pregnant|Shoulder tip pain|Moderate pain",
        "Vomiting|Warm|" + RECENT]),
    "shortness_of_breath_adults": _chart("Shortness of breath in adults", "respiratory_symptoms", 152,
        ["Airway compromise|Stridor|Drooling|Inadequate breathing|Shock",
         "Inhalational injury|Unable to talk in sentences|Very low SpO2|Exhaustion|Very low PEFR|Massive haemoptysis|New abnormal pulse|Altered conscious level|Very hot|Possible sepsis|Acute onset after injury|Significant respiratory history|Cardiac pain",
         "Low SpO2|Smoke exposure|Low PEFR|Hot|Pleuritic pain",
         "Wheeze|Warm|Chest injury|Productive cough|Recent problem"],
        extra_measurements=("pefr_measured", "pefr_expected")),
    "major_trauma": _chart("Major trauma", "trauma", 132, [BLEED_RED + "|Unresponsive child",
        "Acutely short of breath|Uncontrollable major haemorrhage|Altered conscious level|New neurological deficit less than 24 hours old|Significant mechanism of injury|Severe pain",
        "Uncontrollable minor haemorrhage|History of unconsciousness|Significant medical history|Moderate pain"], fallback=None),
    "limb_problems": _chart("Limb problems", "trauma", 128, [BLEED_RED,
        "Acutely short of breath|Uncontrollable major haemorrhage|Vascular compromise|New neurological deficit less than 24 hours old|Possible sepsis|Open fracture|Critical skin|Severe pain",
        "Uncontrollable minor haemorrhage|New neurological deficit more than 24 hours old|Gross deformity|Bleeding disorder|Inappropriate history|Pleuritic pain|Moderate pain",
        "Deformity|Swelling|" + RECENT]),
    "head_injury": _chart("Head injury", "trauma", 122, [BLEED_RED + "|Unresponsive child|Currently fitting",
        "Uncontrollable major haemorrhage|Altered conscious level|New neurological deficit less than 24 hours old|Significant mechanism of injury|Severe pain",
        "Uncontrollable minor haemorrhage|Persistent vomiting|New neurological deficit more than 24 hours old|Bleeding disorder|History of unconsciousness|Inappropriate history|Moderate pain",
        "Vomiting|Scalp haematoma|" + RECENT]),
    "torso_injury": _chart("Torso injury", "trauma", 162, [BLEED_RED + "|Unresponsive child",
        "Very low SpO2|Acutely short of breath|Uncontrollable major haemorrhage|Massive haemoptysis|New abnormal pulse|Altered conscious level|Externalisation of organs|Significant mechanism of injury|Severe pain",
        "Low SpO2|Uncontrollable minor haemorrhage|Inappropriate history|Pleuritic pain|Moderate pain",
        "Local infection|Local inflammation|" + RECENT]),
    "wounds": _chart("Wounds", "trauma", 176, [BLEED_RED,
        "Uncontrollable major haemorrhage|Distal vascular compromise|New neurological deficit less than 24 hours old|Hot baby|Very hot|Possible sepsis|Severe pain",
        "Uncontrollable minor haemorrhage|Tachycardia in children|New neurological deficit more than 24 hours old|Warm newborn|Hot|Vaginal trauma|Inappropriate history|Moderate pain",
        "Warm|Local infection|Local inflammation|" + RECENT]),
}

MEASUREMENTS = {
    "on_supplemental_oxygen": {"label": "Receiving supplemental oxygen", "kind": "boolean", "unit": ""},
    "heartrate": {"label": "Heart rate", "unit": "bpm", "min": 0, "max": 400},
    "resprate": {"label": "Respiratory rate", "unit": "/min", "min": 0, "max": 150},
    "o2sat": {"label": "Oxygen saturation", "unit": "%", "min": 0, "max": 100},
    "sbp": {"label": "Systolic blood pressure", "unit": "mmHg", "min": 0, "max": 350},
    "dbp": {"label": "Diastolic blood pressure", "unit": "mmHg", "min": 0, "max": 250},
    "temperature": {"label": "Temperature", "unit": "C", "min": 10, "max": 50},
    "pain": {"label": "Pain score", "unit": "/10", "min": 0, "max": 10},
    "pefr_measured": {"label": "Measured peak expiratory flow", "unit": "L/min", "min": 0, "max": 1500},
    "pefr_expected": {"label": "Predicted or personal best peak flow", "unit": "L/min", "min": 1, "max": 1500},
}

# Match complete presenting-complaint labels only. Free text, negation and
# ambiguous trauma require a nurse's chart selection, rather than guessing.
ALIASES = {
    "chest pain": "chest_pain", "abdominal pain": "abdominal_pain_adults",
    "shortness of breath": "shortness_of_breath_adults", "breathlessness": "shortness_of_breath_adults",
    "breathing problem / shortness of breath": "shortness_of_breath_adults",
    "limb injury": "limb_problems", "limb problems": "limb_problems", "limb pain": "limb_problems",
    "head injury": "head_injury", "chest injury": "torso_injury", "torso injury": "torso_injury",
    "wound": "wounds", "wounds": "wounds", "laceration": "wounds",
    "major trauma": "major_trauma",
}


def propose_flowchart(complaint):
    text = " ".join(str(complaint or "").casefold().split())
    proposed = ALIASES.get(text)
    return {"flowchart_id": proposed, "method": "exact_complaint_mapping_v1",
            "requires_nurse_confirmation": True,
            "reason": "Exact presenting-complaint mapping" if proposed else "No unambiguous supported mapping; select the appropriate chart"}


def catalog_hash():
    return hashlib.sha256(json.dumps({"source": SOURCE, "charts": CHARTS, "measurements": MEASUREMENTS,
                                     "complaint_aliases": ALIASES}, sort_keys=True).encode()).hexdigest()


def catalog_payload():
    payload = deepcopy({"version": VERSION, "sha256": catalog_hash(), "source": SOURCE,
                        "flowcharts": CHARTS, "measurements": MEASUREMENTS})
    payload["model_measurement_fields"] = list(MODEL_MEASUREMENT_FIELDS)
    for chart in payload["flowcharts"].values():
        chart["measurement_context_fields"] = [key for key in chart["measurement_fields"]
                                                 if key not in MODEL_MEASUREMENT_FIELDS]
    return payload


# Shared source-linked help is also bound into the catalog digest.
from app.rules.mts_definitions import DEFINITIONS, PAEDIATRIC_FINDINGS
for _chart_definition in CHARTS.values():
    _chart_definition["age_scope"] = "16 years and over; initial research scope"
    for _rule in _chart_definition["rules"]:
        _page, _help = DEFINITIONS[_rule["id"]]
        _rule.update(assessment_help=_help, definition_source_page=_page,
                     applicability="paediatric" if _rule["id"] in PAEDIATRIC_FINDINGS else "adult_initial_scope",
                     definition_validation_status="source_paraphrased_pending_clinical_review")
