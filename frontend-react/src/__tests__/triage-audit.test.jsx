import React, { useState } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, act, within } from "@testing-library/react";
import Triage, { AdvisoryPanel, CaseDetail, MultiAgentExplanation } from "../views/Triage.jsx";
import DiscriminatorEvidence from "../DiscriminatorEvidence.jsx";
import { api, decisions } from "../api.js";
import { T } from "../theme.js";

vi.mock("../api.js", () => ({ api: { getCase: vi.fn(), previewAssessment: vi.fn(), runAssessment: vi.fn(), multiagentExplainCase: vi.fn() }, decisions: { accept: vi.fn() } }));
const deferred = () => { let resolve, reject; const promise = new Promise((res, rej) => { resolve = res; reject = rej; }); return { promise, resolve, reject }; };
const rows = ["a", "b"].map((uid) => ({ case_uid: uid, patient_display_label: `Patient ${uid}`, triage: { chiefcomplaint: `Complaint ${uid}` }, workflow_state: { case_status: "new_unreviewed" } }));
const assessment = (extra = {}) => ({ final_category: "Urgent (Yellow)", predicted_acuity: 3, ml_prediction_available: true, ...extra });
const permissions = ["can_run_triage_assessment", "can_accept_acuity"];
function Harness() {
  const [uid, setUid] = useState("a");
  return <Triage cases={rows} selectedUid={uid} setSelectedUid={setUid} permissions={permissions} canExplainAcuity toast={() => {}} refresh={() => {}} />;
}
beforeEach(() => {
  vi.resetAllMocks();
  api.getCase.mockImplementation((uid) => Promise.resolve(rows.find((row) => row.case_uid === uid)));
  api.previewAssessment.mockResolvedValue(assessment({ preview: true }));
  api.runAssessment.mockResolvedValue(assessment({ workflow_run_id: "run-current" }));
  decisions.accept.mockResolvedValue({});
});
afterEach(cleanup);

