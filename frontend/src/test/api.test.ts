/**
 * API client tests.
 *
 * The client is where auth headers, error translation and session expiry are
 * decided, so these assert on behaviour that would otherwise only surface as
 * a confusing UI bug.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, getToken, setToken } from "../api";

interface MockResponse {
  ok?: boolean;
  status?: number;
  headers?: Record<string, string>;
  jsonBody?: unknown;
}

/** A fetch stub whose call arguments stay typed, so assertions are checked. */
function mockFetch(response: MockResponse) {
  const fetchMock = vi.fn(
    async (_url: string, _init: RequestInit = {}) =>
      ({
        ok: response.ok ?? true,
        status: response.status ?? 200,
        headers: new Headers(response.headers ?? {}),
        json: async () => response.jsonBody ?? {},
        blob: async () => new Blob(["x"]),
        arrayBuffer: async () => new ArrayBuffer(8),
      }) as unknown as Response,
  );
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function callArgs(fetchMock: ReturnType<typeof mockFetch>, index = 0) {
  const call = fetchMock.mock.calls[index];
  if (!call) throw new Error("fetch was not called");
  return { url: call[0], init: call[1] ?? {} };
}

function headersOf(fetchMock: ReturnType<typeof mockFetch>, index = 0) {
  return (callArgs(fetchMock, index).init.headers ?? {}) as Record<string, string>;
}

beforeEach(() => setToken(null));

describe("authentication headers", () => {
  it("omits the Authorization header when signed out", async () => {
    const fetchMock = mockFetch({ jsonBody: { mode: "open" } });
    await api.authMode();

    expect(headersOf(fetchMock).Authorization).toBeUndefined();
  });

  it("attaches a bearer token when signed in", async () => {
    setToken("abc123");
    const fetchMock = mockFetch({ jsonBody: [] });
    await api.workspaces();

    expect(headersOf(fetchMock).Authorization).toBe("Bearer abc123");
  });

  it("stores the token in sessionStorage, not localStorage", () => {
    setToken("xyz");
    expect(sessionStorage.getItem("docintel.token")).toBe("xyz");
    expect(localStorage.getItem("docintel.token")).toBeNull();
  });

  it("clears the token on sign out", () => {
    setToken("xyz");
    api.logout();
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem("docintel.token")).toBeNull();
  });
});

describe("error handling", () => {
  it("surfaces the server's detail message", async () => {
    mockFetch({ ok: false, status: 400, jsonBody: { detail: "Page 9 is out of range." } });

    await expect(api.workspaces()).rejects.toThrow("Page 9 is out of range.");
  });

  it("flattens validation errors into one message", async () => {
    mockFetch({
      ok: false, status: 422,
      jsonBody: { errors: [{ msg: "email invalid" }, { msg: "password short" }] },
    });

    await expect(api.workspaces()).rejects.toThrow("email invalid; password short");
  });

  it("says the API is not responding when a 5xx carries no JSON detail", async () => {
    // The API always sends a JSON `detail`. A 5xx without one comes from the
    // dev proxy failing to reach the backend, so the message must point at the
    // dead server rather than at whatever feature the user just clicked.
    globalThis.fetch = vi.fn(async () =>
      ({
        ok: false, status: 500, headers: new Headers(),
        json: async () => { throw new Error("not json"); },
      }) as unknown as Response,
    ) as unknown as typeof fetch;

    await expect(api.workspaces()).rejects.toThrow(/API did not respond \(500\)/);
    await expect(api.workspaces()).rejects.toThrow(/uvicorn/);
  });

  it("keeps a real API error message instead of the not-responding hint", async () => {
    mockFetch({
      ok: false, status: 503,
      jsonBody: { detail: "No OCR engine is installed on this server." },
    });

    await expect(api.workspaces())
      .rejects.toThrow("No OCR engine is installed on this server.");
  });

  it("still reports a plain 4xx generically", async () => {
    globalThis.fetch = vi.fn(async () =>
      ({
        ok: false, status: 418, headers: new Headers(),
        json: async () => { throw new Error("not json"); },
      }) as unknown as Response,
    ) as unknown as typeof fetch;

    await expect(api.workspaces()).rejects.toThrow("Request failed (418)");
  });

  it("signs the user out on 401 rather than leaving a dead token", async () => {
    setToken("expired");
    mockFetch({ ok: false, status: 401 });

    await expect(api.me()).rejects.toBeInstanceOf(ApiError);
    expect(getToken()).toBeNull();
  });
});

describe("request shapes", () => {
  it("sends JSON with the right content type", async () => {
    const fetchMock = mockFetch({ jsonBody: {} });
    await api.rotate("doc1", [1, 2], 90);

    const { url, init } = callArgs(fetchMock);
    expect(url).toBe("/api/v1/documents/doc1/pages/rotate");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ pages: [1, 2], degrees: 90 });
    expect(headersOf(fetchMock)["Content-Type"]).toBe("application/json");
  });

  it("does not set a content type for FormData, so the boundary survives", async () => {
    const fetchMock = mockFetch({ jsonBody: {} });
    await api.upload("ws1", new File(["x"], "a.pdf", { type: "application/pdf" }));

    expect(headersOf(fetchMock)["Content-Type"]).toBeUndefined();
  });

  it("url-encodes query parameters", async () => {
    const fetchMock = mockFetch({ jsonBody: { items: [], total: 0 } });
    await api.documents("ws 1", "a&b");

    expect(callArgs(fetchMock).url).toContain("workspace_id=ws%201");
    expect(callArgs(fetchMock).url).toContain("search=a%26b");
  });

  it("reads the fidelity headers back off a conversion", async () => {
    mockFetch({
      jsonBody: {},
      headers: {
        "content-disposition": 'attachment; filename="report.xlsx"',
        "x-conversion-fidelity": "structural",
        "x-conversion-note": "Text and detected structure are preserved.",
        "x-conversion-warnings": "Check the table boundaries.",
      },
    });

    const result = await api.convert("doc1", "xlsx");
    expect(result.filename).toBe("report.xlsx");
    expect(result.fidelity).toBe("structural");
    expect(result.warnings).toContain("table boundaries");
  });
});
