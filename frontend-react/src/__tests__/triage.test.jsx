import React, { useState } from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";

vi.mock("../api.js", () => ({
  api: {
    previewAssessment: vi.fn(() => new Promise(() => {})),
    runAssessment: vi.fn(() => Promise.resolve({ final_category: "Urgent (Yellow)" })),
    getCase: vi.fn((uid) => Promise.resolve({
      case_uid: uid,
      patient_display_label: "Patient 10000001",
      encounter_display_label: "Stay 30000001",
      triage: { chiefcomplaint: "test complaint" },
      demographics: { gender: "F", arrival_transport: "WALK IN" },
    })),
    uploadSupportingScan: vi.fn(),
    multiagentExplainCase: vi.fn(),
  },
  decisions: {
    accept: vi.fn(), override: vi.fn(), escalate: vi.fn(), requestInfo: vi.fn(),
  },
}));

import { api } from "../api.js";
import Triage from "../views/Triage.jsx";

const cases = Array.from({ length: 40 }, (_, i) => ({
  case_uid: `case-${i + 1}`,
  patient_display_label: `Patient ${10000000 + i}`,
  encounter_display_label: `Stay ${30000000 + i}`,
  triage: { chiefcomplaint: `complaint ${i + 1}` },
  demographics: { gender: i % 2 ? "M" : "F", arrival_transport: "WALK IN" },
  workflow_state: { case_status: "new_unreviewed" },
  queue_metadata: { intime: `2180-01-01 10:${String(i).padStart(2, "0")}:00` },
}));

function Harness({ caseRows = cases } = {}) {
  const [selectedUid, setSelectedUid] = useState(null);
  return (
    <Triage
      cases={caseRows}
      casesError={null}
      refresh={() => {}}
      selectedUid={selectedUid}
      setSelectedUid={setSelectedUid}
      query=""
      searchActive={false}
      searchBusy={false}
      toast={() => {}}
      canDecide
      canAssess
      canExplainAcuity={false}
      presentation={false}
      serverTotal={caseRows.length}
      hasMore={false}
      onLoadMore={() => {}}
      loadingMore={false}
    />
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Triage model estimate loading", () => {
  it("requests an ML estimate only for the selected patient on initial load", async () => {
    render(<Harness />);

    expect(await screen.findByText("Triage recommendation")).toBeTruthy();
    await waitFor(() => expect(api.previewAssessment).toHaveBeenCalledTimes(1));
    expect(api.previewAssessment).toHaveBeenCalledWith("case-1");
  });

  it("does not expose the old loaded-count label", async () => {
    render(<Harness />);

    expect(await screen.findByText("Show 30 more")).toBeTruthy();
    expect(screen.queryByText(/loaded\)/i)).toBeNull();
  });

  it("ignores string safety flags from the model estimate without crashing", async () => {
    api.previewAssessment.mockResolvedValueOnce({
      final_category: "Very Urgent (Orange)",
      ml_prediction_available: true,
      confidence: 0.44,
      preview: true,
      safety_flags: "PROVISIONAL_MTS_CATEGORY_PENDING_CLINICIAN_REVIEW",
    });

    render(<Harness />);

    expect(await screen.findByText("Very urgent")).toBeTruthy();
    expect(screen.queryByText("Safety review flags")).toBeNull();
    expect(screen.queryByText("PROVISIONAL_MTS_CATEGORY_PENDING_CLINICIAN_REVIEW")).toBeNull();
    expect(screen.queryByText(/Consult model for this patient/i)).toBeNull();
    expect(screen.getByText("Record new observations")).toBeTruthy();
  });

  it("keeps the unreviewed queue in arrival order after an estimate appears", async () => {
    api.previewAssessment.mockResolvedValueOnce({
      final_category: "Immediate (Red)",
      ml_prediction_available: true,
      confidence: 0.91,
      preview: true,
    });
    const outOfOrder = [
      { ...cases[2], case_uid: "late", patient_display_label: "Patient Late", queue_metadata: { intime: "2180-01-01 10:20:00" } },
      { ...cases[0], case_uid: "first", patient_display_label: "Patient First", queue_metadata: { intime: "2180-01-01 10:00:00" } },
      { ...cases[1], case_uid: "middle", patient_display_label: "Patient Middle", queue_metadata: { intime: "2180-01-01 10:10:00" } },
    ];

    const { container } = render(<Harness caseRows={outOfOrder} />);
    expect(await screen.findByText("Patient First")).toBeTruthy();
    await waitFor(() => expect(api.previewAssessment).toHaveBeenCalledWith("first"));
    await screen.findByText("Immediate");

    const text = container.textContent;
    expect(text.indexOf("Patient First")).toBeLessThan(text.indexOf("Patient Middle"));
    expect(text.indexOf("Patient Middle")).toBeLessThan(text.indexOf("Patient Late"));
  });

  it("shows the selected acuity without the hidden decision-rule explanation", async () => {
    api.previewAssessment.mockResolvedValueOnce({
      final_category: "Urgent (Yellow)",
      predicted_acuity: 3,
      ml_prediction_available: true,
      assigned_acuity_probability: 0.31,
      argmax_acuity: 4,
      argmax_probability: 0.42,
      decision_rule_changed_prediction: true,
      override_applied: true,
      override_note: "Deterministic CRITICAL vital override: CRITICAL_HEART_RATE_ABOVE_130.",
      class_probabilities: { "1": 0.024, "2": 0.121, "3": 0.31, "4": 0.42, "5": 0.125 },
      preview: true,
    });

    render(<Harness />);

    expect(await screen.findByText("Urgent")).toBeTruthy();
    const accept = screen.getByText("Accept & log Acuity 3").closest("button");
    expect(accept).toBeTruthy();
    expect(accept.style.padding).toBe("8px 10px");
    expect(screen.queryByText(/Model's most likely class/i)).toBeNull();
    expect(screen.queryByText(/Safety-adjusted recommendation/i)).toBeNull();
    expect(screen.queryByText(/Rules override/i)).toBeNull();
    expect(screen.queryByText(/CRITICAL_HEART_RATE_ABOVE_130/i)).toBeNull();
  });
});
