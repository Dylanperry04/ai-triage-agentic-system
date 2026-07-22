import React from "react";
import { X } from "lucide-react";
import { T } from "./theme.js";

export const GlobalStyle = () => (
  <style>{`
    html, body, #root { height: 100%; }
    @keyframes toastIn { from { opacity:0; transform:translateY(-8px);} to { opacity:1; transform:translateY(0);} }
    @keyframes bellPing { 0%{transform:scale(1)} 20%{transform:scale(1.25) rotate(8deg)} 40%{transform:scale(1) rotate(-6deg)} 60%{transform:scale(1.12)} 100%{transform:scale(1)} }
    @keyframes fadeUp { from { opacity:0; transform:translateY(6px);} to { opacity:1; transform:translateY(0);} }
    @keyframes spin { to { transform: rotate(360deg); } }
    .fade-up { animation: fadeUp .28s ease both; }
    .alter *:focus-visible { outline: 3px solid #73E6C2; outline-offset: 2px; border-radius: 6px; }
    .alter ::-webkit-scrollbar { width: 8px; height: 8px; }
    .alter ::-webkit-scrollbar-thumb { background: #C4CCD1; border-radius: 8px; }
    .alter ::-webkit-scrollbar-track { background: transparent; }
    .clickable { cursor: pointer; transition: transform .12s ease, box-shadow .12s ease, background .12s ease; }
    .clickable:hover { transform: translateY(-1px); }
    @media (prefers-reduced-motion: reduce) { .alter * { animation: none !important; transition: none !important; } }
  `}</style>
);

export const Eyebrow = ({ children, style }) => (
  <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase", color: T.grey500, ...style }}>{children}</div>
);
export const Card = ({ children, style, className, onClick }) => (
  <div className={className} onClick={onClick} style={{ background: T.surface, border: `1px solid ${T.borderSoft}`, borderRadius: 12, boxShadow: "0 1px 2px rgba(33,43,50,0.05)", ...style }}>{children}</div>
);
export const Btn = ({ children, kind = "primary", onClick, style, disabled, title, type = "button" }) => {
  const kinds = {
    primary: { background: T.green700, color: "#fff", border: `1px solid ${T.green700}` },
    dark: { background: "#0E3B4D", color: "#fff", border: "1px solid #0E3B4D" },
    quiet: { background: T.surface, color: T.ink, border: `1px solid ${T.border}` },
    danger: { background: T.surface, color: T.red, border: "1px solid #E7C3CE" },
    ghost: { background: "transparent", color: T.slate, border: "1px solid transparent" },
  };
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick} className="clickable"
      style={{ fontFamily: T.font, fontSize: 13.5, fontWeight: 600, padding: "9px 14px", borderRadius: 9, opacity: disabled ? 0.5 : 1, cursor: disabled ? "default" : "pointer", ...kinds[kind], ...style }}>{children}</button>
  );
};
export const Spinner = ({ size = 15, colour = T.green700 }) => (
  <span style={{ display: "inline-block", width: size, height: size, border: `2.5px solid ${colour}33`, borderTopColor: colour, borderRadius: "50%", animation: "spin .8s linear infinite" }} />
);
export const HseTile = ({ size = 44 }) => (
  <div style={{ width: size, height: size, borderRadius: size * 0.24, background: T.green700, display: "flex", alignItems: "center", justifyContent: "center", color: "#fff", fontWeight: 700, fontSize: size * 0.36, letterSpacing: "-0.02em", flexShrink: 0 }}>hse</div>
);
export const ConfBar = ({ v, colour }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
    <div style={{ flex: 1, height: 7, background: "#EAEEF0", borderRadius: 6, overflow: "hidden" }}>
      <div style={{ width: `${Math.min(100, Math.max(0, v * 100)).toFixed(1)}%`, height: "100%", background: colour, borderRadius: 6 }} />
    </div>
    <span style={{ fontFamily: T.mono, fontSize: 13, fontWeight: 700, color: T.ink }}>{(v * 100).toFixed(1)}%</span>
  </div>
);
export const Pill = ({ children, colour = T.slate, bg = "#F1F4F5", border = T.borderSoft, style }) => (
  <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", color: colour, background: bg, border: `1px solid ${border}`, borderRadius: 999, padding: "3px 9px", whiteSpace: "nowrap", ...style }}>{children}</span>
);
export function Modal({ title, children, onClose, width = 500, maxHeight = "88vh" }) {
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : "Dialog"} style={{ position: "fixed", inset: 0, background: "rgba(20,28,32,0.44)", zIndex: 70, display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }} onClick={onClose}>
      <Card style={{ width, maxWidth: "calc(100vw - 32px)", padding: 20, maxHeight, overflowY: "auto" }} onClick={(e) => e.stopPropagation()} className="fade-up">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <div style={{ fontSize: 16.5, fontWeight: 700 }}>{title}</div>
          <button onClick={onClose} aria-label="Close" style={{ border: "none", background: "none", cursor: "pointer", color: T.grey500 }}><X size={17} /></button>
        </div>
        {children}
      </Card>
    </div>
  );
}
export function Toasts({ toasts }) {
  return (
    <div style={{ position: "fixed", top: 74, right: 20, zIndex: 90, display: "flex", flexDirection: "column", gap: 8, width: 350 }}>
      {toasts.map((t) => (
        <div key={t.id} style={{ animation: "toastIn .25s ease", background: T.surface, border: `1px solid ${T.borderSoft}`, borderLeft: `4px solid ${t.tone === "warn" ? "#E86C00" : t.tone === "err" ? T.red : T.green500}`, borderRadius: 10, boxShadow: "0 10px 26px rgba(33,43,50,0.14)", padding: "11px 13px" }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: T.ink }}>{t.title}</div>
          {t.body && <div style={{ fontSize: 12.5, color: T.slate, marginTop: 2, lineHeight: 1.4 }}>{t.body}</div>}
        </div>
      ))}
    </div>
  );
}
export const EmptyState = ({ children }) => (
  <div style={{ textAlign: "center", color: T.slate, fontSize: 13.5, padding: "42px 12px", lineHeight: 1.5 }}>{children}</div>
);
export const ErrorNote = ({ children }) => (
  <div style={{ background: T.red50, border: "1px solid #EFC5D1", color: T.red, borderRadius: 10, padding: "10px 13px", fontSize: 13, lineHeight: 1.45 }}>{children}</div>
);
