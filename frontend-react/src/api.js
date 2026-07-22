/* API layer — same-origin calls into the FastAPI backend (app/main.py).
   Mirrors frontend/api_client.py. In the demo profile the backend accepts
   X-Demo-Role (and X-Demo-User for persona display); both are ignored by the
   backend in every real-auth / patient-data / local-research profile. */

let demoRole = null;
let demoUser = null;
/* HTTP header values must be ASCII: fold diacritics (Sinéad -> Sinead) for the
   header/audit identity; the UI keeps the accented display name. */
export const asciiFold = (s) => s ? s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^\x20-\x7E]/g, "").trim() : null;
export function setDemoIdentity(role, user) { demoRole = role || null; demoUser = asciiFold(user); }

export class ApiError extends Error {
  constructor(status, detail) { super(detail || `HTTP ${status}`); this.status = status; this.detail = detail; }
}

async function request(method, path, body) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (demoRole) headers["X-Demo-Role"] = demoRole;
  if (demoUser) headers["X-Demo-User"] = demoUser;
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timeout = controller ? setTimeout(() => controller.abort(), 45000) : null;
  let resp;
  try {
    resp = await fetch(path, {
      method, headers, credentials: "same-origin", signal: controller?.signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    if (e?.name === "AbortError") {
      throw new ApiError(408, "Request timed out. The workflow did not finish; try again or check the backend logs.");
    }
    throw e;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
  const ctype = resp.headers.get("content-type") || "";
  const payload = ctype.includes("json") ? await resp.json().catch(() => null) : await resp.text();
  if (!resp.ok) {
    const detail = payload && typeof payload === "object" ? payload.detail ?? JSON.stringify(payload) : String(payload || resp.statusText);
    throw new ApiError(resp.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}
async function uploadFile(path, file) {
  const headers = { Accept: "application/json" };
  if (demoRole) headers["X-Demo-Role"] = demoRole;
  if (demoUser) headers["X-Demo-User"] = demoUser;
  const data = new FormData();
  data.append("file", file);
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timeout = controller ? setTimeout(() => controller.abort(), 60000) : null;
  let resp;
  try {
    resp = await fetch(path, {
      method: "POST", headers, credentials: "same-origin", signal: controller?.signal, body: data,
    });
  } catch (e) {
    if (e?.name === "AbortError") {
      throw new ApiError(408, "Upload timed out. Try again with a smaller file or check the backend logs.");
    }
    throw e;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
  const ctype = resp.headers.get("content-type") || "";
  const payload = ctype.includes("json") ? await resp.json().catch(() => null) : await resp.text();
  if (!resp.ok) {
    const detail = payload && typeof payload === "object" ? payload.detail ?? JSON.stringify(payload) : String(payload || resp.statusText);
    throw new ApiError(resp.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}
const get = (p) => request("GET", p);
const post = (p, b) => request("POST", p, b);
const qs = (params) => {
  const u = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") u.set(k, v); });
  const s = u.toString(); return s ? `?${s}` : "";
};

export const api = {
  authSession: () => get("/auth/session"),
  uiAccess: (b) => post("/auth/ui-access", b),
  systemMeta: () => get("/system/meta"),
  health: () => get("/health"),
  runtimeStatus: () => get("/runtime/status"),
  fullMimicStatus: () => get("/status/full-mimic"),
  llmStatus: () => get("/status/llm"),
  securityStatus: () => get("/security/status"),
  listCases: (params) => get(`/cases${qs(params)}`),
  getCase: (uid) => get(`/cases/${encodeURIComponent(uid)}`),
  workflowQueue: (params) => get(`/workflow/queue${qs(params)}`),
  runAssessment: (uid) => post(`/cases/${encodeURIComponent(uid)}/assessments`),
  /* Read-only acuity for queue colouring: computes the advisory
     category WITHOUT writing a workflow-run audit record, so rendering or
     scrolling a queue never creates review evidence or clinician attribution. */
  previewAssessment: (uid) => post(`/cases/${encodeURIComponent(uid)}/assessments?preview=true`),
  uploadSupportingScan: (uid, file) => uploadFile(`/cases/${encodeURIComponent(uid)}/supporting-uploads`, file),
  /* Reassessment / requested-information response (PERM_RUN_ASSESSMENT).
     body: { updated_vitals?, updated_complaint?, updated_context?, scan_uploads? }
     Vitals use MIMIC units — temperature in °F (convert from °C before calling).
     Note: patient-specific LLM explanation routes are disabled server-side by
     default (see _require_patient_explanation_route_enabled), so no explain
     helper exists here by design. */
  followupCase: (uid, body) => post(`/cases/${encodeURIComponent(uid)}/followups`, body),
  /* Clinician-facing MULTI-AGENT explanation of THIS case's acuity — the
     IntakeAgent/ValidationAgent/SafetyReviewAgent/ExplanationAgent team.
     Distinct from the ITD system chatbot. Returns { status, agent_turns,
     final_explanation, safety_failures }. status may be PASS, NOT_CONFIGURED
     (no Azure OpenAI), SAFETY_FAIL, or ERROR. */
  multiagentExplainCase: (uid, question) => post(`/cases/${encodeURIComponent(uid)}/multiagent-explanations`, question ? { question } : {}),
  submitReview: (uid, body) => post(`/cases/${encodeURIComponent(uid)}/reviews`, body),
  sweepOverdueVitals: () => post("/workflow/overdue-vitals/sweep?limit=50000"),
  acknowledgeOverdue: (uid) => post(`/cases/${encodeURIComponent(uid)}/vitals/acknowledge-overdue`),
  auditEvents: (limit = 300) => get(`/audit/events${qs({ limit })}`),
  auditRecords: (limit = 400) => get(`/audit/records${qs({ limit })}`),
  auditDashboard: (filters) => get(`/audit/dashboard${qs(filters)}`),
  modelPerformance: () => get("/model/performance"),
  systemAssistant: (question) => post("/system/assistant", { question }),
  governanceReport: () => get("/governance/report"),
};

/* Decision helpers — exact review_status vocabulary from app/schemas/review.py. */
export const decisions = {
  accept: (uid, { systemPrediction, comment }) => api.submitReview(uid, {
    review_status: "ACCEPTED_AS_PRESENTED",
    review_comment: comment || "Advisory accepted at triage.",
    system_prediction: systemPrediction ?? null,
    clinician_decision: systemPrediction ?? null,
  }),
  override: (uid, { systemPrediction, decision, reason }) => api.submitReview(uid, {
    review_status: "OVERRIDDEN",
    review_comment: `Nurse override recorded at triage.`,
    system_prediction: systemPrediction ?? null,
    clinician_decision: decision,
    clinician_override: decision,
    override_reason: reason,
  }),
  escalate: (uid, { systemPrediction, toRole, reason }) => api.submitReview(uid, {
    review_status: "ESCALATION_REQUIRED",
    review_comment: reason,
    system_prediction: systemPrediction ?? null,
    escalation_target_role: toRole || "ed_doctor",
  }),
  requestInfo: (uid, { fields, comment }) => api.submitReview(uid, {
    review_status: "REQUEST_MORE_INFORMATION",
    review_comment: comment || "Further information requested before triage decision.",
    requested_fields: fields || [],
  }),
  confirmEscalation: (uid, { note, decision }) => api.submitReview(uid, {
    review_status: "ESCALATION_CONFIRMED",
    review_comment: note,
    clinician_decision: decision ?? null,
  }),
  resolveEscalation: (uid, { note, decision }) => api.submitReview(uid, {
    review_status: "ESCALATION_RESOLVED",
    review_comment: note,
    clinician_decision: decision ?? null,
  }),
  discharge: (uid, { comment }) => api.submitReview(uid, {
    review_status: "DISCHARGED",
    review_comment: comment || "Discharged from ED.",
  }),
  closeAdmitted: (uid, { comment }) => api.submitReview(uid, {
    review_status: "CASE_CLOSED",
    review_comment: comment || "Case closed — admitted to ward.",
  }),
};
