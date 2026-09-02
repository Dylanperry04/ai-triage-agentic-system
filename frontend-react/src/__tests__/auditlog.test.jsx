import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../api.js", () => ({
  api: {
    auditDashboard: vi.fn(() => Promise.resolve({
      count: 1, matched: 3, total_unfiltered: 3,
      entries: [{
        timestamp_utc: "2026-09-01T10:00:00Z", case_uid: "uhl~case-a",
        display_identifier: "UHL Case 1", record_kind: "workflow_rerun",
        action_type: "followup_reassessment", decision_type: "ESCALATION",
      }],
    })),
    auditEvents: vi.fn(() => Promise.resolve({ events: [] })),
    downloadAuditJourney: vi.fn(() => Promise.resolve({
      blob: new Blob(["complete"]), filename: "audit-complete-patient-journeys.csv",
    })),
  },
}));

import { api } from "../api.js";
import AuditLog from "../views/AuditLog.jsx";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("complete audit journey export", () => {
  it("downloads the server-generated unpaged journey with active filters", async () => {
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    URL.createObjectURL = vi.fn(() => "blob:audit");
    URL.revokeObjectURL = vi.fn();
    try {
      render(<AuditLog />);
      const button = await screen.findByText("Download complete journey CSV (3)");
      expect(screen.getByText(/explicit previous\/new\/delta observations/i)).toBeTruthy();
      fireEvent.click(button);
      await waitFor(() => expect(api.downloadAuditJourney).toHaveBeenCalledTimes(1));
      const filters = api.downloadAuditJourney.mock.calls[0][0];
      expect(filters.start_utc).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(filters.limit).toBeUndefined();
    } finally {
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
      anchorClick.mockRestore();
    }
  });
});
