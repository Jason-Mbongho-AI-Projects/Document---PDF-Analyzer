/** App shell: auth gate, workspace selection, library ⇄ workspace routing. */
import { useCallback, useEffect, useState } from "react";
import { api, getToken, onAuthChange, type User, type Workspace } from "./api";
import { Library } from "./views/Library";
import { Workspace as DocumentWorkspace } from "./views/Workspace";
import { SigningView } from "./views/SigningView";

interface Toast {
  id: number;
  message: string;
  tone: "ok" | "error" | "info";
}

/** Recipients arrive at /sign/<token>; the token is their credential. */
function signingToken(): string | null {
  const match = /^\/sign\/([A-Za-z0-9_-]{20,})\/?$/.exec(window.location.pathname);
  return match ? match[1] : null;
}

export default function App() {
  const [token, setTokenState] = useState<string | null>(getToken());
  const [signing] = useState<string | null>(signingToken);
  const [user, setUser] = useState<User | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [active, setActive] = useState<Workspace | null>(null);
  const [openDocument, setOpenDocument] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [booting, setBooting] = useState(true);
  const [openAccess, setOpenAccess] = useState<boolean | null>(null);
  const [forceLogin, setForceLogin] = useState(false);

  useEffect(() => onAuthChange(setTokenState), []);

  // The signing page is public and must render before any auth work.
  if (signing) return <SigningView token={signing} />;

  const notify = useCallback(
    (message: string, tone: "ok" | "error" | "info" = "info") => {
      const id = Date.now() + Math.random();
      setToasts((current) => [...current, { id, message, tone }]);
      setTimeout(() => setToasts((c) => c.filter((t) => t.id !== id)), 6000);
    },
    [],
  );

  // Ask the server whether sign-in is required before rendering anything.
  useEffect(() => {
    api
      .authMode()
      .then((mode) => setOpenAccess(mode.open_access))
      .catch(() => setOpenAccess(false));   // fail closed
  }, []);

  useEffect(() => {
    if (openAccess === null) return;        // still probing

    if (!token && !openAccess) {
      setUser(null);
      setWorkspaces([]);
      setActive(null);
      setOpenDocument(null);
      setBooting(false);
      return;
    }
    (async () => {
      try {
        const [me, spaces] = await Promise.all([api.me(), api.workspaces()]);
        setUser(me);
        setWorkspaces(spaces);
        setActive((current) => current ?? spaces[0] ?? null);
      } catch (e) {
        notify((e as Error).message, "error");
      } finally {
        setBooting(false);
      }
    })();
  }, [token, openAccess, notify]);

  if (openAccess === null) {
    return (
      <div className="auth-shell">
        <div className="row"><span className="spinner" /> Starting…</div>
      </div>
    );
  }

  if (!token && (!openAccess || forceLogin)) {
    return (
      <AuthView
        toasts={toasts}
        onCancel={openAccess ? () => setForceLogin(false) : undefined}
      />
    );
  }

  if (booting) {
    return (
      <div className="auth-shell">
        <div className="row"><span className="spinner" /> Loading…</div>
      </div>
    );
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="mark">◈</span>
          <span>DocIntel</span>
        </div>

        {openDocument ? (
          <span className="doc-title muted small">Document workspace</span>
        ) : (
          <select
            className="input"
            style={{ maxWidth: 220 }}
            value={active?.id ?? ""}
            onChange={(e) =>
              setActive(workspaces.find((w) => w.id === e.target.value) ?? null)}
          >
            {workspaces.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>{workspace.name}</option>
            ))}
          </select>
        )}

        <div style={{ flex: 1 }} />

        {openAccess && !token && (
          <span className="open-pill" title={
            "Authentication is disabled in this environment. " +
            "Anyone who can reach this service has full access."
          }>
            Open access — no sign-in required
          </span>
        )}

        <span className="small muted">{user?.email}</span>
        {token ? (
          <button className="btn sm ghost" onClick={() => api.logout()}>Sign out</button>
        ) : (
          <button className="btn sm ghost" onClick={() => setForceLogin(true)}>Sign in</button>
        )}
      </header>

      {openDocument ? (
        <DocumentWorkspace
          documentId={openDocument}
          onBack={() => setOpenDocument(null)}
          notify={notify}
        />
      ) : active ? (
        <div className="body">
          <Library workspace={active} onOpen={setOpenDocument} notify={notify} />
        </div>
      ) : (
        <div className="body" style={{ display: "grid", placeItems: "center" }}>
          <div className="empty"><h4>No workspace available</h4></div>
        </div>
      )}

      <Toasts toasts={toasts} />
    </div>
  );
}

function AuthView({ toasts, onCancel }: { toasts: Toast[]; onCancel?: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "login") await api.login(email, password);
      else await api.register(email, password);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <form className="card auth-card" onSubmit={submit}>
        <h1>Doc<em>Intel</em></h1>
        <p className="muted small" style={{ marginTop: 0, marginBottom: "1.4rem" }}>
          {mode === "login" ? "Sign in to your workspace." : "Create an account."}
        </p>

        <label className="field">
          <span>Email</span>
          <input className="input" type="email" required autoComplete="email"
            value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>

        <label className="field">
          <span>Password</span>
          <input className="input" type="password" required
            minLength={mode === "register" ? 10 : 1}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password} onChange={(e) => setPassword(e.target.value)} />
          {mode === "register" && (
            <span className="small muted" style={{ textTransform: "none",
              letterSpacing: 0, fontWeight: 400, marginTop: 4 }}>
              At least 10 characters.
            </span>
          )}
        </label>

        {error && <div className="error" style={{ marginBottom: "0.8rem" }}>{error}</div>}

        <button className="btn primary" style={{ width: "100%" }} disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
        </button>

        <button type="button" className="btn ghost" style={{ width: "100%", marginTop: 8 }}
          onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
          {mode === "login" ? "Create an account instead" : "I already have an account"}
        </button>

        {onCancel && (
          <button type="button" className="btn ghost"
            style={{ width: "100%", marginTop: 4 }} onClick={onCancel}>
            Continue without signing in
          </button>
        )}
      </form>
      <Toasts toasts={toasts} />
    </div>
  );
}

function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="toasts">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.tone}`}>{toast.message}</div>
      ))}
    </div>
  );
}
