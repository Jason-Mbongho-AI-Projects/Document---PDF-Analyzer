/**
 * Signing panel: manage saved signatures, Fill & Sign, and send/track
 * signature requests.
 *
 * The legal notice from the server is shown verbatim wherever a signature is
 * applied. It says this is a visible signature with an audit trail, not a
 * cryptographic one — that distinction should reach the person signing, not
 * sit in an API response nobody reads.
 */
import { useEffect, useRef, useState } from "react";
import {
  api, type SignatureAsset, type SignatureRequest,
} from "../api";

type Mode = "signatures" | "self" | "request";

export function SignPanel({
  documentId, currentPage, notify, onChanged,
}: {
  documentId: string;
  currentPage: number;
  notify: (message: string, tone?: "ok" | "error") => void;
  onChanged: (message: string) => void;
}) {
  const [mode, setMode] = useState<Mode>("signatures");
  const [assets, setAssets] = useState<SignatureAsset[]>([]);
  const [requests, setRequests] = useState<SignatureRequest[]>([]);

  const load = async () => {
    try {
      const [a, r] = await Promise.all([
        api.signatures(),
        // Ask for signing links so a sender can re-copy them after a reload.
        api.signatureRequests(documentId, true),
      ]);
      setAssets(a);
      setRequests(r);
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  useEffect(() => { load(); }, [documentId]);

  return (
    <>
      <div className="row" style={{ marginBottom: "0.8rem" }}>
        {(["signatures", "self", "request"] as Mode[]).map((m) => (
          <button key={m} className={`btn sm ${mode === m ? "primary" : "ghost"}`}
            onClick={() => setMode(m)}>
            {m === "signatures" ? "My signatures" : m === "self" ? "Fill & Sign" : "Request"}
          </button>
        ))}
      </div>

      {mode === "signatures" && (
        <SignatureLibrary assets={assets} onChange={load} notify={notify} />
      )}

      {mode === "self" && (
        <FillAndSign
          documentId={documentId}
          currentPage={currentPage}
          assets={assets}
          notify={notify}
          onSigned={onChanged}
        />
      )}

      {mode === "request" && (
        <RequestManager
          documentId={documentId}
          currentPage={currentPage}
          requests={requests}
          onChange={load}
          notify={notify}
          onFinalised={onChanged}
        />
      )}
    </>
  );
}

// ------------------------------------------------------- saved signatures

function SignatureLibrary({
  assets, onChange, notify,
}: {
  assets: SignatureAsset[];
  onChange: () => void;
  notify: (m: string, t?: "ok" | "error") => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [previews, setPreviews] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    const urls: string[] = [];

    (async () => {
      const next: Record<string, string> = {};
      for (const asset of assets) {
        try {
          const blob = await api.signatureImage(asset.id);
          const url = URL.createObjectURL(blob);
          urls.push(url);
          next[asset.id] = url;
        } catch {
          /* a missing preview is not fatal */
        }
      }
      if (!cancelled) setPreviews(next);
    })();

    return () => {
      cancelled = true;
      urls.forEach(URL.revokeObjectURL);
    };
  }, [assets]);

  async function createTyped() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await api.createTypedSignature(name.trim());
      setName("");
      onChange();
      notify("Signature saved", "ok");
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="notice" style={{ marginBottom: "0.8rem" }}>
        Saved signatures are private to your account and are never shared with
        others in the workspace.
      </div>

      <label className="field">
        <span>Type a signature</span>
        <input className="input" value={name} placeholder="Your name"
          onChange={(e) => setName(e.target.value)} />
      </label>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <button className="btn primary sm" disabled={busy || !name.trim()}
          onClick={createTyped}>Save typed</button>
        <DrawSignature onSaved={onChange} notify={notify} />
      </div>

      {assets.length === 0 ? (
        <div className="empty"><h4>No saved signatures</h4></div>
      ) : assets.map((asset) => (
        <div key={asset.id} className="item" style={{ cursor: "default" }}>
          <div className="spread">
            <strong className="small">{asset.label}</strong>
            <span className="badge info">{asset.kind}</span>
          </div>
          {previews[asset.id] && (
            <img src={previews[asset.id]} alt={asset.label}
              style={{ maxWidth: "100%", maxHeight: 60, marginTop: 6 }} />
          )}
          <button className="btn sm ghost" style={{ color: "var(--bad)", marginTop: 6 }}
            onClick={async () => {
              await api.deleteSignature(asset.id);
              onChange();
            }}>Delete</button>
        </div>
      ))}
    </>
  );
}

