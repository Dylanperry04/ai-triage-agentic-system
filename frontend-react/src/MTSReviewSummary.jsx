import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import { ErrorNote, Spinner } from "./atoms.jsx";
import MTSTrace from "./MTSTrace.jsx";

export default function MTSReviewSummary({ caseUid, runId }) {
  const [run, setRun] = useState(null), [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setRun(null); setError("");
    if (runId) api.mtsAssessment(caseUid, runId).then(r => active && setRun(r)).catch(e => active && setError(e.detail || e.message));
    return () => { active = false; };
  }, [caseUid, runId]);
  if (!runId) return <p>No exact recorded assessment is linked to this case.</p>;
  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!run) return <p><Spinner /> Loading the recorded assessment…</p>;
  const trace = run.mts_assessment;
  if (!trace) return <p>The recorded assessment has no MTS trace.</p>;
  const unresolved = trace.unresolved_earlier_discriminators || trace.unresolved_discriminators;
  return <section aria-label="Recorded MTS assessment" style={{ fontSize: 12.5, marginTop: 12 }}>
    <b>{trace.selected_flowchart?.label || "Flowchart not confirmed"}</b>
    <p>ML: {run.ml_acuity ?? "unavailable"}. MTS: {trace.priority ?? "incomplete"}. Hybrid advisory: {run.final_acuity ?? "unavailable"}.</p>
    <p>Assessment: {String(trace.status || "not recorded").replaceAll("_", " ")}. Determining finding: {trace.determining_discriminator?.replaceAll("_", " ") || "none recorded"}.</p>
    {!!unresolved?.length && <p>{trace.unresolved_earlier_discriminators ? "Unresolved earlier findings" : "Missing higher-priority findings"}: {unresolved.join(", ").replaceAll("_", " ")}</p>}
    {!!trace.conflicting_observations?.length && <p>Conflicts requiring resolution: {trace.conflicting_observations.join(", ").replaceAll("_", " ")}</p>}
    {trace.notes && <p>Recorded symptoms/context: {trace.notes}</p>}
    <MTSTrace trace={trace} />
  </section>;
}
