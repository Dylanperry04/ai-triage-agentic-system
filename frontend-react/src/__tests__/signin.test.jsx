import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SignIn from "../views/SignIn.jsx";

describe("SignIn", () => {
  it("does not display the demo role-selector authentication disclaimer", () => {
    render(
      <SignIn
        session={{
          demo_role_switcher_available: true,
          demo_role_switcher_label: "Demo role selector - not real authentication",
          all_role_options: [{ role: "triage_nurse", label: "Triage Nurse" }],
        }}
        onSignIn={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByText(/demo role selector/i)).toBeNull();
    expect(screen.queryByText(/not real authentication/i)).toBeNull();
  });
});
