import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { BadgeCheck, Undo2, Activity } from "lucide-react";
import { T, MTS, catOf, NEUTRAL_CAT, patientWithEncounter, acuityLabel, fmtTime, fmtDate } from "../theme.js";
import { Btn, Eyebrow, Modal, Pill, EmptyState, ErrorNote } from "../atoms.jsx";
import { api, decisions } from "../api.js";
import { isQueueRow, ReassessModal } from "./Triage.jsx";

const CLOSED = new Set(["discharged", "closed", "case_closed"]);
const QUEUEISH = new Set(["new_unreviewed", "request_more_info", "", "reopened"]);
const isClosed = (c) => CLOSED.has(String(c.workflow_state?.case_status || "").toLowerCase()) || !!c.workflow_state?.discharged_at;
const escState = (c) => String(c.workflow_state?.escalation_status || "").toLowerCase();
const ACTIVE_ESCALATIONS = new Set(["requested", "pending", "confirmed"]);
const activeEscalation = (c) => {
  const ws = c.workflow_state || c || {};
  const es = String(ws.escalation_status || ws.escalation_state || "").toLowerCase();
  const cs = String(ws.case_status || "").toLowerCase();
  return ACTIVE_ESCALATIONS.has(es) || cs === "escalation_requested";
};
const reviewTimestamp = (c) => {
  const ws = c.workflow_state || {};
  return ws.accepted_timestamp || ws.review_state_updated_at || ws.updated_at_utc || ws.submitted_at_utc || c.queue_metadata?.intime || "";
};
const reviewTimeMs = (c) => {
  const raw = reviewTimestamp(c);
  if (!raw) return 0;
  const n = Date.parse(String(raw).replace(" ", "T"));
  return Number.isNaN(n) ? 0 : n;
};

