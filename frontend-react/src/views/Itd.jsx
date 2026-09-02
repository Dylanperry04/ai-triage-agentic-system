import React, { useState, useRef, useEffect } from "react";
import { SendHorizonal, ShieldCheck, RotateCcw, Download, Database } from "lucide-react";
import { T } from "../theme.js";
import { Card, Eyebrow, Spinner, Btn, Modal, ErrorNote } from "../atoms.jsx";
import { api } from "../api.js";

const SUGGESTED = [
  "How many patients have been escalated today and which staff member escalated the most?",
  "At what hour were assessments busiest today?",
  "Which acuity level is overridden most frequently?",
  "What were the main recorded reasons for overrides?",
  "Is the current security configuration safe?",
  "What roles exist and what can each role do?",
  "Were there any access denials, and by which role?",
  "How many escalations are open and are any vitals overdue?",
];

export default function Itd({ identity, onDemoReset }) {
  const [msgs, setMsgs] = useState([{ from: "sys", text: "ITD assistant — ask a plain-language question about the system or any calculation supported by the recorded audit fields. Foundry may translate your wording into a validated read-only plan; the backend calculates every figure and will say when the requested fact was not recorded." }]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetPhrase, setResetPhrase] = useState("");
  const [resetBusy, setResetBusy] = useState(false);
  const [resetErr, setResetErr] = useState(null);
  const [resetDone, setResetDone] = useState(null);
  const [exports, setExports] = useState(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportErr, setExportErr] = useState(null);
  const [preview, setPreview] = useState(null);
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, busy]);
  const loadExports = () => api.retrainingExports().then(async (r) => {
    setExports(r); setExportErr(null);
    if (r.demo_preview_available) {
      try { setPreview(await api.currentRetrainingPreview()); }
      catch { setPreview(null); }
    } else setPreview(null);
  }).catch((e) => setExportErr(e.detail || e.message));
  useEffect(() => { loadExports(); }, []);

  const ask = async (q) => {
    const text = (q || input).trim(); if (!text || busy) return;
    setMsgs((m) => [...m, { from: "me", text }]); setInput(""); setBusy(true);
    try {
      const r = await api.systemAssistant(text);
      setMsgs((m) => [...m, { from: "sys", text: r?.answer || "No answer returned.", refused: r?.status === "refused_patient_context" }]);
    } catch (e) {
      setMsgs((m) => [...m, { from: "sys", text: `Assistant unavailable: ${e.detail || e.message}`, refused: true }]);
    } finally { setBusy(false); }
  };

  const generateExport = async () => {
    if (exportBusy) return;
    setExportBusy(true); setExportErr(null);
    try { await api.generateRetrainingExport(exports?.next_reporting_month); await loadExports(); }
    catch (e) { setExportErr(e.detail || e.message); }
    finally { setExportBusy(false); }
  };
  const downloadExport = async (month) => {
    try {
      const result = await api.downloadRetrainingExport(month);
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = result.filename; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setExportErr(e.detail || e.message); }
  };
  const downloadPreview = async () => {
    try {
      const result = await api.downloadCurrentRetrainingPreview();
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = result.filename; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setExportErr(e.detail || e.message); }
  };

  const runReset = async () => {
    setResetBusy(true); setResetErr(null);
    try {
      const r = await api.demoReset(resetPhrase);
      setResetDone(r);
      setMsgs((m) => [...m, { from: "sys", text: `Demo state reset. ${r.records_archived} record(s) archived to ${r.archive_directory}. Nothing was deleted — the previous state can be restored by moving those files back.` }]);
      setResetOpen(false); setResetPhrase("");
      onDemoReset?.();   // backend is empty; drop the now-stale client state
    } catch (e) {
      setResetErr(e.detail || e.message);
    } finally { setResetBusy(false); }
  };

  return (
    <div style={{ maxWidth: 780, margin: "0 auto", display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 38, height: 38, borderRadius: 10, background: "#0E3B4D", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center" }}><ShieldCheck size={18} /></div>
        <div>
          <div style={{ fontSize: 19, fontWeight: 700 }}>ITD console</div>
          <div style={{ fontSize: 12.5, color: T.slate }}>System · security · governance · audit evidence</div>
        </div>
        <Btn kind="quiet" style={{ marginLeft: "auto" }} onClick={() => { setResetOpen(true); setResetErr(null); }}>
          <RotateCcw size={14} style={{ verticalAlign: -2, marginRight: 6 }} />Reset demo data
        </Btn>
      </div>
      <Card style={{ marginTop: 14, padding: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
          <Database size={17} style={{ color: T.green700 }} />
          <div style={{ flex: 1 }}><div style={{ fontSize: 14, fontWeight: 800 }}>Monthly retraining data</div><div style={{ fontSize: 11.5, color: T.slate, marginTop: 2 }}>Prepared from the previous calendar month's eligible, exactly-linked clinician feedback. Download only — no training is started.</div></div>
          <Btn kind="quiet" disabled={exportBusy || !exports?.next_reporting_month} onClick={generateExport}>{exportBusy ? "Preparing…" : `Prepare ${exports?.next_reporting_month || "previous month"}`}</Btn>
        </div>
        {exportErr && <div style={{ marginTop: 8 }}><ErrorNote>{String(exportErr)}</ErrorNote></div>}
        {exports === null && !exportErr && <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: T.slate, marginTop: 10 }}><Spinner size={12} /> Checking prepared exports…</div>}
        {preview && <div style={{ borderTop: `1px solid ${T.borderSoft}`, marginTop: 10, paddingTop: 10, display: "grid", gridTemplateColumns: "1fr auto", gap: 10, alignItems: "center", background: T.yellow50, borderRadius: 8, padding: 10 }}>
          <div style={{ fontSize: 11.5, color: T.slate, lineHeight: 1.45 }}><b>Demo preview — {preview.reporting_month}</b><br />{preview.eligible_cases || 0} eligible record(s) from actions performed so far this month. This is not the official completed-month artifact and is not persisted or notified.</div>
          <Btn kind="quiet" onClick={downloadPreview}><Download size={13} style={{ verticalAlign: -2, marginRight: 5 }} />Download demo preview</Btn>
        </div>}
        {(exports?.exports || []).slice(0, 4).map((row) => <div key={row.reporting_month} style={{ display: "grid", gridTemplateColumns: "90px 1fr auto", alignItems: "center", gap: 10, borderTop: `1px solid ${T.borderSoft}`, marginTop: 10, paddingTop: 10 }}>
          <div><div style={{ fontFamily: T.mono, fontSize: 12.5, fontWeight: 800 }}>{row.reporting_month}</div><div style={{ fontSize: 10.5, color: T.grey500 }}>{String(row.generated_at_utc || "").slice(0, 10)} · rev {row.revision || 1}</div></div>
          <div style={{ fontSize: 11.5, color: T.slate, lineHeight: 1.45 }}><b>{row.eligible_cases || 0}</b> eligible · {row.accepted_cases || 0} accepted · {row.overrides || 0} overrides · {row.excluded_unresolved_cases || 0} unresolved/excluded · <b>{row.download_status || "unknown"}</b></div>
          <Btn kind="quiet" onClick={() => downloadExport(row.reporting_month)}><Download size={13} style={{ verticalAlign: -2, marginRight: 5 }} />Download Monthly Retraining Data</Btn>
        </div>)}
        {exports && !(exports.exports || []).length && <div style={{ fontSize: 12, color: T.slate, marginTop: 10 }}>No monthly export has been finalised yet.</div>}
      </Card>
      <Card style={{ flex: 1, marginTop: 14, padding: 16, overflowY: "auto", display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
        {msgs.map((m, i) => (
          <div key={i} style={{ alignSelf: m.from === "me" ? "flex-end" : "flex-start", maxWidth: "82%" }} className="fade-up">
            <div style={{ background: m.from === "me" ? T.green700 : m.refused ? T.yellow50 : "#F4F6F7", color: m.from === "me" ? "#fff" : T.ink, border: m.from === "me" ? "none" : `1px solid ${m.refused ? "#EEDD9A" : T.borderSoft}`, borderRadius: 12, padding: "10px 13px", fontSize: 13.5, lineHeight: 1.55, whiteSpace: "pre-wrap" }}>{m.text}</div>
          </div>
        ))}
        {busy && <div style={{ display: "flex", gap: 9, alignItems: "center", color: T.slate, fontSize: 13 }}><Spinner size={13} /> Checking recorded evidence…</div>}
        <div ref={endRef} />
      </Card>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
        {SUGGESTED.map((s) => <button key={s} onClick={() => ask(s)} style={{ fontFamily: T.font, fontSize: 12, fontWeight: 600, color: T.green900, background: T.green50, border: `1px solid #CBEADF`, borderRadius: 999, padding: "6px 12px", cursor: "pointer" }}>{s}</button>)}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} placeholder="Ask about the audit log, security posture, access denials, roles, escalations, model artefacts…" style={{ flex: 1, fontFamily: T.font, fontSize: 13.5, padding: "11px 13px", borderRadius: 10, border: `1px solid ${T.border}` }} />
        <button onClick={() => ask()} disabled={busy || !input.trim()} style={{ width: 46, borderRadius: 10, border: "none", background: T.green700, color: "#fff", cursor: "pointer", opacity: busy || !input.trim() ? 0.5 : 1, display: "flex", alignItems: "center", justifyContent: "center" }}><SendHorizonal size={16} /></button>
      </div>
      {resetOpen && (
        <Modal title="Reset demo data" onClose={() => { setResetOpen(false); setResetErr(null); }} width={540}>
          <div style={{ fontSize: 13.5, color: T.ink, lineHeight: 1.6 }}>
            This returns the app to an empty starting state: triage decisions, reviews,
            model-assessment runs, reassessments and the access-audit log are cleared
            from the live view.
          </div>
          <div style={{ fontSize: 12.5, color: T.slate, lineHeight: 1.6, marginTop: 10 }}>
            Nothing is deleted. Every file is archived to a timestamped folder on the
            server and can be restored by moving it back. The reset is recorded as the
            first entry of the new audit log, against your identity. It is refused
            outright in patient-data mode.
          </div>
          <div style={{ fontSize: 12.5, color: T.slate, marginTop: 12, marginBottom: 6 }}>
            Type <b>RESET DEMO DATA</b> to confirm.
          </div>
          <input value={resetPhrase} onChange={(e) => setResetPhrase(e.target.value)}
            placeholder="RESET DEMO DATA"
            style={{ width: "100%", boxSizing: "border-box", fontFamily: T.mono, fontSize: 13.5, padding: "10px 12px", borderRadius: 9, border: `1px solid ${T.border}` }} />
          {resetErr && <div style={{ marginTop: 10 }}><ErrorNote>{String(resetErr)}</ErrorNote></div>}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
            <Btn kind="quiet" onClick={() => { setResetOpen(false); setResetErr(null); }}>Cancel</Btn>
            <Btn kind="danger" disabled={resetBusy || resetPhrase.trim().toUpperCase() !== "RESET DEMO DATA"} onClick={runReset}>
              {resetBusy ? "Resetting…" : "Reset demo data"}
            </Btn>
          </div>
        </Modal>
      )}
    </div>
  );
}
