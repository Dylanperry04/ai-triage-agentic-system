import React from "react";
import { it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import ModelPerformance from "../views/ModelPerformance.jsx";
import { api } from "../api.js";

vi.mock("../api.js", () => ({ api: { modelPerformance: vi.fn() } }));
vi.mock("recharts", async (original) => ({ ...await original(), ResponsiveContainer: () => null }));
afterEach(cleanup);

it("identifies metric scope and never invents zero patient overlap", async () => {
  api.modelPerformance.mockResolvedValue({ artefacts: {
    model_card: { model_name: "UHL model", headline_metrics: {} },
    metric_scope_note: "The current deployment policy was not evaluated by these metrics.",
    model_comparison: { patient_overlap_train_test: null },
    confusion_matrix: { labels: [1, 2], confusion_matrix: [[5, 1], [2, 8]] },
  } });
  render(<ModelPerformance />);
  expect(await screen.findByRole("note")).toHaveProperty("textContent", "The current deployment policy was not evaluated by these metrics.");
  expect(screen.queryByText("0 patient overlap train/test")).toBeNull();
  expect(screen.getByText(/above the diagonal is under-triage/i)).toBeTruthy();
});
