import React, { useEffect, useImperativeHandle, useState, useRef } from "react";
import { api } from "./api.js";
import { T, vitalState } from "./theme.js";
import { Btn, ErrorNote, Spinner, Modal } from "./atoms.jsx";
import MTSTrace from "./MTSTrace.jsx";

const control = { font: "inherit", border: `1px solid ${T.border}`, borderRadius: 5, padding: 6, maxWidth: "100%" };
const unknown = () => ({ state: "not_assessed", value: null, note: "" });
const findingValue = o => o?.state === "assessed" ? (o.value ? "present" : "absent") : o?.state || "not_assessed";
const priorities = { 1: "Immediate", 2: "Very urgent", 3: "Urgent", 4: "Standard" };
const findingResolved = o => o?.state === "assessed" || (o?.state === "not_applicable" && Boolean(o.note?.trim()));

export default function MTSChecklist({ c, permissions = [], onSaved, assessment, onStateChange, onGeneralObservations, checklistActions }) {
  const [data, setData] = useState(null), [form, setForm] = useState(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [saved, setSaved] = useState(false);
  const canEdit = permissions.includes("can_record_mts_checklist");
  const [dirty, setDirty] = useState(false);
  const loadedUid = useRef(null);
  const saving = useRef(false);
  const [reload, setReload] = useState(0);
  const [confirmation, setConfirmation] = useState(null);
  const uid = c?.case_uid;
  const charts = data?.catalog.flowcharts;
  const chart = charts?.[form?.flowchart_id];
  const canSave = Boolean(canEdit && chart && form.flowchart_confirmed
    && (form.flowchart_id === data.proposal.flowchart_id || form.selection_reason.trim()));
  const save = async (reassess = true) => {
    if (!canSave || saving.current) return false;
    saving.current = true;
    setBusy(true); setError(""); setSaved(false);
    try {
      const allowed = new Set([...chart.measurement_fields, ...chart.rules.map(r => r.id)]);
      const submitted = { ...form, observations: Object.fromEntries(Object.entries(form.observations).filter(([k]) => allowed.has(k))) };
      const result = await api.saveMtsChecklist(uid, submitted);
      setDirty(false); setForm({ ...result.checklist, expected_revision: result.revision }); setSaved(result);
      if (result.status !== "unchanged" || result.assessment_required) onSaved?.(uid, result, reassess);
      return true;
    } catch (e) {
      setError(typeof e.detail === "string" ? e.detail : e.message);
      return false;
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };
  useImperativeHandle(checklistActions, () => ({ save }));
  useEffect(() => {
    onStateChange?.({ dirty, busy, canSave, hasChart: Boolean(chart) });
  }, [dirty, busy, canSave, chart, onStateChange]);
  useEffect(() => {
    const warn = e => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    let active = true;
    if (dirty && loadedUid.current === uid) {
      setError("Recorded observations changed while you were editing. Your draft is retained; saving will check for a stale revision.");
      return () => { active = false; };
    }
    loadedUid.current = uid;
    setDirty(false);
    setData(null); setForm(null); setError(""); setSaved(false);
    if (!uid) return () => { active = false; };
    api.mtsChecklist(uid).then(d => {
      if (!active) return;
      setData(d);
      const source = {};
      const sourceTriage = d.recorded_triage || d.source_triage || c.triage || {};
      for (const [k, raw] of Object.entries(sourceTriage)) {
        if (k in d.catalog.measurements && raw != null && raw !== "" && Number.isFinite(Number(raw))) {
          let value = Number(raw);
          if (k === "temperature" && sourceTriage.temperature_unit !== "C") value = (value - 32) * 5 / 9;
          const provenance = d.observation_provenance?.[k];
          source[k] = { state: provenance ? "recorded" : "source_recorded", value,
            note: provenance ? `Previously recorded by ${provenance.recorded_by_role || "clinician"} at ${provenance.observed_at || "unknown time"}; not reassessed` : "Original dataset measurement; nursing assessment not asserted" };
        }
      }
      setForm(d.checklist ? { ...d.checklist, expected_revision: d.revision, expected_observations_at: d.observations_at } : {
        catalog_version: d.catalog.version, catalog_sha256: d.catalog.sha256,
        flowchart_id: d.proposal.flowchart_id || "", flowchart_confirmed: false,
        selection_reason: "", observations: source, notes: "", expected_revision: d.revision, expected_observations_at: d.observations_at,
      });
    }).catch(e => active && setError(e.detail || e.message));
    return () => { active = false; };
  }, [uid, c?.workflow_state?.mts_checklist_revision, c?.workflow_state?.latest_triage_updates_at, reload]);
  if (!uid) return null;
  if (error && !form) return <ErrorNote>{error}</ErrorNote>;
  if (!data || !form) return <div style={{ marginTop: 18 }}><Spinner /> Loading flowchart checklist…</div>;
  const editForm = update => { setDirty(true); setSaved(false); setForm(update); };
  const setObservation = (key, value) => { editForm(f => ({ ...f, observations: { ...f.observations, [key]: value },
    reassessed_measurements: value.state === "assessed" && key in data.catalog.measurements ? [...new Set([...(f.reassessed_measurements || []), key])] : f.reassessed_measurements || [] })); };
  const applyChartChange = id => {
    const allowed = new Set([...(charts[id]?.measurement_fields || []), ...(charts[id]?.rules || []).map(r => r.id)]);
    setSaved(false);
    editForm(f => ({ ...f, flowchart_id: id, flowchart_confirmed: false,
      observations: Object.fromEntries(Object.entries(f.observations).filter(([k]) => allowed.has(k))),
      reassessed_measurements: (f.reassessed_measurements || []).filter(k => allowed.has(k)) }));
  };
  const changeChart = id => dirty ? setConfirmation({ kind: "chart", id }) : applyChartChange(id);
  const applicableRules = chart?.rules.filter(rule => rule.applicability !== "paediatric") || [];
  const bedsideRules = applicableRules.filter(rule => rule.kind !== "measurement_predicate");
  const positiveCount = bedsideRules.filter(rule => findingValue(form.observations[rule.id]) === "present").length;
  const modelFields = data.catalog.model_measurement_fields || chart?.measurement_fields;
  const modelMeasurements = (chart?.measurement_fields || []).filter(key => modelFields.includes(key));
  const contextMeasurements = (chart?.measurement_fields || []).filter(key => !modelFields.includes(key));
  const renderMeasurement = key => {
    const definition = data.catalog.measurements[key], o = form.observations[key] || unknown();
    return <tr key={key} style={{ borderTop: `1px solid ${T.borderSoft}` }}><td>{definition.label}</td>
      <td><select aria-label={`${definition.label} status`} value={o.state} disabled={!canEdit || busy} style={control} onChange={e => setObservation(key, { ...o, state: e.target.value, value: ["assessed", "source_recorded", "recorded"].includes(e.target.value) ? o.value : null })}>
        <option value="not_assessed">Not assessed</option>{definition.kind !== "boolean" && <option value={data.observation_provenance?.[key] ? "recorded" : "source_recorded"}>{data.observation_provenance?.[key] ? "Previously recorded" : "Original source"}</option>}<option value="assessed">Assessed</option><option value="unavailable">Unavailable</option>
      </select></td>
      <td>{definition.kind === "boolean" ? <select aria-label={`${definition.label} value`} value={o.value == null ? "" : String(o.value)} disabled={!canEdit || busy || o.state !== "assessed"} style={control} onChange={e => setObservation(key, { ...o, value: e.target.value === "" ? null : e.target.value === "true" })}><option value="">Not specified</option><option value="false">No (room air)</option><option value="true">Yes (oxygen therapy)</option></select> : <input aria-label={`${definition.label} measurement`} type="number" step="any" min={definition.min} max={definition.max} value={o.state === "assessed" ? o.value ?? "" : o.value == null ? "" : Number(o.value.toFixed(2))} disabled={!canEdit || busy || o.state !== "assessed"} style={{ ...control, width: 95, color: vitalState(key === "temperature" ? "tempC" : key, o.value) === "crit" ? T.red : T.ink }} onChange={e => setObservation(key, { ...o, value: e.target.value === "" ? null : Number(e.target.value) })} />} {definition.unit}
        {definition.kind !== "boolean" && o.state === "assessed" && <label style={{ display: "block", fontSize: 11.5 }}><input type="checkbox" aria-label={`Repeat ${definition.label}`} disabled={!canEdit || busy} checked={(form.reassessed_measurements || []).includes(key)} onChange={e => editForm(f => ({ ...f, reassessed_measurements: e.target.checked ? [...new Set([...(f.reassessed_measurements || []), key])] : (f.reassessed_measurements || []).filter(k => k !== key) }))} /> Repeat reading</label>}
      </td></tr>;
  };
  return <section aria-label="MTS flowchart observation checklist" style={{ marginTop: 14 }}>
    {confirmation && <Modal title={confirmation.kind === "chart" ? "Change flowchart?" : "Discard this draft?"} onClose={() => setConfirmation(null)}>
      <p>{confirmation.kind === "chart" ? "Compatible observations will be retained. Findings outside the new chart will be removed." : "Reloading restores the saved observations and discards your unsaved changes."}</p>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <Btn kind="quiet" onClick={() => setConfirmation(null)}>Keep editing</Btn>
        <Btn onClick={() => { const action = confirmation; setConfirmation(null); if (action.kind === "chart") applyChartChange(action.id); else { loadedUid.current = null; setDirty(false); setReload(v => v + 1); } }}>{confirmation.kind === "chart" ? "Change flowchart" : "Discard and reload"}</Btn>
      </div>
    </Modal>}
    <h3 style={{ fontSize: 16, margin: "0 0 8px" }}>Flowchart observation checklist</h3>
    {data.previous_checklist_requires_reassessment && <p role="status">Saved evidence changed. Reassess the checklist.</p>}
    <label>Presenting-complaint flowchart {chart && !form.flowchart_confirmed && form.flowchart_id === data.proposal.flowchart_id && <small style={{ color: T.slate }}>· Proposed</small>}
    <select aria-label="MTS flowchart" value={form.flowchart_id} disabled={!canEdit || busy} onChange={e => changeChart(e.target.value)} style={{ ...control, width: "100%", marginTop: 4 }}>
      <option value="">Select a supported flowchart</option>{Object.entries(charts).map(([id, d]) => <option key={id} value={id}>{d.name}</option>)}
    </select></label>
    {form.flowchart_id !== data.proposal.flowchart_id && <div style={{ color: T.slate, fontSize: 12, margin: "5px 0" }}>Proposed: {charts[data.proposal.flowchart_id]?.name || "No supported mapping"}</div>}
    {!chart && <>
      <p style={{ fontSize: 12.5 }}>No supported chart selected. Use clinician review and general observations for presentations outside the available charts.</p>
      {onGeneralObservations && canEdit && <Btn kind="quiet" onClick={onGeneralObservations}>Record general observations</Btn>}
      <table className="mts-observation-table" style={{ width: "100%", fontSize: 12.5 }}><thead><tr><th align="left">Observation</th><th align="left">Status</th><th align="left">Measurement</th></tr></thead>
        <tbody>{["heartrate", "sbp", "dbp", "o2sat", "resprate", "temperature", "pain"].map(key => { const d = data.catalog.measurements[key], o = form.observations[key]; return <tr key={key}><td>{d.label}</td><td>{o?.state?.replaceAll("_", " ") || "Not assessed"}</td><td>{o?.value != null ? Number(o.value.toFixed(2)) : "Unavailable"} {d.unit}</td></tr>; })}</tbody>
      </table>
    </>}
    {chart && <>
      <label style={{ display: "block", margin: "8px 0" }}><input type="checkbox" aria-label="Confirm selected flowchart for this presentation" disabled={!canEdit || busy} checked={form.flowchart_confirmed} onChange={e => editForm(f => ({ ...f, flowchart_confirmed: e.target.checked }))} /> Confirm selected flowchart</label>
      {form.flowchart_id !== data.proposal.flowchart_id && <label>Clinical reason for chart selection<input aria-label="Flowchart selection reason" style={{ ...control, width: "100%", boxSizing: "border-box" }} value={form.selection_reason} disabled={!canEdit || busy} onChange={e => editForm(f => ({ ...f, selection_reason: e.target.value }))} maxLength={1000} /></label>}
      <div className="mts-savebar">
      {error && <ErrorNote>{error}</ErrorNote>}
      {error && <Btn kind="quiet" onClick={() => setConfirmation({ kind: "reload" })}>Reload saved observations</Btn>}
      {dirty && <span role="status" style={{ fontSize: 12, color: T.red }}>Unsaved checklist</span>}
      {canEdit && <Btn onClick={() => save()} disabled={!canSave || busy}>{busy ? "Saving observations…" : permissions.includes("can_run_triage_assessment") ? "Save and reassess" : "Save checklist observations"}</Btn>}
      {saved && <div role="status">{saved.status === "unchanged" ? "No observation changes." : saved.assessment_required ? "Observations saved · reassessment required" : "Observations saved"}</div>}
      </div>
      <h4 style={{ margin: "10px 0 4px" }}>Clinical discriminators</h4>
      <div aria-live="polite">{positiveCount > 0 && <div className="mts-checklist-progress"><b>{positiveCount} marked present{dirty ? " · draft" : ""}</b></div>}</div>
      {[1, 2, 3, 4].map(priority => {
        const rules = applicableRules.filter(r => r.priority === priority);
        if (!rules.length) return null;
        const manual = rules.filter(rule => rule.kind !== "measurement_predicate");
        const unresolved = manual.filter(rule => !findingResolved(form.observations[rule.id])).length;
        return <details key={`${form.flowchart_id}:${priority}`} open={priority === 1} style={{ padding: "8px 0", borderTop: `1px solid ${T.borderSoft}` }}>
          <summary style={{ cursor: "pointer", fontWeight: 700 }}>{priorities[priority]} findings
            {manual.length > 0 && <span style={{ color: unresolved && priority === 1 ? "#8A4B00" : T.slate, fontSize: 12 }}> · {unresolved} bedside unresolved</span>}
          </summary>
          {rules.map(rule => { const o = form.observations[rule.id] || unknown(); return <div key={rule.id} style={{ padding: "6px 0" }}>
            {rule.kind === "measurement_predicate" ? <div>{rule.label} <span style={{ color: T.slate }}>· calculated from assessed measurements and required context</span></div> : <>
            <label style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>{rule.label}
              <select aria-label={rule.label} value={findingValue(o)} disabled={!canEdit || busy} style={control} onChange={e => {
                const value = e.target.value;
                setObservation(rule.id, { state: ["present", "absent"].includes(value) ? "assessed" : value,
                  value: ["present", "absent"].includes(value) ? value === "present" : null, note: o.note || "" });
              }}><option value="not_assessed">Not assessed</option><option value="present">Assessed: present</option><option value="absent">Assessed: absent</option><option value="unavailable">Unavailable</option><option value="not_applicable">Not applicable</option></select>
            </label>
            {o.state === "not_applicable" && <input aria-label={`${rule.label} inapplicability reason`} placeholder="Clinical reason required" value={o.note || ""} maxLength={500} disabled={!canEdit || busy} style={{ ...control, width: "100%", boxSizing: "border-box", marginTop: 4 }} onChange={e => setObservation(rule.id, { ...o, note: e.target.value })} />}
            </>}
            {rule.assessment_help && <details style={{ fontSize: 11.5, color: T.slate }}><summary aria-label={`Definition: ${rule.label}`}>Definition</summary>{rule.assessment_help} <span>Source p. {rule.definition_source_page}; pending clinical review.</span></details>}
          </div>; })}
        </details>;
      })}
      <h4 style={{ margin: "12px 0 6px" }}>Measurements</h4>
      <details style={{ fontSize: 12, color: T.slate, marginBottom: 6 }}><summary>Measurement guidance</summary>Model inputs can also support flowchart rules. Original and previously recorded values are historical. Use Repeat reading only for a newly measured value.</details>
      <table className="mts-observation-table" style={{ width: "100%", fontSize: 12.5, borderCollapse: "collapse" }}>
        <thead><tr><th align="left">Observation</th><th align="left">Status</th><th align="left">Measurement</th></tr></thead>
        {contextMeasurements.length > 0 && <tbody><tr><th scope="rowgroup" colSpan={3} align="left">Additional flowchart context</th></tr>{contextMeasurements.map(renderMeasurement)}</tbody>}
        {modelMeasurements.length > 0 && <tbody><tr><th scope="rowgroup" colSpan={3} align="left">Common model measurements</th></tr>{modelMeasurements.map(renderMeasurement)}</tbody>}
      </table>
      <details style={{ marginTop: 10 }}><summary>Symptoms and contextual observations{form.notes ? " · recorded" : ""}</summary><label>Additional notes<textarea aria-label="MTS contextual observations" style={{ ...control, width: "100%", boxSizing: "border-box" }} rows={2} value={form.notes} disabled={!canEdit || busy} maxLength={2000} onChange={e => editForm(f => ({ ...f, notes: e.target.value }))} /></label></details>
      {onGeneralObservations && canEdit && <Btn kind="quiet" style={{ marginTop: 8, fontSize: 12 }} disabled={dirty || busy} onClick={onGeneralObservations}>Update complaint / add supporting information</Btn>}
      <details style={{ color: T.slate, fontSize: 12, marginTop: 10 }}><summary>Source and implementation scope</summary>Emergency Triage, edition {data.catalog.source.edition}, p. {chart.source_page}. Eight charts, age 16+. Research implementation pending clinical validation. Missing information remains unassessed.</details>
    </>}
    {assessment?.mts_assessment && <MTSTrace trace={assessment.mts_assessment} />}
  </section>;
}
