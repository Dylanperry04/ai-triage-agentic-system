import React, { useState, useRef, useEffect } from "react";
import { SendHorizonal, ShieldCheck, RotateCcw } from "lucide-react";
import { T } from "../theme.js";
import { Card, Eyebrow, Spinner, Btn, Modal, ErrorNote } from "../atoms.jsx";
import { api } from "../api.js";

const SUGGESTED = [
  "Is the current security configuration safe?",
  "What happened in the audit log this week?",
  "Who submitted decisions in the last 7 days?",
  "Were there any access denials, and by which role?",
  "How many escalations are open and are any vitals overdue?",
  "Is the pinned UHL model artefact configured and present?",
];

export default function Itd({ identity, onDemoReset }) {
  const [msgs, setMsgs] = useState([{ from: "sys", text: "ITD assistant — answers system, security, governance, audit and model-artefact questions from recorded backend evidence, including the access-audit log (volumes, denials, who submitted what, escalations). Every figure is counted from a record and the evidence used is shown beneath each answer. It refuses patient-specific triage questions by design." }]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetPhrase, setResetPhrase] = useState("");
  const [resetBusy, setResetBusy] = useState(false);
  const [resetErr, setResetErr] = useState(null);
  const [resetDone, setResetDone] = useState(null);
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, busy]);

  const ask = async (q) => {
    const text = (q || input).trim(); if (!text || busy) return;
    setMsgs((m) => [...m, { from: "me", text }]); setInput(""); setBusy(true);
    try {
      const r = await api.systemAssistant(text);
      setMsgs((m) => [...m, { from: "sys", text: r?.answer || "No answer returned.", refused: r?.status === "refused_patient_context", evidence: { ...(r?.evidence || {}), ...(r?.audit_evidence || {}) } }]);
    } catch (e) {
      setMsgs((m) => [...m, { from: "sys", text: `Assistant unavailable: ${e.detail || e.message}`, refused: true }]);
    } finally { setBusy(false); }
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
          <div style={{ fontSize: 12.5, color: T.slate }}>System · security · governance · audit — no patient content, enforced server-side.</div>
        </div>
        <Btn kind="quiet" style={{ marginLeft: "auto" }} onClick={() => { setResetOpen(true); setResetErr(null); }}>
          <RotateCcw size={14} style={{ verticalAlign: -2, marginRight: 6 }} />Reset demo data
        </Btn>
      </div>
      <Card style={{ flex: 1, marginTop: 14, padding: 16, overflowY: "auto", display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
        {msgs.map((m, i) => (
          <div key={i} style={{ alignSelf: m.from === "me" ? "flex-end" : "flex-start", maxWidth: "82%" }} className="fade-up">
            <div style={{ background: m.from === "me" ? T.green700 : m.refused ? T.yellow50 : "#F4F6F7", color: m.from === "me" ? "#fff" : T.ink, border: m.from === "me" ? "none" : `1px solid ${m.refused ? "#EEDD9A" : T.borderSoft}`, borderRadius: 12, padding: "10px 13px", fontSize: 13.5, lineHeight: 1.55, whiteSpace: "pre-wrap" }}>{m.text}</div>
            {m.evidence && typeof m.evidence === "object" && (
              <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 5 }}>
                {Object.entries(m.evidence).slice(0, 8).map(([k, v]) => <span key={k} style={{ fontSize: 10.5, fontFamily: T.mono, color: T.slate, background: "#F1F4F5", border: `1px solid ${T.borderSoft}`, borderRadius: 6, padding: "2px 7px" }}>{k}: {String(v).slice(0, 40)}</span>)}
              </div>
            )}
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
