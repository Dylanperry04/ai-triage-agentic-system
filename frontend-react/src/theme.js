/* HSE digital design system tokens (service-manual.hse.ie) + the project's
   acuity→MTS-style display scale (app/rules/acuity_mts_mapping.py — display
   convention, not official Manchester classification). */
export const T = {
  ink: "#212B32", slate: "#455C68", grey500: "#768692", border: "#D8DDE0", borderSoft: "#E8ECEE",
  canvas: "#F3F3F3", surface: "#FFFFFF",
  green900: "#00473E", green700: "#02594C", green500: "#02A78B", green300: "#73E6C2", green50: "#ECFBF6",
  blue: "#0B55B7", blue50: "#DBE7FF", red: "#B30638", red50: "#FBE9EE", yellow: "#FFDE0E", yellow50: "#FFF9D6",
  font: "'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif",
  mono: "Consolas, 'SF Mono', ui-monospace, Menlo, monospace",
};

export const MTS = {
  1: { name: "Immediate",   colour: "#C8102E", soft: "#FBE7EA", text: "#FFFFFF", target: "0 min",   targetMin: 0 },
  2: { name: "Very urgent", colour: "#E86C00", soft: "#FDF0E3", text: "#FFFFFF", target: "10 min",  targetMin: 10 },
  3: { name: "Urgent",      colour: "#F2A900", soft: "#FEF6E0", text: "#212B32", target: "60 min",  targetMin: 60 },
  4: { name: "Standard",    colour: "#2E7D32", soft: "#E9F3EA", text: "#FFFFFF", target: "120 min", targetMin: 120 },
  5: { name: "Non-urgent",  colour: "#1565C0", soft: "#E7F0FA", text: "#FFFFFF", target: "240 min", targetMin: 240 },
};
export const NEUTRAL_CAT = { name: "Awaiting assessment", colour: "#8A98A0", soft: "#EEF1F3", text: "#FFFFFF" };
export const catOf = (p) => MTS[p] || NEUTRAL_CAT;

/* Category strings from the backend look like "Very Urgent (Orange)" — map to priority. */
export function priorityFromCategory(cat, fallback = null) {
  if (cat == null) return fallback;
  if (typeof cat === "number") return MTS[cat] ? cat : fallback;
  const s = String(cat).toLowerCase();
  if (s.includes("immediate") || s.includes("red")) return 1;
  if (s.includes("very") || s.includes("orange")) return 2;
  if (s.includes("urgent") && !s.includes("non")) return 3;
  if (s.includes("standard") || s.includes("green")) return 4;
  if (s.includes("non") || s.includes("blue")) return 5;
  const n = parseInt(s, 10);
  return MTS[n] ? n : fallback;
}

export const acuityLabel = (value, fallback = "Acuity pending") => {
  const p = priorityFromCategory(value, value);
  return p == null || p === "" ? fallback : `Acuity ${p}`;
};

export const patientLabel = (c) => (
  c?.patient_display_name
  || c?.patient_display_label
  || c?.display_identifier
  || c?.encounter_display_label
  || "Patient details"
);

export const encounterLabel = (c) => (
  c?.encounter_display_label
  || ""
);

export const patientWithEncounter = (c) => {
  const patient = patientLabel(c);
  const encounter = encounterLabel(c);
  return patient && encounter && patient !== encounter ? `${patient} · ${encounter}` : patient || encounter;
};

export const ROLE_META = {
  triage_nurse:       { icon: "HeartPulse",  blurb: "Front-line triage decisions" },
  ed_doctor:          { icon: "Stethoscope", blurb: "Senior review & escalations" },
  clinical_supervisor:{ icon: "Users",       blurb: "Oversight & audit dashboards" },
  researcher:         { icon: "FlaskConical",blurb: "Aggregate model evidence" },
  security_admin:     { icon: "ShieldCheck", blurb: "ITD — security & governance" },
  governance_auditor: { icon: "ScrollText",  blurb: "Read-only governance audit" },
};

/* Configured staff personas for the role-selector profile. Sent as X-Demo-User
   so audit entries are attributed to the selected staff member. */
export const DEMO_STAFF = {
  triage_nurse: [
    { name: "Sinéad Hartigan", grade: "CNM1, Emergency Dept" },
    { name: "Dara Ó Cinnéide", grade: "Staff Nurse, ED" },
    { name: "Roisín Culhane", grade: "Staff Nurse, ED" },
    { name: "Marcus Adeyemi", grade: "Staff Nurse, ED" },
  ],
  ed_doctor: [
    { name: "Prof. Éilis Moloney", grade: "Consultant in EM" },
    { name: "Tomás Gleeson", grade: "Consultant in EM" },
    { name: "Priya Raghavan", grade: "Consultant in EM" },
  ],
  clinical_supervisor: [
    { name: "Bernadette Ryan-Frawley", grade: "CNM3, ED" },
    { name: "Colm Stack", grade: "ADON, ED Directorate" },
  ],
  researcher: [
    { name: "Fionnuala Meade", grade: "Clinical Data Science" },
    { name: "Gearóid Hassett", grade: "Health Research Institute" },
  ],
  security_admin: [
    { name: "Aoibhinn Costelloe", grade: "ITD Cyber Security" },
    { name: "Séamus Ó Dálaigh", grade: "ITD Service Delivery" },
  ],
  governance_auditor: [
    { name: "Nuala Brosnahan", grade: "Quality & Patient Safety" },
  ],
};

export const initialsOf = (n) => (n || "?").replace(/prof\.|dr\.|mr\.|ms\./gi, "").trim().split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase();
export const fmtTime = (x) => { const d = x instanceof Date ? x : new Date(x); return isNaN(d) ? "—" : d.toLocaleTimeString("en-IE", { hour: "2-digit", minute: "2-digit" }); };
export const fmtDate = (x) => { const d = x instanceof Date ? x : new Date(x); return isNaN(d) ? "—" : d.toLocaleDateString("en-IE", { day: "numeric", month: "short" }); };
export const fmtWait = (m) => m == null ? "—" : m < 60 ? `${Math.round(m)}m` : `${Math.floor(m / 60)}h ${Math.round(m % 60)}m`;
export const toC = (v, unit) => { if (v == null) return null; const n = Number(v); if (Number.isNaN(n)) return null; return (unit || "").toUpperCase() === "F" ? +(((n - 32) * 5) / 9).toFixed(1) : +n.toFixed(1); };

export const VITAL_REF = { heartrate: [60, 100], sbp: [100, 140], o2sat: [94, 100], resprate: [12, 20], tempC: [36.1, 38.0], pain: [0, 3] };
export function vitalState(k, v) {
  const r = VITAL_REF[k]; if (!r || v == null || v === "") return "ok";
  const n = Number(v); if (Number.isNaN(n)) return "ok";
  if (k === "o2sat") return n < r[0] ? (n < r[0] - 2 ? "crit" : "warn") : "ok";
  if (k === "tempC") return n > 38.5 ? "crit" : n > r[1] ? "warn" : n < r[0] ? "warn" : "ok";
  if (k === "pain") return n >= 7 ? "crit" : n > r[1] ? "warn" : "ok";
  if (n > r[1]) return n > r[1] * 1.15 ? "crit" : "warn";
  if (n < r[0]) return "warn";
  return "ok";
}
