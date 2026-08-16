/**
 * Recipient signing page, reached at /sign/:token.
 *
 * The recipient has no account. The token in the URL is their entire
 * credential and grants access to exactly one request, so this view never
 * touches the authenticated API surface and never asks them to sign in.
 */
import { useCallback, useEffect, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";

import { api, type SigningView as View } from "../api";
import { PdfPage, type Overlay } from "../components/PdfPage";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export function SigningView({ token }: { token: string }) {
  const [view, setView] = useState<View | null>(null);
  const [pages, setPages] = useState<PDFPageProxy[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<null | { completed: boolean; remaining: number }>(null);
  const [declined, setDeclined] = useState(false);
  const scale = 1.15;

  const load = useCallback(async () => {
    try {
      const opened = await api.openSigning(token);
      setView(opened);

      const buffer = await api.signingDocument(token);
      const pdf: PDFDocumentProxy = await pdfjs.getDocument({ data: buffer }).promise;
      setPages(await Promise.all(
        Array.from({ length: pdf.numPages }, (_, i) => pdf.getPage(i + 1)),
      ));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  async function submit() {
    if (!view) return;
    const missing = view.fields.filter(
      (f) => f.required && !(values[f.id] ?? "").trim(),
    );
    if (missing.length) {
      setError(`Please complete: ${missing.map((f) => f.label ?? f.type).join(", ")}`);
      return;
    }

    setBusy(true);
    setError("");
    try {
      setDone(await api.submitSigning(token, values));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function decline() {
    const reason = window.prompt("Reason for declining (optional)") ?? "";
    setBusy(true);
    try {
      await api.declineSigning(token, reason);
      setDeclined(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !view) {
    return (
      <div className="auth-shell">
        <div className="card auth-card">
          <h1>Signing link</h1>
          <div className="error">{error}</div>
        </div>
      </div>
    );
  }

  if (declined) {
    return (
      <div className="auth-shell">
        <div className="card auth-card">
          <h1>Declined</h1>
          <p className="muted">You declined to sign this document. The sender
            has been notified in the audit trail.</p>
        </div>
      </div>
    );
  }

  if (done) {
    return (
      <div className="auth-shell">
        <div className="card auth-card">
          <h1>Thank you</h1>
          <p className="muted">
            {done.completed
              ? "All parties have now signed."
              : `Your signature is recorded. Waiting on ${done.remaining} other signer(s).`}
          </p>
          {view && <p className="small muted">{view.legal_notice}</p>}
        </div>
      </div>
    );
  }

  if (!view) {
    return (
      <div className="auth-shell">
        <div className="row"><span className="spinner" /> Loading…</div>
      </div>
    );
  }

  // Draw each assigned field on its page so the signer can see where their
  // marks will land.
  const overlaysFor = (page: number): Overlay[] =>
    view.fields
      .filter((f) => f.page === page)
      .map((f) => ({
        key: f.id,
        rect: { x: f.x, y: f.y, width: f.width, height: f.height },
        className: "sign-slot",
        title: f.label ?? f.type,
      }));

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><span className="mark">◈</span><span>DocIntel</span></div>
        <span className="doc-title">{view.title}</span>
        <div style={{ flex: 1 }} />
        <span className="small muted">{view.recipient.email}</span>
      </header>

      <div className="body">
        <main className="canvas-area">
          {pages.map((page, index) => (
            <PdfPage
              key={index}
              page={page}
              pageNumber={index + 1}
              scale={scale}
              overlays={overlaysFor(index + 1)}
              snapshotMode={false}
            />
          ))}
        </main>

        <aside className="panel">
          <div className="panel-body">
            {view.message && (
              <div className="notice" style={{ marginBottom: "0.9rem" }}>
                {view.message}
              </div>
            )}

            {!view.your_turn && (
              <div className="error" style={{ marginBottom: "0.9rem" }}>
                It is not your turn yet — an earlier signer must complete first.
              </div>
            )}

            <div className="meta">Your fields</div>
            {view.fields.map((field) => (
              <label key={field.id} className="field">
                <span>
                  {field.label ?? field.type}
                  {field.required && <em style={{ color: "var(--bad)" }}> *</em>}
                </span>
                {field.type === "checkbox" ? (
                  <input
                    type="checkbox"
                    checked={(values[field.id] ?? "") === "yes"}
                    onChange={(e) =>
                      setValues({ ...values, [field.id]: e.target.checked ? "yes" : "" })}
                  />
                ) : (
                  <input
                    className="input"
                    placeholder={
                      field.type === "signature" ? "Type your full name"
                        : field.type === "date" ? "YYYY-MM-DD" : ""
                    }
                    value={values[field.id] ?? ""}
                    onChange={(e) => setValues({ ...values, [field.id]: e.target.value })}
                  />
                )}
              </label>
            ))}

            {error && <div className="error" style={{ marginBottom: 10 }}>{error}</div>}

            <button className="btn primary" style={{ width: "100%" }}
              disabled={busy || !view.your_turn} onClick={submit}>
              {busy ? "Submitting…" : "Sign document"}
            </button>
            <button className="btn ghost" style={{ width: "100%", marginTop: 6 }}
              disabled={busy} onClick={decline}>
              Decline
            </button>

            <p className="small muted" style={{ marginTop: 14, lineHeight: 1.5 }}>
              {view.legal_notice}
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
