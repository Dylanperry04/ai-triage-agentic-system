import React, { useState, useEffect, useMemo } from "react";
import { PieChart, Pie, Cell, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, LineChart, Line } from "recharts";
import { ChevronRight, X } from "lucide-react";
import { T, MTS, priorityFromCategory, catOf, acuityLabel, patientWithEncounter } from "../theme.js";
import { Card, Eyebrow, EmptyState, ErrorNote, Spinner } from "../atoms.jsx";
import { api } from "../api.js";

const RANGES = { "Today": 1, "7 days": 7, "30 days": 30, "90 days": 90 };
const KPI = ({ label, value, accent }) => (
  <Card style={{ padding: "13px 15px", borderTop: `3px solid ${accent || T.green500}` }}>
    <Eyebrow>{label}</Eyebrow>
    <div style={{ fontFamily: T.mono, fontSize: 25, fontWeight: 700, marginTop: 5 }}>{value ?? "—"}</div>
  </Card>
);

export default function Analytics() {
  const [range, setRange] = useState("7 days");
  const [drillCat, setDrillCat] = useState(null);   // CANONICAL acuity int 1-5 (the API filter is typed int)
  const [drillRole, setDrillRole] = useState(null); // reviewer role id
  const [base, setBase] = useState(null);           // unfiltered window query (for donut + KPIs)
  const [drilled, setDrilled] = useState(null);     // server-filtered query for the drill panels
  const [err, setErr] = useState(null);

  const startUtc = useMemo(() => { const d = new Date(); d.setUTCDate(d.getUTCDate() - RANGES[range]); return d.toISOString(); }, [range]);

  useEffect(() => {
    let dead = false; setBase(null); setErr(null); setDrillCat(null); setDrillRole(null);
    api.auditDashboard({ limit: 2000, start_utc: startUtc }).then((r) => !dead && setBase(r)).catch((e) => !dead && setErr(e.detail || e.message));
    return () => { dead = true; };
  }, [startUtc]);

  useEffect(() => {
    let dead = false;
    if (!drillCat && !drillRole) { setDrilled(null); return; }
    setDrilled(null);
    api.auditDashboard({ limit: 2000, start_utc: startUtc, acuity: drillCat || undefined, reviewer_role: drillRole || undefined })
      /* A failed drill must NOT fall back to unfiltered data: the chip would
         still read "Acuity 2" while the panels showed every acuity. Surface the
         error and clear the selection instead of silently lying about scope. */
      .then((r) => !dead && setDrilled(r))
      .catch((e) => { if (!dead) { setDrilled(null); setDrillCat(null); setDrillRole(null); setErr(`Drill-down failed: ${e.detail || e.message}`); } });
    return () => { dead = true; };
  }, [drillCat, drillRole, startUtc]);

  const active = drilled || base;
  const s = base?.aggregations?.summary || {};
  /* by_acuity groups on the backend's CANONICAL acuity, so one clinical acuity
     is one slice. by_triage_level groups on each record's raw text, which split
     the same acuity across several slices (int "2" from a workflow run vs
     "Very Urgent (Orange)" from a human review) and double-counted the chart. */
  const donut = useMemo(() => (base?.aggregations?.by_acuity || [])
    .map((d) => ({ ...d, p: d.acuity }))
    .filter((d) => d.p != null && d.count > 0), [base]);
  /* Decisions SUBMITTED per role — not by_reviewer_role, which counts every
     audit record including one row per authorised API request and so reported
     thousands of "submissions" for a role that had submitted none. */
  const byRole = useMemo(() => ((active?.aggregations?.by_reviewer_role_decisions) || [])
    .filter((d) => d.label && d.label !== "Unknown")
    .map((d) => ({ role: d.label.replace(/_/g, " "), roleRaw: d.label, n: d.count })), [active]);
  /* Per-person breakdown for the role drill-down. */
  const byActor = useMemo(() => ((drilled?.aggregations?.by_actor) || [])
    .filter((a) => a.count > 0), [drilled]);
  const timeline = useMemo(() => ((active?.aggregations?.timeline) || []).map((t) => ({ day: String(t.date).slice(5), n: t.count })), [active]);
  /* Only the human-review record is the authoritative override. Matching on
     override_status/decision_type across ALL record kinds also picked up the
     workflow run and the workflow-state rows for the same event, so a single
     override could be listed two or three times. */
  const overrides = useMemo(() => ((active?.entries) || [])
    .filter((e) => e.record_kind === "human_review"
      /* Must match the Overrides KPI exactly. override_status is "yes" whenever
         an override_reason exists, which an UNCERTAIN review also carries, so
         this list showed rows the KPI (correctly) excludes — the panel and the
         number above it disagreed about the same window. */
      && String(e.decision_type || "").toUpperCase() === "OVERRIDDEN"
      && e.clinician_acuity != null), [active]);
  const escWork = base?.aggregations?.escalation_worklist || [];

  const clearDrill = () => { setDrillCat(null); setDrillRole(null); };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ fontSize: 19, fontWeight: 700 }}>Service analytics</div>
          <div style={{ fontSize: 13, color: T.slate, marginTop: 2 }}>Live from the backend audit evidence. Click an acuity or a role to drill down.</div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {Object.keys(RANGES).map((r) => (
            <button key={r} onClick={() => setRange(r)} style={{ fontFamily: T.font, fontSize: 12.5, fontWeight: 700, padding: "7px 14px", borderRadius: 999, cursor: "pointer", border: `1px solid ${range === r ? T.green700 : T.border}`, background: range === r ? T.green700 : T.surface, color: range === r ? "#fff" : T.ink }}>{r}</button>
          ))}
        </div>
      </div>
      {err && <div style={{ marginTop: 16 }}><ErrorNote>{err}</ErrorNote></div>}
      {!base && !err && <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 26, color: T.slate, fontSize: 13.5 }}><Spinner /> Loading the audit dashboard…</div>}
      {base && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(155px, 1fr))", gap: 11, marginTop: 16 }}>
            {/* Two distinct populations. total_reviews is every submitted
                workflow action (accept, override, request-info, escalate,
                discharge, close, uncertain); acuity_decisions is accept+override
                only, which is what the acuity donut counts. Showing the first
                under "Clinical decisions" beside a donut built from the second
                made the two disagree by design. */}
            <KPI label="Acuity decisions" value={s.acuity_decisions ?? s.total_reviews} />
            <KPI label="All submitted actions" value={s.total_reviews} />
            <KPI label="Accepted" value={s.accepted_cases} accent="#2E7D32" />
            <KPI label="Overrides" value={s.overrides} accent="#E86C00" />
            <KPI label="Escalations open" value={s.open_escalations} accent={T.red} />
            <KPI label="Discharged / closed" value={s.discharged_cases} accent={T.blue} />
            <KPI label="Overdue vitals alerts" value={s.overdue_vitals_alerts} accent="#8A4B00" />
          </div>
          {(drillCat || drillRole) && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
              <span style={{ fontSize: 12.5, fontWeight: 700, color: T.slate }}>Drilled into:</span>
              {drillCat && <span style={{ fontSize: 12.5, fontWeight: 700, color: catOf(drillCat).colour }}>{acuityLabel(drillCat)}</span>}
              {drillRole && <span style={{ fontSize: 12.5, fontWeight: 700 }}>{drillRole.replace(/_/g, " ")}</span>}
              <button onClick={clearDrill} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontFamily: T.font, fontSize: 12, fontWeight: 700, color: T.green700, background: "none", border: "none", cursor: "pointer" }}><X size={12} /> Clear</button>
              {drilled === null && <Spinner size={12} />}
            </div>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "minmax(300px, 1.05fr) minmax(320px, 1.4fr)", gap: 14, marginTop: 14 }}>
            <Card style={{ padding: 16 }}>
              <Eyebrow>Decided cases by acuity</Eyebrow>
              {donut.length === 0 ? <EmptyState>No categorised decisions in this window yet — they appear the moment a decision is logged.</EmptyState> : (
                <>
                  <div style={{ height: 218 }}>
                    <ResponsiveContainer>
                      <PieChart>
                        <Pie data={donut} dataKey="count" nameKey="label" innerRadius={58} outerRadius={88} paddingAngle={2} onClick={(d) => setDrillCat(drillCat === d.acuity ? null : d.acuity)}>
                          {donut.map((d) => <Cell key={d.label} fill={MTS[d.p].colour} opacity={drillCat && drillCat !== d.acuity ? 0.3 : 1} style={{ cursor: "pointer" }} />)}
                        </Pie>
                        <Tooltip formatter={(v, n) => [`${v} record${v === 1 ? "" : "s"}`, n]} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center" }}>
                    {donut.map((d) => <button key={d.acuity} onClick={() => setDrillCat(drillCat === d.acuity ? null : d.acuity)} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: T.font, fontSize: 11.5, fontWeight: 600, color: T.slate, background: "none", border: "none", cursor: "pointer", opacity: drillCat && drillCat !== d.acuity ? 0.45 : 1 }}><span style={{ width: 9, height: 9, borderRadius: 3, background: MTS[d.p].colour }} />{acuityLabel(d.p)} ({d.count})</button>)}
                  </div>
                </>
              )}
            </Card>
            <Card style={{ padding: 16 }}>
              <Eyebrow>{drillCat ? `Who worked ${acuityLabel(drillCat)} cases` : "Activity by role"}</Eyebrow>
              {byRole.length === 0 ? <EmptyState>No matching activity.</EmptyState> : (
                <div style={{ height: 232, marginTop: 8 }}>
                  <ResponsiveContainer>
                    <BarChart data={byRole} layout="vertical" margin={{ left: 26, right: 18 }}>
                      <CartesianGrid horizontal={false} stroke={T.borderSoft} />
                      <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: T.slate }} />
                      <YAxis type="category" dataKey="role" width={132} tick={{ fontSize: 11.5, fill: T.ink }} />
                      <Tooltip formatter={(v) => [`${v} decision${v === 1 ? "" : "s"} submitted`]} />
                      <Bar dataKey="n" radius={[0, 6, 6, 0]} onClick={(d) => setDrillRole(drillRole === d.roleRaw ? null : d.roleRaw)}>
                        {byRole.map((d) => <Cell key={d.roleRaw} fill={drillRole === d.roleRaw ? T.green900 : T.green500} style={{ cursor: "pointer" }} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
              {drillRole && (
                <div style={{ marginTop: 14, borderTop: `1px solid ${T.borderSoft}`, paddingTop: 12 }}>
                  <Eyebrow>Who submitted these — {drillRole.replace(/_/g, " ")}</Eyebrow>
                  {!drilled && <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10, color: T.slate, fontSize: 12.5 }}><Spinner /> Loading…</div>}
                  {drilled && byActor.length === 0 && (
                    <div style={{ fontSize: 12.5, color: T.slate, marginTop: 8, lineHeight: 1.55 }}>
                      No decision in this window carries a recorded person. Attribution
                      was added in v22.1; earlier decisions recorded only a role and are
                      not back-filled, because inferring a person from a role would
                      invent an actor.
                    </div>
                  )}
                  {drilled && byActor.map((a) => {
                    const max = Math.max(...byActor.map((x) => x.count), 1);
                    return (
                      <div key={a.actor_user_id || "unattributed"} style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 0" }}>
                        <div style={{ width: 30, height: 30, flexShrink: 0, borderRadius: "50%", background: a.actor_user_id ? T.green700 : T.borderSoft, color: a.actor_user_id ? "#fff" : T.slate, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11.5, fontWeight: 700 }}>
                          {a.actor_user_id ? a.label.split(/[\s-]+/).filter(Boolean).slice(-2).map((w) => w[0]).join("").toUpperCase().slice(0, 2) : "—"}
                        </div>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ fontSize: 12.5, fontWeight: 600, color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                            {a.label}
                            {a.actor_user_id && !a.identity_verified && (
                              <span title="Role asserted by a client-supplied demo header, not proven by authentication"
                                style={{ marginLeft: 6, fontSize: 10, fontWeight: 700, color: "#8A4B00", background: T.yellow50, border: "1px solid #EEDD9A", borderRadius: 4, padding: "1px 5px" }}>UNVERIFIED</span>
                            )}
                          </div>
                          <div style={{ height: 5, borderRadius: 3, background: T.borderSoft, marginTop: 5, overflow: "hidden" }}>
                            <div style={{ width: `${(a.count / max) * 100}%`, height: "100%", background: a.actor_user_id ? T.green500 : T.border }} />
                          </div>
                        </div>
                        <div style={{ fontFamily: T.mono, fontSize: 12.5, fontWeight: 700, color: T.ink }}>{a.count}</div>
                      </div>
                    );
                  })}
                </div>
              )}
            </Card>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1.4fr) minmax(300px, 1.05fr)", gap: 14, marginTop: 14 }}>
            <Card style={{ padding: 16 }}>
              <Eyebrow>Audit activity over time {drillCat || drillRole ? "(drilled)" : ""}</Eyebrow>
              {timeline.length === 0 ? <EmptyState>Nothing logged in this window.</EmptyState> : (
                <div style={{ height: 190, marginTop: 8 }}>
                  <ResponsiveContainer>
                    <LineChart data={timeline} margin={{ left: -18, right: 12, top: 6 }}>
                      <CartesianGrid stroke={T.borderSoft} vertical={false} />
                      <XAxis dataKey="day" tick={{ fontSize: 11, fill: T.slate }} />
                      <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: T.slate }} />
                      <Tooltip />
                      <Line type="monotone" dataKey="n" stroke={T.green700} strokeWidth={2.4} dot={{ r: 3 }} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Card>
            <Card style={{ padding: 16 }}>
              <Eyebrow>Override detail — system estimate → clinician decision</Eyebrow>
              <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8, maxHeight: 190, overflowY: "auto" }}>
                {overrides.length === 0 && <EmptyState>No overrides in this view — worth knowing too.</EmptyState>}
                {overrides.slice(0, 24).map((e, i) => {
                  /* system_acuity/clinician_acuity are direction-labelled by the
                     backend. The old code read predicted_category -> triage_level,
                     which on a human-review record is clinician -> model: the
                     exact reverse of the heading. Those two generic fields carry
                     opposite meanings on different record kinds, so they must not
                     be used for a directional display. */
                  const from = e.system_acuity ?? priorityFromCategory(e.triage_level);
                  const to = e.clinician_acuity ?? priorityFromCategory(e.predicted_category);
                  return (
                    <div key={i} style={{ display: "flex", alignItems: "center", gap: 9, border: `1px solid ${T.borderSoft}`, borderRadius: 9, padding: "8px 11px" }}>
                      <span style={{ width: 22, height: 22, borderRadius: 6, background: catOf(from).colour, color: catOf(from).text, fontFamily: T.mono, fontSize: 12, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center" }}>{from ?? "?"}</span>
                      <ChevronRight size={13} style={{ color: T.grey500 }} />
                      <span style={{ width: 22, height: 22, borderRadius: 6, background: catOf(to).colour, color: catOf(to).text, fontFamily: T.mono, fontSize: 12, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center" }}>{to ?? "?"}</span>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontSize: 12, fontWeight: 700 }}>{String(e.reviewer_role || "reviewer").replace(/_/g, " ")}</div>
                        <div style={{ fontSize: 11.5, color: T.slate, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{patientWithEncounter(e)}</div>
                      </div>
                      <span style={{ fontFamily: T.mono, fontSize: 10.5, color: T.grey500 }}>{String(e.timestamp_utc || "").slice(11, 16)}</span>
                    </div>
                  );
                })}
              </div>
            </Card>
          </div>
          {escWork.length > 0 && (
            <Card style={{ padding: 16, marginTop: 14 }}>
              <Eyebrow>Escalation worklist</Eyebrow>
              <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 1fr 1fr 1fr", gap: 0, marginTop: 10, borderBottom: `1px solid ${T.borderSoft}`, paddingBottom: 6 }}>
                {["Case", "Status", "Requested by", "Target", "Case state"].map((h) => <Eyebrow key={h}>{h}</Eyebrow>)}
              </div>
              {escWork.slice(0, 12).map((w, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 1fr 1fr 1fr", padding: "8px 0", borderBottom: `1px solid ${T.borderSoft}`, fontSize: 12.5, alignItems: "center" }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: T.slate, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{patientWithEncounter(w)}</span>
                  <span style={{ fontWeight: 700, color: ["requested", "pending"].includes(String(w.escalation_status)) ? T.red : String(w.escalation_status) === "confirmed" ? "#8A4B00" : "#2E7D32" }}>{String(w.escalation_status || "—").toUpperCase()}</span>
                  <span>{String(w.requested_by_role || "—").replace(/_/g, " ")}</span>
                  <span>{String(w.target_role || "—").replace(/_/g, " ")}</span>
                  <span style={{ color: T.slate }}>{String(w.case_status || "—").replace(/_/g, " ")}</span>
                </div>
              ))}
            </Card>
          )}
          <div style={{ fontSize: 11.5, color: T.grey500, marginTop: 12, lineHeight: 1.5 }}>All figures come from the backend audit dashboard ({active?.count} record{active?.count === 1 ? "" : "s"} in view of {base.total_unfiltered} total). Full source records stay out of this screen; per-person attribution lives in the audit log for permitted roles.</div>
        </>
      )}
    </div>
  );
}
