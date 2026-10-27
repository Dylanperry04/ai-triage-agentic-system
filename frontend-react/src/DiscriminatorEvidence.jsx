import React from "react";
import { T, acuityLabel } from "./theme.js";
import { Eyebrow } from "./atoms.jsx";

const items = (value) => Array.isArray(value) ? value : [];
const statusLabel = { matched: "Matched", not_matched: "Not matched", not_assessed: "Not assessed" };
const observationLabel = { o2sat: "oxygen saturation", heartrate: "heart rate", resprate: "respiratory rate", sbp: "systolic blood pressure", temperature: "temperature" };

function Rule({ rule, showStatus = false }) {
  const value = rule.observed_value;
  const compare = { "<": (a, b) => a < b, ">": (a, b) => a > b, ">=": (a, b) => a >= b }[rule.operator];
  const numeric = Number(value);
  const rounded = Number(numeric.toFixed(2));
  // Keep the exact value whenever rounding would change the rule's result.
  const display = compare && Number.isFinite(rule.threshold)
    && compare(numeric, rule.threshold) === compare(rounded, rule.threshold) ? rounded : numeric;
  const observed = value != null && Number.isFinite(Number(value))
    ? `${display} ${rule.unit || ""}` : "Observation unavailable";
  return <li style={{ marginTop: 7, lineHeight: 1.45 }}>
    <div style={{ fontWeight: 650 }}>{rule.label || rule.code}</div>
    <div style={{ color: T.slate }}>{observed} · Rule: {rule.criterion}</div>
    {showStatus && <div style={{ color: T.slate }}>{statusLabel[rule.status] || "Status unavailable"}</div>}
  </li>;
}

export default function DiscriminatorEvidence({ evidence }) {
  const available = evidence?.status === "EVALUATED";
  const decisive = items(evidence?.decisive);
  const matched = items(evidence?.matched);
  const decisiveCodes = new Set(decisive.map((rule) => rule.code));
  const other = matched.filter((rule) => !decisiveCodes.has(rule.code));
  const evaluated = items(evidence?.evaluated);
  const missing = items(evidence?.missing_observations);
  const policy = evidence?.model_policy || {};
  const thresholdApplied = policy.type === "modal_with_urgent_class_threshold"
    && policy.changed_prediction === true && policy.ml_acuity < policy.modal_acuity;
  return <section aria-label="Discriminators and decision evidence" style={{ marginTop: 18, paddingTop: 14, borderTop: `1px solid ${T.borderSoft}`, fontSize: 12 }}>
    <Eyebrow>Discriminators &amp; decision evidence</Eyebrow>
    {!available ? <p>Discriminator evidence was not recorded for this assessment. Run a new assessment to view it.</p> : <>
      {decisive.length > 0 && <>
        <strong>Changed the recommendation to {acuityLabel(evidence.safety_floor_acuity)}</strong>
        <ul style={{ paddingLeft: 18, marginTop: 4 }}>{decisive.map((rule) => <Rule key={rule.code} rule={rule} />)}</ul>
      </>}
      {other.length > 0 && <>
        <ul style={{ paddingLeft: 18, marginTop: 4 }}>{other.map((rule) => <Rule key={rule.code} rule={rule} />)}</ul>
      </>}
      {!matched.length && <p>No measured vital triggered these safety thresholds. This does not rule out other clinical risks.</p>}
      {missing.length > 0 && <p style={{ color: "#8A4B00" }}>Not assessed: {missing.map((field) => observationLabel[field] || field).join(", ")}. Missing observations are not negative findings.</p>}
      {thresholdApplied && <p style={{ lineHeight: 1.5 }}>
        Threshold rule raised the model estimate from {acuityLabel(policy.modal_acuity)} to {acuityLabel(policy.ml_acuity)}: {acuityLabel(policy.ml_acuity)} probability met {Number(policy.threshold) * 100}%.
      </p>}
      <details>
        <summary style={{ cursor: "pointer", color: T.green700, fontWeight: 650 }}>All evaluated safety checks ({evaluated.length})</summary>
        <ul style={{ paddingLeft: 18 }}>{evaluated.map((rule) => <Rule key={rule.code} rule={rule} showStatus />)}</ul>
        <div style={{ color: T.grey500, overflowWrap: "anywhere" }}>Rule version: {evidence.rule_version || "unavailable"}</div>
      </details>
    </>}
  </section>;
}
