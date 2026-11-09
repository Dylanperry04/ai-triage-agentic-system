import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import MTSChecklist from "../MTSChecklist.jsx";
import { Modal } from "../atoms.jsx";
import { api } from "../api.js";
import { checklistResponse } from "./mts-fixtures.js";

vi.mock("../api.js", () => ({ api: { mtsChecklist: vi.fn(), saveMtsChecklist: vi.fn() } }));
const permissions = ["can_record_mts_checklist", "can_run_triage_assessment"];
const patient = { case_uid: "ui-fixture" };
beforeEach(() => { vi.resetAllMocks(); api.mtsChecklist.mockResolvedValue(checklistResponse()); });
afterEach(cleanup);

it("places the save control and Immediate findings before measurements", async () => {
  render(<MTSChecklist c={patient} permissions={permissions} />);
  const save = await screen.findByRole("button", { name: "Save and reassess" });
  const finding = screen.getByLabelText("Airway compromise");
  const measurement = screen.getByLabelText("Heart rate measurement");
  expect(save.compareDocumentPosition(finding) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(finding.compareDocumentPosition(measurement) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(save.closest(".mts-savebar")).toBeTruthy();
});

it("submits an explicit repeat without inventing a changed measurement", async () => {
  const saved = { catalog_version: "ui-contract-test", catalog_sha256: "test-digest", flowchart_id: "chest_pain", flowchart_confirmed: true,
    observations: { heartrate: { state: "assessed", value: 80, note: "" } }, notes: "", expected_revision: "a" };
  api.mtsChecklist.mockResolvedValue({ ...checklistResponse(), checklist: saved, revision: "a" });
  api.saveMtsChecklist.mockResolvedValue({ checklist: saved, revision: "b", assessment_required: true });
  render(<MTSChecklist c={patient} permissions={permissions} />);
  fireEvent.click(await screen.findByLabelText("Repeat Heart rate"));
  fireEvent.click(screen.getByRole("button", { name: "Save and reassess" }));
  await waitFor(() => expect(api.saveMtsChecklist).toHaveBeenCalledOnce());
  const body = api.saveMtsChecklist.mock.calls[0][1];
  expect(body.observations.heartrate.value).toBe(80);
  expect(body.reassessed_measurements).toEqual(["heartrate"]);
});

it("does not request a new assessment for an unchanged saved checklist", async () => {
  const onSaved = vi.fn();
  api.saveMtsChecklist.mockImplementation(async (_, body) => ({ checklist: body, revision: "same", status: "unchanged", assessment_required: false }));
  render(<MTSChecklist c={patient} permissions={permissions} onSaved={onSaved} />);
  fireEvent.click(await screen.findByLabelText("Confirm selected flowchart for this presentation"));
  fireEvent.click(screen.getByRole("button", { name: "Save and reassess" }));
  await screen.findByText("No observation changes.");
  expect(onSaved).not.toHaveBeenCalled();
});

it("keeps keyboard focus inside a modal and restores it on close", () => {
  const close = vi.fn();
  const outside = document.createElement("button"); document.body.appendChild(outside); outside.focus();
  const { unmount } = render(<Modal title="Keyboard fixture" onClose={close}><button>Last action</button></Modal>);
  const first = screen.getByLabelText("Close"), last = screen.getByText("Last action");
  expect(document.activeElement).toBe(first);
  last.focus(); fireEvent.keyDown(window, { key: "Tab" }); expect(document.activeElement).toBe(first);
  fireEvent.keyDown(window, { key: "Tab", shiftKey: true }); expect(document.activeElement).toBe(last);
  fireEvent.keyDown(window, { key: "Escape" }); expect(close).toHaveBeenCalledOnce();
  unmount(); expect(document.activeElement).toBe(outside); outside.remove();
});

it("cancels a flowchart change without losing the current findings", async () => {
  render(<MTSChecklist c={patient} permissions={permissions} />);
  fireEvent.change(await screen.findByLabelText("Airway compromise"), { target: { value: "present" } });
  fireEvent.change(screen.getByLabelText("MTS flowchart"), { target: { value: "major_trauma" } });
  fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
  expect(screen.getByLabelText("MTS flowchart").value).toBe("chest_pain");
  expect(screen.getByLabelText("Airway compromise").value).toBe("present");
  expect(screen.queryByRole("dialog")).toBeNull();
});
