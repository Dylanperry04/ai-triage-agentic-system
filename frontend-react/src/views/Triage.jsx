import React, { useState, useEffect, useMemo, useRef } from "react";
import { CheckCircle2, ArrowUpRight, ChevronDown, Ambulance, Footprints, Activity, Sparkles, Users, ShieldCheck, ClipboardCheck, UploadCloud, FileText, Trash2, Undo2 } from "lucide-react";
import { T, MTS, catOf, priorityFromCategory, toC, vitalState, patientLabel, encounterLabel, patientWithEncounter, acuityLabel } from "../theme.js";
import { Card, Btn, Eyebrow, ConfBar, Modal, Pill, Spinner, EmptyState, ErrorNote } from "../atoms.jsx";
import { api, decisions } from "../api.js";

const QUEUE_STATES = new Set(["new_unreviewed", "request_more_info"]);
const DECIDED_REVIEW = new Set(["accepted_as_presented", "overridden", "uncertain"]);
export const isQueueRow = (c) => {
  const ws = c.workflow_state || {};
  const cs = String(ws.case_status || "").toLowerCase();
  if (!Object.keys(ws).length) return true;
  // Legacy rows written before the backend stamped case_status on decisions:
  // a recorded review decision means the case is on the review board, not in
  // the triage queue, even when case_status is empty.
  if (cs === "" && DECIDED_REVIEW.has(String(ws.review_status || "").toLowerCase())) return false;
  return QUEUE_STATES.has(cs) || cs === "" || cs === "reopened";
};

/* request_more_info means information was REQUESTED; only after a followup is
   recorded (information_response_received_at) has it actually been supplied. */
const infoState = (c) => {
  const ws = c.workflow_state || {};
  if (String(ws.case_status || "").toLowerCase() !== "request_more_info") return null;
  return ws.information_response_received_at ? "reassessed" : "requested";
};

function QueueCard({ c, selected, onClick, prio }) {
  const cat = catOf(prio);
  const info = infoState(c);
  const patient = patientLabel(c);
  const encounter = encounterLabel(c);
  return (
    <div onClick={onClick} role="button" tabIndex={0} aria-label={`Open ${patientWithEncounter(c)}`}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } }}
      className="clickable" style={{ background: selected ? T.green50 : T.surface, border: `1px solid ${selected ? T.green500 : T.borderSoft}`, borderLeft: `5px solid ${cat.colour}`, borderRadius: 12, padding: "13px 14px", marginBottom: 10, boxShadow: selected ? "0 4px 14px rgba(2,89,76,0.14)" : "0 1px 5px rgba(33,43,50,0.08)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 13.5, fontWeight: 800, color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{patient}</span>
        {encounter && encounter !== patient && <span style={{ fontFamily: T.mono, fontSize: 12, fontWeight: 700, color: T.slate, whiteSpace: "nowrap" }}>{encounter}</span>}
      </div>
      <div style={{ fontSize: 16, fontWeight: 800, marginTop: 7, lineHeight: 1.35, textTransform: "capitalize", color: T.ink }}>{c.triage?.chiefcomplaint || "Chief complaint withheld for this role"}</div>
      <div style={{ display: "flex", gap: 8, marginTop: 8, fontSize: 12.5, color: T.slate, alignItems: "center", flexWrap: "wrap" }}>
        {c.demographics?.gender && <span>{c.demographics.gender}</span>}
        {c.demographics?.arrival_transport && <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>{String(c.demographics.arrival_transport).includes("AMB") ? <Ambulance size={12} /> : <Footprints size={12} />}{String(c.demographics.arrival_transport).toLowerCase()}</span>}
        {info === "requested" && <Pill colour="#8A4B00" bg={T.yellow50} border="#EEDD9A">INFO REQUESTED</Pill>}
        {info === "reassessed" && <Pill colour={T.green900} bg={T.green50} border="#CBEADF">REASSESSED</Pill>}
        {prio && <span style={{ marginLeft: "auto", fontFamily: T.mono, fontWeight: 800, color: cat.colour }}>{acuityLabel(prio)}</span>}
      </div>
    </div>
  );
}

function VitalTile({ label, k, v, unit, note }) {
  const st = vitalState(k, v);
  const c = st === "crit" ? T.red : st === "warn" ? "#B25E00" : T.ink;
  const edge = st === "crit" ? T.red : st === "warn" ? "#E86C00" : T.borderSoft;
  return (
    <div style={{ border: `1px solid ${T.borderSoft}`, borderLeft: `5px solid ${edge}`, borderRadius: 12, padding: "17px 18px", background: T.surface, minHeight: 108, boxShadow: "0 1px 5px rgba(33,43,50,0.06)" }}>
      <Eyebrow>{label}</Eyebrow>
      <div style={{ marginTop: 7, display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontFamily: T.mono, fontSize: 34, fontWeight: 800, color: c, lineHeight: 1 }}>{v ?? "—"}</span>
        <span style={{ fontSize: 13, color: T.grey500, fontWeight: 600 }}>{unit}</span>
      </div>
      {note && <div style={{ fontSize: 12, color: T.grey500, marginTop: 5 }}>{note}</div>}
    </div>
  );
}

