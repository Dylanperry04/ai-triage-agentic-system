import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { T, priorityFromCategory, patientWithEncounter } from "./theme.js";
import { GlobalStyle, Toasts, Spinner } from "./atoms.jsx";
import { Sidebar, Header, navForSession, NAV } from "./shell.jsx";
import { api, setDemoIdentity } from "./api.js";
import SignIn from "./views/SignIn.jsx";
import Triage from "./views/Triage.jsx";
import ReviewQueue from "./views/ReviewQueue.jsx";
import Itd from "./views/Itd.jsx";
import Security from "./views/Security.jsx";
import Health from "./views/Health.jsx";
/* The oversight views pull in recharts (~the bulk of the bundle); lazy-load
   them so the clinical workspace ships as a small initial chunk. */
const Analytics = React.lazy(() => import("./views/Analytics.jsx"));
const AuditLog = React.lazy(() => import("./views/AuditLog.jsx"));
const ModelPerformance = React.lazy(() => import("./views/ModelPerformance.jsx"));

export default function App() {
  const [session, setSession] = useState(null);
  const [entered, setEntered] = useState(false);
  const [persona, setPersona] = useState(null);
  const [tab, setTab] = useState(null);
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [cases, setCases] = useState(null);
  const [casesError, setCasesError] = useState(null);
  const [casesMeta, setCasesMeta] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [searchResults, setSearchResults] = useState(null);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchError, setSearchError] = useState(null);
  const [focusUid, setFocusUid] = useState(null);
  const searchSeq = useRef(0);
  const loadedDepth = useRef(200);
  const [meta, setMeta] = useState(null);
  const [decisionMap, setDecisionMap] = useState({});
  const [selectedUid, setSelectedUid] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [notifs, setNotifs] = useState([]);
  const [notifOpen, setNotifOpen] = useState(false);
  const [ringKey, setRingKey] = useState(0);
  const seenNotifs = useRef(new Set());
  const bootErr = useRef(null);

  const toast = useCallback((title, body, tone = "ok") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, title, body, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5200);
  }, []);
  const pushNotif = useCallback((n) => {
    if (seenNotifs.current.has(n.key)) return;
    seenNotifs.current.add(n.key);
    setNotifs((xs) => [{ ...n, id: n.key, at: Date.now(), read: false }, ...xs].slice(0, 30));
    setRingKey((k) => k + 1);
    toast(n.title, n.body, "warn");
  }, [toast]);

  useEffect(() => { api.authSession().then(setSession).catch((e) => { bootErr.current = e.detail || e.message; setSession({ authenticated: false, all_role_options: [] }); }); }, []);
  useEffect(() => { api.systemMeta().then(setMeta).catch(() => setMeta(null)); }, []);

  const refreshCases = useCallback(() => {
    const canView = (session?.permissions || []).includes("can_view_case");
    if (!canView) { setCases([]); return; }
    // Refresh preserves the loaded depth so "load more" pages are not lost
    // every time an action refreshes the list.
    const limit = Math.min(1000, Math.max(200, loadedDepth.current));
    api.listCases({ limit }).then((r) => { setCases(r.cases || []); setCasesMeta(r.pagination || null); setCasesError(null); })
      .catch((e) => { setCases([]); setCasesMeta(null); setCasesError(`Cases unavailable: ${e.detail || e.message}`); });
  }, [session]);

  /* Server-side pagination: append the next page (the backend reports
     has_more / next_offset) and dedupe by case_uid. */
  const loadMoreCases = useCallback(() => {
    if (!casesMeta?.has_more || casesMeta?.next_offset == null || loadingMore) return;
    setLoadingMore(true);
    api.listCases({ limit: 200, offset: casesMeta.next_offset })
      .then((r) => {
        setCases((prev) => {
          const seen = new Set((prev || []).map((c) => c.case_uid));
          const merged = [...(prev || []), ...(r.cases || []).filter((c) => !seen.has(c.case_uid))];
          loadedDepth.current = merged.length;
          return merged;
        });
        setCasesMeta(r.pagination || null);
      })
      .catch((e) => toast("Could not load more cases", e.detail || e.message, "err"))
      .finally(() => setLoadingMore(false));
  }, [casesMeta, loadingMore, toast]);

  /* Server-side search: the /cases endpoint filters with ?q= across the full
     case window, so searching is not limited to the locally loaded page. */
  useEffect(() => {
    const q = query.trim();
    if (!entered || !(session?.permissions || []).includes("can_view_case")) return;
    if (!q) { setSearchResults(null); setSearchBusy(false); setSearchError(null); return; }
    setSearchBusy(true);
    setSearchError(null);
    const seq = ++searchSeq.current;
    const t = setTimeout(() => {
      api.listCases({ q, limit: 200 })
        .then((r) => { if (seq === searchSeq.current) setSearchResults(r.cases || []); })
        .catch((e) => { if (seq === searchSeq.current) { setSearchResults([]); setSearchError(`Search failed: ${e.detail || e.message}`); } })
        .finally(() => { if (seq === searchSeq.current) setSearchBusy(false); });
    }, 350);
    return () => clearTimeout(t);
  }, [query, entered, session]);

  const signIn = async (role, personName) => {
    if (session?.demo_role_switcher_available && role) {
      setDemoIdentity(role, personName);
      const s = await api.authSession().catch(() => session);
      setSession(s); setPersona(personName);
    }
    setEntered(true);
  };
  const signOut = () => {
    setDemoIdentity(null, null); setEntered(false); setPersona(null); setTab(null);
    setCases(null); setDecisionMap({}); setNotifs([]); seenNotifs.current = new Set(); setSelectedUid(null);
    api.authSession().then(setSession).catch(() => {});
  };

  const navItems = useMemo(() => navForSession(session), [session]);
  useEffect(() => { if (entered && (!tab || !navItems.includes(tab))) setTab(navItems[0] || null); }, [entered, navItems, tab]);
  useEffect(() => { if (entered) refreshCases(); }, [entered, refreshCases]);

  /* Notification engine: poll the backend worklist for overdue-vitals alerts
     targeted at my role, and escalations awaiting senior review. */
  useEffect(() => {
    if (!entered || !(session?.permissions || []).includes("can_view_workflow_queue")) return;
    let stop = false;
    const roles = new Set(session?.roles || []);
    const poll = async () => {
      try {
        const wq = await api.workflowQueue({ limit: 500 });
        if (stop) return;
        (wq.rows || []).forEach((row) => {
          const rowLabel = patientWithEncounter(row);
          const target = row.notification_target_role;
          if (row.overdue_vitals_alert_active && (!target || roles.has(target))) {
            pushNotif({ key: `ov-${row.case_uid}-${row.last_vitals_updated_at || ""}`, kind: "recheck", caseUid: row.case_uid, caseLabel: rowLabel, title: "Vitals recheck due", body: `${rowLabel} — observations have not been repeated within the recheck window. Open the case to acknowledge.` });
          }
          if (["requested", "pending"].includes(String(row.escalation_status || "").toLowerCase())) {
            const t = row.escalation_target_role;
            if ((t && roles.has(t)) || (!t && (roles.has("ed_doctor") || roles.has("clinical_supervisor")))) {
              pushNotif({ key: `esc-${row.case_uid}-${row.escalation_requested_at || ""}`, kind: "escalation", caseUid: row.case_uid, caseLabel: rowLabel, title: "Escalation awaiting review", body: `${rowLabel} was escalated${row.escalation_requested_by_role ? ` by the ${String(row.escalation_requested_by_role).replace(/_/g, " ")}` : ""} and needs a senior decision.` });
            }
          }
        });
      } catch { /* worklist unavailable — keep quiet, retry next tick */ }
    };
    poll();
    const t1 = setInterval(poll, 60000);
    // The sweep mutates alert state; only roles with the assessment permission
    // may trigger it (backend enforces the same).
    let t2 = null;
    if ((session?.permissions || []).includes("can_run_assessment")) {
      const sweep = () => api.sweepOverdueVitals().catch(() => {});
      sweep();
      t2 = setInterval(sweep, 300000);
    }
    return () => { stop = true; clearInterval(t1); if (t2) clearInterval(t2); };
  }, [entered, session, pushNotif]);

  const onOpenCase = async (n) => {
    setNotifOpen(false);
    setNotifs((xs) => xs.map((x) => x.id === n.id ? { ...x, read: true } : x));
    if (n.kind === "recheck") {
      try { await api.acknowledgeOverdue(n.caseUid); toast("Recheck acknowledged", `${n.caseLabel || "Patient"} — logged against your identity.`); } catch (e) { toast("Could not acknowledge", e.detail || e.message, "err"); }
      setTab(navItems.includes("triage") ? "triage" : navItems[0]);
      setSelectedUid(n.caseUid);
    } else {
      setTab(navItems.includes("escalations") ? "escalations" : navItems.includes("review") ? "review" : navItems[0]);
      setFocusUid(n.caseUid);
    }
    refreshCases();
  };

  const recordDecision = useCallback((uid, prio) => { if (uid && prio) setDecisionMap((m) => ({ ...m, [uid]: prio })); }, []);
  const wrapToast = useCallback((title, body, tone) => {
    toast(title, body, tone);
    const m = /Acuity (\d)/.exec(title);
    if (m && selectedUid) recordDecision(selectedUid, Number(m[1]));
  }, [toast, selectedUid, recordDecision]);

  if (!session) return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: T.canvas, fontFamily: T.font, color: T.slate, gap: 12 }}><Spinner /> Connecting to the triage backend…</div>
  );
  if (!entered) return (<><GlobalStyle /><div className="alter"><SignIn session={session} onSignIn={signIn} /></div></>);

  const identity = persona || session.display_name || session.user_id || "Signed in";
  const roleLabel = (session.display_roles || []).join(", ") || "No role";
  const canSearch = (session.permissions || []).includes("can_view_case");
  const canDecide = (session.permissions || []).includes("can_submit_review");
  const canAssess = (session.permissions || []).includes("can_run_assessment");
  const canExplainAcuity = (session.permissions || []).includes("can_explain_case_acuity");
  const presentation = Boolean(meta?.presentation_ui_mode);
  const searchActive = query.trim().length > 0;

  const view = (() => {
    switch (tab) {
      case "triage": return <Triage cases={searchActive ? (searchResults || []) : (cases || [])} casesError={searchActive ? (searchError || casesError) : casesError} refresh={refreshCases} selectedUid={selectedUid} setSelectedUid={setSelectedUid} query={query} searchActive={searchActive} searchBusy={searchBusy} toast={wrapToast} canDecide={canDecide} canAssess={canAssess} canExplainAcuity={canExplainAcuity} presentation={presentation} serverTotal={casesMeta?.total ?? (cases || []).length} hasMore={Boolean(casesMeta?.has_more)} onLoadMore={loadMoreCases} loadingMore={loadingMore} />;
      case "review": return <ReviewQueue cases={cases || []} casesError={casesError} refresh={refreshCases} decisionMap={decisionMap} toast={toast} session={session} focusUid={focusUid} onFocusHandled={() => setFocusUid(null)} />;
      case "escalations": return <ReviewQueue cases={cases || []} casesError={casesError} refresh={refreshCases} decisionMap={decisionMap} toast={toast} session={session} escalationsOnly focusUid={focusUid} onFocusHandled={() => setFocusUid(null)} />;
      case "analytics": return <Analytics toast={toast} />;
      case "audit": return <AuditLog />;
      case "model": return <ModelPerformance />;
      case "itd": return <Itd identity={identity} />;
      case "security": return <Security />;
      case "health": return <Health session={session} />;
      default: return null;
    }
  })();

  return (
    <>
      <GlobalStyle />
      <div className="alter" style={{ display: "flex", height: "100vh", fontFamily: T.font, color: T.ink, background: T.canvas }}>
        <Sidebar session={session} tab={tab} setTab={(k) => { setTab(k); setNotifOpen(false); }} collapsed={collapsed} setCollapsed={setCollapsed} presentation={presentation} sourceLabel={session.current_mode === "azure_supervisor_demo" ? "Supervisor demo · synthetic data" : session.current_mode === "patient_data" ? "Patient-data profile" : "Research prototype"} />
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          <Header identity={identity} roleLabel={roleLabel} onSignOut={signOut} notifs={notifs} notifOpen={notifOpen} onToggleNotifs={() => setNotifOpen((o) => !o)}
            onDismissNotif={(id) => setNotifs((xs) => xs.map((x) => x.id === id ? { ...x, read: true } : x))} onOpenCase={onOpenCase}
            query={query} setQuery={setQuery} ringKey={ringKey} canSearch={canSearch && tab === "triage"} />
          <main style={{ flex: 1, overflowY: "auto", padding: 18, minHeight: 0 }} onClick={() => notifOpen && setNotifOpen(false)}>
            <React.Suspense fallback={<div style={{ display: "flex", gap: 10, alignItems: "center", color: T.slate, fontSize: 13.5 }}><Spinner /> Loading view…</div>}>
              {view}
            </React.Suspense>
          </main>
        </div>
      </div>
      <Toasts toasts={toasts} />
    </>
  );
}
