import React, { useState, useEffect, useMemo } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, Cell, LineChart, Line, ReferenceLine, Legend } from "recharts";
import { T, MTS, acuityLabel } from "../theme.js";
import { Card, Eyebrow, EmptyState, ErrorNote, Spinner, Pill } from "../atoms.jsx";
import { api } from "../api.js";

const pct = (v, dp = 1) => v == null || Number.isNaN(Number(v)) ? "—" : `${(Number(v) * 100).toFixed(dp)}%`;
const Metric = ({ label, value, hint }) => (
  <div style={{ border: `1px solid ${T.borderSoft}`, borderRadius: 10, padding: "11px 13px" }}>
    <Eyebrow>{label}</Eyebrow>
    <div style={{ fontFamily: T.mono, fontSize: 22, fontWeight: 700, marginTop: 4 }}>{value}</div>
    {hint && <div style={{ fontSize: 11, color: T.grey500, marginTop: 2 }}>{hint}</div>}
  </div>
);

export default function ModelPerformance() {
  const [perf, setPerf] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { let dead = false; api.modelPerformance().then((p) => !dead && setPerf(p)).catch((e) => !dead && setErr(e.detail || e.message)); return () => { dead = true; }; }, []);
  const a = perf?.artefacts || {};
  const card = a.model_card; const hm = card?.headline_metrics || {};
  const cm = a.confusion_matrix; const cal = a.calibration; const dist = a.class_distribution;
  const distRows = useMemo(() => {
    const counts = dist?.overall?.class_counts; if (!counts) return [];
    return Object.entries(counts).map(([k, v]) => ({ cat: Number(String(k).replace(".0", "")), n: Number(v) })).filter((r) => MTS[r.cat]).sort((x, y) => x.cat - y.cat);
  }, [dist]);
  const matrix = cm?.confusion_matrix; const labels = (cm?.labels || []).map((l) => Number(String(l).replace(".0", "")));
  const maxCell = useMemo(() => matrix ? Math.max(...matrix.flat()) : 1, [matrix]);

  /* Per-category recall (sensitivity) + specificity, one-vs-rest from the test
     confusion matrix: recall = diag/rowSum; specificity = TN/(TN+FP) where
     FP = colSum − diag and TN = total − rowSum − colSum + diag. */
  const perClass = useMemo(() => {
    if (!matrix || !labels.length) return [];
    const total = matrix.flat().reduce((s, v) => s + v, 0);
    return labels.map((lab, i) => {
      const rowSum = matrix[i].reduce((s, v) => s + v, 0);
      const colSum = matrix.reduce((s, row) => s + row[i], 0);
      const tp = matrix[i][i];
      const tn = total - rowSum - colSum + tp;
      const fp = colSum - tp;
      return { cat: lab, name: MTS[lab]?.name || acuityLabel(lab), recall: rowSum ? tp / rowSum : null, specificity: (tn + fp) ? tn / (tn + fp) : null, support: rowSum };
    });
  }, [matrix, labels]);

  const rocPts = a.roc_curve?.points || null;
  const prPts = a.pr_curve?.points || null;
  const comparison = useMemo(() => {
    const rows = a.model_comparison?.candidates;
    if (!Array.isArray(rows)) return [];
    return rows.map((r) => ({
      model: r.model_name,
      short: String(r.model_name).replace(/_/g, " "),
      recall: r.high_acuity_recall,
      specificity: r.over_triage_specificity,
      macroF1: r.macro_f1,
      passes: r.passes_over_triage_constraint,
      selected: r.model_name === card?.model_kind,
    })).sort((x, y) => (y.recall ?? 0) - (x.recall ?? 0));
  }, [a.model_comparison, card]);

  if (err) return <ErrorNote>{err}</ErrorNote>;
  if (!perf) return <div style={{ display: "flex", gap: 10, alignItems: "center", color: T.slate, fontSize: 13.5 }}><Spinner /> Loading model evidence…</div>;
  if (!card) return <EmptyState>No UHL model evidence is available. Verify UHL_REPORT_DIR and the packaged artifacts/reports/single_seed directory.</EmptyState>;

  return (
    <div>
      <div style={{ fontSize: 19, fontWeight: 700 }}>Model evidence</div>
      <div style={{ fontSize: 13, color: T.slate, marginTop: 2, lineHeight: 1.5 }}>Training artefacts for the deployed acuity model — generated once on the approved environment, reported on the untouched test split.</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 14 }}>
        <Pill colour={T.green900} bg={T.green50} border="#CBEADF">{card.model_name}</Pill>
        <Pill>{card.model_kind}</Pill>
        {a.model_comparison?.split_kind && <Pill>{String(a.model_comparison.split_kind).replace(/_/g, " ")}</Pill>}
        {a.model_comparison?.patient_overlap_train_test === 0 && <Pill colour="#2E7D32" bg="#E9F3EA" border="#CBE3CD">0 patient overlap train/test</Pill>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(165px, 1fr))", gap: 11, marginTop: 14 }}>
        <Metric label="High-acuity recall" value={pct(hm.high_acuity_recall)} hint="share of true Acuity 1–2 caught" />
        <Metric label="Severe under-triage" value={pct(hm.severe_under_triage_rate, 2)} hint="lower is safer" />
        <Metric label="Under-triage" value={pct(hm.under_triage_rate)} hint="any level too low" />
        <Metric label="Within ±1 level" value={pct(hm.within_1_acuity_level_accuracy)} />
        <Metric label="Macro F1" value={hm.macro_f1 != null ? Number(hm.macro_f1).toFixed(3) : "—"} />
        <Metric label="Brier (mean)" value={cal?.calibration?.brier_mean != null ? Number(cal.calibration.brier_mean).toFixed(3) : "—"} hint="lower = better calibrated" />
      </div>
      <div style={{ fontSize: 12, color: T.slate, marginTop: 9, lineHeight: 1.5 }}>Training-run evidence for the selected model. Live queue recommendations use the configured deployment rule and remain subject to clinician review.</div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(300px, 1fr) minmax(300px, 1fr)", gap: 14, marginTop: 16 }}>
        {matrix && (
          <Card style={{ padding: 16 }}>
            <Eyebrow>Confusion matrix (test split) — rows: true, columns: predicted</Eyebrow>
            <div style={{ display: "grid", gridTemplateColumns: `44px repeat(${labels.length}, 1fr)`, gap: 4, marginTop: 12 }}>
              <div />
              {labels.map((l) => <div key={`h${l}`} style={{ textAlign: "center", fontSize: 11, fontWeight: 700, color: MTS[l]?.colour || T.slate }}>{acuityLabel(l)}</div>)}
              {matrix.map((row, ri) => (
                <React.Fragment key={ri}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: MTS[labels[ri]]?.colour || T.slate, display: "flex", alignItems: "center" }}>{acuityLabel(labels[ri])}</div>
                  {row.map((v, ci) => {
                    const heat = v / maxCell;
                    return <div key={ci} title={`true ${acuityLabel(labels[ri])} → predicted ${acuityLabel(labels[ci])}: ${v.toLocaleString()}`} style={{ background: ri === ci ? `rgba(2,167,139,${0.14 + heat * 0.8})` : `rgba(179,6,56,${0.05 + heat * 0.55})`, borderRadius: 6, padding: "8px 4px", textAlign: "center", fontFamily: T.mono, fontSize: 11.5, fontWeight: 700, color: heat > 0.55 ? "#fff" : T.ink }}>{v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v}</div>;
                  })}
                </React.Fragment>
              ))}
            </div>
            <div style={{ fontSize: 11.5, color: T.grey500, marginTop: 10 }}>Green diagonal = correct; red = miss. Mass above the diagonal is over-triage (safe direction); below is under-triage.</div>
          </Card>
        )}
        {distRows.length > 0 && (
          <Card style={{ padding: 16 }}>
            <Eyebrow>Acuity distribution — full dataset ({(dist.overall.total || 0).toLocaleString()} visits)</Eyebrow>
            <div style={{ height: 232, marginTop: 8 }}>
              <ResponsiveContainer>
                <BarChart data={distRows} margin={{ left: -12, right: 12, top: 8 }}>
                  <CartesianGrid stroke={T.borderSoft} vertical={false} />
                  <XAxis dataKey="cat" tickFormatter={(c) => acuityLabel(c)} tick={{ fontSize: 11.5, fill: T.slate }} />
                  <YAxis tick={{ fontSize: 11, fill: T.slate }} tickFormatter={(v) => v >= 1000 ? `${v / 1000}k` : v} />
                  <Tooltip formatter={(v) => [Number(v).toLocaleString(), "visits"]} labelFormatter={(c) => `${acuityLabel(c)} · ${MTS[c]?.name || ""}`} />
                  <Bar dataKey="n" radius={[6, 6, 0, 0]}>{distRows.map((r) => <Cell key={r.cat} fill={MTS[r.cat]?.colour || T.green500} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div style={{ fontSize: 11.5, color: T.grey500, marginTop: 8, lineHeight: 1.45 }}>Acuity 1 is {pct(dist.overall.class_percentages?.["1"], 2)} of visits — the imbalance the safety-first selection rule exists to handle.</div>
          </Card>
        )}
      </div>

      {perClass.length > 0 && (
        <Card style={{ padding: 16, marginTop: 14 }}>
          <Eyebrow>Per-acuity recall (sensitivity) & specificity — test split, one-vs-rest</Eyebrow>
          <div style={{ height: 250, marginTop: 8 }}>
            <ResponsiveContainer>
              <BarChart data={perClass} margin={{ left: -12, right: 12, top: 8 }}>
                <CartesianGrid stroke={T.borderSoft} vertical={false} />
                <XAxis dataKey="cat" tickFormatter={(c) => acuityLabel(c)} tick={{ fontSize: 11.5, fill: T.slate }} />
                <YAxis domain={[0, 1]} tick={{ fontSize: 11, fill: T.slate }} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                <Tooltip formatter={(v, name) => [pct(v), name]} labelFormatter={(c) => `${acuityLabel(c)} · ${MTS[c]?.name || ""}`} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar name="Recall" dataKey="recall" radius={[5, 5, 0, 0]}>{perClass.map((r) => <Cell key={r.cat} fill={MTS[r.cat]?.colour || T.green500} />)}</Bar>
                <Bar name="Specificity" dataKey="specificity" fill="#9DB8C6" radius={[5, 5, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div style={{ fontSize: 11.5, color: T.grey500, marginTop: 8, lineHeight: 1.5 }}>The safety-first operating point buys near-total Acuity 1–2 recall at the cost of mid-acuity discrimination — Acuity 3 recall collapses because ambiguous cases are pushed up, not down. Specificity shows how each acuity avoids absorbing everyone else's patients.</div>
        </Card>
      )}

      {(rocPts || prPts) && (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(300px, 1fr) minmax(300px, 1fr)", gap: 14, marginTop: 14 }}>
          {rocPts && (
            <Card style={{ padding: 16 }}>
              <Eyebrow>ROC — high acuity (Acuity 1–2) vs non-high (3–5)</Eyebrow>
              <div style={{ height: 230, marginTop: 8 }}>
                <ResponsiveContainer>
                  <LineChart data={rocPts} margin={{ left: -8, right: 12, top: 8, bottom: 4 }}>
                    <CartesianGrid stroke={T.borderSoft} />
                    <XAxis dataKey="false_positive_rate" type="number" domain={[0, 1]} tick={{ fontSize: 10.5, fill: T.slate }} tickFormatter={(v) => v.toFixed(1)} label={{ value: "False positive rate", position: "insideBottom", offset: -2, fontSize: 11, fill: T.grey500 }} />
                    <YAxis dataKey="true_positive_rate" type="number" domain={[0, 1]} tick={{ fontSize: 10.5, fill: T.slate }} tickFormatter={(v) => v.toFixed(1)} label={{ value: "True positive rate", angle: -90, position: "insideLeft", fontSize: 11, fill: T.grey500 }} />
                    <Tooltip formatter={(v) => Number(v).toFixed(3)} labelFormatter={(v) => `FPR ${Number(v).toFixed(3)}`} />
                    <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke={T.grey500} strokeDasharray="4 4" />
                    <Line type="monotone" dataKey="true_positive_rate" stroke={T.green700} dot={false} strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div style={{ fontSize: 11.5, color: T.grey500, marginTop: 6 }}>{a.roc_curve.point_count?.toLocaleString?.() || ""} thresholds{a.roc_curve.downsampled ? " (downsampled for display)" : ""} · dashed line = chance.</div>
            </Card>
          )}
          {prPts && (
            <Card style={{ padding: 16 }}>
              <Eyebrow>Precision–recall — high acuity (Acuity 1–2)</Eyebrow>
              <div style={{ height: 230, marginTop: 8 }}>
                <ResponsiveContainer>
                  <LineChart data={prPts} margin={{ left: -8, right: 12, top: 8, bottom: 4 }}>
                    <CartesianGrid stroke={T.borderSoft} />
                    <XAxis dataKey="recall" type="number" domain={[0, 1]} tick={{ fontSize: 10.5, fill: T.slate }} tickFormatter={(v) => v.toFixed(1)} label={{ value: "Recall", position: "insideBottom", offset: -2, fontSize: 11, fill: T.grey500 }} />
                    <YAxis dataKey="precision" type="number" domain={[0, 1]} tick={{ fontSize: 10.5, fill: T.slate }} tickFormatter={(v) => v.toFixed(1)} label={{ value: "Precision", angle: -90, position: "insideLeft", fontSize: 11, fill: T.grey500 }} />
                    <Tooltip formatter={(v) => Number(v).toFixed(3)} labelFormatter={(v) => `Recall ${Number(v).toFixed(3)}`} />
                    <Line type="monotone" dataKey="precision" stroke={T.blue} dot={false} strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div style={{ fontSize: 11.5, color: T.grey500, marginTop: 6 }}>The deployed operating point sits at the far-right of this curve — recall first, precision the accepted cost.</div>
            </Card>
          )}
        </div>
      )}

      {comparison.length > 0 && (
        <Card style={{ padding: 16, marginTop: 14 }}>
          <Eyebrow>Cross-model comparison — serving candidates on the identical test split</Eyebrow>
          <div style={{ height: 56 + comparison.length * 40, marginTop: 8 }}>
            <ResponsiveContainer>
              <BarChart data={comparison} layout="vertical" margin={{ left: 8, right: 40, top: 4 }}>
                <CartesianGrid stroke={T.borderSoft} horizontal={false} />
                <XAxis type="number" domain={[0, 1]} tick={{ fontSize: 10.5, fill: T.slate }} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                <YAxis type="category" dataKey="short" width={210} tick={{ fontSize: 11, fill: T.ink }} />
                <Tooltip formatter={(v, name) => [pct(v), name]} />
                <Bar name="High-acuity recall" dataKey="recall" radius={[0, 5, 5, 0]} label={{ position: "right", formatter: (v) => pct(v), fontSize: 10.5, fill: T.slate }}>
                  {comparison.map((r) => <Cell key={r.model} fill={r.selected ? T.green700 : "#B9CDD6"} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div style={{ overflowX: "auto", marginTop: 8 }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12.5 }}>
              <thead><tr style={{ textAlign: "left", color: T.grey500 }}>
                <th style={{ padding: "6px 10px 6px 0", fontWeight: 600 }}>Model</th>
                <th style={{ padding: "6px 10px", fontWeight: 600 }}>High-acuity recall</th>
                <th style={{ padding: "6px 10px", fontWeight: 600 }}>Over-triage specificity</th>
                <th style={{ padding: "6px 10px", fontWeight: 600 }}>Macro F1</th>
                <th style={{ padding: "6px 10px", fontWeight: 600 }}>Safety constraint</th>
              </tr></thead>
              <tbody>
                {comparison.map((r) => (
                  <tr key={r.model} style={{ borderTop: `1px solid ${T.borderSoft}`, background: r.selected ? T.green50 : "transparent" }}>
                    <td style={{ padding: "7px 10px 7px 0", fontFamily: T.mono, fontSize: 11.5 }}>{r.model}{r.selected && <span style={{ marginLeft: 8, fontSize: 10, fontWeight: 700, color: T.green900, letterSpacing: "0.05em" }}>SELECTED</span>}</td>
                    <td style={{ padding: "7px 10px", fontFamily: T.mono }}>{pct(r.recall, 2)}</td>
                    <td style={{ padding: "7px 10px", fontFamily: T.mono }}>{pct(r.specificity)}</td>
                    <td style={{ padding: "7px 10px", fontFamily: T.mono }}>{r.macroF1 != null ? Number(r.macroF1).toFixed(3) : "—"}</td>
                    <td style={{ padding: "7px 10px" }}>{r.passes ? <span style={{ color: "#2E7D32", fontWeight: 700 }}>passes</span> : <span style={{ color: T.red, fontWeight: 700 }}>fails</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 11.5, color: T.grey500, marginTop: 8, lineHeight: 1.45 }}>Selection favoured the highest high-acuity recall among candidates passing the over-triage specificity constraint{Array.isArray(a.model_comparison?.experimental_non_serving_candidates) ? ` · ${a.model_comparison.experimental_non_serving_candidates.length} experimental candidates evaluated but not serving-eligible` : ""}.</div>
        </Card>
      )}
    </div>
  );
}
