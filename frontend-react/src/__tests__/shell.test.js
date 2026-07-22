import { describe, it, expect } from "vitest";
import { navForSession } from "../shell.jsx";
import roles from "./fixtures/roles.json";

/* fixtures/roles.json is generated from app/security/authz.py
   (ROLE_PERMISSIONS + ROLE_VISIBLE_TABS) at packaging time, so these tests
   assert the UI navigation against the backend's real authorization matrix. */

const nav = (role) => navForSession(roles[role]);

describe("navForSession — role-scoped navigation matches the backend matrix", () => {
  it("triage nurse: workspace only — no analytics, audit, model, ITD, or security", () => {
    const items = nav("triage_nurse");
    expect(items).toContain("triage");
    expect(items).toContain("review");
    expect(items).not.toContain("escalations");
    expect(items).not.toContain("analytics");
    expect(items).not.toContain("audit");
    expect(items).not.toContain("model");
    expect(items).not.toContain("itd");
    expect(items).not.toContain("security");
  });

  it("ed doctor: workspace + escalations, no oversight dashboards", () => {
    const items = nav("ed_doctor");
    expect(items).toEqual(expect.arrayContaining(["triage", "review", "escalations"]));
    expect(items).not.toContain("analytics");
    expect(items).not.toContain("itd");
  });

  it("clinical supervisor: full oversight, no ITD console", () => {
    const items = nav("clinical_supervisor");
    expect(items).toEqual(expect.arrayContaining(["triage", "review", "escalations", "analytics", "audit", "model", "health"]));
    expect(items).not.toContain("itd");
  });

  it("researcher: aggregate evidence only — never the live patient queue", () => {
    const items = nav("researcher");
    expect(items).toContain("model");
    expect(items).not.toContain("triage");
    expect(items).not.toContain("review");
    expect(items).not.toContain("audit");
  });

  it("ITD (security_admin): the only role with the ITD console, plus security & health", () => {
    const items = nav("security_admin");
    expect(items).toEqual(expect.arrayContaining(["itd", "security", "health", "escalations"]));
  });

  it("governance auditor: read-only oversight, no workspace and no ITD", () => {
    const items = nav("governance_auditor");
    expect(items).toEqual(expect.arrayContaining(["analytics", "audit", "model", "health"]));
    expect(items).not.toContain("triage");
    expect(items).not.toContain("itd");
  });

  it("ITD is gated on BOTH the tab and can_ask_chatbot, so clinical roles never see it", () => {
    for (const role of ["triage_nurse", "ed_doctor", "clinical_supervisor"]) {
      expect(nav(role)).not.toContain("itd");
      expect(roles[role].permissions).not.toContain("can_ask_chatbot");
    }
  });
});
