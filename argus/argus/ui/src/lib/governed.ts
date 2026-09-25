
export interface GovernedResult {
  plan_hash: string;
  request_hash: string;
  result: {
    status?: string;
    job_id?: string;
    receipt?: string;
    result?: unknown;
    [k: string]: unknown;
  };
}

export interface GovernedRefusal {
  refused: true;
  status: number;
  reason: string;
  detail: unknown;
}

export interface GovernedPlan {
  plan_hash: string;
  request_hash: string;
  plan: Record<string, unknown>;
  read_only: true;
}

let CSRF: string | null = null;
let EXPIRES_AT = 0;
const SESSION_LISTENERS = new Set<() => void>();

export function subscribeGovernedSession(listener: () => void): () => void {
  SESSION_LISTENERS.add(listener);
  return () => SESSION_LISTENERS.delete(listener);
}

function announceSessionChange(): void {
  for (const listener of SESSION_LISTENERS) listener();
}

export function sessionIsOpen(): boolean {
  return CSRF !== null && Date.now() < EXPIRES_AT;
}

export function sessionSecondsLeft(): number {
  return sessionIsOpen() ? Math.max(0, Math.round((EXPIRES_AT - Date.now()) / 1000)) : 0;
}

export async function openSession(): Promise<void> {
  const accessKey = window.prompt(
    "Paste the local ARGUS operator access key from ARGUS_HOME/state/ui_access_key. " +
    "This authorizes governed changes in this browser tab for 15 minutes.",
  )?.trim() ?? "";
  if (!accessKey) throw new Error("an operator access key is required to open a session");
  const r = await fetch("/ui/session", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-Argus-Access-Key": accessKey },
  });
  if (!r.ok) throw new Error(`the transport refused a session: HTTP ${r.status}`);
  const d = (await r.json()) as { csrf: string; expires_in_s: number };
  CSRF = d.csrf;
  EXPIRES_AT = Date.now() + d.expires_in_s * 1000;
  announceSessionChange();
}

export function closeSession(): void {
  CSRF = null;
  EXPIRES_AT = 0;
  announceSessionChange();
}

export function idempotencyKey(action: string, params: Record<string, unknown>): string {
  const basis = `${action}|${JSON.stringify(params, Object.keys(params).sort())}`;
  let h = 5381;
  for (let i = 0; i < basis.length; i += 1) h = ((h << 5) + h + basis.charCodeAt(i)) >>> 0;
  return `ui-${h.toString(16)}-${basis.length.toString(16)}`;
}

export async function operations(): Promise<Record<string, { why: string; required: string[] }>> {
  const r = await fetch("/ui/operations", { credentials: "same-origin" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const d = (await r.json()) as { operations: Record<string, { why: string; required: string[] }> };
  return d.operations;
}

export async function runGoverned(
  action: string,
  params: Record<string, unknown>,
): Promise<GovernedResult | GovernedRefusal> {
  if (!sessionIsOpen()) {
    return {
      refused: true,
      status: 401,
      reason:
        "no governed session is open. Opening one is a deliberate act, so that reaching this " +
        "page is never the same as being authorised to act.",
      detail: null,
    };
  }
  const r = await fetch(`/ui/act/${encodeURIComponent(action)}`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Argus-Csrf": CSRF as string,
      "X-Idempotency-Key": idempotencyKey(action, params),
    },
    body: JSON.stringify({ params }),
  });
  const body = await r.json().catch(() => null);
  if (!r.ok) {
    const d = (body as { detail?: { error?: string; why?: string } } | null)?.detail;
    return {
      refused: true,
      status: r.status,
      reason: d?.error ? `${d.error}${d.why ? ` — ${d.why}` : ""}` : `HTTP ${r.status}`,
      detail: body,
    };
  }
  return body as GovernedResult;
}

export function approvedHash(p: GovernedPlan): string {
  const inner = (p.plan as { plan_sha256?: unknown }).plan_sha256;
  return typeof inner === "string" && inner ? inner : p.plan_hash;
}

export function isRefusal<T>(x: T | GovernedRefusal): x is GovernedRefusal {
  return (x as GovernedRefusal).refused === true;
}