function CaseDetail({ c, presentation }) {
  if (!c) return <EmptyState>Select a patient from the queue to begin triage.</EmptyState>;
  const t = c.triage || {}; const d = c.demographics || {};
  const ws = c.workflow_state || {};
  const tempC = toC(t.temperature, t.temperature_unit);
  const converted = (t.temperature_unit || "").toUpperCase() === "F";
  const info = infoState(c);
  const encounter = encounterLabel(c);
  return (
    <div className="fade-up" key={c.case_uid} style={{ padding: "4px 2px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 28, fontWeight: 800, lineHeight: 1.2 }}>{patientLabel(c)}</div>
          <div style={{ fontSize: 17, color: T.ink, marginTop: 8, lineHeight: 1.35, textTransform: "capitalize", fontWeight: 700 }}>{t.chiefcomplaint || "Clinical presentation"}</div>
          {encounter && <div style={{ fontSize: 13.5, color: T.slate, marginTop: 6, fontFamily: T.mono, fontWeight: 700 }}>{encounter}</div>}
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {t.age != null && <Pill>{Math.round(Number(t.age))} years</Pill>}
          {d.gender && <Pill>{d.gender === "F" ? "Female" : d.gender === "M" ? "Male" : d.gender}</Pill>}
          {d.arrival_transport && <Pill>{String(d.arrival_transport).toLowerCase()}</Pill>}
          {tempC != null && tempC >= 38 && <Pill colour="#8A4B00" bg={T.yellow50} border="#EEDD9A">● FEBRILE</Pill>}
          {info === "requested" && <Pill colour="#8A4B00" bg={T.yellow50} border="#EEDD9A">AWAITING REQUESTED INFO</Pill>}
          {info === "reassessed" && <Pill colour={T.green900} bg={T.green50} border="#CBEADF">REASSESSED — DECISION NEEDED</Pill>}
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 14, marginTop: 22 }}>
        <VitalTile label="Heart rate" k="heartrate" v={t.heartrate != null ? Math.round(t.heartrate) : null} unit="bpm" />
        <VitalTile label="BP" k="sbp" v={t.sbp != null ? `${Math.round(t.sbp)}/${t.dbp != null ? Math.round(t.dbp) : "—"}` : null} unit="mmHg" />
        <VitalTile label="SpO₂" k="o2sat" v={t.o2sat != null ? Math.round(t.o2sat) : null} unit="%" />
        <VitalTile label="Resp rate" k="resprate" v={t.resprate != null ? Math.round(t.resprate) : null} unit="/min" />
        <VitalTile label="Temp" k="tempC" v={tempC} unit="°C" note={converted ? "converted from °F" : null} />
        <VitalTile label="Pain" k="pain" v={t.pain ?? null} unit="/10" />
      </div>
      {ws.latest_triage_updates_at && (
        <div style={{ fontSize: 12, color: T.green900, background: T.green50, border: "1px solid #CBEADF", borderRadius: 9, padding: "8px 11px", marginTop: 10 }}>
          Observations updated {String(ws.latest_triage_updates_at).replace("T", " ").slice(0, 16)} UTC{ws.information_response_by_role ? ` by ${String(ws.information_response_by_role).replace(/_/g, " ")}` : ""} — the vitals above and the advisory reflect the latest recorded values.
        </div>
      )}
    </div>
  );
}