function DrawSignature({
  onSaved, notify,
}: {
  onSaved: () => void;
  notify: (m: string, t?: "ok" | "error") => void;
}) {
  const [open, setOpen] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const dirty = useRef(false);

  function start(event: React.PointerEvent) {
    const canvas = canvasRef.current!;
    canvas.setPointerCapture(event.pointerId);
    const context = canvas.getContext("2d")!;
    const box = canvas.getBoundingClientRect();
    context.beginPath();
    context.moveTo(event.clientX - box.left, event.clientY - box.top);
    drawing.current = true;
  }

  function move(event: React.PointerEvent) {
    if (!drawing.current) return;
    const canvas = canvasRef.current!;
    const context = canvas.getContext("2d")!;
    const box = canvas.getBoundingClientRect();
    context.lineWidth = 2.4;
    context.lineCap = "round";
    context.strokeStyle = "#0f172a";
    context.lineTo(event.clientX - box.left, event.clientY - box.top);
    context.stroke();
    dirty.current = true;
  }

  async function save() {
    if (!dirty.current) {
      notify("Draw a signature first", "error");
      return;
    }
    canvasRef.current!.toBlob(async (blob) => {
      if (!blob) return;
      try {
        await api.createDrawnSignature(blob);
        setOpen(false);
        dirty.current = false;
        onSaved();
        notify("Signature saved", "ok");
      } catch (e) {
        notify((e as Error).message, "error");
      }
    }, "image/png");
  }

  if (!open) {
    return <button className="btn sm" onClick={() => setOpen(true)}>Draw…</button>;
  }

  return (
    <div className="modal-backdrop" onClick={() => setOpen(false)}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Draw your signature</h3>
        <canvas
          ref={canvasRef}
          width={420}
          height={140}
          style={{
            width: "100%", border: "1px dashed var(--line-strong)",
            borderRadius: "var(--r-sm)", background: "#fff",
            touchAction: "none", cursor: "crosshair",
          }}
          onPointerDown={start}
          onPointerMove={move}
          onPointerUp={() => { drawing.current = false; }}
          onPointerLeave={() => { drawing.current = false; }}
        />
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary" onClick={save}>Save</button>
          <button className="btn" onClick={() => {
            const canvas = canvasRef.current!;
            canvas.getContext("2d")!.clearRect(0, 0, canvas.width, canvas.height);
            dirty.current = false;
          }}>Clear</button>
          <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------ fill & sign

function FillAndSign({
  documentId, currentPage, assets, notify, onSigned,
}: {
  documentId: string;
  currentPage: number;
  assets: SignatureAsset[];
  notify: (m: string, t?: "ok" | "error") => void;
  onSigned: (m: string) => void;
}) {
  const [kind, setKind] = useState("signature");
  const [text, setText] = useState("");
  const [assetId, setAssetId] = useState("");
  const [busy, setBusy] = useState(false);

  async function place() {
    setBusy(true);
    try {
      const result = await api.selfSign(documentId, [{
        page: currentPage,
        // Dropped near the foot of the page; the user can re-run on another
        // page. Drag-to-place is a viewer feature that is not built yet.
        x: 72, y: 640, width: kind === "signature" ? 180 : 150,
        height: kind === "signature" ? 48 : 22,
        kind,
        text: kind === "date" ? new Date().toISOString().slice(0, 10) : text,
        asset_id: kind === "signature" ? assetId || undefined : undefined,
      }]);
      onSigned(`Signed — version ${result.version}`);
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="notice" style={{ marginBottom: "0.8rem" }}>
        Applies a mark to <strong>page {currentPage}</strong> and saves a new
        version. The original stays available.
      </div>

      <label className="field">
        <span>What to place</span>
        <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="signature">Signature</option>
          <option value="initial">Initials</option>
          <option value="text">Text</option>
          <option value="date">Today's date</option>
          <option value="check">Checkmark</option>
          <option value="cross">Cross</option>
          <option value="dot">Dot</option>
        </select>
      </label>

      {kind === "signature" && (
        <label className="field">
          <span>Use signature</span>
          <select className="input" value={assetId}
            onChange={(e) => setAssetId(e.target.value)}>
            <option value="">Typed text instead…</option>
            {assets.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
          </select>
        </label>
      )}

      {(kind === "text" || kind === "initial" ||
        (kind === "signature" && !assetId)) && (
        <label className="field">
          <span>Text</span>
          <input className="input" value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      )}

      <button className="btn primary" disabled={busy} onClick={place}>
        {busy ? "Applying…" : `Place on page ${currentPage}`}
      </button>
    </>
  );
}

// ------------------------------------------------------- request workflow

function RequestManager({
  documentId, currentPage, requests, onChange, notify, onFinalised,
}: {
  documentId: string;
  currentPage: number;
  requests: SignatureRequest[];
  onChange: () => void;
  notify: (m: string, t?: "ok" | "error") => void;
  onFinalised: (m: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [emails, setEmails] = useState("");
  const [sequential, setSequential] = useState(false);
  const [busy, setBusy] = useState(false);
  const [audit, setAudit] = useState<string | null>(null);

  async function create() {
    const list = emails.split(/[,\n]/).map((e) => e.trim()).filter(Boolean);
    if (!list.length) {
      notify("Add at least one recipient email", "error");
      return;
    }

    setBusy(true);
    try {
      const created = await api.createSignatureRequest(documentId, {
        title: title.trim() || "Signature request",
        sequential,
        recipients: list.map((email, index) => ({ email, order: index + 1 })),
        // One signature field per recipient on the current page. Placing
        // fields by dragging on the canvas is not built yet.
        fields: list.map((email, index) => ({
          type: "signature",
          page: currentPage,
          x: 72,
          y: 620 - index * 70,
          width: 180,
          height: 48,
          recipient_email: email,
          label: `Signature — ${email}`,
        })),
      });

      await api.sendSignatureRequest(created.id);
      setTitle("");
      setEmails("");
      onChange();
      notify("Request sent", "ok");
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label className="field">
        <span>Title</span>
        <input className="input" value={title} placeholder="Mutual NDA"
          onChange={(e) => setTitle(e.target.value)} />
      </label>

      <label className="field">
        <span>Recipients (one per line)</span>
        <textarea className="input" rows={3} value={emails}
          placeholder={"alice@example.com\nbob@example.com"}
          onChange={(e) => setEmails(e.target.value)} />
      </label>

      <label className="row small muted" style={{ cursor: "pointer", marginBottom: 10 }}>
        <input type="checkbox" checked={sequential}
          onChange={(e) => setSequential(e.target.checked)} />
        Sign in order
      </label>

      <button className="btn primary" disabled={busy} onClick={create}>
        {busy ? "Sending…" : `Send request (fields on page ${currentPage})`}
      </button>

      <div className="meta" style={{ marginTop: 14 }}>Requests</div>
      {requests.length === 0 && (
        <div className="empty"><h4>No requests yet</h4></div>
      )}

      {requests.map((request) => (
        <div key={request.id} className="item" style={{ cursor: "default" }}>
          <div className="spread">
            <strong className="small">{request.title}</strong>
            <span className={`badge ${
              request.state === "completed" ? "ok"
                : ["declined", "cancelled", "expired"].includes(request.state) ? "bad"
                  : "info"}`}>
              {request.state.replace("_", " ")}
            </span>
          </div>

          {request.recipients.map((recipient) => (
            <div key={recipient.id} className="small muted" style={{ marginTop: 4 }}>
              {recipient.order}. {recipient.email} — {recipient.state}
              {recipient.signing_path && (
                <button className="btn sm ghost" style={{ marginLeft: 6 }}
                  onClick={() => {
                    const url = window.location.origin + recipient.signing_path;
                    navigator.clipboard?.writeText(url);
                    notify("Signing link copied", "ok");
                  }}>Copy link</button>
              )}
            </div>
          ))}

          <div className="row" style={{ marginTop: 8 }}>
            {request.state === "completed" && !request.signed_version && (
              <button className="btn sm primary" onClick={async () => {
                try {
                  const result = await api.finaliseSignatureRequest(request.id);
                  onChange();
                  onFinalised(`Signed document saved as version ${result.signed_version}`);
                } catch (e) {
                  notify((e as Error).message, "error");
                }
              }}>Finalise</button>
            )}
            <button className="btn sm ghost" onClick={async () => {
              const trail = await api.signatureAudit(request.id);
              setAudit(
                trail.events
                  .map((e) => `${e.at.slice(0, 19)}  ${e.event}  ${e.actor ?? ""}`)
                  .join("\n"),
              );
            }}>Audit trail</button>
            {!["completed", "cancelled", "declined"].includes(request.state) && (
              <button className="btn sm ghost" style={{ color: "var(--bad)" }}
                onClick={async () => {
                  await api.cancelSignatureRequest(request.id);
                  onChange();
                }}>Cancel</button>
            )}
          </div>
        </div>
      ))}

      {audit && (
        <div className="modal-backdrop" onClick={() => setAudit(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Audit trail</h3>
            <pre className="small" style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
              {audit}
            </pre>
            <button className="btn" onClick={() => setAudit(null)}>Close</button>
          </div>
        </div>
      )}
    </>
  );
}