export interface RunOutcome {
  kind: "done" | "refused";
  text: string;
}

export function runOutcome(out: GovernedResult): RunOutcome {
  const r = out.result ?? {};
  const status = String(r.status ?? "OK");
  const job = r.job_id ? ` Recorded as job ${r.job_id}.` : "";
  if (status === "REFUSED" || status === "FAILED") {
    const inner = (r.result ?? {}) as { code?: string; why?: string; reason?: string; blocker_kind?: string };
    const why = inner.why ?? inner.reason ?? "no reason was recorded";
    const blocker = inner.blocker_kind ? ` Blocked by: ${inner.blocker_kind}.` : "";
    return {
      kind: "refused",
      text: `${status === "FAILED" ? "Failed" : "Refused"} by the action itself${inner.code ? ` (${inner.code})` : ""}: ${why}.${blocker}${job}`,
    };
  }
  return { kind: "done", text: `Done — ${status}.${job || " Recorded as job (no job id)."}` };
}

export async function planGoverned(
  action: string,
  params: Record<string, unknown>,
): Promise<GovernedPlan | GovernedRefusal> {
  if (!sessionIsOpen()) {
    return {
      refused: true,
      status: 401,
      reason: "no governed session is open; open one deliberately before planning an action",
      detail: null,
    };
  }
  const r = await fetch(`/ui/plan/${encodeURIComponent(action)}`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Argus-Csrf": CSRF as string,
      "X-Idempotency-Key": idempotencyKey(`plan:${action}`, params),
    },
    body: JSON.stringify({ params }),
  });
  const body = await r.json().catch(() => null);
  if (!r.ok) {
    const d = (body as { detail?: { error?: string; why?: string } } | null)?.detail;
    return {
      refused: true,
      status: r.status,
      reason: d?.error ? `${d.error}${d.why ? ` — ${d.why}` : ""}` : `HTTP ${r.status}`,
      detail: body,
    };
  }
  return body as GovernedPlan;
}

export interface StructuredIntentDefinition {
  intent: string;
  action: string;
  example: string;
}

export interface StructuredIntentPreview {
  status: "PREVIEW";
  resolved: true;
  intent: string;
  action: string;
  params: Record<string, unknown>;
  plan: Record<string, unknown>;
  confirmation_required: boolean;
  preview_sha256: string;
  idempotency_key: string;
  note?: string;
}

export interface StructuredIntentResult {
  status: string;
  resolved: boolean;
  intent?: string;
  action?: string;
  job_id?: string;
  code?: string;
  why?: string;
  result?: unknown;
  supported?: StructuredIntentDefinition[];
}

function intentRefusal(status: number, body: unknown): GovernedRefusal {
  const detail = (body as { detail?: unknown } | null)?.detail ?? body;
  const record = detail as { error?: string; why?: string; code?: string } | null;
  const reason = record?.why ?? record?.error ?? record?.code ?? `HTTP ${status}`;
  return { refused: true, status, reason, detail: body };
}

export async function structuredIntentCatalog(): Promise<StructuredIntentDefinition[]> {
  const r = await fetch("/ui/nl/intents", { credentials: "same-origin" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = (await r.json()) as { intents?: StructuredIntentDefinition[] };
  return body.intents ?? [];
}

export async function previewStructuredIntent(
  text: string,
): Promise<StructuredIntentPreview | StructuredIntentResult | GovernedRefusal> {
  if (!sessionIsOpen()) return intentRefusal(401, { why: "open a governed session first" });
  const key = idempotencyKey("structured-intent", { text });
  const r = await fetch("/ui/nl/preview", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Argus-Csrf": CSRF as string,
      "X-Idempotency-Key": key,
    },
    body: JSON.stringify({ text }),
  });
  const body = await r.json().catch(() => null);
  return r.ok
    ? (body as StructuredIntentPreview | StructuredIntentResult)
    : intentRefusal(r.status, body);
}

