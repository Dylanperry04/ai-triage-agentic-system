import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import MTSChecklist from "../MTSChecklist.jsx";
import MTSEvaluation from "../MTSEvaluation.jsx";
import { api } from "../api.js";
import { checklistResponse, emptyEvaluation } from "./mts-fixtures.js";

vi.mock("../api.js", () => ({ api: { mtsChecklist: vi.fn(), saveMtsChecklist: vi.fn(), mtsEvaluation: vi.fn() } }));
const patient = { case_uid: "a", triage: { heartrate: 88, temperature: 98.6, temperature_unit: "F" } };
beforeEach(() => { vi.resetAllMocks(); vi.spyOn(window, "confirm").mockReturnValue(true); api.mtsChecklist.mockResolvedValue(checklistResponse("a")); });
afterEach(cleanup);

it("keeps source values distinct from assessed measurements and never prechecks a clinical finding", async () => {
  render(<MTSChecklist c={patient} permissions={["can_record_mts_checklist"]} />);
  const heart = await screen.findByLabelText("Heart rate measurement");
  expect(heart.disabled).toBe(true);
  expect(screen.getByLabelText("Heart rate status").value).toBe("source_recorded");
  expect(Number(screen.getByLabelText("Temperature measurement").value)).toBeCloseTo(37);
  expect(screen.getByLabelText("Airway compromise").value).toBe("not_assessed");
  expect(screen.getByText("Save checklist observations").disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Heart rate status"), { target: { value: "assessed" } });
  expect(heart.disabled).toBe(false);
});

it("saves explicit unavailable findings, version and concurrency tokens only after nurse confirmation", async () => {
  const response = { ...checklistResponse("a"), revision: "rev-before", observations_at: "observed-before" };
  api.mtsChecklist.mockResolvedValue(response);
  const onSaved = vi.fn();
  api.saveMtsChecklist.mockImplementation(async (_, body) => ({ revision: "rev-after", checklist: { ...body, expected_observations_at: "observed-after" } }));
  render(<MTSChecklist c={patient} permissions={["can_record_mts_checklist"]} onSaved={onSaved} />);
  fireEvent.change(await screen.findByLabelText("Heart rate status"), { target: { value: "unavailable" } });
  fireEvent.click(screen.getByLabelText("Confirm selected flowchart for this presentation"));
  fireEvent.click(screen.getByText("Save checklist observations"));
  await waitFor(() => expect(api.saveMtsChecklist).toHaveBeenCalledTimes(1));
  const [uid, body] = api.saveMtsChecklist.mock.calls[0];
  expect(uid).toBe("a");
  expect(body.expected_revision).toBe("rev-before");
  expect(body.expected_observations_at).toBe("observed-before");
  expect(body.catalog_sha256).toBe(response.catalog.sha256);
  expect(body.observations.heartrate).toMatchObject({ state: "unavailable", value: null });
  expect(body.observations.airway_compromise).toBeUndefined();
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith("a", expect.objectContaining({ revision: "rev-after" }), true));
});

it("requires an explanation for a different chart and drops findings belonging to the previous chart", async () => {
  render(<MTSChecklist c={patient} permissions={["can_record_mts_checklist"]} />);
  fireEvent.change(await screen.findByLabelText("Airway compromise"), { target: { value: "present" } });
  fireEvent.change(screen.getByLabelText("MTS flowchart"), { target: { value: "major_trauma" } });
  expect(screen.getByRole("dialog", { name: "Change flowchart?" })).toBeTruthy();
  expect(screen.getByLabelText("Airway compromise").value).toBe("present");
  fireEvent.click(screen.getByRole("button", { name: "Change flowchart", exact: true }));
  expect(screen.queryByLabelText("Airway compromise")).toBeNull();
  expect(screen.getByLabelText("Shock").value).toBe("not_assessed");
  fireEvent.click(screen.getByLabelText("Confirm selected flowchart for this presentation"));
  expect(screen.getByText("Save checklist observations").disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Flowchart selection reason"), { target: { value: "Test chart choice only" } });
  expect(screen.getByText("Save checklist observations").disabled).toBe(false);
});

it("reports missing reference labels as unevaluated for every complaint group", async () => {
  api.mtsEvaluation.mockResolvedValue(emptyEvaluation);
  render(<MTSEvaluation />);
  expect(await screen.findByText("Chest pain")).toBeTruthy();
  expect(screen.getByText("Abdominal pain")).toBeTruthy();
  expect(screen.getByText("Trauma")).toBeTruthy();
  expect(screen.getByText("Respiratory symptoms")).toBeTruthy();
  expect(screen.getAllByText("Not evaluated").length).toBeGreaterThan(10);
  expect(screen.queryByText("100.0%")).toBeNull();
  fireEvent.change(screen.getByLabelText("Recommendation being compared"), { target: { value: "mts_category" } });
  expect(screen.getAllByText("0 / 0").length).toBe(4);
});

it("does not present a failed metric request as a zero error rate", async () => {
  api.mtsEvaluation.mockRejectedValue(new Error("Reference storage unavailable"));
  render(<MTSEvaluation />);
  expect(await screen.findByText("Reference storage unavailable")).toBeTruthy();
  expect(screen.queryByText("0.0%")).toBeNull();
});
