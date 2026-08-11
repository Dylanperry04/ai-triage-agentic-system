import { describe, it, expect, beforeEach, vi } from "vitest";
import { api, decisions, setDemoIdentity, asciiFold } from "../api.js";

/* Capture every fetch and answer with an empty JSON body. */
let calls;
beforeEach(() => {
  calls = [];
  global.fetch = vi.fn(async (url, opts) => {
    calls.push({ url, opts });
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({}) };
  });
  setDemoIdentity(null, null);
});
const lastBody = () => JSON.parse(calls[calls.length - 1].opts.body);

describe("decision payloads match the backend review contract", () => {
  it("accept logs ACCEPTED_AS_PRESENTED with system prediction and clinician decision", async () => {
    await decisions.accept("case-1", { systemPrediction: "Very Urgent (Orange)" });
    const b = lastBody();
    expect(calls.at(-1).url).toBe("/cases/case-1/reviews");
    expect(b.review_status).toBe("ACCEPTED_AS_PRESENTED");
    expect(b.system_prediction).toBe("Very Urgent (Orange)");
    expect(b.clinician_decision).toBe("Very Urgent (Orange)");
  });

  it("override carries clinician_override and the mandatory reason", async () => {
    await decisions.override("case-2", { systemPrediction: "Urgent (Yellow)", decision: "Very urgent (priority 2)", reason: "Rigors and new hypotension." });
    const b = lastBody();
    expect(b.review_status).toBe("OVERRIDDEN");
    expect(b.clinician_override).toBe("Very urgent (priority 2)");
    expect(b.override_reason).toBe("Rigors and new hypotension.");
  });

  it("requestInfo posts the routing action with requested fields", async () => {
    await decisions.requestInfo("case-3", { fields: ["ECG"], comment: "Awaiting tracing." });
    const b = lastBody();
    expect(b.review_status).toBe("REQUEST_MORE_INFORMATION");
    expect(b.requested_fields).toEqual(["ECG"]);
  });
});

describe("multi-agent case-acuity explanation endpoint", () => {
  it("posts to the multiagent-explanations route with the question", async () => {
    await api.multiagentExplainCase("case-7", "Why not a higher category?");
    expect(calls.at(-1).url).toBe("/cases/case-7/multiagent-explanations");
    expect(lastBody()).toEqual({ question: "Why not a higher category?" });
  });
  it("omits the question body when none is given", async () => {
    await api.multiagentExplainCase("case-7");
    expect(calls.at(-1).url).toBe("/cases/case-7/multiagent-explanations");
    expect(lastBody()).toEqual({});
  });
});

describe("read-only preview assessment", () => {
  it("previewAssessment targets the non-auditing preview endpoint", async () => {
    await api.previewAssessment("case-9");
    expect(calls.at(-1).url).toBe("/cases/case-9/assessments?preview=true");
  });
  it("runAssessment (opened case) targets the auditing endpoint", async () => {
    await api.runAssessment("case-9");
    expect(calls.at(-1).url).toBe("/cases/case-9/assessments");
  });
});

describe("reassessment endpoint", () => {
  it("posts followups with updated vitals in backend (MIMIC/°F) units", async () => {
    await api.followupCase("case-4", { updated_vitals: { heartrate: 128, temperature: 102.2 }, updated_context: "ECG done." });
    expect(calls.at(-1).url).toBe("/cases/case-4/followups");
    const b = lastBody();
    expect(b.updated_vitals.temperature).toBe(102.2);
    expect(b.updated_context).toBe("ECG done.");
  });

  it("uploads supporting scans as multipart form data", async () => {
    const file = new File(["scan"], "chest-xray.png", { type: "image/png" });
    await api.uploadSupportingScan("case-4", file);
    expect(calls.at(-1).url).toBe("/cases/case-4/supporting-uploads");
    expect(calls.at(-1).opts.method).toBe("POST");
    expect(calls.at(-1).opts.body).toBeInstanceOf(FormData);
    expect(calls.at(-1).opts.headers["Content-Type"]).toBeUndefined();
  });
});

describe("demo identity headers", () => {
  it("folds fadas out of X-Demo-User (h11 rejects non-ASCII header bytes)", () => {
    expect(asciiFold("Sinéad Ó Dálaigh")).toBe("Sinead O Dalaigh");
  });
  it("sends X-Demo-Role / X-Demo-User only when set", async () => {
    setDemoIdentity("triage_nurse", "Sinéad Hartigan");
    await api.listCases({ limit: 5 });
    const h = calls.at(-1).opts.headers;
    expect(h["X-Demo-Role"]).toBe("triage_nurse");
    expect(h["X-Demo-User"]).toBe("Sinead Hartigan");
  });
});
