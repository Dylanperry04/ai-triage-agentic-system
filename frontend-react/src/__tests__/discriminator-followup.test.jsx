import React from "react";
import { it, expect, afterEach } from "vitest";
import { render, screen, cleanup, within } from "@testing-library/react";
import DiscriminatorEvidence from "../DiscriminatorEvidence.jsx";

afterEach(cleanup);

it("keeps matched checks and the full dropdown without the removed commentary or pathway block", () => {
  const rule = { code: "RR", label: "High respiratory rate", observed_value: 32.9, unit: "/min", criterion: "> 29 /min", status: "matched" };
  render(<DiscriminatorEvidence evidence={{ status: "EVALUATED", matched: [rule], evaluated: [rule], model_policy: { type: "modal_with_urgent_class_threshold", changed_prediction: false, modal_acuity: 2, ml_acuity: 2, threshold: .25 }, pathway: { reason_codes: ["CRITICAL_PHYSIOLOGY_DETECTED"], ruleset_id: "provisional_mts_research_v0" } }} />);
  const dropdown = screen.getByText("All evaluated safety checks (1)").closest("details");
  expect(within(dropdown).getByText("Matched")).toBeTruthy();
  expect(screen.getAllByText("High respiratory rate")).toHaveLength(2);
  expect(screen.queryByText("Matched safety checks")).toBeNull();
  expect(screen.queryByText(/Provisional vital safety checks|These checks did not change|Model selection policy|Threshold rule raised|Additional provisional pathway|CRITICAL_PHYSIOLOGY_DETECTED/)).toBeNull();
});

it("only explains the threshold when it actually raised the model estimate", () => {
  const props = { status: "EVALUATED", matched: [], evaluated: [] };
  const { rerender } = render(<DiscriminatorEvidence evidence={{ ...props, model_policy: { type: "modal_with_urgent_class_threshold", changed_prediction: true, modal_acuity: 4, ml_acuity: 3, threshold: .25 } }} />);
  expect(screen.getByText(/Acuity 3 probability met 25%/)).toBeTruthy();
  rerender(<DiscriminatorEvidence evidence={{ ...props, model_policy: { type: "modal_with_urgent_class_threshold", changed_prediction: false, modal_acuity: 3, ml_acuity: 3, threshold: .25 } }} />);
  expect(screen.queryByText(/Threshold rule/)).toBeNull();
});
