/**
 * Typed client for the DocIntel API.
 *
 * The token lives in memory plus sessionStorage rather than localStorage:
 * sessionStorage is cleared when the tab closes and is not shared across tabs,
 * which limits the blast radius if a script ever gets injected. It is still
 * reachable from JS — a httpOnly cookie would be stronger, and is the right
 * move once the API supports it.
 */

const TOKEN_KEY = "docintel.token";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let token: string | null = sessionStorage.getItem(TOKEN_KEY);
const listeners = new Set<(t: string | null) => void>();

export function getToken() {
  return token;
}

export function setToken(next: string | null) {
  token = next;
  if (next) sessionStorage.setItem(TOKEN_KEY, next);
  else sessionStorage.removeItem(TOKEN_KEY);
  listeners.forEach((fn) => fn(next));
}

export function onAuthChange(fn: (t: string | null) => void) {
  listeners.add(fn);
  // Returns void so it can be used directly as a React effect cleanup.
  return () => {
    listeners.delete(fn);
  };
}

function authHeaders(extra: Record<string, string> = {}) {
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

async function handle(response: Response) {
  if (response.status === 401) {
    setToken(null);
    throw new ApiError(401, "Your session has expired. Please sign in again.");
  }
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.errors)) {
        detail = body.errors.map((e: any) => e.msg ?? String(e)).join("; ");
      }
    } catch {
      /* non-JSON error body — handled below */
    }

    // The API answers every error with a JSON `detail`. A 5xx without one did
    // not come from the API at all: it is the dev proxy reporting that it
    // could not reach the backend. "Request failed (500)" sends people looking
    // for a bug in the feature they just clicked, when the server is simply
    // not running.
    if (!detail) {
      detail = response.status >= 500
        ? `The API did not respond (${response.status}). It may not be running — ` +
          "start it with: python -m uvicorn docintel.main:app --port 8000"
        : `Request failed (${response.status})`;
    }
    throw new ApiError(response.status, detail);
  }
  return response;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await handle(
    await fetch(path, {
      ...init,
      headers: authHeaders({
        ...(init.body && !(init.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        ...(init.headers as Record<string, string>),
      }),
    }),
  );
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function requestBlob(path: string, init: RequestInit = {}): Promise<Blob> {
  const response = await handle(
    await fetch(path, { ...init, headers: authHeaders(init.headers as Record<string, string>) }),
  );
  return response.blob();
}

async function requestBuffer(path: string): Promise<ArrayBuffer> {
  const response = await handle(await fetch(path, { headers: authHeaders() }));
  return response.arrayBuffer();
}

const json = (body: unknown) => JSON.stringify(body);

// ------------------------------------------------------------------ types

export interface User {
  id: string;
  email: string;
  full_name: string | null;
}

export interface Workspace {
  id: string;
  name: string;
  description: string | null;
  role?: string;
}

export type DocumentStatus = "uploaded" | "processing" | "ready" | "failed";

export interface DocumentSummary {
  id: string;
  workspace_id: string;
  filename: string;
  size_bytes: number;
  status: DocumentStatus;
  page_count: number | null;
  is_archived: boolean;
  created_at: string;
}

export interface DocumentDetail extends DocumentSummary {
  doc_metadata: Record<string, any>;
  version_count: number;
}

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface WordBox {
  text: string;
  start: number;
  end: number;
  pdf_rect: Rect;
  view_rect: Rect;
}

export interface PageText {
  page: number;
  width: number;
  height: number;
  text: string;
  words: WordBox[];
}

export interface SearchMatch {
  page: number;
  start: number;
  end: number;
  text: string;
  context: string;
  rects: Rect[];
}

export type AnnotationKind =
  | "highlight" | "underline" | "strikethrough" | "note"
  | "comment" | "drawing" | "shape" | "arrow" | "textbox" | "stamp";

export interface Annotation {
  id: string;
  document_id: string;
  kind: AnnotationKind;
  page: number;
  rect: Rect | Record<string, never>;
  quads: Rect[];
  colour: string;
  opacity: number;
  selected_text: string | null;
  body: string | null;
  parent_id: string | null;
  is_resolved: boolean;
  author_id: string | null;
  created_at: string;
}

export interface SecurityFinding {
  finding_id: string;
  title: string;
  severity: "info" | "low" | "medium" | "high";
  detail: string;
  locations: string;
}

export interface SecurityReport {
  document_id: string;
  scanned: boolean;
  risk_level?: string;
  risk_label?: string;
  headline?: string;
  findings: SecurityFinding[];
}

export interface FormField {
  name: string;
  kind: string;
  value: string | null;
  required: boolean;
  read_only: boolean;
  options: string[];
  tooltip: string | null;
  page: number | null;
  rect: number[] | null;
  max_length: number | null;
}

export interface FormReport {
  has_form: boolean;
  is_xfa: boolean;
  fillable: boolean;
  note: string;
  required_fields: string[];
  fields: FormField[];
}

export interface RedactCandidate {
  kind: string;
  text: string;
  page: number;
  start: number;
  end: number;
  rects: Rect[];
}

export interface VersionResult {
  version: number;
  label: string;
  size_bytes: number;
  page_count: number | null;
  note?: string | null;
}

export interface AiSelectionResult {
  mode: string;
  output: string;
  model: string;
  tokens: number;
  injection_detected: boolean;
  injection_note: string | null;
}

export interface AiCitation {
  page: number;
  excerpt: string;
}

export interface AiAnswer {
  question: string;
  answer: string;
  citations: AiCitation[];
  pages_searched: number[];
  dropped_citations: number[];
  retrieval: string;
  model: string;
  tokens: number;
  note: string | null;
}

export interface ConversionTarget {
  target: string;
  label: string;
  extension: string;
  fidelity: string;
  fidelity_note: string;
  available: boolean;
  reason: string | null;
}

export interface ComparisonResult {
  identical: boolean;
  summary: string;
  old_page_count: number;
  new_page_count: number;
  added_pages: number[];
  removed_pages: number[];
  changed_pages: number[];
  numbers_changed: { old: string; new: string; context: string }[];
  dates_changed: { old: string; new: string; context: string }[];
  pages: {
    old_page: number | null;
    new_page: number | null;
    status: string;
    similarity: number;
    changes: { kind: string; old: string; new: string }[];
  }[];
  interpretation: string | null;
  note: string;
}

export interface SignatureAsset {
  id: string;
  label: string;
  kind: string;
  width: number;
  height: number;
}

export interface SignatureField {
  id: string;
  type: string;
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
  required: boolean;
  label: string | null;
  recipient_id?: string | null;
  filled?: boolean;
  value?: string | null;
}

export interface SignatureRecipient {
  id: string;
  email: string;
  name: string | null;
  order: number;
  state: string;
  signing_path?: string;
}

export interface SignatureRequest {
  id: string;
  document_id: string;
  title: string;
  message: string | null;
  state: string;
  sequential: boolean;
  document_hash: string | null;
  signed_version: number | null;
  recipients: SignatureRecipient[];
  fields: SignatureField[];
  legal_notice: string;
}

export interface AuditEvent {
  at: string;
  event: string;
  actor: string | null;
  detail: string | null;
  ip_address: string | null;
  document_hash: string | null;
}

export interface SigningView {
  request_id: string;
  title: string;
  message: string | null;
  state: string;
  your_turn: boolean;
  recipient: { email: string; name: string | null; state: string; order: number };
  fields: SignatureField[];
  legal_notice: string;
}

export interface OcrAssessment {
  classification: string;
  summary: string;
  pages_needing_ocr: number[];
  pages: { page: number; characters: number; needs_ocr: boolean; reason: string }[];
  engine: { name: string; available: boolean; reason: string | null; languages: string[] };
}

export interface TranslationResult {
  target_language: string;
  pages: { page: number; original: string; translated: string }[];
  glossary: Record<string, string>;
  version: number | null;
  fidelity: string;
  note: string;
  tokens: number;
}

export interface SummaryResult {
  document_id: string;
  mode: string;
  summary: string;
  page_count: number;
  sections: { index: number; pages: string; summary: string; failed: boolean }[];
  model: string;
  tokens: number;
  injection_detected: boolean;
  injection_note: string | null;
  note: string;
  cached: boolean;
}

export interface AnalysisResult {
  from_document: {
    document_type: string;
    purpose: string;
    audience: string;
    topics: string[];
    key_points: { point: string; page: number | null }[];
    entities: Record<string, string[]>;
    dates: { date: string; what: string; page: number | null }[];
    obligations: { who: string; must: string; page: number | null }[];
    risks: { risk: string; page: number | null }[];
    stated_recommendations: { recommendation: string; page: number | null }[];
  };
  ai_interpretation: { observations: string[] };
  note: string;
  tokens: number;
}

export interface InsightsResult {
  word_count: number;
  character_count: number;
  page_count: number;
  keywords: string[];
  sentiment: { sentiment: string; score: number;
    positive_words: number; negative_words: number };
  readability: { reading_level: string; complexity_score: number;
    avg_words_per_sentence: number; avg_word_length: number };
  method_notes: Record<string, string>;
  note: string;
}

export interface QuotesResult {
  quotes: { text: string; page: number | null }[];
  note: string;
  cached: boolean;
}

export interface Job {
  id: string;
  type: string;
  state: "queued" | "processing" | "completed" | "failed" | "cancelled";
  progress: number;
  progress_note: string | null;
  error: string | null;
}

// ------------------------------------------------------------------- api

export const api = {
  // --- auth
  async register(email: string, password: string) {
    const body = await request<{ access_token: string }>("/api/v1/auth/register", {
      method: "POST",
      body: json({ email, password }),
    });
    setToken(body.access_token);
  },

  async login(email: string, password: string) {
    const body = await request<{ access_token: string }>("/api/v1/auth/login", {
      method: "POST",
      body: json({ email, password }),
    });
    setToken(body.access_token);
  },

  logout() {
    setToken(null);
  },

  me: () => request<User>("/api/v1/auth/me"),

  /** Public: tells the client whether to show the sign-in screen. */
  authMode: () =>
    request<{
      mode: "required" | "open";
      open_access: boolean;
      environment: string;
      warning: string | null;
    }>("/api/v1/auth/mode"),

  // --- workspaces
  workspaces: () => request<Workspace[]>("/api/v1/workspaces"),

  createWorkspace: (name: string, description?: string) =>
    request<Workspace>("/api/v1/workspaces", {
      method: "POST",
      body: json({ name, description }),
    }),

  // --- documents
  documents: (workspaceId: string, search = "", includeArchived = false) =>
    request<{ items: DocumentSummary[]; total: number }>(
      `/api/v1/documents?workspace_id=${encodeURIComponent(workspaceId)}` +
        (search ? `&search=${encodeURIComponent(search)}` : "") +
        (includeArchived ? "&include_archived=true" : ""),
    ),

  document: (id: string) => request<DocumentDetail>(`/api/v1/documents/${id}`),

  upload(workspaceId: string, file: File) {
    const form = new FormData();
    form.append("workspace_id", workspaceId);
    form.append("file", file);
    return request<{ document: DocumentSummary; jobs: string[] }>("/api/v1/documents", {
      method: "POST",
      body: form,
    });
  },

  /** Permanent: removes the database rows AND every stored byte. */
  deleteDocument: (id: string) =>
    request<void>(`/api/v1/documents/${id}`, { method: "DELETE" }),

  /** Reversible: hides the document from the default listing. */
  archiveDocument: (id: string) =>
    request<DocumentSummary>(`/api/v1/documents/${id}/archive`, { method: "POST" }),

  unarchiveDocument: (id: string) =>
    request<DocumentSummary>(`/api/v1/documents/${id}/restore`, { method: "POST" }),

  downloadBuffer: (id: string, version?: number) =>
    requestBuffer(
      `/api/v1/documents/${id}/download` + (version ? `?version=${version}` : ""),
    ),

  downloadBlob: (id: string, version?: number) =>
    requestBlob(`/api/v1/documents/${id}/download` + (version ? `?version=${version}` : "")),

  // --- content
  text: (id: string, page?: number) =>
    request<{ pages: PageText[] }>(
      `/api/v1/documents/${id}/text` + (page ? `?page=${page}` : ""),
    ),

  search: (id: string, query: string, wholeWords = false, caseSensitive = false) =>
    request<{ total: number; matches: SearchMatch[] }>(
      `/api/v1/documents/${id}/search?q=${encodeURIComponent(query)}` +
        `&whole_words=${wholeWords}&case_sensitive=${caseSensitive}`,
    ),

  // --- annotations
  annotations: (id: string) =>
    request<Annotation[]>(`/api/v1/documents/${id}/annotations`),

  createAnnotation: (
    id: string,
    // `points` carries arrows and freehand strokes; `quads` carries areas.
    body: Partial<Annotation> & { kind: AnnotationKind; page: number;
                                  points?: { x: number; y: number }[] },
  ) =>
    request<Annotation>(`/api/v1/documents/${id}/annotations`, {
      method: "POST",
      body: json(body),
    }),

  updateAnnotation: (id: string, annotationId: string, body: Record<string, unknown>) =>
    request<Annotation>(`/api/v1/documents/${id}/annotations/${annotationId}`, {
      method: "PATCH",
      body: json(body),
    }),

  resolveAnnotation: (id: string, annotationId: string) =>
    request<Annotation>(`/api/v1/documents/${id}/annotations/${annotationId}/resolve`, {
      method: "POST",
    }),

  reopenAnnotation: (id: string, annotationId: string) =>
    request<Annotation>(`/api/v1/documents/${id}/annotations/${annotationId}/reopen`, {
      method: "POST",
    }),

  deleteAnnotation: (id: string, annotationId: string) =>
    request<void>(`/api/v1/documents/${id}/annotations/${annotationId}`, {
      method: "DELETE",
    }),

  // --- security, forms
  security: (id: string) => request<SecurityReport>(`/api/v1/documents/${id}/security`),
  form: (id: string) => request<FormReport>(`/api/v1/documents/${id}/form`),

  fillForm: (id: string, values: Record<string, string>, flatten = false) =>
    request<VersionResult>(`/api/v1/documents/${id}/form/fill`, {
      method: "POST",
      body: json({ values, flatten }),
    }),

  // --- redaction
  detectSensitive: (id: string, customTerms: string[] = []) =>
    request<{ total: number; candidates: RedactCandidate[]; available_kinds: string[] }>(
      `/api/v1/documents/${id}/redact/detect`,
      { method: "POST", body: json({ custom_terms: customTerms }) },
    ),

  applyRedaction: (id: string, targets: RedactCandidate[]) =>
    request<{ version: number; verified: boolean; note: string }>(
      `/api/v1/documents/${id}/redact/apply`,
      { method: "POST", body: json({ targets }) },
    ),

  // --- page operations
  rotate: (id: string, pages: number[], degrees: number) =>
    request<VersionResult>(`/api/v1/documents/${id}/pages/rotate`, {
      method: "POST",
      body: json({ pages, degrees }),
    }),

  deletePages: (id: string, pages: number[]) =>
    request<VersionResult>(`/api/v1/documents/${id}/pages/delete`, {
      method: "POST",
      body: json({ pages }),
    }),

  duplicatePages: (id: string, pages: number[]) =>
    request<VersionResult>(`/api/v1/documents/${id}/pages/duplicate`, {
      method: "POST",
      body: json({ pages }),
    }),

  reorderPages: (id: string, order: number[]) =>
    request<VersionResult>(`/api/v1/documents/${id}/pages/reorder`, {
      method: "POST",
      body: json({ order }),
    }),

  extractPages: (id: string, pages: number[]) =>
    requestBlob(`/api/v1/documents/${id}/pages/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: json({ pages }),
    }),

  watermark: (id: string, text: string) =>
    request<VersionResult>(`/api/v1/documents/${id}/watermark`, {
      method: "POST",
      body: json({ text }),
    }),

  pageNumbers: (id: string, position: string) =>
    request<VersionResult>(`/api/v1/documents/${id}/page-numbers`, {
      method: "POST",
      body: json({ position }),
    }),

  compress: (id: string, preset: string) =>
    request<VersionResult & {
      original_bytes: number;
      compressed_bytes: number;
      reduction_percent: number;
    }>(`/api/v1/documents/${id}/compress`, { method: "POST", body: json({ preset }) }),

  protect: (
    id: string,
    userPassword: string,
    permissions?: {
      allow_print?: boolean;
      allow_copy?: boolean;
      allow_modify?: boolean;
      allow_annotate?: boolean;
    },
  ) =>
    request<VersionResult>(`/api/v1/documents/${id}/protect`, {
      method: "POST",
      body: json({ user_password: userPassword, ...(permissions ?? {}) }),
    }),

  unlock: (id: string, password: string) =>
    request<VersionResult>(`/api/v1/documents/${id}/unlock`, {
      method: "POST",
      body: json({ password }),
    }),

  cropPages: (
    id: string,
    pages: number[],
    box: { left: number; bottom: number; right: number; top: number },
  ) =>
    request<VersionResult>(`/api/v1/documents/${id}/pages/crop`, {
      method: "POST",
      body: json({ pages, ...box }),
    }),

  // Each range becomes its own version of this document, downloadable
  // individually — the source stays intact.
  splitDocument: (id: string, ranges: [number, number][]) =>
    request<{
      document_id: string;
      parts: { version: number; label: string; pages: string; size_bytes: number }[];
    }>(`/api/v1/documents/${id}/pages/split`, {
      method: "POST",
      body: json({ ranges }),
    }),

  headerFooter: (
    id: string,
    options: { header?: string; footer?: string; align?: string },
  ) =>
    request<VersionResult>(`/api/v1/documents/${id}/header-footer`, {
      method: "POST",
      body: json(options),
    }),

  // --- links, attachments, Bates, scan cleanup
  links: (id: string) =>
    request<{ count: number; links: {
      page: number; index: number; kind: string; target: string | null;
      rect: Rect;
    }[] }>(`/api/v1/documents/${id}/links`),

  addLink: (id: string, page: number, rect: Rect, url: string) =>
    request<VersionResult>(`/api/v1/documents/${id}/links`, {
      method: "POST", body: json({ page, rect, url }),
    }),

  removeLinks: (id: string, page?: number, index?: number) =>
    request<VersionResult>(`/api/v1/documents/${id}/links/remove`, {
      method: "POST", body: json({ page, index }),
    }),

  attachments: (id: string) =>
    request<{ count: number; attachments: {
      name: string; size_bytes: number; risky: boolean;
    }[] }>(`/api/v1/documents/${id}/attachments`),

  attachFile: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<VersionResult>(`/api/v1/documents/${id}/attachments`, {
      method: "POST", body: form,
    });
  },

  attachmentUrl: (id: string, name: string) =>
    `/api/v1/documents/${id}/attachments/${encodeURIComponent(name)}`,

  removeAttachment: (id: string, name?: string) =>
    request<VersionResult>(
      `/api/v1/documents/${id}/attachments/remove` +
      (name ? `?name=${encodeURIComponent(name)}` : ""),
      { method: "POST" }),

  bates: (id: string, options: {
    prefix?: string; suffix?: string; start_at?: number;
    digits?: number; position?: string;
  }) =>
    request<VersionResult>(`/api/v1/documents/${id}/bates`, {
      method: "POST", body: json(options),
    }),

  enhanceScan: (id: string, options: {
    deskew?: boolean; despeckle?: boolean; contrast?: boolean;
    binarise?: boolean; confirm_rasterise?: boolean;
  }) =>
    request<VersionResult>(`/api/v1/documents/${id}/enhance`, {
      method: "POST", body: json(options),
    }),

  // --- assembling from other documents
  insertPages: (id: string, sourceDocumentId: string, after: number,
                pages?: number[]) =>
    request<VersionResult>(`/api/v1/documents/${id}/pages/insert`, {
      method: "POST",
      body: json({ source_document_id: sourceDocumentId, after, pages }),
    }),

  replacePagesFrom: (id: string, sourceDocumentId: string,
                     targets: number[], pages?: number[]) =>
    request<VersionResult>(`/api/v1/documents/${id}/pages/replace`, {
      method: "POST",
      body: json({ source_document_id: sourceDocumentId, targets, pages }),
    }),

  addBlankPages: (id: string, after: number, count: number) =>
    request<VersionResult>(`/api/v1/documents/${id}/pages/blank`, {
      method: "POST",
      body: json({ after, count }),
    }),

  // --- document structure
  /** Writes the stored annotations into the file as a new version. */
  flattenAnnotations: (id: string) =>
    request<VersionResult>(`/api/v1/documents/${id}/annotations/flatten`,
      { method: "POST" }),

  properties: (id: string) =>
    request<{
      page_count: number;
      encrypted: boolean;
      metadata: Record<string, string>;
      pages: { page: number; width: number; height: number; rotation: number }[];
      hidden_data: Record<string, number | boolean>;
    }>(`/api/v1/documents/${id}/properties`),

  setProperties: (id: string, values: Record<string, string>) =>
    request<VersionResult>(`/api/v1/documents/${id}/properties`, {
      method: "POST",
      body: json(values),
    }),

  sanitise: (id: string, options: Record<string, boolean> = {}) =>
    request<VersionResult>(`/api/v1/documents/${id}/sanitise`, {
      method: "POST",
      body: json(options),
    }),

  outline: (id: string) =>
    request<{ entries: { title: string; page: number; depth: number }[] }>(
      `/api/v1/documents/${id}/outline`),

  setOutline: (id: string,
               entries: { title: string; page: number; depth: number }[]) =>
    request<VersionResult>(`/api/v1/documents/${id}/outline`, {
      method: "POST",
      body: json({ entries }),
    }),

  // --- text editing
  findText: (id: string, q: string, page?: number) =>
    request<{
      count: number;
      occurrences: {
        page: number; text: string;
        x: number; y: number; width: number; height: number;
      }[];
    }>(`/api/v1/documents/${id}/text/find?q=${encodeURIComponent(q)}` +
       (page ? `&page=${page}` : "")),

  editText: (
    id: string,
    edits: {
      page: number;
      find: string;
      replace?: string;
      occurrence?: number;
      style?: {
        font?: string; size?: number | null; colour?: string;
        bold?: boolean; italic?: boolean;
      };
    }[],
  ) =>
    request<VersionResult>(`/api/v1/documents/${id}/text/edit`, {
      method: "POST",
      body: json({ edits }),
    }),

  addText: (
    id: string,
    page: number,
    x: number,
    y: number,
    text: string,
    style?: { font?: string; size?: number; colour?: string;
              bold?: boolean; italic?: boolean },
  ) =>
    request<VersionResult>(`/api/v1/documents/${id}/text/add`, {
      method: "POST",
      body: json({ page, x, y, text, style: style ?? {} }),
    }),

  /** Combine several documents into a brand-new one; sources are untouched. */
  mergeDocuments: (documentIds: string[], filename: string) =>
    request<{ document: DocumentSummary; jobs: string[] }>(
      "/api/v1/documents/merge",
      { method: "POST", body: json({ document_ids: documentIds, filename }) },
    ),

  snapshot: (id: string, page: number, rect: Rect, scale = 2) =>
    requestBlob(`/api/v1/documents/${id}/snapshot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: json({
        page,
        left: rect.x,
        top: rect.y,
        right: rect.x + rect.width,
        bottom: rect.y + rect.height,
        scale,
      }),
    }),

  /**
   * Rendered page image as a blob.
   *
   * Fetched rather than set as an <img src>: the render endpoint is
   * authenticated, and a plain img tag cannot carry the bearer token, so a
   * direct URL would simply 401.
   */
  renderPage: (id: string, page: number, scale = 0.28) =>
    requestBlob(`/api/v1/documents/${id}/render/${page}?scale=${scale}`),

  // --- ai
  aiStatus: (id: string) =>
    request<{ available: boolean; provider: string; reason: string | null }>(
      `/api/v1/documents/${id}/ai/status`,
    ),

  aiSelection: (
    id: string,
    text: string,
    mode: "explain" | "summarize" | "translate" | "rewrite" | "shorten",
    targetLanguage?: string,
  ) =>
    request<AiSelectionResult>(`/api/v1/documents/${id}/ai/selection`, {
      method: "POST",
      body: json({ text, mode, target_language: targetLanguage }),
    }),

  aiAsk: (id: string, question: string) =>
    request<AiAnswer>(`/api/v1/documents/${id}/ai/ask`, {
      method: "POST",
      body: json({ question }),
    }),

  // --- analysis
  summarize: (id: string, mode: string, refresh = false) =>
    request<SummaryResult>(`/api/v1/documents/${id}/ai/summarize`, {
      method: "POST",
      body: json({ mode, refresh }),
    }),

  analyzeDocument: (id: string) =>
    request<AnalysisResult>(`/api/v1/documents/${id}/ai/analyze`, { method: "POST" }),

  insights: (id: string) =>
    request<InsightsResult>(`/api/v1/documents/${id}/ai/insights`),

  quotes: (id: string, refresh = false) =>
    request<QuotesResult>(
      `/api/v1/documents/${id}/ai/quotes?refresh=${refresh}`, { method: "POST" },
    ),

  summaryExportUrl: (id: string, mode: string, format: "txt" | "csv") =>
    `/api/v1/documents/${id}/ai/summary/export?mode=${mode}&format=${format}`,

  downloadSummary: (id: string, mode: string, format: "txt" | "csv") =>
    requestBlob(`/api/v1/documents/${id}/ai/summary/export?mode=${mode}&format=${format}`),

  // --- form builder + versions
  formBuilderDescribe: (id: string) =>
    request<{ has_form: boolean; fillable: boolean; note: string;
      fields: { name: string; kind: string; page: number | null }[] }>(
      `/api/v1/documents/${id}/form/builder`,
    ),

  buildForm: (id: string, fields: unknown[]) =>
    request<{ version: number; note: string | null }>(
      `/api/v1/documents/${id}/form/builder`,
      { method: "POST", body: json({ fields }) },
    ),

  versions: (id: string) =>
    request<{
      current: number | null;
      note: string;
      versions: { version: number; label: string; size_bytes: number;
        content_hash: string; created_at: string }[];
    }>(`/api/v1/documents/${id}/versions`),

  restoreVersion: (id: string, version: number) =>
    request<{ version: number; restored_from: number; note: string }>(
      `/api/v1/documents/${id}/versions/restore`,
      { method: "POST", body: json({ version }) },
    ),

  // --- conversion
  conversionCapabilities: () =>
    request<{ from_pdf: ConversionTarget[]; to_pdf: string[]; note: string }>(
      "/api/v1/convert/capabilities",
    ),

  /** Returns the file plus the fidelity the server reported for it. */
  async convert(id: string, target: string) {
    const response = await fetch(`/api/v1/documents/${id}/convert`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: json({ target }),
    });
    await handle(response);

    const disposition = response.headers.get("content-disposition") ?? "";
    const match = /filename="([^"]+)"/.exec(disposition);
    return {
      blob: await response.blob(),
      filename: match?.[1] ?? `converted.${target}`,
      fidelity: response.headers.get("x-conversion-fidelity") ?? "",
      note: response.headers.get("x-conversion-note") ?? "",
      warnings: response.headers.get("x-conversion-warnings") ?? "",
    };
  },

  createPdfFrom(workspaceId: string, file: File) {
    const form = new FormData();
    form.append("workspace_id", workspaceId);
    form.append("file", file);
    return request<{ document_id: string; filename: string; converted_from: string }>(
      "/api/v1/convert/to-pdf",
      { method: "POST", body: form },
    );
  },

  // --- compare
  compare: (id: string, againstId: string, interpret = false) =>
    request<ComparisonResult>(`/api/v1/documents/${id}/compare`, {
      method: "POST",
      body: json({ against_document_id: againstId, interpret }),
    }),

  // --- signing
  signatures: () => request<SignatureAsset[]>("/api/v1/signatures"),

  createTypedSignature(name: string, label = "Signature") {
    const form = new FormData();
    form.append("kind", "typed");
    form.append("typed_name", name);
    form.append("label", label);
    return request<SignatureAsset>("/api/v1/signatures", { method: "POST", body: form });
  },

  createDrawnSignature(blob: Blob, label = "Signature") {
    const form = new FormData();
    form.append("kind", "drawn");
    form.append("label", label);
    form.append("file", blob, "signature.png");
    return request<SignatureAsset>("/api/v1/signatures", { method: "POST", body: form });
  },

  signatureImage: (id: string) => requestBlob(`/api/v1/signatures/${id}/image`),

  deleteSignature: (id: string) =>
    request<void>(`/api/v1/signatures/${id}`, { method: "DELETE" }),

  selfSign: (id: string, placements: unknown[]) =>
    request<{ version: number; placements: number; legal_notice: string }>(
      `/api/v1/documents/${id}/sign/self`,
      { method: "POST", body: json({ placements }) },
    ),

  createSignatureRequest: (id: string, body: unknown) =>
    request<SignatureRequest>(`/api/v1/documents/${id}/signature-requests`, {
      method: "POST",
      body: json(body),
    }),

  /** includeLinks asks for signing URLs, which the sender needs to re-copy. */
  signatureRequests: (id: string, includeLinks = false) =>
    request<SignatureRequest[]>(
      `/api/v1/documents/${id}/signature-requests` +
        (includeLinks ? "?include_links=true" : ""),
    ),

  sendSignatureRequest: (requestId: string) =>
    request<SignatureRequest>(`/api/v1/signature-requests/${requestId}/send`, {
      method: "POST",
    }),

  cancelSignatureRequest: (requestId: string) =>
    request<SignatureRequest>(`/api/v1/signature-requests/${requestId}/cancel`, {
      method: "POST",
    }),

  finaliseSignatureRequest: (requestId: string) =>
    request<{ signed_version: number }>(
      `/api/v1/signature-requests/${requestId}/finalise`, { method: "POST" },
    ),

  signatureAudit: (requestId: string) =>
    request<{ document_hash: string; state: string; events: AuditEvent[];
      legal_notice: string }>(`/api/v1/signature-requests/${requestId}/audit`),

  // --- recipient side (token is the credential; no account needed)
  openSigning: (token: string) => request<SigningView>(`/api/v1/sign/${token}`),

  signingDocument: (token: string) =>
    requestBuffer(`/api/v1/sign/${token}/document`),

  submitSigning: (token: string, values: Record<string, string>) =>
    request<{ state: string; remaining: number; completed: boolean }>(
      `/api/v1/sign/${token}/submit`, { method: "POST", body: json({ values }) },
    ),

  declineSigning: (token: string, reason: string) =>
    request<{ state: string }>(`/api/v1/sign/${token}/decline`, {
      method: "POST",
      body: json({ reason }),
    }),

  // --- ocr + translation
  ocrAssess: (id: string) =>
    request<OcrAssessment>(`/api/v1/documents/${id}/ocr/assess`),

  runOcr: (id: string, language = "eng") =>
    request<{ engine: string; mean_confidence: number | null;
      pages: { page: number; text: string; confidence: number | null }[] }>(
      `/api/v1/documents/${id}/ocr`, { method: "POST", body: json({ language }) },
    ),

  translate: (id: string, targetLanguage: string, pages?: number[]) =>
    request<TranslationResult>(`/api/v1/documents/${id}/ai/translate`, {
      method: "POST",
      body: json({ target_language: targetLanguage, pages, save_as_version: true }),
    }),

  // --- jobs
  jobs: (workspaceId: string) =>
    request<{ items: Job[] }>(`/api/v1/jobs?workspace_id=${encodeURIComponent(workspaceId)}`),
};
