import { describe, it, expect } from "vitest";
import { priorityFromCategory, toC, vitalState, catOf, MTS, NEUTRAL_CAT } from "../theme.js";

describe("priorityFromCategory", () => {
  it("maps backend category strings to MTS-style priorities", () => {
    expect(priorityFromCategory("Immediate (Red)")).toBe(1);
    expect(priorityFromCategory("Very Urgent (Orange)")).toBe(2);
    expect(priorityFromCategory("Urgent (Yellow)")).toBe(3);
    expect(priorityFromCategory("Standard (Green)")).toBe(4);
    expect(priorityFromCategory("Non-Urgent (Blue)")).toBe(5);
  });
  it("accepts numeric acuities and numeric strings", () => {
    expect(priorityFromCategory(2)).toBe(2);
    expect(priorityFromCategory("4")).toBe(4);
  });
  it("falls back for null, out-of-range, or garbage", () => {
    expect(priorityFromCategory(null)).toBeNull();
    expect(priorityFromCategory(9)).toBeNull();
    expect(priorityFromCategory("no category here", null)).toBeNull();
    expect(priorityFromCategory(null, 3)).toBe(3);
  });
});

describe("toC (MIMIC stores Fahrenheit — the historical unit bug this guards)", () => {
  it("converts °F to °C when the unit says F", () => {
    expect(toC(98.6, "F")).toBe(37.0);
    expect(toC(103.1, "F")).toBe(39.5);
  });
  it("passes °C through untouched", () => {
    expect(toC(37.2, "C")).toBe(37.2);
  });
  it("handles null and non-numeric safely", () => {
    expect(toC(null, "F")).toBeNull();
    expect(toC("abc", "F")).toBeNull();
  });
});

describe("vitalState thresholds", () => {
  it("flags hypoxia as critical below the warning band", () => {
    expect(vitalState("o2sat", 98)).toBe("ok");
    expect(vitalState("o2sat", 93)).toBe("warn");
    expect(vitalState("o2sat", 88)).toBe("crit");
  });
  it("flags fever bands on tempC", () => {
    expect(vitalState("tempC", 37.0)).toBe("ok");
    expect(vitalState("tempC", 38.2)).toBe("warn");
    expect(vitalState("tempC", 39.0)).toBe("crit");
  });
  it("treats severe pain as critical", () => {
    expect(vitalState("pain", 2)).toBe("ok");
    expect(vitalState("pain", 7)).toBe("crit");
  });
  it("marks extreme tachycardia critical (15% over range)", () => {
    expect(vitalState("heartrate", 80)).toBe("ok");
    expect(vitalState("heartrate", 105)).toBe("warn");
    expect(vitalState("heartrate", 130)).toBe("crit");
  });
});

describe("catOf", () => {
  it("returns the MTS entry for known priorities and a neutral card otherwise", () => {
    expect(catOf(1)).toBe(MTS[1]);
    expect(catOf(undefined)).toBe(NEUTRAL_CAT);
  });
});
