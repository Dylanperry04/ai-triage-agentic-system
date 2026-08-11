import React, { useState, useEffect, useMemo } from "react";
import { Download } from "lucide-react";
import { T, patientWithEncounter, acuityLabel } from "../theme.js";
import { Card, Eyebrow, Btn, EmptyState, ErrorNote, Spinner, Pill } from "../atoms.jsx";
import { api } from "../api.js";

const toneFor = (a) => /DENIED/i.test(a) ? { c: T.red, bg: T.red50, b: "#EFC5D1" } : /overrid/i.test(a) ? { c: "#8A4B00", bg: T.yellow50, b: "#EEDD9A" } : /escalat/i.test(a) ? { c: T.red, bg: T.red50, b: "#EFC5D1" } : { c: T.slate, bg: "#F1F4F5", b: T.borderSoft };
const fmtAt = (x) => String(x || "").replace("T", " ").slice(0, 16);

const ROLES = ["", "triage_nurse", "ed_doctor", "clinical_supervisor", "researcher", "security_admin", "governance_auditor"];
const DECISIONS = ["", "ACCEPTED_AS_PRESENTED", "OVERRIDDEN", "REQUEST_MORE_INFORMATION", "ESCALATION_REQUIRED", "ESCALATION_CONFIRMED", "ESCALATION_RESOLVED", "DISCHARGED", "CASE_CLOSED", "UNCERTAIN"];
const ESCALATIONS = ["", "requested", "pending", "confirmed", "resolved", "rejected", "closed"];
const RANGES = [["24h", 1], ["7d", 7], ["30d", 30], ["all", null]];

const sel = { fontFamily: "inherit", fontSize: 12.5, padding: "7px 9px", borderRadius: 8, border: `1px solid ${T.border}`, background: T.surface, color: T.ink };

