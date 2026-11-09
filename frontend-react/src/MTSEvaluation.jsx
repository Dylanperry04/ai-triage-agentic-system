import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import { Card, Btn, ErrorNote, Spinner } from "./atoms.jsx";
import { T } from "./theme.js";

const percent = value => value == null ? "Not evaluated" : `${(value * 100).toFixed(1)}%`;
const groups = { chest_pain: "Chest pain", abdominal_pain: "Abdominal pain", trauma: "Trauma", respiratory_symptoms: "Respiratory symptoms" };

export default function MTSEvaluation() {
  const [report, setReport] = useState(null), [error, setError] = useState("");
  const [mode, setMode] = useState("final_category");
  useEffect(() => { let active = true; api.mtsEvaluation().then(d => active && setReport(d)).catch(e => active && setError(e.detail || e.message)); return () => { active = false; }; }, []);
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const a = document.createElement("a"); a.href = url; a.download = "mts-clinical-reference-evaluation.json"; a.click(); URL.revokeObjectURL(url);
  };
  return <Card style={{ padding: 16, marginTop: 14, marginBottom: 18 }}>
    <h3 style={{ marginTop: 0 }}>MTS process and presenting-complaint evaluation</h3>
    {error ? <ErrorNote>{error}</ErrorNote> : !report ? <><Spinner /> Loading MTS evaluation…</> : <>
      <p style={{ fontSize: 12.5, color: T.slate }}>Clinical reference cases: <strong>{report.reference_cases}</strong>. Missing annotations remain unevaluated.</p>
      {report.reference_scope && <p style={{ fontSize: 12.5, color: T.slate }}>Chart metrics cover the implemented charts. Reviewers attest to reference independence. Coverage across all presenting complaints remains unmeasured.</p>}
      <table style={{ width: "100%", fontSize: 12.5, textAlign: "left", borderCollapse: "collapse" }}>
        <thead><tr><th>Process metric</th><th>Result</th><th>Reference coverage</th></tr></thead>
        <tbody>
          <tr><td>Automated flowchart selection accuracy</td><td>{percent(report.flowchart_selection.accuracy)}</td><td>{report.flowchart_selection.evaluated_cases} cases · {report.flowchart_selection.abstentions} abstentions{report.flowchart_selection.proposal_coverage != null && ` · ${percent(report.flowchart_selection.proposal_coverage)} proposal coverage`}</td></tr>
          <tr><td>Discriminator precision / recall / F1 (micro)</td><td>{["precision", "recall", "f1"].map(k => percent(report.discriminator_identification[k])).join(" / ")}</td><td>{report.discriminator_identification.evaluated_cases} annotated cases</td></tr>
          <tr><td>Guideline adherence: displayed hybrid</td><td>{percent(report.guideline_adherence.rate)}</td><td>{report.guideline_adherence.reviewed_decisions} independently adjudicated decisions</td></tr>
          {report.mts_guideline_adherence && <tr><td>Guideline adherence: completed MTS</td><td>{percent(report.mts_guideline_adherence.rate)}</td><td>{report.mts_guideline_adherence.reviewed_decisions} independently adjudicated decisions</td></tr>}
          <tr><td>Explanations with unsupported clinical claims</td><td>{report.unsupported_clinical_claims.explanations_with_unsupported_claims ?? "Not reviewed"}</td><td>{report.unsupported_clinical_claims.reviewed_explanations} reviewed explanations</td></tr>
        </tbody>
      </table>
      <div style={{ margin: "14px 0 8px", fontSize: 12.5 }}><label>Recommendation being compared <select value={mode} onChange={e => setMode(e.target.value)}>
        <option value="final_category">Displayed hybrid recommendation</option>
        <option value="ml_category">ML prediction before vital safeguards</option>
        {report.by_presenting_complaint.chest_pain.ml_safety_category && <option value="ml_safety_category">ML with vital safeguards</option>}
        <option value="mts_category">Structured MTS research comparison</option>
      </select></label></div>
      <div style={{ overflowX: "auto" }}><table style={{ width: "100%", fontSize: 12.5, borderCollapse: "collapse", textAlign: "left" }}>
        <thead><tr><th>Presenting complaint</th><th>n evaluated / references</th><th>Accuracy / exact agreement</th><th>Undertriage</th><th>Overtriage</th><th>Weighted κ</th></tr></thead>
        <tbody>{Object.entries(groups).map(([id, label]) => { const m = report.by_presenting_complaint[id][mode]; return <tr key={id} style={{ borderTop: `1px solid ${T.borderSoft}` }}>
          <td>{label}</td><td>{m.evaluated_cases} / {m.reference_cases}</td><td>{percent(m.classification_accuracy)}</td><td>{percent(m.undertriage_rate)}</td><td>{percent(m.overtriage_rate)}</td><td>{m.quadratic_weighted_kappa == null ? "Not estimable" : m.quadratic_weighted_kappa.toFixed(3)}</td>
        </tr>; })}</tbody>
      </table></div>
      <details style={{ fontSize: 12, color: T.slate, margin: "10px 0" }}><summary>Methods and interpretation</summary>Undertriage means a less urgent category than the reference. Exact category agreement equals classification accuracy. Weighted κ also considers the size of disagreements. The curated 100-case cohort is not an independent evaluation sample. A comparison between complaint groups requires suitable independent reference data.</details>
      <Btn kind="quiet" onClick={download}>Download evaluation and denominators (JSON)</Btn>
    </>}
  </Card>;
}
