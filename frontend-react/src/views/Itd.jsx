import React, { useState, useRef, useEffect } from "react";
import { SendHorizonal, ShieldCheck } from "lucide-react";
import { T } from "../theme.js";
import { Card, Eyebrow, Spinner } from "../atoms.jsx";
import { api } from "../api.js";

const SUGGESTED = [
  "Is the current security configuration safe?",
  "Which audit sink is active and is it durable?",
  "Is the full-MIMIC model artefact configured and present?",
  "What warnings exist in the current deployment profile?",
];

export default function Itd({ identity }) {
  const [msgs, setMsgs] = useState([{ from: "sys", text: "ITD assistant — answers system, security, governance, audit and model-artefact questions from recorded backend evidence. It refuses patient-specific triage questions by design." }]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, busy]);

  const ask = async (q) => {
    const text = (q || input).trim(); if (!text || busy) return;
    setMsgs((m) => [...m, { from: "me", text }]); setInput(""); setBusy(true);
    try {
      const r = await api.systemAssistant(text);
      setMsgs((m) => [...m, { from: "sys", text: r?.answer || "No answer returned.", refused: r?.status === "refused_patient_context", evidence: r?.evidence || r?.evidence_scope }]);
    } catch (e) {
      setMsgs((m) => [...m, { from: "sys", text: `Assistant unavailable: ${e.detail || e.message}`, refused: true }]);
    } finally { setBusy(false); }
  };

  return (
    <div style={{ maxWidth: 780, margin: "0 auto", display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 38, height: 38, borderRadius: 10, background: "#0E3B4D", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center" }}><ShieldCheck size={18} /></div>
        <div>
          <div style={{ fontSize: 19, fontWeight: 700 }}>ITD console</div>
          <div style={{ fontSize: 12.5, color: T.slate }}>System · security · governance · audit — no patient content, enforced server-side.</div>
        </div>
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
        <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} placeholder="Ask about security posture, audit, governance, model artefacts…" style={{ flex: 1, fontFamily: T.font, fontSize: 13.5, padding: "11px 13px", borderRadius: 10, border: `1px solid ${T.border}` }} />
        <button onClick={() => ask()} disabled={busy || !input.trim()} style={{ width: 46, borderRadius: 10, border: "none", background: T.green700, color: "#fff", cursor: "pointer", opacity: busy || !input.trim() ? 0.5 : 1, display: "flex", alignItems: "center", justifyContent: "center" }}><SendHorizonal size={16} /></button>
      </div>
    </div>
  );
}