function AdvisoryPanel({ c, assessment, loading, loadingLabel, error, onAccept, onOverride, onEscalate, onRequestInfo, onReassess, onRetry, canDecide, canAssess, canExplainAcuity }) {
  if (!c) return null;
  if (loading) return (
    <Card style={{ padding: 16 }}>
      <Eyebrow>Triage recommendation</Eyebrow>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
        <Spinner />
        <div style={{ fontSize: 13.5, color: T.slate, lineHeight: 1.45 }}>{loadingLabel || "Preparing the acuity estimate..."}</div>
      </div>
    </Card>
  );
  if (error) return (
    <Card style={{ padding: 16 }}>
      <Eyebrow style={{ marginBottom: 10 }}>Triage recommendation</Eyebrow>
      <ErrorNote>Assessment unavailable: {error}</ErrorNote>
      {canAssess && <Btn kind="quiet" style={{ width: "100%", marginTop: 10 }} onClick={onRetry}>Try assessment again</Btn>}
    </Card>
  );
  if (!assessment) return (
    <Card style={{ padding: 16 }}>
      <Eyebrow>Triage recommendation</Eyebrow>
      <div style={{ fontSize: 13, color: T.slate, lineHeight: 1.5, marginTop: 8 }}>No estimate has been calculated for this patient yet.</div>
      {canAssess && <Btn kind="quiet" style={{ width: "100%", marginTop: 12 }} onClick={onRetry}>Run model estimate</Btn>}
    </Card>
  );
  const prio = priorityFromCategory(assessment.final_category ?? assessment.predicted_acuity);
  const cat = catOf(prio);
  const probs = assessment.class_probabilities || assessment.mimic_acuity_probabilities || null;
  const conf = assessment.assigned_acuity_probability
    ?? assessment.top_class_confidence
    ?? assessment.confidence;
  const mlDown = !assessment.ml_prediction_available;
  const awaitingInfo = infoState(c) === "requested";
  return (
    <div className="fade-up" key={c.case_uid}>
      <Card style={{ overflow: "hidden" }}>
        <div style={{ height: 5, background: cat.colour }} />
        <div style={{ padding: 16 }}>
          <Eyebrow>Triage recommendation</Eyebrow>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
            <div style={{ width: 52, height: 52, borderRadius: 12, background: cat.colour, color: cat.text, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: T.mono, fontSize: 26, fontWeight: 700 }}>{prio ?? "—"}</div>
            <div>
              <div style={{ fontSize: 16.5, fontWeight: 700 }}>{cat.name}</div>
              <div style={{ fontSize: 12.5, color: T.slate }}>{prio ? `Target time to treatment ${MTS[prio].target}` : "No acuity assigned"}</div>
            </div>
          </div>
          {conf != null && (
            <div style={{ marginTop: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <Eyebrow>Probability of {acuityLabel(prio, "this acuity")}</Eyebrow>
              </div>
              <ConfBar v={Number(conf)} colour={cat.colour} />
            </div>
          )}
          {mlDown && <div style={{ marginTop: 12, fontSize: 12.5, color: "#8A4B00", background: T.yellow50, border: "1px solid #EEDD9A", borderRadius: 8, padding: "8px 11px", lineHeight: 1.45 }}>{assessment.model_note || assessment.ml_prediction_error || "ML prediction unavailable in this profile."} {!assessment.model_note && "The acuity above comes from the deterministic rules engine."}</div>}
          {probs && (
            <div style={{ marginTop: 14 }}>
              <Eyebrow>Acuity probabilities</Eyebrow>
              <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 7 }}>
                {Object.entries(probs).sort((a, b) => Number(a[0]) - Number(b[0])).map(([k, p]) => {
                  const kp = priorityFromCategory(k); const km = catOf(kp);
                  return (
                    <div key={k}>
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 3 }}>
                        <span style={{ color: T.ink }}>{acuityLabel(kp ?? k)} · {km.name}</span>
                        <span style={{ fontFamily: T.mono, color: T.slate }}>{(Number(p) * 100).toFixed(1)}%</span>
                      </div>
                      <div style={{ height: 5, background: "#EAEEF0", borderRadius: 4 }}><div style={{ width: `${Math.min(100, Number(p) * 100)}%`, height: "100%", background: km.colour, borderRadius: 4 }} /></div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </Card>
      {canDecide && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
          <Btn kind="dark" onClick={onAccept} style={{ padding: "8px 10px", fontSize: 12.5 }} disabled={!prio}><CheckCircle2 size={13} style={{ verticalAlign: -3, marginRight: 6 }} />Accept & log {acuityLabel(prio, "Acuity —")}</Btn>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <Btn kind="quiet" onClick={onOverride} style={{ padding: "7px 9px", fontSize: 12.5 }}>Override…</Btn>
            <Btn kind="quiet" onClick={onEscalate} style={{ padding: "7px 9px", fontSize: 12.5 }}><ArrowUpRight size={13} style={{ verticalAlign: -2, marginRight: 5 }} />Escalate…</Btn>
          </div>
          {canAssess && (
            <Btn kind="quiet" onClick={onReassess} style={{ padding: "7px 9px", fontSize: 12.5, ...(awaitingInfo ? { border: "1.5px solid #E86C00", color: "#8A4B00" } : {}) }}>
              <Activity size={13} style={{ verticalAlign: -2, marginRight: 6 }} />{awaitingInfo ? "Record requested information" : "Record new observations"}
            </Btn>
          )}
          <Btn kind="quiet" onClick={onRequestInfo} style={{ padding: "7px 9px", fontSize: 12.5 }}><Undo2 size={13} style={{ verticalAlign: -2, marginRight: 6 }} />Request more information…</Btn>
        </div>
      )}
    </div>
  );
}

/* Reassessment / requested-information form. Vitals are entered in the units
   clinicians use here (Temp in °C) and converted to the UHL model's °F units
   (°F) before submission. Ranges mirror the backend allow-list. */
// Minimums mirror the backend allow-list (app/api/case_routes.py
// _ALLOWED_FOLLOWUP_VITALS): a perfusing vital of 0 is missing/erroneous data,
// not an observation, so the form rejects it inline instead of surfacing a 422.
// Pain 0 (no pain) stays valid.
const REASSESS_FIELDS = [
  { key: "heartrate", label: "Heart rate", unit: "bpm", min: 10, max: 350 },
  { key: "resprate", label: "Resp rate", unit: "/min", min: 2, max: 120 },
  { key: "o2sat", label: "SpO₂", unit: "%", min: 20, max: 100 },
  { key: "sbp", label: "Systolic BP", unit: "mmHg", min: 20, max: 400 },
  { key: "dbp", label: "Diastolic BP", unit: "mmHg", min: 10, max: 300 },
  { key: "temperature", label: "Temp", unit: "°C", min: 10, max: 46.1 },
  { key: "pain", label: "Pain", unit: "/10", min: 0, max: 10 },
];

export function ReassessModal({ c, onClose, onDone, toast }) {
  const [vals, setVals] = useState({});
  const [complaint, setComplaint] = useState("");
  const [context, setContext] = useState("");
  const [scanFiles, setScanFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const ws = c.workflow_state || {};
  const requestNote = infoState(c) === "requested";

  const problems = REASSESS_FIELDS.filter((f) => {
    const raw = vals[f.key]; if (raw === undefined || raw === "") return false;
    const n = Number(raw); return Number.isNaN(n) || n < f.min || n > f.max;
  });
  const filled = REASSESS_FIELDS.some((f) => vals[f.key] !== undefined && vals[f.key] !== "") || complaint.trim() || context.trim() || scanFiles.length > 0;

  const addFiles = (fileList) => {
    const incoming = Array.from(fileList || []);
    if (!incoming.length) return;
    const accepted = [];
    let rejected = false;
    for (const file of incoming) {
      if (file.size > 50 * 1024 * 1024) { rejected = true; continue; }
      accepted.push(file);
    }
    setScanFiles((prev) => [...prev, ...accepted].slice(0, 5));
    if (rejected) toast("File too large", "Supporting files must be 50MB or smaller.", "err");
    if (scanFiles.length + accepted.length > 5) toast("Upload limit reached", "Attach up to 5 supporting files per reassessment.", "warn");
  };

  const removeFile = (idx) => {
    setScanFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const submit = async () => {
    if (busy || !filled || problems.length) return;
    const updated_vitals = {};
    for (const f of REASSESS_FIELDS) {
      const raw = vals[f.key]; if (raw === undefined || raw === "") continue;
      const n = Number(raw);
      updated_vitals[f.key] = f.key === "temperature" ? +((n * 9) / 5 + 32).toFixed(1) : n;
    }
    const body = { updated_vitals };
    if (complaint.trim()) body.updated_complaint = complaint.trim();
    if (context.trim()) body.updated_context = context.trim();
    setBusy(true);
    try {
      if (scanFiles.length) {
        body.scan_uploads = [];
        for (const file of scanFiles) {
          body.scan_uploads.push(await api.uploadSupportingScan(c.case_uid, file));
        }
      }
      const r = await api.followupCase(c.case_uid, body);
      setResult(r);
    } catch (e) { toast("Reassessment failed", e.detail || e.message, "err"); }
    finally { setBusy(false); }
  };

  if (result) {
    const from = result.previous_acuity, to = result.new_acuity;
    const fc = catOf(from), tc = catOf(to);
    const escalated = result.change === "escalation";
    const comparisonAvailable = from != null || to != null;
    return (
      <Modal title={`Reassessment recorded — ${patientWithEncounter(c)}`} onClose={() => onDone(result)} width={620}>
        {!comparisonAvailable && (
          <div style={{ fontSize: 12.5, color: T.slate, background: "#F5F7F7", border: `1px solid ${T.borderSoft}`, borderRadius: 8, padding: "9px 12px", marginBottom: 12, lineHeight: 1.5 }}>
            The updated observations are saved and timestamped. A numeric previous→new acuity comparison needs the ML-serving profile — in this profile the deterministic rules engine recomputes the advisory from your updated observations instead (shown when you close this).
          </div>
        )}
        {comparisonAvailable && <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8, background: fc.colour, color: fc.text, borderRadius: 9, padding: "8px 13px", fontWeight: 700 }}><span style={{ fontFamily: T.mono, fontSize: 17 }}>{from ?? "—"}</span> {fc.name}</span>
          <span style={{ color: T.grey500, fontWeight: 700 }}>→</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8, background: tc.colour, color: tc.text, borderRadius: 9, padding: "8px 13px", fontWeight: 700 }}><span style={{ fontFamily: T.mono, fontSize: 17 }}>{to ?? "—"}</span> {tc.name}</span>
        </div>}
        {comparisonAvailable && <div style={{ fontSize: 13.5, lineHeight: 1.55 }}>{result.change_summary}</div>}
        {(result.changed_vitals || []).length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Eyebrow>Changed observations</Eyebrow>
            <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 7 }}>
              {result.changed_vitals.map((v, i) => (
                <div key={i} style={{ fontSize: 12.5, fontFamily: T.mono, color: T.slate }}>{v.vital || v.field || v.key}: {v.previous ?? v.from ?? "—"} → {v.updated ?? v.new ?? v.to ?? "—"}</div>
              ))}
            </div>
          </div>
        )}
        {escalated && <div style={{ marginTop: 12, fontSize: 12.5, color: T.red, background: T.red50, border: "1px solid #EFC5D1", borderRadius: 8, padding: "8px 11px", lineHeight: 1.45 }}>The reassessed acuity is higher — an escalation to the clinical supervisor has been opened automatically.</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
          <Btn onClick={() => onDone(result)}>Done — refresh advisory</Btn>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title={`Record observations — ${patientWithEncounter(c)}`} onClose={onClose} width={920} maxHeight="94vh">
      {requestNote && (
        <div style={{ fontSize: 12.5, color: "#8A4B00", background: T.yellow50, border: "1px solid #EEDD9A", borderRadius: 8, padding: "8px 11px", marginBottom: 12, lineHeight: 1.45 }}>
          Further information was requested for this case{ws.updated_at_utc ? ` (${String(ws.updated_at_utc).replace("T", " ").slice(0, 16)} UTC)` : ""}. Enter what has come back — the case is reassessed against the updated picture.
        </div>
      )}
      <Eyebrow style={{ marginBottom: 8 }}>Updated vital signs (leave blank if unchanged)</Eyebrow>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10, marginBottom: 8 }}>
        {REASSESS_FIELDS.map((f) => (
          <label key={f.key} style={{ fontSize: 11, fontWeight: 700, color: T.slate, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
            {f.label} <span style={{ fontWeight: 500, color: T.grey500 }}>({f.unit})</span>
            <input inputMode="decimal" value={vals[f.key] ?? ""} onChange={(e) => setVals((v) => ({ ...v, [f.key]: e.target.value }))}
              style={{ width: "100%", boxSizing: "border-box", fontFamily: T.mono, fontSize: 13.5, padding: "8px 9px", borderRadius: 8, border: `1px solid ${problems.some((p) => p.key === f.key) ? T.red : T.border}` }} />
          </label>
        ))}
      </div>
      {problems.length > 0 && <div style={{ fontSize: 12, color: T.red, marginBottom: 8 }}>{problems.map((p) => `${p.label} must be ${p.min}–${p.max} ${p.unit}`).join(" · ")}</div>}
      <Eyebrow style={{ margin: "8px 0" }}>Updated complaint (optional)</Eyebrow>
      <input value={complaint} maxLength={240} onChange={(e) => setComplaint(e.target.value)} placeholder="Only if the presenting complaint has changed" style={{ width: "100%", boxSizing: "border-box", fontFamily: T.font, fontSize: 13.5, padding: "9px 11px", borderRadius: 9, border: `1px solid ${T.border}`, marginBottom: 10 }} />
      <Eyebrow style={{ marginBottom: 8 }}>Clinical context / requested information (optional)</Eyebrow>
      <textarea value={context} maxLength={1000} onChange={(e) => setContext(e.target.value)} rows={2} placeholder="e.g. Collateral history from family; ECG shows sinus tachycardia." style={{ width: "100%", boxSizing: "border-box", fontFamily: T.font, fontSize: 13.5, padding: 10, borderRadius: 9, border: `1px solid ${T.border}`, resize: "vertical", marginBottom: 10 }} />
      <Eyebrow style={{ marginBottom: 8 }}>Supporting MRI / X-ray / document</Eyebrow>
      <div style={{ border: `1px dashed ${T.border}`, borderRadius: 10, padding: 12, marginBottom: 12, background: "#FAFBFB" }}>
        <input id={`supporting-upload-${c.case_uid}`} type="file" multiple accept="image/*,.dcm,.dicom,application/dicom,application/pdf" onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} style={{ display: "none" }} />
        <label htmlFor={`supporting-upload-${c.case_uid}`} className="clickable" style={{ display: "inline-flex", alignItems: "center", gap: 8, fontFamily: T.font, fontSize: 13.5, fontWeight: 700, color: T.green700, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 9, padding: "9px 13px" }}>
          <UploadCloud size={16} /> Upload scan or document
        </label>
        {scanFiles.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
            {scanFiles.map((file, i) => (
              <div key={`${file.name}-${file.size}-${file.lastModified}-${i}`} style={{ display: "flex", alignItems: "center", gap: 8, border: `1px solid ${T.borderSoft}`, borderRadius: 8, padding: "7px 9px", background: T.surface }}>
                <FileText size={15} style={{ color: T.slate, flexShrink: 0 }} />
                <span style={{ minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12.5, color: T.ink }}>{file.name}</span>
                <span style={{ fontFamily: T.mono, fontSize: 11.5, color: T.grey500, whiteSpace: "nowrap" }}>{Math.max(1, Math.round(file.size / 1024))} KB</span>
                <button type="button" aria-label={`Remove ${file.name}`} onClick={() => removeFile(i)} style={{ border: "none", background: "transparent", color: T.grey500, cursor: "pointer", padding: 2 }}>
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <Btn kind="quiet" onClick={onClose}>Cancel</Btn>
        <Btn disabled={!filled || problems.length > 0 || busy} onClick={submit}>{busy ? (scanFiles.length ? "Uploading & reassessing…" : "Reassessing…") : "Save & reassess"}</Btn>
      </div>
    </Modal>
  );
}

const AGENT_META = {
  IntakeAgent: { icon: ClipboardCheck, label: "Intake", blurb: "States the verified facts" },
  ValidationAgent: { icon: ShieldCheck, label: "Validation", blurb: "Checks completeness" },
  SafetyReviewAgent: { icon: Users, label: "Safety review", blurb: "Confirms review is required" },
  ExplanationAgent: { icon: Sparkles, label: "Explanation", blurb: "Plain-language summary" },
};

/* Multi-agent case-acuity explanation — sits on the triage-review screen under
   the ML estimate. The AutoGen team (Intake -> Validation -> Safety review ->
   Explanation) is the only UI surface that explains why this acuity was chosen.
   It never decides the acuity. Distinct from the ITD system chatbot. */
function MultiAgentExplanation({ caseUid, canExplain, toast }) {
  const [state, setState] = useState("idle");   // idle | loading | done | error
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [showTurns, setShowTurns] = useState(false);

  // Reset when the selected case changes.
  useEffect(() => { setState("idle"); setResult(null); setErr(null); setShowTurns(false); }, [caseUid]);

  if (!canExplain) return null;

  const run = () => {
    if (state === "loading") return;
    setState("loading"); setErr(null);
    api.multiagentExplainCase(caseUid)
      .then((r) => { setResult(r); setState("done"); if (r.status === "SAFETY_FAIL") toast("Explanation withheld", "The safety filter blocked the generated explanation.", "err"); })
      .catch((e) => { setErr(e.detail?.reason || e.detail || e.message); setState("error"); });
  };

  const notConfigured = result && result.status === "NOT_CONFIGURED";
  const passed = result && result.status === "PASS";

  return (
    <Card style={{ marginTop: 12, padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <Sparkles size={15} style={{ color: T.green700 }} />
        <div style={{ fontSize: 13.5, fontWeight: 700 }}>AutoGen team explanation</div>
      </div>
      <div style={{ fontSize: 11.5, color: T.grey500, lineHeight: 1.45, marginBottom: 12 }}>
        The AutoGen team explains why this acuity was chosen. It explains the estimate — it does not decide it.
      </div>

      {state === "idle" && (
        <Btn kind="quiet" style={{ width: "100%" }} onClick={run}><Sparkles size={14} style={{ verticalAlign: -2, marginRight: 6 }} />Ask AutoGen team why</Btn>
      )}

      {state === "loading" && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: T.slate, fontSize: 12.5, padding: "6px 0" }}>
          <Spinner /> The agents are reviewing the verified evidence…
        </div>
      )}

      {state === "error" && (
        <>
          <ErrorNote>Explanation unavailable: {String(err)}</ErrorNote>
          <Btn kind="quiet" style={{ width: "100%", marginTop: 8 }} onClick={run}>Try again</Btn>
        </>
      )}

      {notConfigured && (
        <div style={{ fontSize: 12.5, color: "#8A4B00", background: T.yellow50, border: "1px solid #EEDD9A", borderRadius: 8, padding: "9px 11px", lineHeight: 1.5 }}>
          The multi-agent explanation service is not configured in this environment (no Azure OpenAI connection), so no narrative was generated. The deterministic model and rules evidence above is unchanged and remains the basis for review.
        </div>
      )}

      {passed && (
        <>
          <div style={{ fontSize: 13, color: T.ink, lineHeight: 1.6, background: T.green50, border: "1px solid #CBEADF", borderRadius: 9, padding: "11px 13px" }}>
            {result.final_explanation}
          </div>
          {(result.agent_turns || []).length > 0 && (
            <>
              <button onClick={() => setShowTurns((v) => !v)} style={{ marginTop: 10, background: "none", border: "none", cursor: "pointer", color: T.green700, fontSize: 12, fontWeight: 700, padding: 0, display: "flex", alignItems: "center", gap: 5 }}>
                <ChevronDown size={13} style={{ transform: showTurns ? "rotate(180deg)" : "none", transition: "transform .15s" }} />{showTurns ? "Hide" : "Show"} how the agents reached this ({result.agent_turns.length} steps)
              </button>
              {showTurns && (
                <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8 }}>
                  {result.agent_turns.map((turn, i) => {
                    const meta = AGENT_META[turn.agent] || { icon: Sparkles, label: turn.agent, blurb: "" };
                    const Icon = meta.icon;
                    return (
                      <div key={i} style={{ display: "flex", gap: 9, alignItems: "flex-start" }}>
                        <div style={{ flexShrink: 0, width: 26, height: 26, borderRadius: 7, background: T.green50, display: "flex", alignItems: "center", justifyContent: "center", marginTop: 1 }}><Icon size={14} style={{ color: T.green700 }} /></div>
                        <div>
                          <div style={{ fontSize: 11.5, fontWeight: 700, color: T.slate }}>{meta.label}{meta.blurb ? <span style={{ fontWeight: 400, color: T.grey500 }}> · {meta.blurb}</span> : null}</div>
                          <div style={{ fontSize: 12.5, color: T.ink, lineHeight: 1.5, marginTop: 2 }}>{turn.text}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
          <Btn kind="ghost" style={{ width: "100%", marginTop: 10, fontSize: 12 }} onClick={() => { setState("idle"); setResult(null); }}>Run again</Btn>
        </>
      )}

      {result && result.status === "SAFETY_FAIL" && (
        <div style={{ fontSize: 12.5, color: T.red, background: T.red50, border: "1px solid #EFC5D1", borderRadius: 8, padding: "9px 11px", lineHeight: 1.5 }}>
          The generated explanation did not pass the safety filter and was withheld. Rely on the deterministic evidence above; clinician review is required.
        </div>
      )}
    </Card>
  );
}

export default function Triage({ cases, casesError, refresh, selectedUid, setSelectedUid, query, searchActive, searchBusy, toast, onDecision, canDecide, canAssess, canExplainAcuity, presentation, serverTotal, hasMore, onLoadMore, loadingMore }) {
  const [assessments, setAssessments] = useState({});
  const [assessState, setAssessState] = useState({});
  const [detail, setDetail] = useState(null);
  const [modal, setModal] = useState(null);
  const [ovCat, setOvCat] = useState(3);
  const [reason, setReason] = useState("");
  const [escTo, setEscTo] = useState("ed_doctor");
  const [infoFields, setInfoFields] = useState([]);
  const [visibleCount, setVisibleCount] = useState(30);
  const busyRef = useRef(false);
  const explicitSelectRef = useRef(false);
  const selectExplicit = (uid) => { explicitSelectRef.current = true; setSelectedUid(uid); };

  const queue = useMemo(() => {
    const rows = (cases || []).filter(isQueueRow);
    const arrivedAt = (c) => String(c.queue_metadata?.intime || c.queue_metadata?.arrival_time || c.queue_metadata?.arrival_time_utc || "");
    const arrivedMs = (c) => {
      const raw = arrivedAt(c);
      if (!raw) return Number.POSITIVE_INFINITY;
      const n = Date.parse(raw.replace(" ", "T"));
      return Number.isNaN(n) ? Number.POSITIVE_INFINITY : n;
    };
    // The model estimate can colour a row, but it must not reshuffle the queue.
    return rows.map((c, i) => ({ c, i }))
      .sort((a, b) => (arrivedMs(a.c) - arrivedMs(b.c)) || a.i - b.i)
      .map((row) => row.c);
  }, [cases]);
  const visible = queue.slice(0, visibleCount);
  const sel = useMemo(() => queue.find((c) => c.case_uid === selectedUid) || null, [queue, selectedUid]);

  useEffect(() => { setVisibleCount(30); }, [searchActive, query]);

  useEffect(() => { if (!selectedUid && queue.length) setSelectedUid(queue[0].case_uid); }, [queue, selectedUid, setSelectedUid]);

  const loadDetail = (uid) => api.getCase(uid).then(setDetail).catch(() => setDetail(sel));
  const runAssess = (uid, force = false) => {
    // Opening a case runs the REAL (audited) assessment: this is the clinician
    // consulting the model on the selected patient, which is recorded.
    if (!force && assessState[uid] === "loading") return;
    setAssessState((s) => ({ ...s, [uid]: "loading" }));
    api.runAssessment(uid)
      .then((a) => { setAssessments((m) => ({ ...m, [uid]: a })); setAssessState((s) => ({ ...s, [uid]: "done" })); })
      .catch((e) => setAssessState((s) => ({ ...s, [uid]: e.detail || e.message })));
  };

  useEffect(() => {
    let dead = false;
    setDetail(null);
    if (!sel) return;
    const wasExplicit = explicitSelectRef.current;
    explicitSelectRef.current = false;
    api.getCase(sel.case_uid).then((d) => { if (!dead) setDetail(d); }).catch(() => { if (!dead) setDetail(sel); });
    if (wasExplicit) {
      // The clinician explicitly opened this patient — run the real, audited
      // assessment (recording that the model was consulted on this case).
      runAssess(sel.case_uid);
    } else if (!assessments[sel.case_uid] && assessState[sel.case_uid] === undefined) {
      // Auto-landing / auto-advance: use the non-auditing preview so no
      // workflow-run record is created for a case the clinician did not pick.
      setAssessState((s) => ({ ...s, [sel.case_uid]: "previewing" }));
      api.previewAssessment(sel.case_uid)
        .then((a) => {
          if (!dead) {
            setAssessments((m) => ({ ...m, [sel.case_uid]: a }));
            setAssessState((s) => s[sel.case_uid] === "previewing" ? { ...s, [sel.case_uid]: "done" } : s);
          }
        })
        .catch(() => { if (!dead) setAssessState((s) => s[sel.case_uid] === "previewing" ? { ...s, [sel.case_uid]: "preview_failed" } : s); });
    }
    return () => { dead = true; };
  }, [sel?.case_uid]);

  const a = sel ? assessments[sel.case_uid] : null;
  const selectedAssessState = sel ? assessState[sel.case_uid] : null;
  const selectedAssessError = selectedAssessState === "preview_failed"
    ? "The estimate preview did not complete. Run the model estimate again."
    : selectedAssessState && !["loading", "done", "previewing"].includes(selectedAssessState) ? selectedAssessState : null;
  const selectedAssessLoading = selectedAssessState === "loading" || selectedAssessState === "previewing";
  const selectedLoadingLabel = selectedAssessState === "loading"
    ? "Running validation, rules, model prediction, and safety review..."
    : "Preparing the model estimate...";
  const sysPred = a?.final_category ?? (a?.predicted_acuity != null ? String(a.predicted_acuity) : null);
  const advance = () => { const i = queue.findIndex((c) => c.case_uid === sel?.case_uid); const nxt = queue[i + 1] || queue[0]; setSelectedUid(nxt && nxt.case_uid !== sel?.case_uid ? nxt.case_uid : null); };

  /* decidedAcuity is passed EXPLICITLY by each caller and forwarded to
     onDecision for optimistic queue colouring. It is deliberately not derived
     from the toast text: the authoritative value is the acuity the backend
     persists on the case, and a display string must never be load-bearing. */
  const doDecision = async (fn, okTitle, okBody, decidedAcuity = null) => {
    if (busyRef.current) return; busyRef.current = true;
    const uid = sel?.case_uid;
    try {
      await fn();
      toast(okTitle, okBody);
      if (uid && decidedAcuity != null) onDecision?.(uid, decidedAcuity);
      setModal(null); setReason(""); advance(); refresh();
    }
    catch (e) { toast("Action failed", e.detail || e.message, "err"); }
    finally { busyRef.current = false; }
  };

  const onReassessDone = (result) => {
    setModal(null);
    /* The backend merged the new observations into workflow state; the advisory
       and detail must be recomputed against them. */
    setAssessments((m) => { const n = { ...m }; delete n[sel.case_uid]; return n; });
    runAssess(sel.case_uid, true);
    loadDetail(sel.case_uid);
    refresh();
    toast("Reassessment recorded", `${patientWithEncounter(sel)}: ${result.change_summary || "advisory refreshed on the updated observations."}`);
  };

  return (
    <div style={{ display: "flex", gap: 16, height: "100%", minHeight: 0 }}>
      <div style={{ width: 296, flexShrink: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "2px 2px 10px" }}>
          <div style={{ fontSize: 17, fontWeight: 800 }}>{searchActive ? "Search results" : "Unreviewed queue"}</div>
          {searchBusy && <span style={{ fontFamily: T.mono, fontSize: 12, color: T.slate }}>searching…</span>}
        </div>
        <div style={{ overflowY: "auto", paddingRight: 4, flex: 1 }}>
          {casesError && <ErrorNote>{casesError}</ErrorNote>}
          {visible.map((c) => <QueueCard key={c.case_uid} c={c} selected={c.case_uid === selectedUid} prio={priorityFromCategory(assessments[c.case_uid]?.final_category)} onClick={() => selectExplicit(c.case_uid)} />)}
          {visibleCount < queue.length && (
            <Btn kind="quiet" style={{ width: "100%", marginBottom: 8 }} onClick={() => setVisibleCount((n) => n + 30)}>Show 30 more</Btn>
          )}
          {!searchActive && visibleCount >= queue.length && hasMore && (
            <Btn kind="quiet" style={{ width: "100%", marginBottom: 8 }} disabled={loadingMore} onClick={onLoadMore}>{loadingMore ? "Loading…" : "Load more patients"}</Btn>
          )}
          {!casesError && queue.length === 0 && !searchBusy && <EmptyState>{searchActive ? "No patients match that search." : "Queue clear. New arrivals appear here automatically."}</EmptyState>}
        </div>
      </div>
      <Card style={{ flex: 1, minWidth: 0, padding: 32, overflowY: "auto" }}>
        <CaseDetail c={detail} presentation={presentation} />
      </Card>
      <div style={{ width: 330, flexShrink: 0, overflowY: "auto", paddingRight: 2 }}>
        <AdvisoryPanel c={sel} assessment={a} loading={selectedAssessLoading} loadingLabel={selectedLoadingLabel} error={selectedAssessError}
          canDecide={canDecide} canAssess={canAssess} canExplainAcuity={canExplainAcuity}
          onAccept={() => doDecision(() => decisions.accept(sel.case_uid, { systemPrediction: sysPred }), `${acuityLabel(priorityFromCategory(sysPred), "Acuity —")} logged`, `${patientWithEncounter(sel)} moved to the review queue.`, priorityFromCategory(sysPred))}
          onOverride={() => { setOvCat(priorityFromCategory(sysPred) || 3); setModal("override"); }}
          onEscalate={() => setModal("escalate")}
          onRequestInfo={() => { setInfoFields([]); setModal("info"); }}
          onReassess={() => setModal("reassess")}
          onRetry={() => runAssess(sel.case_uid, true)} />
        {sel && <MultiAgentExplanation caseUid={sel.case_uid} canExplain={canExplainAcuity} toast={toast} />}
      </div>

      {modal === "override" && sel && (
        <Modal title={`Override advisory — ${patientWithEncounter(sel)}`} onClose={() => setModal(null)}>
          <Eyebrow style={{ marginBottom: 8 }}>Set acuity</Eyebrow>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 7, marginBottom: 14 }}>
            {[1, 2, 3, 4, 5].map((cN) => (
              <button key={cN} onClick={() => setOvCat(cN)} style={{ fontFamily: T.mono, fontSize: 17, fontWeight: 700, padding: "11px 0", borderRadius: 9, cursor: "pointer", border: `2px solid ${ovCat === cN ? MTS[cN].colour : T.border}`, background: ovCat === cN ? MTS[cN].colour : T.surface, color: ovCat === cN ? MTS[cN].text : T.ink }}>{cN}</button>
            ))}
          </div>
          <div style={{ fontSize: 12.5, color: T.slate, marginBottom: 14 }}>{MTS[ovCat].name} · target {MTS[ovCat].target}{ovCat === priorityFromCategory(sysPred) ? " · matches advisory" : ""}</div>
          <Eyebrow style={{ marginBottom: 8 }}>Clinical reason (required, recorded in the audit trail)</Eyebrow>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} placeholder="e.g. Pain settled after analgesia; repeat observations within normal limits." style={{ width: "100%", boxSizing: "border-box", fontFamily: T.font, fontSize: 13.5, padding: 11, borderRadius: 9, border: `1px solid ${T.border}`, resize: "vertical", marginBottom: 16 }} />
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <Btn kind="quiet" onClick={() => setModal(null)}>Cancel</Btn>
            <Btn disabled={!reason.trim() || ovCat === priorityFromCategory(sysPred)} onClick={() => doDecision(() => decisions.override(sel.case_uid, { systemPrediction: sysPred, decision: `${MTS[ovCat].name} (acuity ${ovCat})`, reason: reason.trim() }), `Override logged — Acuity ${ovCat}`, `${patientWithEncounter(sel)} moved to the review queue with your reason.`, ovCat)}>Log override to Acuity {ovCat}</Btn>
          </div>
        </Modal>
      )}
      {modal === "escalate" && sel && (
        <Modal title={`Escalate ${patientWithEncounter(sel)} for senior review`} onClose={() => setModal(null)}>
          <Eyebrow style={{ marginBottom: 8 }}>Escalate to</Eyebrow>
          <div style={{ position: "relative", marginBottom: 14 }}>
            <select value={escTo} onChange={(e) => setEscTo(e.target.value)} style={{ width: "100%", appearance: "none", fontFamily: T.font, fontSize: 14, padding: "10px 34px 10px 12px", borderRadius: 9, border: `1px solid ${T.border}`, background: T.surface }}>
              <option value="ed_doctor">ED Doctor (on-call senior)</option>
              <option value="clinical_supervisor">Clinical Supervisor</option>
            </select>
            <ChevronDown size={15} style={{ position: "absolute", right: 12, top: 12, color: T.grey500, pointerEvents: "none" }} />
          </div>
          <Eyebrow style={{ marginBottom: 8 }}>Reason for escalation</Eyebrow>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} placeholder="e.g. Possible ACS — requesting senior review before the acuity is finalised." style={{ width: "100%", boxSizing: "border-box", fontFamily: T.font, fontSize: 13.5, padding: 11, borderRadius: 9, border: `1px solid ${T.border}`, resize: "vertical", marginBottom: 16 }} />
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <Btn kind="quiet" onClick={() => setModal(null)}>Cancel</Btn>
            <Btn disabled={!reason.trim()} onClick={() => doDecision(() => decisions.escalate(sel.case_uid, { systemPrediction: sysPred, toRole: escTo, reason: reason.trim() }), "Escalation sent", `${patientWithEncounter(sel)} is awaiting senior review — the target role has been notified.`)}>Send escalation</Btn>
          </div>
        </Modal>
      )}
      {modal === "info" && sel && (
        <Modal title={`Request more information — ${patientWithEncounter(sel)}`} onClose={() => setModal(null)}>
          <Eyebrow style={{ marginBottom: 8 }}>What is missing before a decision can be made?</Eyebrow>
          <div style={{ display: "flex", flexDirection: "column", gap: 7, marginBottom: 14 }}>
            {["Repeat vital signs", "Pain reassessment after analgesia", "Collateral history", "ECG", "Blood glucose"].map((f) => (
              <label key={f} style={{ display: "flex", gap: 9, alignItems: "center", fontSize: 13.5, cursor: "pointer" }}>
                <input type="checkbox" checked={infoFields.includes(f)} onChange={(e) => setInfoFields((xs) => e.target.checked ? [...xs, f] : xs.filter((x) => x !== f))} /> {f}
              </label>
            ))}
          </div>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} placeholder="Optional note for the record…" style={{ width: "100%", boxSizing: "border-box", fontFamily: T.font, fontSize: 13.5, padding: 11, borderRadius: 9, border: `1px solid ${T.border}`, resize: "vertical", marginBottom: 16 }} />
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <Btn kind="quiet" onClick={() => setModal(null)}>Cancel</Btn>
            <Btn disabled={!infoFields.length && !reason.trim()} onClick={() => doDecision(() => decisions.requestInfo(sel.case_uid, { fields: infoFields, comment: reason.trim() }), "Information requested", `${patientWithEncounter(sel)} is parked pending the requested details — record them with "Record requested information" when they arrive.`)}>Log request</Btn>
          </div>
        </Modal>
      )}
      {modal === "reassess" && sel && <ReassessModal c={detail || sel} onClose={() => setModal(null)} onDone={onReassessDone} toast={toast} />}
    </div>
  );
}
