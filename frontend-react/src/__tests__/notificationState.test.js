import { describe, expect, it } from "vitest";
import {
  mergeNotificationFallback,
  reconcileNotificationSnapshot,
} from "../notificationState.js";

const row = (id, { read = false, semanticKey = id, at = 1 } = {}) => ({
  id, semanticKey, read, at, title: id, body: id,
});

describe("notification state reconciliation", () => {
  it("loads the initial server snapshot quietly, including unread rows", () => {
    const result = reconcileNotificationSnapshot([row("initial")], new Set(), false);
    expect(result.newlyUnread).toEqual([]);
    expect(result.announced.has("initial")).toBe(true);
  });

  it("rings only for genuinely new unread server events", () => {
    const previous = new Set(["existing", "already-read"]);
    const result = reconcileNotificationSnapshot([
      row("existing"), row("already-read", { read: true }), row("new"),
    ], previous, true);
    expect(result.newlyUnread.map((item) => item.id)).toEqual(["new"]);
  });

  it("does not ring when a workflow fallback is replaced by its durable ID", () => {
    const event = "overdue_vitals:case-1:2026-08-14T12:00:00.000Z";
    const previous = new Set([event]);
    const result = reconcileNotificationSnapshot([
      row("ntf-v1-durable", { semanticKey: event }),
    ], previous, true);
    expect(result.newlyUnread).toEqual([]);
  });

  it("deduplicates degraded fallback rows by semantic event identity", () => {
    const event = "escalation:case-1:2026-08-14T12:00:00.000Z";
    const merged = mergeNotificationFallback(
      [row("durable", { semanticKey: event })],
      [row("fallback", { semanticKey: event }), row("other")],
    );
    expect(merged.map((item) => item.id)).toEqual(["durable", "other"]);
  });

  it("authoritatively removes rows absent from the next server snapshot", () => {
    const result = reconcileNotificationSnapshot([], new Set(["old"]), true);
    expect(result.ordered).toEqual([]);
  });
});
