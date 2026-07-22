import React, { useState, useEffect, useMemo } from "react";
import { T } from "../theme.js";
import { Card, Eyebrow, EmptyState, ErrorNote, Spinner, Pill } from "../atoms.jsx";
import { api } from "../api.js";

const Stat = ({ label, value, accent }) => (
  <Card style={{ padding: "13px 15px", borderTop: `3px solid ${accent}` }}>
    <Eyebrow>{label}</Eyebrow>
    <div style={{ fontFamily: T.mono, fontSize: 24, fontWeight: 700, marginTop: 5 }}>{value}</div>
  </Card>
);

export default function Security() {
  const [status, setStatus] = useState(null);
  const [events, setEvents] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let dead = false;
    api.securityStatus().then((s) => !dead && setStatus(s)).catch((e) => !dead && setErr(e.detail || e.message));
    api.auditEvents(400).then((r) => !dead && setEvents(Array.isArray(r) ? r : r?.events || r?.entries || [])).catch(() => !dead && setEvents([]));
    return () => { dead = true; };
  }, []);
  const evs = events || [];
  const denied = evs.filter((e) => String(e.decision).toUpperCase() === "DENIED");
  const signins = useMemo(() => { const seen = new Set(); evs.forEach((e) => e.user_id && seen.add(e.user_id)); return seen.size; }, [evs]);
  const unsafe = status?.unsafe_combinations || [];

  return (
    <div>
      <div style={{ fontSize: 19, fontWeight: 700 }}>Security events</div>
      <div style={{ fontSize: 13, color: T.slate, marginTop: 2 }}>Access decisions and configuration posture, from the backend access-audit sink.</div>
      {err && <div style={{ marginTop: 14 }}><ErrorNote>{err}</ErrorNote></div>}
      {!status && !err && <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 24, color: T.slate, fontSize: 13.5 }}><Spinner /> Loading security status…</div>}
      {status && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 11, marginTop: 16 }}>
            <Stat label="Profile" value={String(status.current_mode || "—").replace(/_/g, " ")} accent={T.blue} />
            <Stat label="Configuration" value={status.is_safe === false ? "UNSAFE" : "SAFE"} accent={status.is_safe === false ? T.red : "#2E7D32"} />
            <Stat label="Distinct identities seen" value={signins} accent={T.green500} />
            <Stat label="Access decisions" value={evs.length} accent={T.green500} />
            <Stat label="Denied" value={denied.length} accent={denied.length ? T.red : "#2E7D32"} />
          </div>
          {unsafe.length > 0 && (
            <Card style={{ marginTop: 12, padding: 14, borderLeft: `4px solid ${T.red}` }}>
              <Eyebrow>Unsafe configuration findings</Eyebrow>
              {unsafe.map((u, i) => <div key={i} style={{ fontSize: 13, color: T.red, marginTop: 6 }}>{u}</div>)}
            </Card>
          )}
          <Card style={{ marginTop: 14, overflow: "hidden" }}>
            <div style={{ display: "grid", gridTemplateColumns: "150px 1.2fr 1fr 1fr 1.4fr", padding: "10px 14px", borderBottom: `1px solid ${T.borderSoft}`, background: "#FAFBFB" }}>
              {["Time (UTC)", "Identity", "Action", "Decision", "Page / permission"].map((h) => <Eyebrow key={h}>{h}</Eyebrow>)}
            </div>
            <div style={{ maxHeight: "52vh", overflowY: "auto" }}>
              {evs.slice(0, 300).map((e, i) => (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "150px 1.2fr 1fr 1fr 1.4fr", padding: "9px 14px", borderBottom: `1px solid ${T.borderSoft}`, fontSize: 12.5, alignItems: "center" }}>
                  <span style={{ fontFamily: T.mono, fontSize: 11.5, color: T.slate }}>{String(e.timestamp_utc || e.timestamp || "—").replace("T", " ").slice(0, 19)}</span>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}><b>{e.display_name || e.user_id || "—"}</b>{(e.roles || []).length ? <span style={{ color: T.grey500 }}> · {(e.roles || []).join(", ").replace(/_/g, " ")}</span> : null}</span>
                  <span style={{ color: T.slate }}>{String(e.action || "—").replace(/_/g, " ")}</span>
                  <span><Pill colour={String(e.decision).toUpperCase() === "DENIED" ? T.red : "#2E7D32"} bg={String(e.decision).toUpperCase() === "DENIED" ? T.red50 : "#E9F3EA"} border={String(e.decision).toUpperCase() === "DENIED" ? "#EFC5D1" : "#CBE3CD"}>{String(e.decision || "—").toUpperCase()}</Pill></span>
                  <span style={{ fontFamily: T.mono, fontSize: 11.5, color: T.slate, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.page || e.permission || ""}</span>
                </div>
              ))}
              {evs.length === 0 && <EmptyState>No access events recorded yet.</EmptyState>}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
