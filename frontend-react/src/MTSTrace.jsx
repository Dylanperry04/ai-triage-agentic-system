import React from "react";
import { T, acuityLabel } from "./theme.js";

export default function MTSTrace({ trace }) {
  if (!trace?.catalog_version) return null;
  return <details style={{ marginTop: 12, fontSize: 12.5, lineHeight: 1.5 }}>
    <summary style={{ cursor: "pointer", fontWeight: 700 }}>MTS decision evidence{trace.selected_flowchart ? ` · ${trace.selected_flowchart.label || trace.selected_flowchart.name}` : " · checklist pending"}</summary>
    <p>{trace.priority != null ? `Research MTS comparison: ${acuityLabel(trace.priority)}.` : `MTS comparison incomplete: ${String(trace.status || "").replaceAll("_", " ")}.`} Clinical validation remains outstanding.</p>
    {!trace.assessment_complete && trace.earliest_known_match && <p>Earliest known match: {trace.executed_rules?.find(rule => rule.id === trace.earliest_known_match)?.label || trace.earliest_known_match.replaceAll("_", " ")}.</p>}
    {!!trace.conflicting_observations?.length && <p>Conflicting measurements and clinical findings require review: {trace.conflicting_observations.join(", ")}.</p>}
    <p>Complaint: {trace.presenting_complaint || "Not recorded"}. Chart proposal: {trace.flowchart_proposal?.flowchart_id || "No unambiguous mapping"}. Selected chart: {trace.selected_flowchart?.label || trace.selected_flowchart?.name || "Not confirmed"}.</p>
    {trace.selected_flowchart?.selection_reason && <p>Selection reason: {trace.selected_flowchart.selection_reason}</p>}
    <div>Source: {trace.source?.title}, edition {trace.source?.edition}, p. {trace.selected_flowchart?.source_page || "—"}. Version: {trace.catalog_version}.</div>
    <details><summary>Technical audit identity</summary><div style={{ color: T.slate, overflowWrap: "anywhere" }}>Catalog SHA-256: {trace.catalog_sha256}<br />Policy SHA-256: {trace.system_identity?.system_policy_sha256 || "Not recorded"}</div></details>
    <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 8 }}>
      <caption style={{ textAlign: "left", fontWeight: 700 }}>Rules evaluated and supporting observations</caption>
      <thead><tr><th align="left">Discriminator</th><th align="left">Priority</th><th align="left">Recorded observation</th><th align="left">Rule result</th></tr></thead>
      <tbody>{(trace.executed_rules || []).map(r => <tr key={r.id} style={{ borderTop: `1px solid ${T.borderSoft}` }}>
        <td>{r.label}{trace.decisive_discriminators?.includes(r.id) ? " (decisive)" : ""}</td><td>{r.priority}</td>
        <td>{r.observation?.state?.replaceAll("_", " ")}{r.observation?.value != null ? `: ${String(r.observation.value)}` : ""}{r.observation?.note ? ` · ${r.observation.note}` : ""}{r.measurement_criterion && <div style={{ fontSize: 11, overflowWrap: "anywhere" }}>Measurement rule: {JSON.stringify(r.measurement_criterion)}</div>}</td><td>{r.outcome?.replaceAll("_", " ")}</td>
      </tr>)}</tbody>
    </table>
    <details style={{ marginTop: 8 }}><summary>Complete observation snapshot and provenance</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 11 }}>{JSON.stringify({ observations: trace.observations, provenance: trace.observation_provenance, recorded_by: trace.recording_metadata }, null, 2)}</pre></details>
  </details>;
}
