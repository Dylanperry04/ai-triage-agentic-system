import React from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "../App.jsx";
import { api, setDemoIdentity } from "../api.js";
import { checklistResponse } from "./mts-fixtures.js";

vi.mock("../api.js", () => ({
  api: {
    authSession: vi.fn(), systemMeta: vi.fn(), listCases: vi.fn(), getCase: vi.fn(),
    notifications: vi.fn(), markNotificationRead: vi.fn(), mtsChecklist: vi.fn(),
    saveMtsChecklist: vi.fn(), currentAssessment: vi.fn(), previewAssessment: vi.fn(), runAssessment: vi.fn(), workflowQueue: vi.fn(),
  },
  decisions: {},
  setDemoIdentity: vi.fn(),
}));

const caseRows = ["a", "b"].map(uid => ({
  case_uid: uid, patient_display_label: `Case ${uid}`, triage: { chiefcomplaint: "Chest pain" },
}));

beforeEach(() => {
  vi.resetAllMocks();
  api.authSession.mockResolvedValue({
    authenticated: true, roles: ["triage_nurse"], visible_tabs: ["triage_review", "review_queue"],
    permissions: ["can_view_case", "can_view_workflow_queue", "can_record_mts_checklist", "can_run_triage_assessment"],
  });
  api.systemMeta.mockResolvedValue({});
  api.listCases.mockImplementation(async ({ q }) => ({ cases: q ? [caseRows[1]] : caseRows }));
  api.getCase.mockImplementation(async uid => caseRows.find(row => row.case_uid === uid));
  api.notifications.mockResolvedValue({ notifications: [
    { notification_id: "notice-b", kind: "recheck", case_uid: "b", title: "Repeat observations" },
  ] });
  api.markNotificationRead.mockResolvedValue({});
  api.workflowQueue.mockResolvedValue({ rows: [] });
  api.currentAssessment.mockResolvedValue({ assessment: null });
  api.previewAssessment.mockResolvedValue({ final_category: "3", ml_prediction_available: true });
  api.mtsChecklist.mockImplementation(async uid => checklistResponse(uid));
  api.saveMtsChecklist.mockImplementation(async (uid, body) => ({
    status: "saved", revision: "revision-2", checklist: { ...body, expected_observations_at: null },
  }));
});

async function openDraft() {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Enter workspace" }));
  const airway = await screen.findByLabelText("Airway compromise");
  fireEvent.click(screen.getByLabelText("Confirm selected flowchart for this presentation"));
  fireEvent.change(airway, { target: { value: "present" } });
  return airway;
}

const navigationDialog = () => screen.getByRole("dialog", { name: "Discard unsaved checklist changes?" });

it("keeps a positive draft on keyboard cancellation and guards a browser reload", async () => {
  const airway = await openDraft();
  const sidebar = screen.getByRole("button", { name: "Review Queue" });
  sidebar.focus();
  fireEvent.click(sidebar);
  expect(navigationDialog().contains(document.activeElement)).toBe(true);
  fireEvent.keyDown(document.activeElement, { key: "Escape" });
  expect(document.activeElement).toBe(sidebar);
  expect(airway.value).toBe("present");
  const unload = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);
  expect(api.saveMtsChecklist).not.toHaveBeenCalled();
  expect(api.runAssessment).not.toHaveBeenCalled();
});

it("waits for a successful save before navigating and keeps a failed draft intact", async () => {
  const airway = await openDraft();
  api.saveMtsChecklist.mockRejectedValueOnce(new Error("Revision conflict"));
  fireEvent.click(screen.getByRole("button", { name: "Review Queue" }));
  fireEvent.click(within(navigationDialog()).getByRole("button", { name: "Save and continue" }));
  await screen.findByText(/Could not save the draft/);
  expect(airway.value).toBe("present");
  fireEvent.click(within(navigationDialog()).getByRole("button", { name: "Save and continue" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(api.saveMtsChecklist).toHaveBeenLastCalledWith("a", expect.objectContaining({
    expected_revision: null,
    observations: expect.objectContaining({ airway_compromise: expect.objectContaining({ state: "assessed", value: true }) }),
  }));
  expect(screen.queryByLabelText("Airway compromise")).toBeNull();
  expect(api.runAssessment).not.toHaveBeenCalled();
});

it("keeps a searched-out draft and requires confirmation for notifications and sign-out", async () => {
  const airway = await openDraft();
  fireEvent.change(screen.getByPlaceholderText("Search by complaint, patient, or stay..."), { target: { value: "case b" } });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Open Case a" })).toBeNull());
  expect(airway.value).toBe("present");
  fireEvent.click(screen.getByTitle("Notifications"));
  fireEvent.click(await screen.findByRole("button", { name: "Open case" }));
  await screen.findByRole("dialog");
  fireEvent.click(within(navigationDialog()).getByRole("button", { name: "Keep editing" }));
  expect(api.markNotificationRead).not.toHaveBeenCalled();
  expect(airway.value).toBe("present");
  fireEvent.click(screen.getByTitle("Sign out"));
  expect(setDemoIdentity).not.toHaveBeenCalled();
  fireEvent.click(within(navigationDialog()).getByRole("button", { name: "Discard and leave" }));
  expect(await screen.findByRole("button", { name: "Enter workspace" })).toBeTruthy();
  expect(setDemoIdentity).toHaveBeenCalledWith(null, null);
  expect(api.saveMtsChecklist).not.toHaveBeenCalled();
});

it("opens an active case's notification without discarding or re-saving its draft", async () => {
  api.notifications.mockResolvedValue({ notifications: [
    { notification_id: "notice-a", kind: "recheck", case_uid: "a", title: "Repeat observations" },
  ] });
  const airway = await openDraft();
  fireEvent.click(screen.getByTitle("Notifications"));
  fireEvent.click(await screen.findByRole("button", { name: "Open case" }));
  await waitFor(() => expect(api.markNotificationRead).toHaveBeenCalledWith("notice-a"));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(airway.value).toBe("present");
  expect(api.saveMtsChecklist).not.toHaveBeenCalled();
});
