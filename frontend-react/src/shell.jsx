import React from "react";
import { Bell, LogOut, Search, ChevronLeft, ChevronRight, Activity, ClipboardList, LayoutDashboard, ShieldCheck, ScrollText, ArrowUpRight, HeartPulse, ServerCog, MessageSquareWarning, BarChart3 } from "lucide-react";
import { T, initialsOf, fmtTime } from "./theme.js";
import { Card, Eyebrow, HseTile } from "./atoms.jsx";

export const NAV = {
  triage:      { label: "Triage Queue",     icon: ClipboardList, group: "Workspace" },
  review:      { label: "Review Queue",     icon: LayoutDashboard, group: "Workspace" },
  escalations: { label: "Escalations",      icon: ArrowUpRight, group: "Workspace" },
  analytics:   { label: "Analytics",        icon: Activity, group: "Oversight" },
  audit:       { label: "Audit Log",        icon: ScrollText, group: "Oversight" },
  model:       { label: "Model Evidence",   icon: BarChart3, group: "Oversight" },
  itd:         { label: "ITD Console",      icon: MessageSquareWarning, group: "System" },
  health:      { label: "System Health",    icon: ServerCog, group: "System" },
};

/* Backend visible_tabs (app/security/authz.py) → UI views. The backend list is
   authoritative; explainability is folded into the advisory panel and
   maintainability into System Health, per the v18 UI decision. */
export function navForSession(session) {
  const tabs = new Set(session?.visible_tabs || []);
  const perms = new Set(session?.permissions || []);
  const roles = new Set(session?.roles || []);
  const items = [];
  if (tabs.has("triage_review")) items.push("triage");
  if (tabs.has("review_queue")) items.push("review");
  if (tabs.has("review_queue") && (roles.has("ed_doctor") || roles.has("clinical_supervisor") || roles.has("security_admin"))) items.push("escalations");
  if (tabs.has("audit_dashboard")) items.push("analytics", "audit");
  if (tabs.has("model_performance")) items.push("model");
  if (tabs.has("itd_ask_tools") && perms.has("can_ask_chatbot")) items.push("itd");
  if (tabs.has("governance") || tabs.has("system_status") || roles.has("security_admin")) items.push("health");
  return items;
}

export function Sidebar({ session, tab, setTab, collapsed, setCollapsed, presentation }) {
  const items = navForSession(session);
  const groups = ["Workspace", "Oversight", "System"];
  return (
    <aside style={{ width: collapsed ? 72 : 252, transition: "width .22s ease", background: `linear-gradient(180deg, ${T.green900}, #013A33)`, color: "#E9F5F1", display: "flex", flexDirection: "column", flexShrink: 0, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 11, padding: collapsed ? "18px 14px" : "18px 16px" }}>
        <HseTile size={40} />
        {!collapsed && (
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 15, whiteSpace: "nowrap" }}>ALTER Triage</div>
            <div style={{ fontSize: 11.5, color: "#9CC8BD", whiteSpace: "nowrap" }}>University Hospital Limerick</div>
          </div>
        )}
      </div>
      <nav style={{ flex: 1, padding: "6px 10px", overflowY: "auto" }}>
        {groups.map((g) => {
          const keys = Object.keys(NAV).filter((k) => NAV[k].group === g && items.includes(k));
          if (!keys.length) return null;
          return (
            <div key={g} style={{ marginBottom: 14 }}>
              {!collapsed && <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: "#7FB5A8", padding: "8px 10px 5px" }}>{g}</div>}
              {keys.map((k) => {
                const Ic = NAV[k].icon; const active = tab === k;
                return (
                  <button key={k} onClick={() => setTab(k)} title={NAV[k].label}
                    style={{ width: "100%", display: "flex", alignItems: "center", gap: 11, padding: collapsed ? "11px 0" : "10px 11px", justifyContent: collapsed ? "center" : "flex-start", borderRadius: 9, border: "none", cursor: "pointer", fontFamily: T.font, fontSize: 13.5, fontWeight: 600, marginBottom: 2, background: active ? T.green500 : "transparent", color: active ? "#00332B" : "#D4EAE2" }}
                    onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "rgba(255,255,255,0.07)"; }}
                    onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}>
                    <Ic size={17} strokeWidth={2.1} /> {!collapsed && NAV[k].label}
                  </button>
                );
              })}
            </div>
          );
        })}
      </nav>
      <div style={{ padding: 10, borderTop: "1px solid rgba(255,255,255,0.09)" }}>
        <button onClick={() => setCollapsed((c) => !c)} title={collapsed ? "Expand navigation" : "Collapse navigation"}
          style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, padding: "9px 0", borderRadius: 9, border: "1px solid rgba(255,255,255,0.16)", background: "transparent", color: "#D4EAE2", cursor: "pointer", fontFamily: T.font, fontSize: 12.5, fontWeight: 600 }}>
          {collapsed ? <ChevronRight size={15} /> : <><ChevronLeft size={15} /> Collapse</>}
        </button>
      </div>
    </aside>
  );
}

