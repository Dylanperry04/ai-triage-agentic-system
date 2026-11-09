import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Sidebar, navForSession, NAV } from "../shell.jsx";
import SignIn from "../views/SignIn.jsx";
import roles from "./fixtures/roles.json";

afterEach(cleanup);

it.each(["triage_nurse", "ed_doctor", "security_admin"])("uses Review Queue for the %s navigation label and tooltip", role => {
  const { rerender } = render(<Sidebar session={roles[role]} tab="review" collapsed={false} setTab={vi.fn()} setCollapsed={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Review Queue" }).title).toBe("Review Queue");
  expect(screen.queryByRole("button", { name: /Patient Disposition/i })).toBeNull();
  rerender(<Sidebar session={roles[role]} tab="review" collapsed setTab={vi.fn()} setCollapsed={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Review Queue" }).title).toBe("Review Queue");
});

it("has no Research Review workspace for any role, including an old reference permission", () => {
  expect(NAV).not.toHaveProperty("research");
  for (const session of Object.values(roles)) {
    expect(navForSession({ ...session, permissions: [...session.permissions, "can_record_mts_reference"] })).not.toContain("research");
  }
});

it("uses neutral sign-in copy while keeping local identity provenance accurate", () => {
  const { container } = render(<SignIn session={{ demo_role_switcher_available: true,
    all_role_options: [{ role: "triage_nurse", label: "Triage Nurse" }] }} onSignIn={vi.fn()} />);
  expect(container.textContent).not.toMatch(/\bdemo(?:nstration)?\b|\bprototype\b/i);
  expect(screen.getByRole("button", { name: "Open workspace" })).toBeTruthy();
  expect(screen.getByText(/staff identity is not verified/i)).toBeTruthy();
});
