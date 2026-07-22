import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";

/* Mock the API layer so the component renders without a backend. */
vi.mock("../api.js", () => ({
  api: {
    runAssessment: vi.fn(() => Promise.resolve({ final_category: "Urgent (Yellow)" })),
    previewAssessment: vi.fn(() => Promise.resolve({ final_category: "Urgent (Yellow)" })),
    getCase: vi.fn(() => Promise.resolve({})),
    workflowQueue: vi.fn(() => Promise.resolve({
      rows: [{ case_uid: "demo-supervisor-0001", case_status: "accepted", review_status: "accepted_as_presented" }],
    })),
  },
  decisions: {
    discharge: vi.fn(), closeAdmitted: vi.fn(), requestInfo: vi.fn(),
    confirmEscalation: vi.fn(), resolveEscalation: vi.fn(),
  },
}));

import ReviewQueue from "../views/ReviewQueue.jsx";
import { api } from "../api.js";

const CASE = {
  case_uid: "demo-supervisor-0001",
  source_dataset: "MIMIC-IV-ED-Synthetic-Supervisor-Demo",
  patient_display_label: "Patient 900001",
  encounter_display_label: "Stay 1",
  display_identifier: "Patient 900001",
  triage: { chiefcomplaint: "chest pain radiating to left arm" },
  demographics: { gender: "F" },
  workflow_state: { case_status: "accepted", review_status: "accepted_as_presented" },
};
const CASE_2 = {
  ...CASE,
  case_uid: "demo-supervisor-0002",
  patient_display_label: "Patient 900002",
  encounter_display_label: "Stay 2",
  display_identifier: "Patient 900002",
  triage: { chiefcomplaint: "ankle injury" },
};
const sessionFor = (role) => ({
  roles: [role],
  permissions: ["can_view_case", "can_submit_review", "can_run_assessment", "can_view_workflow_queue"],
});
const renderQueue = (role) => render(
  <ReviewQueue cases={[CASE]} casesError={null} refresh={() => {}} decisionMap={{ "demo-supervisor-0001": 3 }} toast={() => {}} session={sessionFor(role)} />
);

afterEach(cleanup);

describe("ReviewQueue disposition gating mirrors backend _CASE_CLOSE_ROLES", () => {
  it("does not prefetch model estimates for the list", async () => {
    renderQueue("clinical_supervisor");
    expect(await screen.findByText("Review queue")).toBeTruthy();
    expect(api.previewAssessment).not.toHaveBeenCalled();
  });

  it("triage nurse: no Discharge / Close buttons, Request-more-info still available", async () => {
    renderQueue("triage_nurse");
    fireEvent.click(await screen.findByText("Patient 900001 · Stay 1"));
    expect(await screen.findByText("Request more info")).toBeTruthy();
    expect(screen.queryByText("Discharge patient")).toBeNull();
    expect(screen.queryByText("Close case (admitted)")).toBeNull();
    expect(screen.getByText(/ED-doctor \/ supervisor decisions/)).toBeTruthy();
  });

  it("ED doctor: Discharge and Close render", async () => {
    renderQueue("ed_doctor");
    fireEvent.click(await screen.findByText("Patient 900001 · Stay 1"));
    expect(await screen.findByText("Discharge patient")).toBeTruthy();
    expect(screen.getByText("Close case (admitted)")).toBeTruthy();
  });

  it("clinical supervisor: Discharge and Close render", async () => {
    renderQueue("clinical_supervisor");
    fireEvent.click(await screen.findByText("Patient 900001 · Stay 1"));
    expect(await screen.findByText("Discharge patient")).toBeTruthy();
  });

  it("keeps active escalations out of the standard review queue", async () => {
    api.workflowQueue.mockResolvedValueOnce({
      rows: [{ case_uid: "demo-supervisor-0001", case_status: "escalation_requested", escalation_status: "requested", review_status: "escalation_required" }],
    });
    renderQueue("clinical_supervisor");
    expect(await screen.findByText("No reviewed patients in this view yet — decisions made in the triage queue land here.")).toBeTruthy();
  });

  it("sorts the review queue by latest review time, not acuity", async () => {
    api.workflowQueue.mockResolvedValueOnce({
      rows: [
        { case_uid: "demo-supervisor-0001", case_status: "accepted", review_status: "accepted_as_presented", updated_at_utc: "2180-01-01T10:00:00Z" },
        { case_uid: "demo-supervisor-0002", case_status: "accepted", review_status: "accepted_as_presented", updated_at_utc: "2180-01-01T11:00:00Z" },
      ],
    });
    const { container } = render(
      <ReviewQueue
        cases={[CASE, CASE_2]}
        casesError={null}
        refresh={() => {}}
        decisionMap={{
          "demo-supervisor-0001": 1,
          "demo-supervisor-0002": 5,
        }}
        toast={() => {}}
        session={sessionFor("clinical_supervisor")}
      />
    );

    expect(await screen.findByText("Patient 900002 · Stay 2")).toBeTruthy();
    const text = container.textContent;
    expect(text.indexOf("Patient 900002")).toBeLessThan(text.indexOf("Patient 900001"));
  });
});