export function Header({ identity, roleLabel, onSignOut, notifs, notifOpen, onToggleNotifs, onDismissNotif, onOpenCase, query, setQuery, ringKey, canSearch }) {
  const unread = notifs.filter((n) => !n.read).length;
  return (
    <header style={{ height: 62, background: T.surface, borderBottom: `1px solid ${T.borderSoft}`, display: "flex", alignItems: "center", gap: 14, padding: "0 18px", position: "relative", zIndex: 30, flexShrink: 0 }}>
      {canSearch ? (
        <div style={{ position: "relative", flex: 1, maxWidth: 460 }}>
          <Search size={15} style={{ position: "absolute", left: 12, top: 11, color: T.grey500 }} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by complaint, patient, or stay..." style={{ width: "100%", boxSizing: "border-box", fontFamily: T.font, fontSize: 13.5, padding: "9px 12px 9px 34px", borderRadius: 9, border: `1px solid ${T.border}`, background: "#FAFBFB" }} />
        </div>
      ) : <div style={{ flex: 1 }} />}
      <div style={{ flex: 1 }} />
      <div style={{ position: "relative" }}>
        <button onClick={onToggleNotifs} title="Notifications" style={{ position: "relative", width: 40, height: 40, borderRadius: 10, border: `1px solid ${T.border}`, background: T.surface, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", color: T.ink }}>
          <Bell size={17} key={ringKey} style={{ animation: ringKey ? "bellPing .7s ease" : "none" }} />
          {unread > 0 && <span style={{ position: "absolute", top: -5, right: -5, minWidth: 17, height: 17, padding: "0 4px", borderRadius: 9, background: T.red, color: "#fff", fontSize: 10.5, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: T.mono }}>{unread}</span>}
        </button>
        {notifOpen && (
          <Card style={{ position: "absolute", right: 0, top: 48, width: 380, maxHeight: 460, overflowY: "auto", padding: 8, boxShadow: "0 12px 32px rgba(33,43,50,0.16)", zIndex: 40 }} className="fade-up">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 10px 6px" }}>
              <div style={{ fontWeight: 700, fontSize: 14 }}>Notifications</div>
              <Eyebrow>{unread} unread</Eyebrow>
            </div>
            {notifs.length === 0 && <div style={{ padding: "22px 12px", color: T.slate, fontSize: 13, textAlign: "center" }}>Nothing needs your attention right now.</div>}
            {notifs.map((n) => (
              <div key={n.id} style={{ display: "flex", gap: 10, padding: "10px 10px", borderRadius: 9, background: n.read ? "transparent" : T.green50, marginBottom: 4 }}>
                <div style={{ width: 32, height: 32, borderRadius: 8, background: n.kind === "recheck" ? T.green700 : n.kind === "escalation" ? "#8A4B00" : T.blue, color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                  {n.kind === "recheck" ? <HeartPulse size={15} /> : n.kind === "escalation" ? <ArrowUpRight size={15} /> : <Bell size={15} />}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.3 }}>{n.title}</div>
                  <div style={{ fontSize: 12.5, color: T.slate, lineHeight: 1.4, marginTop: 2 }}>{n.body}</div>
                  <div style={{ display: "flex", gap: 10, marginTop: 7, alignItems: "center" }}>
                    {n.caseUid && <button onClick={() => onOpenCase(n)} style={{ fontFamily: T.font, fontSize: 12, fontWeight: 700, color: T.green700, background: "none", border: "none", cursor: "pointer", padding: 0 }}>{n.kind === "recheck" ? "Open & acknowledge" : "Open case"}</button>}
                    <button onClick={() => onDismissNotif(n.id)} style={{ fontFamily: T.font, fontSize: 12, fontWeight: 600, color: T.grey500, background: "none", border: "none", cursor: "pointer", padding: 0 }}>Dismiss</button>
                    <span style={{ marginLeft: "auto", fontSize: 11.5, color: T.grey500, fontFamily: T.mono }}>{fmtTime(n.at)}</span>
                  </div>
                </div>
              </div>
            ))}
          </Card>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 8px 6px 12px", border: `1px solid ${T.borderSoft}`, borderRadius: 12, background: T.surface }}>
        <div style={{ textAlign: "right", lineHeight: 1.2 }}>
          <div style={{ fontSize: 13, fontWeight: 700, maxWidth: 210, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{identity}</div>
          <div style={{ fontSize: 11.5, color: T.slate }}>{roleLabel}</div>
        </div>
        <div style={{ width: 34, height: 34, borderRadius: "50%", background: T.green700, color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12.5, fontWeight: 700 }}>{initialsOf(identity)}</div>
        <button onClick={onSignOut} title="Sign out" style={{ width: 32, height: 32, borderRadius: 8, border: `1px solid ${T.border}`, background: T.surface, color: T.slate, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}><LogOut size={14} /></button>
      </div>
    </header>
  );
}
