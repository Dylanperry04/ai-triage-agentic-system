import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { T, priorityFromCategory, patientWithEncounter } from "./theme.js";
import { GlobalStyle, Toasts, Spinner } from "./atoms.jsx";
import { Sidebar, Header, navForSession, NAV } from "./shell.jsx";
import { api, setDemoIdentity } from "./api.js";
import { mergeNotificationFallback, reconcileNotificationSnapshot } from "./notificationState.js";
import SignIn from "./views/SignIn.jsx";
import Triage from "./views/Triage.jsx";
import ReviewQueue from "./views/ReviewQueue.jsx";
import Itd from "./views/Itd.jsx";
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
  const [sweepError, setSweepError] = useState(null);
  const [pollError, setPollError] = useState(null);
  const [selectedUid, setSelectedUid] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [notifs, setNotifs] = useState([]);
  const [notifOpen, setNotifOpen] = useState(false);
  const [ringKey, setRingKey] = useState(0);
  const announcedNotifs = useRef(new Set());
  const notificationBaselineLoaded = useRef(false);
  const bootErr = useRef(null);

  const toast = useCallback((title, body, tone = "ok") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, title, body, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5200);
  }, []);
  const reconcileNotifications = useCallback((next) => {
    const snapshot = reconcileNotificationSnapshot(
      next, announcedNotifs.current, notificationBaselineLoaded.current,
    );
    announcedNotifs.current = snapshot.announced;
    notificationBaselineLoaded.current = true;
    setNotifs(snapshot.ordered);
    if (snapshot.newlyUnread.length) {
      setRingKey((k) => k + 1);
      snapshot.newlyUnread.forEach((n) => toast(n.title, n.body, "warn"));
    }
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
    setCases(null); setDecisionMap({}); setNotifs([]); announcedNotifs.current = new Set();
    notificationBaselineLoaded.current = false; setSelectedUid(null);
    api.authSession().then(setSession).catch(() => {});
  };

  const navItems = useMemo(() => navForSession(session), [session]);
  useEffect(() => { if (entered && (!tab || !navItems.includes(tab))) setTab(navItems[0] || null); }, [entered, navItems, tab]);
  useEffect(() => { if (entered) refreshCases(); }, [entered, refreshCases]);

  /* The durable API is authoritative when complete. During a reported
     reconciliation gap, merge workflow-derived alerts instead of trusting a
     partial HTTP 200 response. */
  useEffect(() => {
    if (!entered || !(session?.permissions || []).includes("can_view_workflow_queue")) return;
    let stop = false;
    const roles = new Set(session?.roles || []);
    const workflowFallback = async () => {
      const wq = await api.workflowQueue({ limit: 5000 });
      const rows = [];
      (wq.rows || []).forEach((row) => {
        const rowLabel = patientWithEncounter(row);
        const target = row.notification_target_role;
        if (row.overdue_vitals_alert_active && (!target || roles.has(target))) {
          const raw = row.overdue_vitals_reference_at || row.last_vitals_updated_at || "";
          const eventTime = Number.isNaN(Date.parse(raw)) ? raw : new Date(raw).toISOString();
          rows.push({ id: `fallback-overdue_vitals:${row.case_uid}:${eventTime}`, semanticKey: `overdue_vitals:${row.case_uid}:${eventTime}`, kind: "recheck", caseUid: row.case_uid, caseLabel: rowLabel, title: "Vitals recheck due", body: `${rowLabel} — observations have not been repeated within the recheck window. Open the case to acknowledge.`, at: row.overdue_vitals_alert_created_at ? Date.parse(row.overdue_vitals_alert_created_at) : Date.now(), read: false, durable: false });
        }
        if (["requested", "pending"].includes(String(row.escalation_status || "").toLowerCase())) {
          const targetRole = row.escalation_target_role;
          if ((targetRole && roles.has(targetRole)) || (!targetRole && (roles.has("ed_doctor") || roles.has("clinical_supervisor")))) {
            const raw = row.escalation_requested_at || "";
            const eventTime = Number.isNaN(Date.parse(raw)) ? raw : new Date(raw).toISOString();
            rows.push({ id: `fallback-escalation:${row.case_uid}:${eventTime}`, semanticKey: `escalation:${row.case_uid}:${eventTime}`, kind: "escalation", caseUid: row.case_uid, caseLabel: rowLabel, title: "Escalation awaiting review", body: `${rowLabel} was escalated${row.escalation_requested_by_role ? ` by the ${String(row.escalation_requested_by_role).replace(/_/g, " ")}` : ""} and needs a senior decision.`, at: raw ? Date.parse(raw) : Date.now(), read: false, durable: false });
          }
        }
      });
      return rows;
    };
    const poll = async () => {
      try {
        let page = await api.notifications(200, 0);
        const durableRows = [...(page.notifications || [])];
        let degraded = Boolean(page.degraded);
        while (page.has_more && page.next_offset != null && durableRows.length < 5000) {
          page = await api.notifications(200, page.next_offset);
          degraded = degraded || Boolean(page.degraded);
          durableRows.push(...(page.notifications || []));
        }
        if (page.has_more) throw new Error("notification list exceeds the 5,000-item safety window");
        if (stop) return;
        const mapped = durableRows.map((n) => ({
          id: n.notification_id, kind: n.kind, caseUid: n.case_uid,
          caseLabel: n.case_label, title: n.title, body: n.body,
          at: n.created_at ? Date.parse(n.created_at) : Date.now(),
          read: Boolean(n.read), durable: true,
          semanticKey: `${n.kind === "recheck" ? "overdue_vitals" : n.kind}:${n.case_uid}:${n.event_time_ms || n.event_key || ""}`,
        }));
        if (!degraded) {
          setPollError(null);
          reconcileNotifications(mapped);
          return;
        }
        const fallback = await workflowFallback();
        if (stop) return;
        reconcileNotifications(mergeNotificationFallback(mapped, fallback));
        setPollError("durable notification reconciliation is degraded; showing a deduplicated workflow fallback");
      } catch (durableError) {
        if (stop) return;
        try {
          const fallback = await workflowFallback();
          if (!stop) reconcileNotifications(fallback);
          if (!stop) setPollError(`durable notification store unavailable (${durableError?.detail || durableError?.message || "request failed"}); showing workflow-derived fallback`);
        } catch (e) {
          if (!stop) setPollError(e?.detail || e?.message || "worklist unavailable — notifications may be stale");
        }
      }
    };
    /* The sweep CREATES overdue-vitals alerts; the poll READS them. Running the
       poll first meant an alert raised by the very first sweep was not visible
       until the next 60s tick. Sweep, then poll, so a freshly created alert
       surfaces immediately. */
    const canSweep = (session?.permissions || []).includes("can_run_assessment");
    /* The previous version chained .catch(set).then(clear): the .then ran on the
       resolved promise the .catch returned, wiping the error in the same
       microtask. Combined with sweepError never being rendered, nothing could
       ever surface a failing sweep — and a silent sweep failure is exactly the
       case where "0 overdue alerts" is misleading rather than reassuring.
       A 200 response can also carry a non-empty errors[]; a partial failure is
       still a failure for the cases it touched. */
    const sweep = () => api.sweepOverdueVitals().then(
      (r) => {
        if (stop) return;
        const partial = Array.isArray(r?.errors) ? r.errors.length : 0;
        setSweepError(partial ? `${partial} case(s) failed the overdue-vitals sweep` : null);
      },
      (e) => {
        if (!stop) setSweepError(e?.detail || e?.message || "overdue-vitals sweep failed");
      },
    );

    (canSweep ? sweep() : Promise.resolve()).finally(() => { if (!stop) poll(); });
    const t1 = setInterval(poll, 60000);
    let t2 = null;
    if (canSweep) t2 = setInterval(sweep, 300000);
    return () => { stop = true; clearInterval(t1); if (t2) clearInterval(t2); };
  }, [entered, session, reconcileNotifications]);

  const onOpenCase = async (n) => {
    setNotifOpen(false);
    if (n.kind === "recheck") {
      try {
        if (n.durable) await api.acknowledgeNotification(n.id);
        else await api.acknowledgeOverdue(n.caseUid);
        setNotifs((xs) => xs.filter((x) => x.id !== n.id));
        toast("Recheck acknowledged", `${n.caseLabel || "Patient"} — logged against your identity.`);
      }
      catch (e) {
        toast("Could not acknowledge", e.detail || e.message, "err");
        return;
      }
      setTab(navItems.includes("triage") ? "triage" : navItems[0]);
      setSelectedUid(n.caseUid);
    } else {
      setNotifs((xs) => xs.map((x) => x.id === n.id ? { ...x, read: true } : x));
      if (n.durable) {
        try { await api.markNotificationRead(n.id); }
        catch (e) { toast("Could not mark notification read", e.detail || e.message, "err"); }
      }
      setTab(navItems.includes("escalations") ? "escalations" : navItems.includes("review") ? "review" : navItems[0]);
      setFocusUid(n.caseUid);
    }
    refreshCases();
  };

  /* Optimistic in-session colour for a decision whose queue reload has not
     landed yet. The authoritative acuity is workflow_state.assigned_acuity from
     the backend; ReviewQueue prefers that and only falls back to this map.
     Callers pass the acuity explicitly — it is NEVER parsed out of toast text,
     which would silently break whenever a message is reworded and could attach
     a decision to whichever case happened to be selected. */
  /* After a demo reset the backend is empty but React still holds cases,
     decision colours, the selected patient, notifications and the seen-set, so
     the screen would keep showing archived data until a manual refresh. This
     clears every piece of client state the reset invalidates. */
  const clearClientStateAfterReset = useCallback(() => {
    setCases(null); setCasesMeta(null); setCasesError(null);
    setDecisionMap({}); setNotifs([]); announcedNotifs.current = new Set();
    notificationBaselineLoaded.current = false;
    setSelectedUid(null); setFocusUid(null); setSweepError(null); setPollError(null);
    /* Search state survived the reset: because `query` does not change, its
       effect does not re-run, so switching back to Triage still showed results
       loaded before the reset from records that no longer exist. */
    setQuery(""); setSearchResults(null); setSearchError(null); setSearchBusy(false);
    refreshCases();
  }, [refreshCases]);

  const recordDecision = useCallback((uid, prio) => {
    const n = Number(prio);
    if (uid && Number.isInteger(n) && n >= 1 && n <= 5) {
      setDecisionMap((m) => ({ ...m, [uid]: n }));
    }
  }, []);

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
      case "triage": return <Triage cases={searchActive ? (searchResults || []) : (cases || [])} casesError={searchActive ? (searchError || casesError) : casesError} refresh={refreshCases} selectedUid={selectedUid} setSelectedUid={setSelectedUid} query={query} searchActive={searchActive} searchBusy={searchBusy} toast={toast} onDecision={recordDecision} canDecide={canDecide} canAssess={canAssess} canExplainAcuity={canExplainAcuity} presentation={presentation} serverTotal={casesMeta?.total ?? (cases || []).length} hasMore={Boolean(casesMeta?.has_more)} onLoadMore={loadMoreCases} loadingMore={loadingMore} />;
      case "review": return <ReviewQueue cases={cases || []} casesError={casesError} refresh={refreshCases} decisionMap={decisionMap} toast={toast} session={session} focusUid={focusUid} onFocusHandled={() => setFocusUid(null)} />;
      case "escalations": return <ReviewQueue cases={cases || []} casesError={casesError} refresh={refreshCases} decisionMap={decisionMap} toast={toast} session={session} escalationsOnly focusUid={focusUid} onFocusHandled={() => setFocusUid(null)} />;
      case "analytics": return <Analytics toast={toast} />;
      case "audit": return <AuditLog />;
      case "model": return <ModelPerformance />;
      case "itd": return <Itd identity={identity} onDemoReset={clearClientStateAfterReset} />;
      case "health": return <Health session={session} />;
      default: return null;
    }
  })();

  return (
    <>
      <GlobalStyle />
      <div className="alter" style={{ display: "flex", height: "100vh", fontFamily: T.font, color: T.ink, background: T.canvas }}>
        <Sidebar session={session} tab={tab} setTab={(k) => { setTab(k); setNotifOpen(false); }} collapsed={collapsed} setCollapsed={setCollapsed} presentation={presentation} />
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          <Header identity={identity} roleLabel={roleLabel} onSignOut={signOut} notifs={notifs} notifOpen={notifOpen} onToggleNotifs={() => setNotifOpen((o) => !o)}
            onDismissNotif={(id) => {
              const target = notifs.find((n) => n.id === id);
              setNotifs((xs) => xs.map((x) => x.id === id ? { ...x, read: true } : x));
              if (target?.durable) api.markNotificationRead(id).catch((e) => toast("Could not dismiss notification", e.detail || e.message, "err"));
            }} onOpenCase={onOpenCase}
            query={query} setQuery={setQuery} ringKey={ringKey} canSearch={canSearch && tab === "triage"} />
          <main style={{ flex: 1, overflowY: "auto", padding: 18, minHeight: 0 }} onClick={() => notifOpen && setNotifOpen(false)}>
            {(sweepError || pollError) && (
              /* Without this the notification state was unobservable: a failing
                 sweep looked identical to "nothing is overdue". */
              <div role="status" style={{ marginBottom: 12, fontSize: 12.5, lineHeight: 1.5, color: "#8A4B00", background: T.yellow50, border: "1px solid #EEDD9A", borderRadius: 9, padding: "9px 12px" }}>
                <b>Notification checks are not running normally:</b> {[sweepError, pollError].filter(Boolean).join("; ")}. Overdue-vitals and escalation alerts may be missing — do not treat an empty notification list as confirmation that nothing is due.
              </div>
            )}
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