describe("assessment evidence and probability", () => {
  it("shows an unavailable result as unassigned rather than Acuity 5", () => {
    render(<AdvisoryPanel c={rows[0]} assessment={{ final_category: "None", ml_prediction_available: false }} />);
    expect(screen.getByText("Awaiting assessment")).toBeTruthy();
    expect(screen.getByText("No acuity assigned")).toBeTruthy();
    expect(screen.queryByText("Non-urgent")).toBeNull();
    expect(screen.queryByText("Acuity 5")).toBeNull();
  });
  it("shows the assigned class probability instead of modal confidence after a policy change", () => {
    render(<AdvisoryPanel c={rows[0]} assessment={assessment({ top_class_confidence: 0.42, argmax_acuity: 4, class_probabilities: { 3: 0.31, 4: 0.42 } })} />);
    const selectedProbability = screen.getByText("Probability of Acuity 3").parentElement.parentElement;
    expect(within(selectedProbability).getByText("31.0%")).toBeTruthy();
    expect(within(selectedProbability).queryByText("42.0%")).toBeNull();
    expect(screen.getByText("Target to first physician contact: 60 min")).toBeTruthy();
    expect(screen.getByText(/Discriminator evidence was not recorded/)).toBeTruthy();
  });
  it("does not invent a selected probability when only modal confidence is available", () => {
    render(<AdvisoryPanel c={rows[0]} assessment={assessment({ confidence: 0.42, argmax_acuity: 4 })} />);
    expect(screen.queryByText("Probability of Acuity 3")).toBeNull();
  });
  it("preserves a real zero probability and excludes nonfinite/out-of-range distribution entries", () => {
    render(<AdvisoryPanel c={rows[0]} assessment={assessment({ assigned_acuity_probability: 0, class_probabilities: { 1: NaN, 2: -1, 4: Infinity, 5: 1.1 } })} />);
    expect(screen.getByText("0.0%")).toBeTruthy();
    expect(screen.queryByText(/NaN%|Infinity%|-100.0%|110.0%/)).toBeNull();
  });
  it("shows decisive checks first, other matches, and all evaluated statuses including missing observations", () => {
    const decisive = { code: "EXTREME", label: "Very low oxygen saturation", observed_value: 82, unit: "%", criterion: "< 85 %", status: "matched" };
    const other = { code: "CRITICAL", label: "High heart rate", observed_value: 140, unit: "bpm", criterion: "> 130 bpm", status: "matched" };
    const normal = { code: "NORMAL", label: "Very low heart rate", observed_value: 140, unit: "bpm", criterion: "< 35 bpm", status: "not_matched" };
    const missing = { code: "MISSING", label: "Low systolic pressure", observed_value: null, unit: "mmHg", criterion: "< 90 mmHg", status: "not_assessed" };
    render(<DiscriminatorEvidence evidence={{ status: "EVALUATED", override_applied: true, safety_floor_acuity: 1, decisive: [decisive], matched: [decisive, other], evaluated: [decisive, other, normal, missing], missing_observations: ["sbp"], model_policy: { type: "modal_with_urgent_class_threshold", changed_prediction: true, threshold: 0.25, modal_acuity: 4, ml_acuity: 3 } }} />);
    expect(screen.getByText("Changed the recommendation to Acuity 1")).toBeTruthy();
    expect(screen.queryByText("Other matched safety checks")).toBeNull();
    const full = screen.getByText("All evaluated safety checks (4)").closest("details");
    expect(full.open).toBe(false);
    expect(within(full).getByText("Not matched")).toBeTruthy();
    expect(within(full).getByText("Not assessed")).toBeTruthy();
    expect(within(full).getByText(/Observation unavailable/)).toBeTruthy();
    expect(screen.getByText(/Missing observations are not negative findings/)).toBeTruthy();
    expect(screen.getByText(/Threshold rule raised the model estimate from Acuity 4 to Acuity 3/)).toBeTruthy();
    expect(screen.queryByText(/not the official MTS discriminators/)).toBeNull();
  });
  it("uses numeric SBP for warning colour while preserving the BP pair", () => {
    render(<CaseDetail c={{ ...rows[0], triage: { sbp: 230, dbp: 110 } }} />);
    const bp = screen.getByText("230/110");
    const normalized = document.createElement("span"); normalized.style.color = T.red;
    expect(bp.style.color).toBe(normalized.style.color);
  });
  it("preserves a threshold-adjacent observation without rounding it across the criterion", () => {
    const rule = { code: "LOW", label: "Low oxygen saturation", observed_value: 84.999, unit: "%", criterion: "< 85 %", operator: "<", threshold: 85, status: "matched" };
    render(<DiscriminatorEvidence evidence={{ status: "EVALUATED", matched: [rule] }} />);
    expect(screen.getByText("84.999 % · Rule: < 85 %")).toBeTruthy();
  });
  it("keeps converted measurements readable when rounding preserves the rule result", () => {
    const rule = { code: "HIGH", label: "High temperature", observed_value: 36.833333333333336, unit: "C", criterion: ">= 41 C", operator: ">=", threshold: 41, status: "not_matched" };
    render(<DiscriminatorEvidence evidence={{ status: "EVALUATED", evaluated: [rule] }} />);
    expect(screen.getByText("36.83 C · Rule: >= 41 C")).toBeTruthy();
  });
});

