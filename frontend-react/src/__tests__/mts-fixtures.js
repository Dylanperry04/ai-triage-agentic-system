// UI contract fixtures; these are not patient observations or clinical references.
const measurements = Object.fromEntries([
  ["heartrate", "Heart rate", "bpm"], ["sbp", "Systolic blood pressure", "mmHg"],
  ["dbp", "Diastolic blood pressure", "mmHg"], ["o2sat", "Oxygen saturation", "%"],
  ["resprate", "Respiratory rate", "/min"], ["temperature", "Temperature", "C"], ["pain", "Pain score", "/10"],
].map(([key, label, unit]) => [key, { label, unit, min: 0, max: 400 }]));
export const catalog = { version: "ui-contract-test", sha256: "test-digest", source: { edition: 4 }, measurements,
  flowcharts: {
    chest_pain: { name: "Chest pain", source_page: 94, measurement_fields: Object.keys(measurements), rules: [
      { id: "airway_compromise", label: "Airway compromise", priority: 1 },
      { id: "cardiac_pain", label: "Cardiac pain", priority: 2 },
    ] },
    major_trauma: { name: "Major trauma", source_page: 132, measurement_fields: Object.keys(measurements), rules: [
      { id: "shock", label: "Shock", priority: 1 },
    ] },
  } };
export const checklistResponse = (case_uid = "test-case") => ({ case_uid, catalog,
  proposal: { flowchart_id: "chest_pain" }, checklist: null, revision: null, observations_at: null });
export const emptyEvaluation = { reference_cases: 0,
  flowchart_selection: { accuracy: null, evaluated_cases: 0, abstentions: 0 },
  discriminator_identification: { precision: null, recall: null, f1: null, evaluated_cases: 0 },
  guideline_adherence: { rate: null, reviewed_decisions: 0 },
  unsupported_clinical_claims: { explanations_with_unsupported_claims: null, reviewed_explanations: 0 },
  by_presenting_complaint: Object.fromEntries(["chest_pain", "abdominal_pain", "trauma", "respiratory_symptoms"].map(id => [id,
    Object.fromEntries(["ml_category", "final_category", "mts_category"].map(k => [k, { evaluated_cases: 0, reference_cases: 0 }]))])) };
