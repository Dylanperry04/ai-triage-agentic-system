import React, { useState, useEffect, useImperativeHandle, useMemo, useRef } from "react";
import { CheckCircle2, ArrowUpRight, Ambulance, Footprints, Activity, Sparkles, UploadCloud, FileText, Trash2, Undo2 } from "lucide-react";
import { T, MTS, catOf, priorityFromCategory, toC, patientLabel, encounterLabel, patientWithEncounter, acuityLabel } from "../theme.js";
import { Card, Btn, Eyebrow, ConfBar, Modal, Pill, Spinner, EmptyState, ErrorNote } from "../atoms.jsx";
import { api, decisions } from "../api.js";
import DiscriminatorEvidence from "../DiscriminatorEvidence.jsx";
import MTSChecklist from "../MTSChecklist.jsx";

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
const CLOSED_CASE_STATES = new Set(["discharged", "closed", "case_closed"]);
export const isObservationRow = (c) => {
  const ws = c.workflow_state || {};
  const cs = String(ws.case_status || "").toLowerCase();
  return !CLOSED_CASE_STATES.has(cs) && !ws.discharged_at && !ws.notifications_suppressed;
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
      className="clickable" style={{ background: selected ? T.green50 : T.surface, borderTop: `1px solid ${selected ? T.green500 : T.borderSoft}`, borderRight: `1px solid ${selected ? T.green500 : T.borderSoft}`, borderBottom: `1px solid ${selected ? T.green500 : T.borderSoft}`, borderLeft: `5px solid ${cat.colour}`, borderRadius: 12, padding: "9px 11px", marginBottom: 7, boxShadow: selected ? "0 4px 14px rgba(2,89,76,0.14)" : "0 1px 5px rgba(33,43,50,0.08)" }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
        <span style={{ fontSize: 13.5, fontWeight: 800, color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{patient}</span>
        {encounter && encounter !== patient && <span style={{ fontFamily: T.mono, fontSize: 12, fontWeight: 700, color: T.slate, whiteSpace: "nowrap" }}>{encounter}</span>}
      </div>
      <div style={{ fontSize: 14, fontWeight: 700, marginTop: 5, lineHeight: 1.35, textTransform: "capitalize", color: T.ink }}>{c.triage?.chiefcomplaint || "Chief complaint withheld for this role"}</div>
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

export function CaseDetail({ c, presentation, canReviewAi = true, permissions, onChecklistStateChange, onGeneralObservations, onChecklistSaved, assessment, checklistActions }) {
  if (!c) return <EmptyState>Select a patient from the queue to begin triage.</EmptyState>;
  const t = c.triage || {}; const d = c.demographics || {};
  const ws = c.workflow_state || {};
  const tempC = toC(t.temperature, t.temperature_unit);
  const info = infoState(c);
  const encounter = encounterLabel(c);
  return (
    <div className="fade-up" key={c.case_uid} style={{ padding: "4px 2px" }}>
      <div className="triage-case-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 21, fontWeight: 800, lineHeight: 1.2 }}>{patientLabel(c)}</div>
          <div style={{ fontSize: 16, color: T.ink, marginTop: 5, lineHeight: 1.35, textTransform: "capitalize", fontWeight: 700 }}>{t.chiefcomplaint || "Clinical presentation"}</div>
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
      {c.is_curated_demo && <details style={{ marginTop: 6, fontSize: 12, color: T.slate }}><summary>Data provenance</summary>{c.demo_data_notice}</details>}
      <MTSChecklist key={c.case_uid} c={c} permissions={permissions} onSaved={onChecklistSaved} assessment={assessment} onStateChange={onChecklistStateChange} onGeneralObservations={onGeneralObservations} checklistActions={checklistActions} />
      {!permissions?.includes("can_record_mts_checklist") && permissions?.includes("can_update_vitals") && <ObservationActions c={c} onRecord={onGeneralObservations} />}
      {ws.latest_triage_updates_at && (
        <div style={{ fontSize: 12, color: T.green900, background: T.green50, border: "1px solid #CBEADF", borderRadius: 9, padding: "8px 11px", marginTop: 10 }}>
          Observations updated {String(ws.latest_triage_updates_at).replace("T", " ").slice(0, 16)} UTC{ws.information_response_by_role ? ` by ${String(ws.information_response_by_role).replace(/_/g, " ")}` : ""} — the values above reflect the latest recorded observations.{canReviewAi ? " Check the model estimate against these observations before recording a decision." : ""}
        </div>
      )}
    </div>
  );
}

const probability = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
const findingLabel = (trace, id) => trace?.executed_rules?.find(rule => rule.id === id)?.label || id?.replaceAll("_", " ");

export function AdvisoryPanel({ c, assessment, loading, loadingLabel, canStartAssessment, error, onAccept, onOverride, onEscalate, onRequestInfo, onRetry, canRunAssessment, canAccept, canOverride, canEscalate, canRequestInfo, workflowRunId, checklistDirty = false }) {
  if (!c) return null;
  if (loading) return (
    <Card style={{ padding: 16 }}>
      <Eyebrow>Triage recommendation</Eyebrow>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
        <Spinner />
        <div style={{ fontSize: 13.5, color: T.slate, lineHeight: 1.45 }}>{loadingLabel || "Preparing the acuity estimate..."}</div>
      </div>
      {canStartAssessment && !checklistDirty && <Btn kind="quiet" onClick={onRetry} style={{ marginTop: 12 }}>Run assessment</Btn>}
    </Card>
  );
  if (error) return (
    <Card style={{ padding: 16 }}>
      <Eyebrow style={{ marginBottom: 10 }}>Triage recommendation</Eyebrow>
      <ErrorNote>Assessment unavailable: {error}</ErrorNote>
      {canRunAssessment && <Btn kind="quiet" style={{ width: "100%", marginTop: 10 }} onClick={onRetry}>Try assessment again</Btn>}
    </Card>
  );
  if (!assessment) return (
    <Card style={{ padding: 16 }}>
      <Eyebrow>Triage recommendation</Eyebrow>
      <div style={{ fontSize: 13, color: T.slate, lineHeight: 1.5, marginTop: 8 }}>No estimate has been calculated for this patient yet.</div>
      {canRunAssessment && <Btn kind="quiet" style={{ width: "100%", marginTop: 12 }} onClick={onRetry}>Run model estimate</Btn>}
    </Card>
  );
  const prio = priorityFromCategory(assessment.final_category ?? assessment.predicted_acuity);
  const cat = catOf(prio);
  const probs = assessment.class_probabilities || assessment.mimic_acuity_probabilities || null;
  // A policy or safety override can change the displayed class. The modal
  // confidence must never be relabelled as that new class's probability.
  const conf = prio == null ? null : probability(assessment.assigned_acuity_probability)
    ?? probability(probs?.[prio])
    ?? (assessment.argmax_acuity === prio ? probability(assessment.top_class_confidence ?? assessment.confidence) : null);
  const mlDown = !assessment.ml_prediction_available;
  const anyDecision = canAccept || canOverride || canEscalate || canRequestInfo;
  const trace = assessment.mts_assessment;
  const pathway = assessment.discriminator_evidence?.pathway;
  const mtsRaised = pathway?.kind === "structured_mts" ? pathway.affects_displayed_acuity : trace?.escalation_applied;
  const beforeMts = pathway?.kind === "structured_mts" ? pathway.advisory_before : trace?.advisory_before_mts;
  const unresolved = trace?.unresolved_earlier_discriminators || trace?.unresolved_discriminators;
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
              <div style={{ fontSize: 12.5, color: T.slate }}>{prio ? `Target to first physician contact: ${MTS[prio].target}` : "No acuity assigned"}</div>
            </div>
          </div>
          <div role="status" style={{ fontSize: 12.5, marginTop: 10 }}>
            <b>{trace?.selected_flowchart?.label || "MTS flowchart not confirmed"}</b><br />
            {trace?.assessment_complete ? "MTS category assessed" : "MTS assessment incomplete"}
            {trace?.conflicting_observations?.length > 0 && " · conflicting observations"}
            {unresolved?.length > 0 && ` · ${unresolved.length} earlier findings unresolved`}
            {trace?.determining_discriminator && <div>Determining finding: {findingLabel(trace, trace.determining_discriminator)}</div>}
            {!trace?.assessment_complete && trace?.earliest_known_match && <div>Earliest known match: {findingLabel(trace, trace.earliest_known_match)}</div>}
            {checklistDirty && <b style={{ display: "block", color: T.red }}>Unsaved checklist changes. Save and reassess before accepting.</b>}
          </div>
          {mlDown && <div style={{ marginTop: 12, fontSize: 12.5, color: "#8A4B00", background: T.yellow50, border: "1px solid #EEDD9A", borderRadius: 8, padding: "8px 11px", lineHeight: 1.45 }}>{assessment.model_note || assessment.ml_prediction_error || "ML prediction unavailable in this profile."}</div>}
          {mtsRaised && <div style={{ marginTop: 8, fontSize: 12.5 }}>MTS findings {beforeMts == null ? `set the advisory to ${acuityLabel(prio)}` : `raised the advisory from ${acuityLabel(beforeMts)} to ${acuityLabel(prio)}`}.</div>}
          {probs && (
            <details style={{ marginTop: 14 }}>
              <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 12.5 }}>Model details</summary>
              {conf != null && <div><Eyebrow>Probability of {acuityLabel(prio)}</Eyebrow><span>{(conf * 100).toFixed(1)}%</span></div>}
              <Eyebrow>Model-estimated probabilities</Eyebrow>
              <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 7 }}>
                {Object.entries(probs).filter(([k, p]) => /^[1-5]$/.test(k) && probability(p) != null).sort((a, b) => Number(a[0]) - Number(b[0])).map(([k, p]) => {
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
            </details>
          )}
        </div>
      </Card>
      {anyDecision && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
          {!workflowRunId && canRunAssessment && <Btn kind="dark" disabled={checklistDirty} onClick={onRetry}>Run assessment</Btn>}
          {canAccept && <Btn kind="dark" onClick={onAccept} style={{ padding: "8px 10px", fontSize: 12.5 }} disabled={!prio || !workflowRunId || checklistDirty}><CheckCircle2 size={13} style={{ verticalAlign: -3, marginRight: 6 }} />{assessment.mts_assessment?.assessment_complete ? `Accept & log ${acuityLabel(prio, "Acuity —")}` : "Record assessment exception…"}</Btn>}
          {(canOverride || canEscalate) && <div style={{ display: "grid", gridTemplateColumns: canOverride && canEscalate ? "1fr 1fr" : "1fr", gap: 8 }}>
            {canOverride && <Btn kind="quiet" disabled={!workflowRunId || checklistDirty} onClick={onOverride} style={{ padding: "7px 9px", fontSize: 12.5 }}>Override…</Btn>}
            {canEscalate && <Btn kind="quiet" disabled={!workflowRunId} onClick={onEscalate} style={{ padding: "7px 9px", fontSize: 12.5 }}><ArrowUpRight size={13} style={{ verticalAlign: -2, marginRight: 5 }} />Escalate…</Btn>}
          </div>}
          {canRequestInfo && <Btn kind="quiet" disabled={!workflowRunId} onClick={onRequestInfo} style={{ padding: "7px 9px", fontSize: 12.5 }}><Undo2 size={13} style={{ verticalAlign: -2, marginRight: 6 }} />Request more information…</Btn>}
        </div>
      )}
      <DiscriminatorEvidence evidence={assessment.discriminator_evidence} />
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

export function ReassessModal({ c, onClose, onDone, toast, allowVitals = true }) {
  const [vals, setVals] = useState({});
  const [complaint, setComplaint] = useState("");
  const [context, setContext] = useState("");
  const [scanFiles, setScanFiles] = useState([]);
  const supportingInput = useRef(null);
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
    const advisoryDeteriorated = result.advisory_deteriorated ?? result.change === "escalation";
    const reassessmentTarget = result.review_target_role === "ed_doctor" ? "ED Doctor" : "Triage Nurse";
    const comparisonAvailable = result.ai_advisory_visible !== false && (from != null || to != null);
    return (
      <Modal title={`Reassessment recorded — ${patientWithEncounter(c)}`} onClose={() => onDone(result)} width={620}>
        {!comparisonAvailable && (
          <div style={{ fontSize: 12.5, color: T.slate, background: "#F5F7F7", border: `1px solid ${T.borderSoft}`, borderRadius: 8, padding: "9px 12px", marginBottom: 12, lineHeight: 1.5 }}>
            {result.result_summary || "The updated observations are saved and timestamped. The reviewing clinician has been notified."}
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
                <div key={i} style={{ fontSize: 12.5, fontFamily: T.mono, color: T.slate }}>{v.vital || v.field || v.key}: {v.previous ?? v.from ?? "—"} → {v.updated ?? v.new ?? v.to ?? "—"} {v.unit || ""}</div>
              ))}
            </div>
          </div>
        )}
        {advisoryDeteriorated && <div style={{ marginTop: 12, fontSize: 12.5, color: T.red, background: T.red50, border: "1px solid #EFC5D1", borderRadius: 8, padding: "8px 11px", lineHeight: 1.45 }}>The model advisory moved to a higher priority. The updated observations have returned to the {reassessmentTarget} for clinical review; this change does not open a new escalation automatically.</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
          <Btn onClick={() => onDone(result)}>{result.ai_advisory_visible === false ? "Done" : "Done — refresh advisory"}</Btn>
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
      {allowVitals ? <Eyebrow style={{ marginBottom: 8 }}>Updated vital signs (leave blank if unchanged)</Eyebrow> : <p style={{ fontSize: 12 }}>Record measurements in the flowchart checklist. This form updates the complaint or supporting information.</p>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10, marginBottom: 8 }}>
        {(allowVitals ? REASSESS_FIELDS : []).map((f) => (
          <label key={f.key} style={{ fontSize: 11, fontWeight: 700, color: T.slate, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
            {f.label} <span style={{ fontWeight: 500, color: T.grey500 }}>({f.unit})</span>
            <input inputMode="decimal" value={vals[f.key] ?? ""} onChange={(e) => setVals((v) => ({ ...v, [f.key]: e.target.value }))}
              style={{ width: "100%", boxSizing: "border-box", fontFamily: T.mono, fontSize: 13.5, padding: "8px 9px", borderRadius: 8, border: `1px solid ${problems.some((p) => p.key === f.key) ? T.red : T.border}` }} />
          </label>
        ))}
      </div>
      {problems.length > 0 && <div style={{ fontSize: 12, color: T.red, marginBottom: 8 }}>{problems.map((p) => `${p.label} must be ${p.min}–${p.max} ${p.unit}`).join(" · ")}</div>}
      <Eyebrow style={{ margin: "8px 0" }}>Updated complaint (optional)</Eyebrow>
      <input aria-label="Updated complaint" value={complaint} maxLength={240} onChange={(e) => setComplaint(e.target.value)} placeholder="Only if the presenting complaint has changed" style={{ width: "100%", boxSizing: "border-box", fontFamily: T.font, fontSize: 13.5, padding: "9px 11px", borderRadius: 9, border: `1px solid ${T.border}`, marginBottom: 10 }} />
      <Eyebrow style={{ marginBottom: 8 }}>Clinical context / requested information (optional)</Eyebrow>
      <textarea aria-label="Clinical context or requested information" value={context} maxLength={1000} onChange={(e) => setContext(e.target.value)} rows={2} placeholder="e.g. Collateral history from family; ECG shows sinus tachycardia." style={{ width: "100%", boxSizing: "border-box", fontFamily: T.font, fontSize: 13.5, padding: 10, borderRadius: 9, border: `1px solid ${T.border}`, resize: "vertical", marginBottom: 10 }} />
      <Eyebrow style={{ marginBottom: 8 }}>Supporting MRI / X-ray / document</Eyebrow>
      <div style={{ border: `1px dashed ${T.border}`, borderRadius: 10, padding: 12, marginBottom: 12, background: "#FAFBFB" }}>
        <input ref={supportingInput} id={`supporting-upload-${c.case_uid}`} type="file" multiple accept="image/*,.dcm,.dicom,application/dicom,application/pdf" onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} style={{ display: "none" }} />
        <button type="button" onClick={() => supportingInput.current?.click()} className="clickable" style={{ display: "inline-flex", alignItems: "center", gap: 8, fontFamily: T.font, fontSize: 13.5, fontWeight: 700, color: T.green700, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 9, padding: "9px 13px" }}>
          <UploadCloud size={16} /> Upload scan or document
        </button>
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

/* Explain the exact displayed run. Older responses may contain generated text. */
export function MultiAgentExplanation({ caseUid, assessmentKey, canExplain, toast }) {
  const [state, setState] = useState("idle");   // idle | loading | done | error
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const requestVersion = useRef(0);

  // Reset when the selected case changes.
  useEffect(() => {
    requestVersion.current += 1;
    setState("idle"); setResult(null); setErr(null);
    return () => { requestVersion.current += 1; };
  }, [caseUid, assessmentKey]);

  if (!canExplain) return null;

  const run = () => {
    if (state === "loading") return;
    const version = ++requestVersion.current;
    setState("loading"); setErr(null);
    api.multiagentExplainCase(caseUid, undefined, assessmentKey)
      .then((r) => {
        if (requestVersion.current !== version) return;
        if (assessmentKey && r?.workflow_run_id !== assessmentKey) {
          setErr("The explanation does not match the displayed assessment. Run a new assessment and try again.");
          setState("error");
          return;
        }
        if (!["PASS", "NOT_CONFIGURED", "SAFETY_FAIL"].includes(r?.status)) {
          setErr(r?.reason || "The explanation service did not return a usable result.");
          setState("error");
          return;
        }
        setResult(r); setState("done");
        if (r.status === "SAFETY_FAIL") toast("Explanation withheld", "The safety filter blocked the generated explanation.", "err");
      })
      .catch((e) => {
        if (requestVersion.current !== version) return;
        setErr(e.detail?.reason || e.detail || e.message); setState("error");
      });
  };

  const notConfigured = result && result.status === "NOT_CONFIGURED";
  const passed = result && result.status === "PASS";
  const recordedEvidence = result?.explanation_source === "recorded_evidence";

  return (
    <Card style={{ marginTop: 12, padding: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <Sparkles size={15} style={{ color: T.green700 }} />
        <div style={{ fontSize: 13.5, fontWeight: 700 }}>Decision explanation</div>
      </div>

      {state === "idle" && (
        <Btn kind="quiet" style={{ width: "100%" }} onClick={run}>Explain this decision</Btn>
      )}

      {state === "loading" && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: T.slate, fontSize: 12.5, padding: "6px 0" }}>
          <Spinner /> Loading the explanation…
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

      {passed && <>
        <p style={{ fontSize: 12, color: T.slate }}>{recordedEvidence ? "Based on the saved assessment" : "Generated explanation · review against the recorded findings"}</p>
        <div style={{ fontSize: 13, color: T.ink, lineHeight: 1.6, background: T.green50, border: "1px solid #CBEADF", borderRadius: 9, padding: "11px 13px" }}>{result.final_explanation}</div>
        {recordedEvidence && result.evidence_links?.length > 0 && <details style={{ marginTop: 8, fontSize: 12 }}>
          <summary>Evidence references</summary>
          <ul>{result.evidence_links.map(({ claim, path }) => <li key={`${claim}:${path}`}>{claim} <code style={{ overflowWrap: "anywhere" }}>{path}</code></li>)}</ul>
        </details>}
        <Btn kind="ghost" style={{ width: "100%", marginTop: 10, fontSize: 12 }} onClick={() => { setState("idle"); setResult(null); }}>Run again</Btn>
      </>}

      {result && result.status === "SAFETY_FAIL" && (
        <div style={{ fontSize: 12.5, color: T.red, background: T.red50, border: "1px solid #EFC5D1", borderRadius: 8, padding: "9px 11px", lineHeight: 1.5 }}>
          The generated explanation did not pass the safety filter and was withheld. Rely on the deterministic evidence above; clinician review is required.
        </div>
      )}
    </Card>
  );
}

function ObservationActions({ c, onRecord }) {
  if (!c) return null;
  const requested = infoState(c) === "requested";
  return (
    <Card style={{ padding: 16 }}>
      <Eyebrow>ED nursing observations</Eyebrow>
      <div style={{ fontSize: 13, color: T.slate, lineHeight: 1.55, marginTop: 8 }}>
        {requested ? "The clinical team has requested more information for this case." : "Record or repeat the patient's observations. The triage decision remains with the Triage Nurse."}
      </div>
      <Btn kind="dark" style={{ width: "100%", marginTop: 12 }} onClick={onRecord}>
        <Activity size={14} style={{ verticalAlign: -2, marginRight: 6 }} />{requested ? "Provide requested information" : "Record / update vitals"}
      </Btn>
    </Card>
  );
}

export default function Triage({ cases, casesError, casesLoading = false, refresh, selectedUid, setSelectedUid, query, searchActive, searchBusy, toast, onDecision, permissions = [], canExplainAcuity, presentation, serverTotal, hasMore, onLoadMore, loadingMore, navigationGuard }) {
  const [assessments, setAssessments] = useState({});
  const [checklistState, setChecklistState] = useState({ dirty: false, hasChart: false });
  const [assessState, setAssessState] = useState({});
  const [detail, setDetail] = useState(null);
  const [modal, setModal] = useState(null);
  const [pendingNavigation, setPendingNavigation] = useState(null);
  const checklistActions = useRef(null);
  const [ovCat, setOvCat] = useState(3);
  const [reason, setReason] = useState("");
  const [infoFields, setInfoFields] = useState([]);
  const [visibleCount, setVisibleCount] = useState(30);
  const busyRef = useRef(false);
  const assessmentRequests = useRef({});
  const observedRevisions = useRef({});
  const detailRequest = useRef(0);
  const selectedUidRef = useRef(selectedUid);
  selectedUidRef.current = selectedUid;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; assessmentRequests.current = {}; detailRequest.current += 1; };
  }, []);
  const permissionSet = useMemo(() => new Set(permissions), [permissions]);
  const canRunAssessment = permissionSet.has("can_run_triage_assessment");
  const canAccept = permissionSet.has("can_accept_acuity");
  const canOverride = permissionSet.has("can_override_acuity");
  const canRequestInfo = permissionSet.has("can_request_information");
  const canEscalate = permissionSet.has("can_escalate_case");
  const canUpdateVitals = permissionSet.has("can_update_vitals") || permissionSet.has("can_record_vitals") || permissionSet.has("can_provide_requested_information");
  const isObservationOnlyView = canUpdateVitals && !canRunAssessment;
  const requestNavigation = (navigate, discardLabel = "Discard and leave", sameViewCaseUid = null) => {
    if ((checklistState.dirty || checklistState.busy) && (!sameViewCaseUid || sameViewCaseUid !== selectedUid)) {
      setPendingNavigation({ navigate, discardLabel });
    } else {
      navigate();
    }
  };
  useImperativeHandle(navigationGuard, () => requestNavigation);
  const saveThenNavigate = async () => {
    const pending = pendingNavigation;
    if (await checklistActions.current?.save(false)) {
      setPendingNavigation(null);
      pending.navigate();
    } else {
      setPendingNavigation({ ...pending, failed: true });
    }
  };
  const openPatient = uid => {
    setChecklistState({ dirty: false, hasChart: false });
    if (uid === selectedUid) return;
    setSelectedUid(uid);
  };
  const selectExplicit = uid => {
    if (uid === selectedUid && checklistState.dirty) { toast("Unsaved checklist", "Save and reassess before running another estimate."); return; }
    requestNavigation(() => openPatient(uid), "Discard and switch patient");
  };

  const queue = useMemo(() => {
    const rows = (cases || []).filter(isObservationOnlyView ? isObservationRow : isQueueRow);
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
  }, [cases, isObservationOnlyView]);
  const visible = queue.slice(0, visibleCount);
  // Queue searches, remote workflow updates and refresh errors can hide a row.
  // Keep its editor mounted until the draft is saved or deliberately discarded.
  const sel = queue.find(c => c.case_uid === selectedUid)
    || ((checklistState.dirty || checklistState.busy) && detail?.case_uid === selectedUid ? detail : null);

  useEffect(() => { setVisibleCount(30); }, [searchActive, query]);

  useEffect(() => { if (!selectedUid && queue.length) setSelectedUid(queue[0].case_uid); }, [queue, selectedUid, setSelectedUid]);

  const loadDetail = (uid, fallback = sel) => {
    const version = ++detailRequest.current;
    const current = () => mounted.current && selectedUidRef.current === uid && detailRequest.current === version;
    return api.getCase(uid).then((d) => { if (current()) setDetail(d); }).catch(() => { if (current()) setDetail(fallback); });
  };
  const requestAssessment = (uid, preview = false, force = false) => {
    // Page selection reads saved evidence or computes a non-auditing estimate.
    // Only the explicit assessment action creates a new recorded run.
    if (!canRunAssessment || (!force && ["loading", "previewing"].includes(assessmentRequests.current[uid]?.state))) return;
    if (!preview && assessmentRequests.current[uid]?.state === "loading") return;
    const request = { state: preview ? "previewing" : "loading" };
    assessmentRequests.current[uid] = request;
    const current = () => mounted.current && assessmentRequests.current[uid] === request;
    setAssessState((s) => ({ ...s, [uid]: request.state }));
    const pending = preview ? api.previewAssessment(uid) : api.runAssessment(uid);
    pending
      .then((result) => {
        if (!current()) return;
        request.state = "done";
        setAssessments((m) => ({ ...m, [uid]: result }));
        setAssessState((s) => ({ ...s, [uid]: "done" }));
      })
      .catch((e) => {
        if (!current()) return;
        request.state = "error";
        setAssessState((s) => ({ ...s, [uid]: preview ? "preview_failed" : e.detail || e.message }));
      });
  };
  const runAssess = (uid, force = false) => requestAssessment(uid, false, force);

  useEffect(() => {
    setDetail(current => current?.case_uid === sel?.case_uid ? current : null);
    detailRequest.current += 1;
    if (!sel) return;
    loadDetail(sel.case_uid, sel);
    const observedAt = sel.workflow_state?.latest_triage_updates_at || "";
    const previousObservedAt = observedRevisions.current[sel.case_uid];
    observedRevisions.current[sel.case_uid] = observedAt;
    const assessmentAt = Date.parse(assessments[sel.case_uid]?.assessment_timestamp_utc || "");
    const changedObservations = previousObservedAt !== undefined && previousObservedAt !== observedAt;
    const currentAssessmentIsNewer = Number.isFinite(assessmentAt) && assessmentAt >= Date.parse(observedAt);
    if (canRunAssessment && changedObservations && !currentAssessmentIsNewer) {
      // A different staff member may update observations while this screen is
      // open. Invalidate both cached and pending estimates before showing new
      // detail; a fresh preview still needs a logged run before any decision.
      setAssessments((m) => { const next = { ...m }; delete next[sel.case_uid]; return next; });
      requestAssessment(sel.case_uid, true, true);
      return () => { detailRequest.current += 1; };
    }
    const savedId = sel.workflow_state?.latest_workflow_run_id;
    if (canRunAssessment && (!assessments[sel.case_uid]
        || (savedId && savedId !== assessments[sel.case_uid]?.workflow_run_id))) {
      requestAssessment(sel.case_uid, true);
    }
    return () => { detailRequest.current += 1; };
  }, [sel?.case_uid, sel?.workflow_state?.updated_at_utc, sel?.workflow_state?.latest_triage_updates_at, sel?.workflow_state?.latest_workflow_run_id, canRunAssessment]);

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
  // A decision may refer only to the exact assessment currently displayed.
  const workflowRunId = a?.workflow_run_id || null;
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
      setModal(null); setReason("");
      // Urgent escalation remains available during a draft and does not discard it.
      if (!checklistState.dirty && !checklistState.busy && selectedUidRef.current === uid) advance();
      refresh();
    }
    catch (e) { toast("Action failed", e.detail || e.message, "err"); }
    finally { busyRef.current = false; }
  };

  const onReassessDone = (result) => {
    setModal(null);
    /* The backend merged the new observations into workflow state; the advisory
       and detail must be recomputed against them. */
    setAssessments((m) => { const n = { ...m }; delete n[sel.case_uid]; return n; });
    if (canRunAssessment) runAssess(sel.case_uid, true);
    loadDetail(sel.case_uid);
    refresh();
    toast("Observations recorded", `${patientWithEncounter(sel)}: ${result.result_summary || result.change_summary || "the reviewing clinician has been notified."}`);
  };

  return (
    <div className={`triage-workspace${isObservationOnlyView ? " observations-workspace" : ""}`} style={{ display: "flex", gap: 16, height: "100%", minHeight: 0 }}>
      <div className="triage-queue" style={{ width: 252, flexShrink: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "2px 2px 10px" }}>
          <div style={{ fontSize: 17, fontWeight: 800 }}>{searchActive ? "Search results" : isObservationOnlyView ? "Observations queue" : "Unreviewed queue"}</div>
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
          {!casesError && casesLoading && !searchBusy && <EmptyState>Loading patients…</EmptyState>}
          {!casesError && !casesLoading && queue.length === 0 && !searchBusy && <EmptyState>{searchActive ? "No patients match that search." : "Queue clear. New arrivals appear here automatically."}</EmptyState>}
        </div>
      </div>
      {!isObservationOnlyView && canRunAssessment && sel && <a className="triage-mobile-recommendation" href="#triage-recommendation">
        <span>{priorityFromCategory(sysPred) ? `${acuityLabel(sysPred)} · ${catOf(priorityFromCategory(sysPred)).name}` : selectedAssessLoading ? "Assessment loading" : "Assessment pending"}</span>
        <span>{checklistState.dirty ? "Unsaved checklist" : "View recommendation"}</span>
      </a>}
      <Card className="triage-detail" style={{ flex: 1, minWidth: 0, padding: 20, overflowY: "auto" }}>
        <CaseDetail c={detail?.case_uid === sel?.case_uid ? detail : null} presentation={presentation} canReviewAi={!isObservationOnlyView}
          permissions={permissions} assessment={a} checklistActions={checklistActions} onChecklistStateChange={setChecklistState} onGeneralObservations={() => setModal("reassess")} onChecklistSaved={(uid, result, reassess) => {
            if (selectedUidRef.current !== uid) return;
            observedRevisions.current[uid] = result.checklist.expected_observations_at;
            setAssessments(m => ({ ...m, [uid]: null }));
            loadDetail(uid); refresh?.();
            if (canRunAssessment && reassess) runAssess(uid, true);
          }} />
      </Card>
      {!isObservationOnlyView && <div id="triage-recommendation" className="triage-advisory" tabIndex={-1} style={{ width: 330, flexShrink: 0, overflowY: "auto", paddingRight: 2 }}>
        {canRunAssessment && <AdvisoryPanel c={sel} assessment={a} loading={selectedAssessLoading} loadingLabel={selectedLoadingLabel} canStartAssessment={selectedAssessState === "previewing"} error={selectedAssessError}
          checklistDirty={checklistState.dirty} canRunAssessment={canRunAssessment} canAccept={canAccept} canOverride={canOverride} canEscalate={canEscalate} canRequestInfo={canRequestInfo} workflowRunId={workflowRunId}
          onAccept={() => !a?.mts_assessment?.assessment_complete ? (setReason(""), setModal("assessment_exception")) : doDecision(() => decisions.accept(sel.case_uid, { workflowRunId, systemPrediction: sysPred }), `${acuityLabel(priorityFromCategory(sysPred), "Acuity —")} logged`, `${patientWithEncounter(sel)} moved to the review queue.`, priorityFromCategory(sysPred))}
          onOverride={() => { setOvCat(priorityFromCategory(sysPred) || 3); setModal("override"); }}
          onEscalate={() => setModal("escalate")}
          onRequestInfo={() => { setInfoFields([]); setModal("info"); }}
          onRetry={() => runAssess(sel.case_uid, true)} />}
        {canRunAssessment && sel && !selectedAssessLoading && workflowRunId && <MultiAgentExplanation key={`${sel.case_uid}:${workflowRunId}`} caseUid={sel.case_uid} assessmentKey={workflowRunId} canExplain={canExplainAcuity} toast={toast} />}
      </div>}

      {pendingNavigation && <Modal title="Discard unsaved checklist changes?" onClose={() => { if (!checklistState.busy) setPendingNavigation(null); }}>
        <p>Save your observations, discard this draft, or stay on this case.</p>
        {pendingNavigation.failed && <ErrorNote>Could not save the draft. Keep editing to see the checklist error.</ErrorNote>}
        {!checklistState.canSave && <p>Confirm the flowchart and provide a reason if it differs from the proposed chart before saving.</p>}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <Btn kind="quiet" disabled={checklistState.busy} onClick={() => setPendingNavigation(null)}>Keep editing</Btn>
          <Btn kind="quiet" disabled={checklistState.busy} onClick={() => { setPendingNavigation(null); pendingNavigation.navigate(); }}>{pendingNavigation.discardLabel}</Btn>
          <Btn disabled={checklistState.busy || !checklistState.canSave} onClick={saveThenNavigate}>{checklistState.busy ? "Saving observations…" : "Save and continue"}</Btn>
        </div>
      </Modal>}
      {modal === "assessment_exception" && sel && (
        <Modal title="Deliberate incomplete-assessment exception" onClose={() => setModal(null)}>
          <p>The checklist is incomplete, conflicting or outside the supported charts. This records acceptance of the hybrid advisory with an explicit exception; it does not mark MTS complete.</p>
          <label>Reason for accepting before MTS completion<textarea aria-label="Assessment exception reason" value={reason} maxLength={1000} rows={3} onChange={e => setReason(e.target.value)} style={{ width: "100%", boxSizing: "border-box" }} /></label>
          <Btn disabled={reason.trim().length < 5 || checklistState.dirty || !workflowRunId} onClick={() => doDecision(() => decisions.accept(sel.case_uid, { workflowRunId, systemPrediction: sysPred, exceptionReason: reason.trim() }), "Assessment exception recorded", patientWithEncounter(sel), priorityFromCategory(sysPred))}>Record exception and accept advisory</Btn>
        </Modal>
      )}
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
            <Btn disabled={!reason.trim() || !workflowRunId || ovCat === priorityFromCategory(sysPred)} onClick={() => doDecision(() => decisions.override(sel.case_uid, { workflowRunId, systemPrediction: sysPred, decision: `${MTS[ovCat].name} (acuity ${ovCat})`, finalAcuity: ovCat, reason: reason.trim() }), `Override logged — Acuity ${ovCat}`, `${patientWithEncounter(sel)} moved to the review queue with your reason.`, ovCat)}>Log override to Acuity {ovCat}</Btn>
          </div>
        </Modal>
      )}
      {modal === "escalate" && sel && (
        <Modal title={`Escalate ${patientWithEncounter(sel)} to the ED Doctor`} onClose={() => setModal(null)}>
          <div style={{ fontSize: 13, color: T.slate, background: "#F5F7F7", border: `1px solid ${T.borderSoft}`, borderRadius: 8, padding: "9px 11px", marginBottom: 14 }}>The ED Doctor is the final clinical escalation authority and will receive this case.</div>
          <Eyebrow style={{ marginBottom: 8 }}>Reason for escalation</Eyebrow>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} placeholder="e.g. Possible ACS — requesting senior review before the acuity is finalised." style={{ width: "100%", boxSizing: "border-box", fontFamily: T.font, fontSize: 13.5, padding: 11, borderRadius: 9, border: `1px solid ${T.border}`, resize: "vertical", marginBottom: 16 }} />
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <Btn kind="quiet" onClick={() => setModal(null)}>Cancel</Btn>
            <Btn disabled={!reason.trim() || !workflowRunId} onClick={() => doDecision(() => decisions.escalate(sel.case_uid, { workflowRunId, systemPrediction: sysPred, reason: reason.trim() }), "Escalation sent", `${patientWithEncounter(sel)} is awaiting ED Doctor review — the doctor has been notified.`)}>Send escalation</Btn>
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
            <Btn disabled={!workflowRunId || (!infoFields.length && !reason.trim())} onClick={() => doDecision(() => decisions.requestInfo(sel.case_uid, { workflowRunId, fields: infoFields, comment: reason.trim() }), "Information requested", `${patientWithEncounter(sel)} is parked pending the requested details — the ED Nurse has been notified.`)}>Log request</Btn>
          </div>
        </Modal>
      )}
      {modal === "reassess" && sel && <ReassessModal key={sel.case_uid} c={detail?.case_uid === sel.case_uid ? detail : sel} allowVitals={!checklistState.hasChart || !permissions.includes("can_record_mts_checklist")} onClose={() => setModal(null)} onDone={onReassessDone} toast={toast} />}
    </div>
  );
}