function csvOf(entries) {
  const cols = ["timestamp_utc", "display_identifier", "reviewer_role", "decision_type", "action_type", "triage_level", "escalation_status", "override_status", "review_comment"];
  const esc = (v) => { const s = v == null ? "" : Array.isArray(v) ? v.join("; ") : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [cols.join(","), ...entries.map((e) => cols.map((c) => esc(e[c] ?? (c === "reviewer_role" ? e.reviewer_roles : undefined))).join(","))].join("\n");
}

/* Decisions & workflow view: bound to /audit/dashboard, which supports the
   full week-6 filter set server-side (case, role, decision, action, triage
   level, escalation, override, dataset, time range). */
function DecisionsView() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ patient_or_case: "", reviewer_role: "", decision_type: "", triage_level: "", escalation_status: "", override_status: "", days: 30 });

  useEffect(() => {
    let dead = false;
    setBusy(true);
    const params = { limit: 1000 };
    if (f.patient_or_case.trim()) params.patient_or_case = f.patient_or_case.trim();
    if (f.reviewer_role) params.reviewer_role = f.reviewer_role;
    if (f.decision_type) params.decision_type = f.decision_type;
    if (f.triage_level) params.triage_level = f.triage_level;
    if (f.escalation_status) params.escalation_status = f.escalation_status;
    if (f.override_status) params.override_status = f.override_status;
    if (f.days) params.start_utc = new Date(Date.now() - f.days * 86400000).toISOString();
    const t = setTimeout(() => {
      api.auditDashboard(params)
        .then((d) => { if (!dead) { setData(d); setErr(null); } })
        .catch((e) => { if (!dead) setErr(e.detail || e.message); })
        .finally(() => !dead && setBusy(false));
    }, 250);
    return () => { dead = true; clearTimeout(t); };
  }, [f]);

  const entries = data?.entries || [];
  const exportCsv = () => {
    const blob = new Blob([csvOf(entries)], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `audit-decisions-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));

  return (
    <>
      <Card style={{ padding: "12px 14px", marginTop: 12 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input value={f.patient_or_case} onChange={set("patient_or_case")} placeholder="Patient or stay contains…" style={{ ...sel, width: 210 }} />
          <select value={f.reviewer_role} onChange={set("reviewer_role")} style={sel}>{ROLES.map((r) => <option key={r} value={r}>{r ? r.replace(/_/g, " ") : "Any role"}</option>)}</select>
          <select value={f.decision_type} onChange={set("decision_type")} style={sel}>{DECISIONS.map((d) => <option key={d} value={d}>{d ? d.replace(/_/g, " ").toLowerCase() : "Any decision"}</option>)}</select>
          <select value={f.triage_level} onChange={set("triage_level")} style={sel}><option value="">Any acuity</option>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={String(n)}>{acuityLabel(n)}</option>)}</select>
          <select value={f.escalation_status} onChange={set("escalation_status")} style={sel}>{ESCALATIONS.map((e) => <option key={e} value={e}>{e || "Any escalation"}</option>)}</select>
          <select value={f.override_status} onChange={set("override_status")} style={sel}><option value="">Overrides: any</option><option value="yes">Overridden only</option><option value="no">Not overridden</option></select>
          <div style={{ display: "flex", gap: 4 }}>
            {RANGES.map(([label, days]) => (
              <button key={label} onClick={() => setF((x) => ({ ...x, days }))} style={{ ...sel, cursor: "pointer", fontWeight: 700, padding: "7px 11px", background: f.days === days ? T.green700 : T.surface, color: f.days === days ? "#fff" : T.ink, border: `1px solid ${f.days === days ? T.green700 : T.border}` }}>{label}</button>
            ))}
          </div>
          <Btn kind="quiet" onClick={exportCsv} disabled={!entries.length} style={{ marginLeft: "auto" }}><Download size={14} style={{ verticalAlign: -2, marginRight: 6 }} />Export CSV ({entries.length})</Btn>
        </div>
      </Card>
      {err && <div style={{ marginTop: 12 }}><ErrorNote>{err}</ErrorNote></div>}
      {!err && !data && <div style={{ display: "flex", gap: 10, alignItems: "center", color: T.slate, fontSize: 13.5, marginTop: 16 }}><Spinner /> Loading audit evidence…</div>}
      {data && (
        <>
          <div style={{ fontSize: 12.5, color: T.slate, marginTop: 12 }}>{busy ? "Filtering… " : ""}{data.count?.toLocaleString?.() ?? entries.length} matching record{entries.length === 1 ? "" : "s"} of {data.total_unfiltered?.toLocaleString?.() ?? "—"} in the window.</div>
          <Card style={{ marginTop: 10, overflow: "hidden" }}>
            {entries.slice(0, 200).map((e, i) => {
              const action = e.decision_type || e.action_type || e.record_kind || "record";
              const tone = toneFor(String(action));
              return (
                <div key={i} style={{ display: "flex", gap: 12, alignItems: "flex-start", padding: "10px 14px", borderTop: i ? `1px solid ${T.borderSoft}` : "none", fontSize: 12.5 }}>
                  <span style={{ fontFamily: T.mono, color: T.grey500, whiteSpace: "nowrap" }}>{fmtAt(e.timestamp_utc)}</span>
                  <Pill colour={tone.c} bg={tone.bg} border={tone.b}>{String(action).replace(/_/g, " ").toLowerCase()}</Pill>
                  <span style={{ fontWeight: 700, color: T.slate, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 240 }}>{patientWithEncounter(e)}</span>
                  <span style={{ color: T.slate, whiteSpace: "nowrap" }}>{String(e.reviewer_role || (e.reviewer_roles || [])[0] || "").replace(/_/g, " ")}</span>
                  {e.escalation_status && <Pill colour={T.red} bg={T.red50} border="#EFC5D1">esc: {e.escalation_status}</Pill>}
                  {e.override_status === "yes" && <Pill colour="#8A4B00" bg={T.yellow50} border="#EEDD9A">override</Pill>}
                  <span style={{ color: T.ink, flex: 1, minWidth: 120, lineHeight: 1.45 }}>{e.review_comment || e.override_reason || ""}</span>
                </div>
              );
            })}
            {!entries.length && <EmptyState>No audit records match these filters.</EmptyState>}
          </Card>
          {entries.length > 200 && <div style={{ fontSize: 12, color: T.grey500, marginTop: 8 }}>Showing the first 200 — narrow the filters or export the full CSV.</div>}
        </>
      )}
    </>
  );
}

/* Access-events view: authentication / permission decisions from the access
   audit trail (unchanged data source). */
function AccessView() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  const [q, setQ] = useState("");
  useEffect(() => {
    let dead = false;
    api.auditEvents(400).then((r) => { if (!dead) setRows(r.events || r.entries || []); })
      .catch((e) => { if (!dead) { setErr(e.detail || e.message); setRows([]); } });
    return () => { dead = true; };
  }, []);
  const filtered = useMemo(() => (rows || []).filter((r) => {
    const s = q.trim().toLowerCase();
    return !s || JSON.stringify(r).toLowerCase().includes(s);
  }), [rows, q]);
  return (
    <>
      <Card style={{ padding: "12px 14px", marginTop: 12 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter access events…" style={{ ...sel, width: 260 }} />
      </Card>
      {err && <div style={{ marginTop: 12 }}><ErrorNote>{err}</ErrorNote></div>}
      {!rows && !err && <div style={{ display: "flex", gap: 10, alignItems: "center", color: T.slate, fontSize: 13.5, marginTop: 16 }}><Spinner /> Loading access events…</div>}
      {rows && (
        <Card style={{ marginTop: 12, overflow: "hidden" }}>
          {filtered.slice(0, 200).map((r, i) => {
            const action = `${r.action || "access"} · ${r.decision || ""}`.trim();
            const tone = toneFor(action);
            return (
              <div key={i} style={{ display: "flex", gap: 12, alignItems: "center", padding: "10px 14px", borderTop: i ? `1px solid ${T.borderSoft}` : "none", fontSize: 12.5 }}>
                <span style={{ fontFamily: T.mono, color: T.grey500, whiteSpace: "nowrap" }}>{fmtAt(r.timestamp_utc || r.timestamp)}</span>
                <Pill colour={tone.c} bg={tone.bg} border={tone.b}>{action}</Pill>
                <span style={{ color: T.slate }}>{r.display_name || r.user_id || "—"}</span>
                <span style={{ fontFamily: T.mono, color: T.grey500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.permission || r.action || ""}</span>
              </div>
            );
          })}
          {!filtered.length && <EmptyState>No access events match.</EmptyState>}
        </Card>
      )}
    </>
  );
}

export default function AuditLog() {
  const [view, setView] = useState("decisions");
  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ fontSize: 19, fontWeight: 700 }}>Audit log</div>
          <div style={{ fontSize: 13, color: T.slate, marginTop: 2 }}>Every clinical decision and access decision, recorded against an authenticated identity. Redacted evidence only — no raw identifiers.</div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {[["decisions", "Decisions & workflow"], ["access", "Access events"]].map(([k, label]) => (
            <button key={k} onClick={() => setView(k)} style={{ fontFamily: T.font, fontSize: 12.5, fontWeight: 700, padding: "6px 13px", borderRadius: 999, cursor: "pointer", border: `1px solid ${view === k ? T.green700 : T.border}`, background: view === k ? T.green700 : T.surface, color: view === k ? "#fff" : T.ink }}>{label}</button>
          ))}
        </div>
      </div>
      {view === "decisions" ? <DecisionsView /> : <AccessView />}
    </div>
  );
}