export async function confirmStructuredIntent(
  text: string,
  confirmSha256?: string,
): Promise<StructuredIntentResult | GovernedRefusal> {
  if (!sessionIsOpen()) return intentRefusal(401, { why: "open a governed session first" });
  const key = idempotencyKey("structured-intent", { text });
  const body: Record<string, unknown> = { text };
  if (confirmSha256) body.confirm_sha256 = confirmSha256;
  const r = await fetch("/ui/nl/confirm", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Argus-Csrf": CSRF as string,
      "X-Idempotency-Key": key,
    },
    body: JSON.stringify(body),
  });
  const out = await r.json().catch(() => null);
  return r.ok ? (out as StructuredIntentResult) : intentRefusal(r.status, out);
}


export interface AttachmentMeta {
  id: string;
  mime: string;
  ext: string;
  size: number;
  filename: string;
  author: string;
  utc: string;
}

export interface OperatorNote {
  id: string;
  kind: string;
  target: string;
  text: string;
  author: string;
  utc: string;
  attachment_ids?: string[];
  attachments?: AttachmentMeta[];
}

export const ALLOWED_ATTACHMENT_MIME: Record<string, string> = {
  "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
  "video/mp4": ".mp4", "video/webm": ".webm",
};
export const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;

export async function readAnnotations(): Promise<{
  ok: boolean;
  why?: string;
  by_target: Record<string, OperatorNote[]>;
}> {
  const r = await fetch("/api/grail/annotations", { credentials: "same-origin" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as { ok: boolean; why?: string; by_target: Record<string, OperatorNote[]> };
}

export async function addAnnotation(
  target: string,
  text: string,
  supersedes?: string,
  attachmentIds?: string[],
): Promise<OperatorNote> {
  if (!sessionIsOpen()) await openSession();
  const body: Record<string, unknown> = { target, text };
  if (supersedes) body.supersedes = supersedes;
  if (attachmentIds && attachmentIds.length) body.attachment_ids = attachmentIds;
  const r = await fetch("/ui/annotations", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Argus-Csrf": CSRF ?? "" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) {
    throw new Error(
      typeof d?.detail?.why === "string" ? d.detail.why : `the transport refused: HTTP ${r.status}`,
    );
  }
  return d.note as OperatorNote;
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("could not read the file"));
    reader.onload = () => {
      const s = String(reader.result ?? "");
      const comma = s.indexOf(",");
      resolve(comma >= 0 ? s.slice(comma + 1) : s);
    };
    reader.readAsDataURL(file);
  });
}

export async function uploadAttachment(file: File): Promise<AttachmentMeta> {
  if (!(file.type in ALLOWED_ATTACHMENT_MIME)) {
    throw new Error(
      `${file.type || "that file type"} is not accepted -- only photos and short clips ` +
        `(${Object.keys(ALLOWED_ATTACHMENT_MIME).join(", ")})`,
    );
  }
  if (file.size > MAX_ATTACHMENT_BYTES) {
    throw new Error(
      `${file.name} is ${Math.round(file.size / 1024 / 1024)} MB; the limit is ` +
        `${Math.round(MAX_ATTACHMENT_BYTES / 1024 / 1024)} MB`,
    );
  }
  if (!sessionIsOpen()) await openSession();
  const data_base64 = await fileToBase64(file);
  const r = await fetch("/ui/annotations/attachments", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Argus-Csrf": CSRF ?? "" },
    body: JSON.stringify({ filename: file.name, mime: file.type, data_base64 }),
  });
  const d = await r.json();
  if (!r.ok) {
    throw new Error(
      typeof d?.detail?.why === "string" ? d.detail.why : `the transport refused: HTTP ${r.status}`,
    );
  }
  return d.attachment as AttachmentMeta;
}

export async function hideAnnotation(noteId: string): Promise<void> {
  if (!sessionIsOpen()) await openSession();
  const r = await fetch(`/ui/annotations/${encodeURIComponent(noteId)}/hide`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-Argus-Csrf": CSRF ?? "" },
  });
  if (!r.ok) throw new Error(`the transport refused: HTTP ${r.status}`);
}

export interface ReviewerIdentity {
  id: string;
  cls: string;
}