export default function ReviewQueue({ cases, casesError, refresh, decisionMap, toast, session, escalationsOnly = false, focusUid = null, onFocusHandled }) {
  const [filter, setFilter] = useState(0);
  const [open, setOpen] = useState(null);
  const [note, setNote] = useState("");
  const [rows, setRows] = useState(null);          // authoritative worklist rows
  const [rowsFailed, setRowsFailed] = useState(false);
  const [fetchedCases, setFetchedCases] = useState({}); // lazy details for rows outside the loaded page
  const [reassessTarget, setReassessTarget] = useState(null);
  const busy = useRef(false);
  const roles = new Set(session?.roles || []);
  const senior = roles.has("ed_doctor") || roles.has("clinical_supervisor") || roles.has("security_admin");
  const canDecide = (session?.permissions || []).includes("can_submit_review");
  const canAssess = (session?.permissions || []).includes("can_run_assessment");

  /* Worklist membership comes from the backend workflow queue (bounded 500),
     NOT from whichever page of cases happens to be loaded — otherwise an
     escalation on case 250 would be invisible behind a 200-case page. Case
     DETAILS are joined from the loaded page, with a capped lazy fetch for the
     rest. Falls back to page-filtering only if the worklist read fails. */
  const reloadRows = useCallback(() => {
    if (!(session?.permissions || []).includes("can_view_workflow_queue")) { setRows([]); return; }
    api.workflowQueue({ limit: 500 })
      .then((r) => { setRows(r.rows || []); setRowsFailed(false); })
      .catch(() => { setRows(null); setRowsFailed(true); });
  }, [session]);
  useEffect(() => { reloadRows(); }, [reloadRows]);

  const caseByUid = useMemo(() => {
    const m = {};
    (cases || []).forEach((c) => { m[c.case_uid] = c; });
    Object.entries(fetchedCases).forEach(([uid, c]) => { if (c && !m[uid]) m[uid] = c; });
    return m;
  }, [cases, fetchedCases]);

  const pool = useMemo(() => {
    if (Array.isArray(rows)) {
      const wanted = rows.filter((row) => {
        const cs = String(row.case_status || "").toLowerCase();
        const es = String(row.escalation_status || "").toLowerCase();
        if (escalationsOnly) return ACTIVE_ESCALATIONS.has(es);
        if (activeEscalation(row)) return false;
        if (CLOSED.has(cs)) return false;
        if (QUEUEISH.has(cs)) {
          const rs = String(row.review_status || "").toLowerCase();
          if (!["accepted_as_presented", "overridden", "uncertain"].includes(rs)) return false;
        }
        return true;
      });
      return wanted.map((row) => {
        const detail = caseByUid[row.case_uid];
        return {
          case_uid: row.case_uid,
          source_dataset: detail?.source_dataset || row.source_dataset || "",
          patient_display_name: detail?.patient_display_name || row.patient_display_name,
          patient_display_label: detail?.patient_display_label || row.patient_display_label,
          encounter_display_label: detail?.encounter_display_label || row.encounter_display_label,
          display_identifier: detail?.display_identifier || row.display_identifier,
          triage: detail?.triage || {},
          demographics: detail?.demographics || {},
          queue_metadata: detail?.queue_metadata || row.queue_metadata || {},
          workflow_state: { ...(detail?.workflow_state || {}), ...row },
          _detail_loaded: Boolean(detail),
        };
      });
    }
    /* Fallback: worklist unavailable — filter the loaded page as before. */
    return (cases || []).filter((c) => {
      if (isClosed(c)) return false;
      if (escalationsOnly) return ACTIVE_ESCALATIONS.has(escState(c));
      if (activeEscalation(c)) return false;
      return !isQueueRow(c);
    });
  }, [rows, cases, caseByUid, escalationsOnly]);

  /* Capped lazy fetch of case details the loaded page doesn't cover. */
  useEffect(() => {
    let dead = false;
    const missing = pool.filter((c) => !c._detail_loaded && fetchedCases[c.case_uid] === undefined).slice(0, 30);
    if (!missing.length) return;
    (async () => {
      for (const c of missing) {
        if (dead) return;
        try { const d = await api.getCase(c.case_uid); if (!dead) setFetchedCases((m) => ({ ...m, [c.case_uid]: d })); }
        catch { if (!dead) setFetchedCases((m) => ({ ...m, [c.case_uid]: null })); }
      }
    })();
    return () => { dead = true; };
  }, [pool, fetchedCases]);

  /* Escalation notifications land here with the referenced case: open it. */
  useEffect(() => {
    if (!focusUid) return;
    const target = pool.find((c) => c.case_uid === focusUid);
    if (target) { setOpen(target); onFocusHandled?.(); }
  }, [focusUid, pool, onFocusHandled]);

  const prioOf = (c) => decisionMap[c.case_uid] ?? null;
  const list = (filter ? pool.filter((c) => prioOf(c) === filter) : pool)
    .slice()
    .sort((a, b) => reviewTimeMs(b) - reviewTimeMs(a));
  const counts = [1, 2, 3, 4, 5].map((cN) => pool.filter((p) => prioOf(p) === cN).length);

  const act = async (fn, okTitle, okBody) => {
    if (busy.current) return; busy.current = true;
    try { await fn(); toast(okTitle, okBody); setOpen(null); setNote(""); refresh(); reloadRows(); }
    catch (e) { toast("Action failed", e.detail || e.message, "err"); }
    finally { busy.current = false; }
  };
  const onReassessDone = (result) => {
    const target = reassessTarget;
    setReassessTarget(null);
    setOpen(null);
    refresh();
    reloadRows();
    toast("Observations updated", `${patientWithEncounter(target)}: ${result.change_summary || "advisory refreshed on the updated observations."}`);
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ fontSize: 19, fontWeight: 700 }}>{escalationsOnly ? "Escalations awaiting senior review" : "Review queue"}</div>
          <div style={{ fontSize: 13, color: T.slate, marginTop: 2 }}>{pool.length} case{pool.length === 1 ? "" : "s"} · sorted by latest review time</div>
        </div>
        {!escalationsOnly && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <button onClick={() => setFilter(0)} style={{ fontFamily: T.font, fontSize: 12.5, fontWeight: 700, padding: "6px 13px", borderRadius: 999, cursor: "pointer", border: `1px solid ${!filter ? T.green700 : T.border}`, background: !filter ? T.green700 : T.surface, color: !filter ? "#fff" : T.ink }}>All</button>
            {[1, 2, 3, 4, 5].map((cN, i) => (
              <button key={cN} onClick={() => setFilter(filter === cN ? 0 : cN)} style={{ fontFamily: T.font, fontSize: 12.5, fontWeight: 700, padding: "6px 13px", borderRadius: 999, cursor: "pointer", border: `1px solid ${filter === cN ? MTS[cN].colour : T.border}`, background: filter === cN ? MTS[cN].colour : T.surface, color: filter === cN ? MTS[cN].text : T.ink }}>{MTS[cN].name} <span style={{ opacity: 0.75, fontFamily: T.mono }}>{counts[i]}</span></button>
            ))}
          </div>
        )}
      </div>
      {casesError && <div style={{ marginTop: 14 }}><ErrorNote>{casesError}</ErrorNote></div>}
      {rowsFailed && !casesError && <div style={{ marginTop: 14 }}><ErrorNote>Workflow worklist unavailable — showing decisions from the loaded case page only.</ErrorNote></div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 18, maxWidth: 860 }}>
        {list.map((c) => {
          const p = prioOf(c); const m = p ? MTS[p] : NEUTRAL_CAT;
          const es = escState(c);
          const ts = reviewTimestamp(c);
          return (
            <div key={c.case_uid} onClick={() => setOpen(c)} role="button" tabIndex={0} aria-label={`Open ${patientWithEncounter(c)}`}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(c); } }}
              className="clickable fade-up" style={{ background: T.surface, color: T.ink, border: `1px solid ${T.borderSoft}`, borderLeft: `7px solid ${m.colour}`, borderRadius: 12, padding: "13px 15px", boxShadow: "0 2px 7px rgba(33,43,50,0.10)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 14 }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 800, color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{patientWithEncounter(c)}</div>
                  <div style={{ fontSize: 16, fontWeight: 800, marginTop: 5, textTransform: "capitalize", lineHeight: 1.35, color: T.ink }}>{c.triage?.chiefcomplaint || (c._detail_loaded === false ? "Loading case details..." : "Chief complaint withheld")}</div>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 7, fontSize: 12, color: T.slate, fontFamily: T.mono }}>
                    <span>{p ? `${acuityLabel(p)} · ${MTS[p].target}` : "acuity pending"}</span>
                    {ts && <span>Reviewed {fmtTime(ts)} · {fmtDate(ts)}</span>}
                    {c.demographics?.gender && <span>{c.demographics.gender}</span>}
                  </div>
                </div>
                <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: "0.04em", color: m.text, background: m.colour, padding: "4px 9px", borderRadius: 999, whiteSpace: "nowrap" }}>
                  {es === "requested" || es === "pending" ? "ESCALATED" : es === "confirmed" ? "ESC CONFIRMED" : String(c.workflow_state?.case_status || (p ? m.name : "reviewed")).replace(/_/g, " ").toUpperCase()}
                </span>
              </div>
            </div>
          );
        })}
        {list.length === 0 && !casesError && <div style={{ gridColumn: "1 / -1" }}><EmptyState>{escalationsOnly ? "No open escalations. New ones appear here with a notification." : "No reviewed patients in this view yet — decisions made in the triage queue land here."}</EmptyState></div>}
      </div>

      {open && (
        <Modal title={patientWithEncounter(open)} onClose={() => setOpen(null)} width={560}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
            {prioOf(open) && <span style={{ display: "inline-flex", alignItems: "center", gap: 7, background: MTS[prioOf(open)].colour, color: MTS[prioOf(open)].text, borderRadius: 8, padding: "6px 12px", fontSize: 14, fontWeight: 700 }}><span style={{ fontFamily: T.mono }}>{prioOf(open)}</span> {MTS[prioOf(open)].name}</span>}
            {["requested", "pending"].includes(escState(open)) && <Pill colour={T.red} bg={T.red50} border="#EFC5D1">Escalation requested{open.workflow_state?.escalation_target_role ? ` → ${open.workflow_state.escalation_target_role.replace(/_/g, " ")}` : ""}</Pill>}
            {escState(open) === "confirmed" && <Pill colour="#8A4B00" bg={T.yellow50} border="#EEDD9A">Escalation confirmed — resolution pending</Pill>}
            {open.workflow_state?.overdue_vitals_alert_active && <Pill colour={T.red} bg={T.red50} border="#EFC5D1">Vitals recheck overdue</Pill>}
          </div>
          <div style={{ fontSize: 13.5, lineHeight: 1.6, color: T.ink, textTransform: "capitalize" }}>{open.triage?.chiefcomplaint || "Chief complaint withheld for this role"}.</div>
          <div style={{ fontSize: 12.5, color: T.slate, marginTop: 8, fontFamily: T.mono }}>Review state: {String(open.workflow_state?.review_status || open.workflow_state?.case_status || "recorded").replace(/_/g, " ")}</div>
          {canDecide && (
            <div style={{ borderTop: `1px solid ${T.borderSoft}`, marginTop: 16, paddingTop: 16 }}>
              <Eyebrow style={{ marginBottom: 10 }}>Disposition</Eyebrow>
              <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="Note for the audit record (required for escalation actions)…" style={{ width: "100%", boxSizing: "border-box", fontFamily: T.font, fontSize: 13, padding: 10, borderRadius: 9, border: `1px solid ${T.border}`, resize: "vertical", marginBottom: 12 }} />
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                {/* Discharge/close are senior decisions: the backend enforces
                    _CASE_CLOSE_ROLES (ED doctor / clinical supervisor) with a
                    403, and clinically a triage nurse does not discharge — so
                    the buttons only render for those roles. */}
                {senior && <Btn onClick={() => act(() => decisions.discharge(open.case_uid, { comment: note.trim() || undefined }), "Patient discharged", `${patientWithEncounter(open)} removed from live queues; the full record stays in the audit trail.`)}><BadgeCheck size={15} style={{ verticalAlign: -3, marginRight: 6 }} />Discharge patient</Btn>}
                {senior && <Btn kind="quiet" onClick={() => act(() => decisions.closeAdmitted(open.case_uid, { comment: note.trim() || "Case closed — admitted to ward." }), "Case closed — admitted", `${patientWithEncounter(open)} removed from live queues; recorded as admitted, not discharged.`)}>Close case (admitted)</Btn>}
                {canAssess && <Btn kind="quiet" onClick={() => setReassessTarget(open)}><Activity size={14} style={{ verticalAlign: -2, marginRight: 6 }} />Update vitals</Btn>}
                {senior && ["requested", "pending"].includes(escState(open)) && <Btn kind="quiet" disabled={!note.trim()} onClick={() => act(() => decisions.confirmEscalation(open.case_uid, { note: note.trim() }), "Escalation confirmed", `You have taken ownership of ${patientWithEncounter(open)}.`)}>Confirm escalation</Btn>}
                {senior && ["requested", "pending", "confirmed"].includes(escState(open)) && <Btn kind="quiet" disabled={!note.trim()} onClick={() => act(() => decisions.resolveEscalation(open.case_uid, { note: note.trim() }), "Escalation resolved", `${patientWithEncounter(open)} returns to the standard review flow.`)}>Resolve escalation</Btn>}
                <Btn kind="danger" onClick={() => act(() => decisions.requestInfo(open.case_uid, { fields: [], comment: note.trim() || "Returned for further information before disposition." }), "Returned to triage", `${patientWithEncounter(open)} is back with the triage nurse for more information.`)}><Undo2 size={14} style={{ verticalAlign: -2, marginRight: 6 }} />Request more info</Btn>
              </div>
              <div style={{ fontSize: 11.5, color: T.grey500, marginTop: 10, lineHeight: 1.45 }}>{senior ? "Discharge and case-closure remove the patient from live queues. " : "Discharge and case-closure are ED-doctor / supervisor decisions. "}Every disposition is written to the audit trail with your authenticated identity.</div>
            </div>
          )}
        </Modal>
      )}
      {reassessTarget && (
        <ReassessModal c={reassessTarget} onClose={() => setReassessTarget(null)} onDone={onReassessDone} toast={toast} />
      )}
    </div>
  );
}