describe("asynchronous patient/run isolation", () => {
  it("invalidates a logged estimate when another staff member records newer observations", async () => {
    const props = { cases: rows, selectedUid: "a", setSelectedUid: vi.fn(), permissions, toast: vi.fn(), refresh: vi.fn() };
    const { rerender } = render(<Triage {...props} />);
    await screen.findByRole("button", { name: "Accept & log Acuity 3" });
    fireEvent.click(screen.getByRole("button", { name: "Open Patient a" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Accept & log Acuity 3" }).disabled).toBe(false));
    api.previewAssessment.mockResolvedValueOnce(assessment({ final_category: "Immediate (Red)", predicted_acuity: 1, preview: true }));
    const updatedRows = rows.map((row) => row.case_uid === "a" ? { ...row, workflow_state: { ...row.workflow_state, updated_at_utc: "2026-10-26T19:00:00Z", latest_triage_updates_at: "2026-10-26T19:00:00Z" } } : row);
    rerender(<Triage {...props} cases={updatedRows} />);
    const accept = await screen.findByRole("button", { name: "Accept & log Acuity 1" });
    expect(accept.disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Accept & log Acuity 3" })).toBeNull();
    expect(api.previewAssessment).toHaveBeenCalledTimes(2);
  });
  it("rejects a late preview after a logged assessment and submits only the displayed run", async () => {
    const preview = deferred();
    api.previewAssessment.mockReturnValueOnce(preview.promise);
    render(<Harness />);
    await waitFor(() => expect(api.previewAssessment).toHaveBeenCalledWith("a"));
    fireEvent.click(screen.getByRole("button", { name: "Open Patient a" }));
    const accept = await screen.findByRole("button", { name: "Accept & log Acuity 3" });
    expect(accept.disabled).toBe(false);
    await act(async () => preview.resolve(assessment({ final_category: "Standard (Green)", predicted_acuity: 4, preview: true })));
    expect(screen.queryByRole("button", { name: "Accept & log Acuity 4" })).toBeNull();
    fireEvent.click(accept);
    await waitFor(() => expect(decisions.accept).toHaveBeenCalledWith("a", { workflowRunId: "run-current", systemPrediction: "Urgent (Yellow)" }));
  });
  it("never binds a preview to a historical run on the case detail", async () => {
    api.getCase.mockResolvedValueOnce({ ...rows[0], workflow_state: { latest_workflow_run_id: "run-old" } });
    render(<Harness />);
    const accept = await screen.findByRole("button", { name: "Accept & log Acuity 3" });
    expect(accept.disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Run logged assessment" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Explain this estimate" })).toBeNull();
  });
  it("ignores detail from a previously selected patient even when it finishes last", async () => {
    const old = deferred();
    api.getCase.mockReturnValueOnce(old.promise);
    render(<Harness />);
    await waitFor(() => expect(api.getCase).toHaveBeenCalledWith("a"));
    fireEvent.click(screen.getByRole("button", { name: "Open Patient b" }));
    await waitFor(() => expect(api.getCase).toHaveBeenCalledWith("b"));
    await act(async () => old.resolve({ ...rows[0], triage: { chiefcomplaint: "STALE DETAIL" } }));
    expect(screen.queryByText("STALE DETAIL")).toBeNull();
    expect(screen.getAllByText("Complaint b").length).toBe(2);
  });
  it("ignores an old patient's explanation after selection changes", async () => {
    const old = deferred();
    api.multiagentExplainCase.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ status: "PASS", workflow_run_id: "run-b", final_explanation: "Current patient explanation" });
    const props = { caseUid: "a", assessmentKey: "run-a", canExplain: true, toast: vi.fn() };
    const { rerender } = render(<MultiAgentExplanation {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Explain this estimate" }));
    expect(api.multiagentExplainCase).toHaveBeenCalledWith("a", undefined, "run-a");
    rerender(<MultiAgentExplanation {...props} caseUid="b" assessmentKey="run-b" />);
    await act(async () => old.resolve({ status: "PASS", final_explanation: "STALE EXPLANATION" }));
    expect(screen.queryByText("STALE EXPLANATION")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Explain this estimate" }));
    expect(await screen.findByText("Current patient explanation")).toBeTruthy();
  });
  it("invalidates an explanation when the same patient is reassessed", async () => {
    const old = deferred();
    api.multiagentExplainCase.mockReturnValueOnce(old.promise);
    const props = { caseUid: "a", assessmentKey: "run-a", canExplain: true, toast: vi.fn() };
    const { rerender } = render(<MultiAgentExplanation {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Explain this estimate" }));
    rerender(<MultiAgentExplanation {...props} assessmentKey="run-new" />);
    await act(async () => old.resolve({ status: "SAFETY_FAIL" }));
    expect(props.toast).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Explain this estimate" })).toBeTruthy();
  });
  it("surfaces returned service errors and permits retry instead of leaving a blank result", async () => {
    api.multiagentExplainCase.mockResolvedValue({ status: "ERROR", reason: "Provider unavailable" });
    render(<MultiAgentExplanation caseUid="a" canExplain toast={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Explain this estimate" }));
    expect(await screen.findByText("Explanation unavailable: Provider unavailable")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
  it("withholds an explanation returned for a different assessment", async () => {
    api.multiagentExplainCase.mockResolvedValue({ status: "PASS", workflow_run_id: "different-run", final_explanation: "WRONG RUN" });
    render(<MultiAgentExplanation caseUid="a" assessmentKey="shown-run" canExplain toast={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Explain this estimate" }));
    expect(await screen.findByText(/does not match the displayed assessment/)).toBeTruthy();
    expect(screen.queryByText("WRONG RUN")).toBeNull();
  });
});