export async function submitReviewAnswer(
  target: string,
  taskId: string,
  value: string,
  confidence: number,
  durationS: number,
  notes?: string,
  reviewer?: ReviewerIdentity | null,
): Promise<unknown> {
  if (!sessionIsOpen()) await openSession();
  const body: Record<string, unknown> = {
    target, task_id: taskId, value, confidence, duration_s: durationS,
  };
  if (notes) body.notes = notes;
  if (reviewer) {
    body.reviewer_id = reviewer.id;
    body.reviewer_class = reviewer.cls;
  }
  const r = await fetch("/ui/review/answer", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Argus-Csrf": CSRF ?? "" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) {
    throw new Error(
      typeof d?.detail?.why === "string" ? d.detail.why : `the transport refused: HTTP ${r.status}`,
    );
  }
  return d.task;
}

export async function submitReviewValidation(
  target: string,
  taskId: string,
  value: string,
  reviewer: ReviewerIdentity,
  notes?: string,
): Promise<unknown> {
  if (!sessionIsOpen()) await openSession();
  const body: Record<string, unknown> = {
    target, task_id: taskId, value, reviewer_id: reviewer.id, reviewer_class: reviewer.cls,
  };
  if (notes) body.notes = notes;
  const r = await fetch("/ui/review/validate", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Argus-Csrf": CSRF ?? "" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) {
    throw new Error(
      typeof d?.detail?.why === "string" ? d.detail.why : `the transport refused: HTTP ${r.status}`,
    );
  }
  return d.task;
}

export interface CoordinateTransform {
  volume_id: string;
  array_path: string;
  crop_row0: number;
  crop_col0: number;
  depth_offset: number;
  depth_planes: number;
  voxel_um: number;
}

async function _post(path: string, body: Record<string, unknown>): Promise<unknown> {
  if (!sessionIsOpen()) await openSession();
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Argus-Csrf": CSRF ?? "" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) {
    throw new Error(
      typeof d?.detail?.why === "string" ? d.detail.why : `the transport refused: HTTP ${r.status}`,
    );
  }
  return d;
}

export async function recordCorrection(
  meshPath: string,
  proposalId: string,
  proposalSha256: string,
  action: string,
  payload: Record<string, unknown>,
  transform: CoordinateTransform,
  viewHash?: string,
): Promise<unknown> {
  const d = (await _post("/ui/corrections/record", {
    mesh_path: meshPath, proposal_id: proposalId, proposal_sha256: proposalSha256,
    action, payload, transform, ...(viewHash ? { view_hash: viewHash } : {}),
  })) as { constraint: unknown };
  return d.constraint;
}

export async function undoCorrection(meshPath: string, eventId: string): Promise<unknown> {
  const d = (await _post("/ui/corrections/undo", { mesh_path: meshPath, event_id: eventId })) as {
    undo: unknown;
  };
  return d.undo;
}

export async function recordDecision(
  meshPath: string,
  proposalId: string,
  decision: "ACCEPTED" | "REJECTED",
  why?: string,
): Promise<unknown> {
  const d = (await _post("/ui/corrections/decision", {
    mesh_path: meshPath, proposal_id: proposalId, decision, ...(why ? { why } : {}),
  })) as { decision: unknown };
  return d.decision;
}

export async function promoteCorrection(
  meshPath: string,
  proposalId: string,
  toState: string,
): Promise<unknown> {
  const d = (await _post("/ui/corrections/promote", {
    mesh_path: meshPath, proposal_id: proposalId, to_state: toState,
  })) as { promotion: unknown };
  return d.promotion;
}

export async function readCorrections(meshPath: string): Promise<{
  contract: string;
  proposals: Record<string, {
    proposal_id: string;
    state: string;
    decision: { value: string; why?: string; utc: string } | null;
    constraints: Array<Record<string, unknown> & { event_id: string; undone: boolean; stale: boolean }>;
    active_constraints: Array<Record<string, unknown>>;
  }>;
}> {
  const r = await fetch(`/api/corrections?path=${encodeURIComponent(meshPath)}`, {
    credentials: "same-origin",
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return await r.json();
}
