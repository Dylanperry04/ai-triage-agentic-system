import React, { useState, useEffect } from "react";
import { CheckCircle2, AlertTriangle, MinusCircle } from "lucide-react";
import { T } from "../theme.js";
import { Card, Eyebrow, EmptyState, Spinner, Pill } from "../atoms.jsx";
import { api } from "../api.js";

const Light = ({ ok, warn, label, detail }) => (
  <Card style={{ padding: "13px 15px", display: "flex", gap: 11, alignItems: "flex-start" }}>
    {ok ? <CheckCircle2 size={18} style={{ color: "#2E7D32", flexShrink: 0, marginTop: 1 }} /> : warn ? <AlertTriangle size={18} style={{ color: "#E86C00", flexShrink: 0, marginTop: 1 }} /> : <MinusCircle size={18} style={{ color: T.grey500, flexShrink: 0, marginTop: 1 }} />}
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 13.5, fontWeight: 700 }}>{label}</div>
      <div style={{ fontSize: 12.5, color: T.slate, marginTop: 2, lineHeight: 1.45 }}>{detail}</div>
    </div>
  </Card>
);

export default function Health({ session }) {
  const [d, setD] = useState({});
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let dead = false;
    const canModel = (session?.permissions || []).includes("can_view_model_performance");
    Promise.allSettled([
      api.health(), api.runtimeStatus(), api.uhlStatus(), api.llmStatus(), api.governanceReport(), api.systemMeta(),
      canModel ? api.modelPerformance() : Promise.reject(new Error("skipped")),
    ]).then(([h, r, m, l, g, meta, mp]) => {
      if (dead) return;
      setD({
        health: h.status === "fulfilled" ? h.value : null,
        runtime: r.status === "fulfilled" ? r.value : null,
        uhl: m.status === "fulfilled" ? m.value : null,
        llm: l.status === "fulfilled" ? l.value : null,
        gov: g.status === "fulfilled" ? g.value : null,
        meta: meta.status === "fulfilled" ? meta.value : null,
        perf: mp.status === "fulfilled" ? mp.value : null,
      });
      setLoading(false);
    });
    return () => { dead = true; };
  }, []);
  if (loading) return <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 8, color: T.slate, fontSize: 13.5 }}><Spinner /> Reading service status…</div>;
  const { health, runtime, uhl, llm, gov, meta, perf } = d;
  const card = perf?.artefacts?.model_card || null;
  const metrics = card?.headline_metrics || card?.metrics || {};
  const pick = (...keys) => { for (const k of keys) { const v = metrics?.[k]; if (v != null && !Number.isNaN(Number(v))) return Number(v); } return null; };
  const fmtPct = (v) => v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(1)}%`;
  const govChecks = gov?.checks || gov?.policy_checks || gov?.gates || null;

  return (
    <div>
      <div style={{ fontSize: 19, fontWeight: 700 }}>System health & governance</div>
      <div style={{ fontSize: 13, color: T.slate, marginTop: 2 }}>Service status, safety gates and the trained-model evidence — one page, no raw JSON.</div>
      <Eyebrow style={{ marginTop: 18, marginBottom: 9 }}>Services</Eyebrow>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 11 }}>
        <Light ok={!!health} label="FastAPI backend" detail={health ? `Healthy · v${meta?.version || health.version || ""}` : "Health probe failed"} />
        <Light ok={meta?.active_case_source && meta.active_case_source !== "not_configured"} warn={meta?.active_case_source === "not_configured"} label="Case source" detail={meta?.active_case_source_mode ? `Configured (${String(meta.active_case_source_mode).replace(/_/g, " ")})` : "Not configured — the app fails closed with no cases"} />
        <Light ok={uhl?.model_ready || meta?.uhl_model_configured} warn={!uhl?.model_ready} label="Prediction model artefact" detail={(uhl?.model_ready || meta?.uhl_model_configured) ? "Pinned UHL CatBoost model verified" : "Missing or invalid — assessments fall back to the rules engine"} />
        <Light ok={llm?.configured || llm?.status === "configured"} warn={!(llm?.configured || llm?.status === "configured")} label="LLM explanation layer" detail={(llm?.configured || llm?.status === "configured") ? "Azure OpenAI configured — explains only, never decides" : "Not configured — predictions unaffected; explanations disabled"} />
        <Light ok={runtime?.overdue_vitals_sweeper_enabled ?? runtime?.sweeper_enabled} warn={!(runtime?.overdue_vitals_sweeper_enabled ?? runtime?.sweeper_enabled)} label="Overdue-vitals sweeper" detail={(runtime?.overdue_vitals_sweeper_enabled ?? runtime?.sweeper_enabled) ? "Server-side recheck notifications active" : "Off in this profile — enable ENABLE_OVERDUE_VITALS_SWEEPER for live recheck alerts"} />
        <Light ok={String(meta?.rules_status || "").includes("ACTIVE")} label="Rules engine" detail={String(meta?.rules_status || "").includes("ACTIVE") ? "Provisional MTS ruleset active — clinician review required" : "No automated categorisation configured"} />
      </div>
      <Eyebrow style={{ marginTop: 20, marginBottom: 9 }}>Governance gates</Eyebrow>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 11 }}>
        <Light ok label="Human review required" detail="Every model output requires a clinician decision; nothing auto-actions." />
        <Light ok label="Leakage guard" detail="Triage-time fields only — recorded acuity and retrospective outcomes are never shown to the model or this UI." />
        <Light ok label="Patient/stay display labels" detail="Clinical screens show patient names when present, otherwise patient or stay numbers." />
        {Array.isArray(govChecks) && govChecks.slice(0, 4).map((c, i) => (
          <Light key={i} ok={c.passed ?? c.ok ?? c.status === "pass"} warn={!(c.passed ?? c.ok ?? c.status === "pass")} label={c.name || c.check || `Policy check ${i + 1}`} detail={c.detail || c.description || c.status || ""} />
        ))}
      </div>
      <Eyebrow style={{ marginTop: 20, marginBottom: 9 }}>Model evidence {card ? "" : "(configure the model report directory to populate)"}</Eyebrow>
      {card ? (
        <Card style={{ padding: 16 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <Pill colour={T.green900} bg={T.green50} border="#CBEADF">{card.model_name || card.selected_model || "Selected model"}</Pill>
            {(card.training_run_id || card.training_run) && <Pill>run {String(card.training_run_id || card.training_run).slice(0, 8)}</Pill>}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 11, marginTop: 13 }}>
            {[
              ["High-acuity recall", pick("high_acuity_recall")],
              ["Severe under-triage", pick("severe_under_triage_rate")],
              ["Within ±1 level", pick("within_1_acuity_level_accuracy")],
              ["Macro F1", pick("macro_f1")],
            ].map(([l, v]) => (
              <div key={l} style={{ border: `1px solid ${T.borderSoft}`, borderRadius: 10, padding: "11px 13px" }}>
                <Eyebrow>{l}</Eyebrow>
                <div style={{ fontFamily: T.mono, fontSize: 22, fontWeight: 700, marginTop: 4 }}>{fmtPct(v)}</div>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 12, color: T.slate, marginTop: 11, lineHeight: 1.5 }}>Figures from the supplied UHL training and untouched-test reports. Full artefacts (calibration, confusion matrix and model comparisons) live under Model Evidence.</div>
        </Card>
      ) : <EmptyState>Model report artefacts not available to this role or not configured in this profile.</EmptyState>}
    </div>
  );
}
