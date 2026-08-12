import React, { useState, useEffect } from "react";
import {
  ShieldCheck,
  ChevronDown,
  HeartPulse,
  Stethoscope,
  Users,
  FlaskConical,
  ScrollText,
} from "lucide-react";
import { T, DEMO_STAFF, ROLE_META } from "../theme.js";
import { Card, Btn, Eyebrow, HseTile } from "../atoms.jsx";

const ICONS = {
  HeartPulse,
  Stethoscope,
  Users,
  FlaskConical,
  ShieldCheck,
  ScrollText,
};

const emailFor = (n) =>
  n
    .toLowerCase()
    .replace(/prof\.|dr\.|mr\.|ms\./g, "")
    .trim()
    .replace(/[áà]/g, "a")
    .replace(/[éè]/g, "e")
    .replace(/[íì]/g, "i")
    .replace(/[óò]/g, "o")
    .replace(/[úù]/g, "u")
    .replace(/[^a-z\s-]/g, "")
    .trim()
    .split(/\s+/)
    .join(".") + "@hse.ie";

export default function SignIn({ session, onSignIn }) {
  const demo = !!session?.demo_role_switcher_available;
  const options = session?.all_role_options || [];

  const [role, setRole] = useState(
    options[0]?.role || "triage_nurse",
  );

  const [person, setPerson] = useState(
    (
      DEMO_STAFF[options[0]?.role] || [
        { name: "Demo user", grade: "" },
      ]
    )[0],
  );

  const [pw, setPw] = useState("");

  useEffect(() => {
    setPerson(
      (
        DEMO_STAFF[role] || [
          { name: "Demo user", grade: "" },
        ]
      )[0],
    );
  }, [role]);

  return (
    <div
      style={{
        minHeight: "100vh",
        background: `linear-gradient(180deg, ${T.green50} 0%, ${T.canvas} 42%)`,
        fontFamily: T.font,
        color: T.ink,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "48px 16px",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 10,
          marginBottom: 26,
        }}
        className="fade-up"
      >
        <HseTile size={56} />

        <div style={{ textAlign: "center" }}>
          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              letterSpacing: "-0.01em",
            }}
          >
            ALTER — Emergency Department Triage
          </div>

          <div
            style={{
              fontSize: 13.5,
              color: T.slate,
              marginTop: 3,
            }}
          >
            University Hospital Limerick · HSE Mid West
          </div>
        </div>
      </div>

      <Card
        style={{
          width: "100%",
          maxWidth: 540,
          padding: 28,
        }}
        className="fade-up"
      >
        {!demo ? (
          <>
            <div
              style={{
                fontSize: 19,
                fontWeight: 700,
              }}
            >
              Continue
            </div>

            <div
              style={{
                fontSize: 13.5,
                color: T.slate,
                marginTop: 4,
                marginBottom: 18,
                lineHeight: 1.5,
              }}
            >
              {session?.authenticated ? (
                <>
                  Signed in through your organisation as{" "}
                  <b>
                    {session.display_name || session.user_id}
                  </b>{" "}
                  (
                  {(session.display_roles || []).join(", ") ||
                    "no role assigned"}
                  ).
                </>
              ) : (
                <>
                  This deployment uses organisation sign-in.
                  Authenticate through your identity provider to
                  continue; the role selector is disabled here (
                  {session?.demo_role_switcher_reason ||
                    "by configuration"}
                  ).
                </>
              )}
            </div>

            <Btn
              onClick={() => onSignIn(null, null)}
              style={{
                width: "100%",
                padding: "12px",
                fontSize: 14.5,
              }}
              disabled={!session?.authenticated}
            >
              Enter workspace
            </Btn>
          </>
        ) : (
          <>
            <div
              style={{
                fontSize: 19,
                fontWeight: 700,
                marginBottom: 18,
              }}
            >
              Sign in
            </div>

            <Eyebrow style={{ marginBottom: 8 }}>
              Account role
            </Eyebrow>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, 1fr)",
                gap: 8,
                marginBottom: 16,
              }}
            >
              {options.map(({ role: roleKey, label }) => {
                const Icon =
                  ICONS[ROLE_META[roleKey]?.icon] ||
                  ShieldCheck;

                const active = role === roleKey;

                return (
                  <button
                    key={roleKey}
                    onClick={() => setRole(roleKey)}
                    className="clickable"
                    title={
                      ROLE_META[roleKey]?.blurb || label
                    }
                    style={{
                      fontFamily: T.font,
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 6,
                      padding: "11px 6px",
                      borderRadius: 10,
                      border: `1.5px solid ${
                        active ? T.green700 : T.border
                      }`,
                      background: active
                        ? T.green50
                        : T.surface,
                      color: active
                        ? T.green900
                        : T.slate,
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: "pointer",
                      lineHeight: 1.25,
                    }}
                  >
                    <Icon size={17} strokeWidth={2} />
                    {label}
                  </button>
                );
              })}
            </div>

            <Eyebrow style={{ marginBottom: 8 }}>
              Staff member
            </Eyebrow>

            <div
              style={{
                position: "relative",
                marginBottom: 14,
              }}
            >
              <select
                value={person.name}
                onChange={(event) =>
                  setPerson(
                    (DEMO_STAFF[role] || []).find(
                      (staffMember) =>
                        staffMember.name ===
                        event.target.value,
                    ) || person,
                  )
                }
                style={{
                  width: "100%",
                  appearance: "none",
                  fontFamily: T.font,
                  fontSize: 14,
                  padding: "11px 38px 11px 12px",
                  borderRadius: 9,
                  border: `1px solid ${T.border}`,
                  background: T.surface,
                  color: T.ink,
                }}
              >
                {(DEMO_STAFF[role] || []).map(
                  (staffMember) => (
                    <option
                      key={staffMember.name}
                      value={staffMember.name}
                    >
                      {staffMember.name}
                      {staffMember.grade
                        ? ` — ${staffMember.grade}`
                        : ""}
                    </option>
                  ),
                )}
              </select>

              <ChevronDown
                size={15}
                style={{
                  position: "absolute",
                  right: 12,
                  top: 13,
                  color: T.grey500,
                  pointerEvents: "none",
                }}
              />
            </div>

            <Eyebrow style={{ marginBottom: 8 }}>
              Email
            </Eyebrow>

            <input
              readOnly
              value={emailFor(person.name)}
              style={{
                width: "100%",
                boxSizing: "border-box",
                fontFamily: T.mono,
                fontSize: 13.5,
                padding: "11px 12px",
                borderRadius: 9,
                border: `1px solid ${T.border}`,
                background: "#FAFBFB",
                color: T.slate,
                marginBottom: 14,
              }}
            />

            <Eyebrow style={{ marginBottom: 8 }}>
              Password
            </Eyebrow>

            <input
              type="password"
              value={pw}
              onChange={(event) =>
                setPw(event.target.value)
              }
              placeholder="Password"
              style={{
                width: "100%",
                boxSizing: "border-box",
                fontFamily: T.font,
                fontSize: 14,
                padding: "11px 12px",
                borderRadius: 9,
                border: `1px solid ${T.border}`,
                marginBottom: 20,
              }}
            />

            <Btn
              onClick={() =>
                onSignIn(role, person.name)
              }
              style={{
                width: "100%",
                padding: "12px",
                fontSize: 14.5,
              }}
            >
              Sign in
            </Btn>
          </>
        )}

        <div
          style={{
            display: "flex",
            gap: 9,
            alignItems: "flex-start",
            marginTop: 18,
            color: T.slate,
            fontSize: 12.5,
            lineHeight: 1.45,
          }}
        >
          <ShieldCheck
            size={15}
            style={{
              marginTop: 1,
              flexShrink: 0,
              color: T.green500,
            }}
          />

          Access is role-based and enforced by the backend on
          every request. You will only see the queues, analytics
          and actions permitted for your role.
        </div>
      </Card>

      <div
        style={{
          marginTop: 18,
          fontSize: 12,
          color: T.grey500,
          textAlign: "center",
        }}
      >
        Authorised use only
      </div>
    </div>
  );
}
