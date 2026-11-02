import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import {
  ArrowRight,
  ArrowUp,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  ExternalLink,
  Eye,
  EyeOff,
  FileText,
  FolderOpen,
  Layers3,
  LoaderCircle,
  LogOut,
  MessageSquare,
  Plus,
  Search,
  ChartNoAxesCombined,
  Sparkles,
  Upload,
  UserRound,
  X,
  AlertCircle,
  Link2,
} from "lucide-react";

const AdminPanel = lazy(() => import("./AdminPanel"));

type User = { id: string; username: string; email: string | null; is_admin: boolean };
type Session = { user: User; csrf_token: string; providers: string[] };
type Provider = "google" | "github";
type Providers = Record<Provider, boolean>;
type Document = {
  id: string;
  original_filename: string;
  status: "queued" | "processing" | "ready" | "failed";
  job_id: string | null;
  created_at: string;
};
type Source = {
  document_id: string;
  filename: string;
  chunk_id: string;
  text: string;
  reranking_score: number;
};
type Answer = { answer: string; sources: Source[] };
type Exchange = {
  id: number;
  question: string;
  result?: Answer;
  error?: string;
};
type Page = "workspace" | "documents" | "account" | "admin";

const OAUTH_ERRORS: Record<string, string> = {
  cancelled: "Sign-in was cancelled. You can try again whenever you’re ready.",
  invalid_state: "That sign-in link expired. Please start again.",
  provider_error: "The provider could not complete sign-in. Please try again.",
  email_unverified:
    "Please verify your email with your provider, then try again.",
  account_exists:
    "An account already uses that email. Sign in using your original method, then connect this provider in Account.",
  identity_in_use:
    "That provider account is already connected to another Folio account.",
  not_configured:
    "This sign-in provider is not configured yet. Please use email and password.",
  session_expired: "Your session expired. Sign in again to continue.",
};

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
const isAborted = (error: unknown) =>
  error instanceof DOMException && error.name === "AbortError";
const errorText = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
const initials = (user: User) =>
  (user.email || user.username || "You").slice(0, 2).toUpperCase();
const formatDate = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Recently added"
    : date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
};

function Brand({ light = false }: { light?: boolean }) {
  return (
    <a
      href="#workspace"
      className={`brand ${light ? "brand-light" : ""}`}
      aria-label="Folio home"
    >
      <span className="brand-mark">
        <Layers3 size={23} strokeWidth={1.8} />
      </span>
      <span>
        folio<span className="brand-period">.</span>
      </span>
    </a>
  );
}
function Spinner({ size = 18 }: { size?: number }) {
  return <LoaderCircle size={size} className="spin" aria-hidden="true" />;
}
function Notice({
  children,
  onClose,
  success = false,
}: {
  children: ReactNode;
  onClose?: () => void;
  success?: boolean;
}) {
  return (
    <div
      className={`notice ${success ? "notice-success" : ""}`}
      role={success ? "status" : "alert"}
    >
      {success ? <Check size={18} /> : <AlertCircle size={18} />}
      <span>{children}</span>
      {onClose && (
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          aria-label="Dismiss message"
        >
          <X size={17} />
        </button>
      )}
    </div>
  );
}
function GitHubIcon({ size = 19 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 19 4.77 5.07 5.07 0 0 0 18.91 1S17.73.65 15 2.48a13.38 13.38 0 0 0-7 0C5.27.65 4.09 1 4.09 1A5.07 5.07 0 0 0 4 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 8 18.13V22" />
    </svg>
  );
}
function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true">
      <path
        fill="currentColor"
        d="M21.6 12.23c0-.71-.06-1.39-.18-2.05H12v3.88h5.38a4.6 4.6 0 0 1-2 3.02v2.51h3.25c1.9-1.75 2.97-4.33 2.97-7.36ZM12 22c2.7 0 4.96-.9 6.62-2.41l-3.25-2.51c-.9.6-2.04.96-3.37.96-2.6 0-4.8-1.75-5.59-4.1H3.05v2.59A10 10 0 0 0 12 22ZM6.41 13.94a6 6 0 0 1 0-3.88V7.47H3.05a10 10 0 0 0 0 9.06l3.36-2.59ZM12 5.96c1.47 0 2.79.51 3.82 1.51l2.87-2.87A9.6 9.6 0 0 0 12 2a10 10 0 0 0-8.95 5.47l3.36 2.59C7.2 7.71 9.4 5.96 12 5.96Z"
      />
    </svg>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const sessionRef = useRef<Session | null>(null);
  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState("");
  const [providers, setProviders] = useState<Providers>({
    google: false,
    github: false,
  });
  const [notice, setNotice] = useState("");
  const [page, setPage] = useState<Page>(() =>
    ["documents", "account", "admin"].includes(location.hash.slice(1))
      ? (location.hash.slice(1) as Page)
      : "workspace",
  );
  const [documents, setDocuments] = useState<Document[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [documentsError, setDocumentsError] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [asking, setAsking] = useState(false);
  const requests = useRef(new Set<AbortController>());
  const generation = useRef(0);

  const clearSession = useCallback((message = "") => {
    generation.current += 1;
    requests.current.forEach((controller) => controller.abort());
    requests.current.clear();
    sessionRef.current = null;
    setSession(null);
    setDocuments([]);
    setExchanges([]);
    setAsking(false);
    setDocumentsError("");
    setDocumentsLoading(false);
    setNotice(message);
  }, []);

  const api = useCallback(
    async <T,>(
      path: string,
      options: RequestInit = {},
      authenticated = true,
    ): Promise<T> => {
      const controller = new AbortController();
      const activeGeneration = generation.current;
      requests.current.add(controller);
      const headers = new Headers(options.headers);
      if (options.body && !(options.body instanceof FormData))
        headers.set("Content-Type", "application/json");
      if (
        authenticated &&
        options.method &&
        options.method !== "GET" &&
        sessionRef.current
      )
        headers.set("X-CSRF-Token", sessionRef.current.csrf_token);
      try {
        const response = await fetch(`/api${path}`, {
          ...options,
          headers,
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (activeGeneration !== generation.current)
          throw new DOMException("Request cancelled", "AbortError");
        if (!response.ok) {
          if (response.status === 401 && authenticated) {
            clearSession("Your session expired. Sign in again to continue.");
            throw new DOMException("Session ended", "AbortError");
          }
          let message = `Request failed (${response.status}). Please try again.`;
          try {
            const data = await response.json();
            if (typeof data.detail === "string") message = data.detail;
            else if (Array.isArray(data.detail))
              message = data.detail
                .map((item: { msg?: string }) => item.msg || "Invalid input")
                .join(". ");
          } catch {
            /* Non-JSON error response. */
          }
          throw new ApiError(message, response.status);
        }
        const data =
          response.status === 204 ? (undefined as T) : await response.json();
        if (activeGeneration !== generation.current)
          throw new DOMException("Request cancelled", "AbortError");
        return data;
      } catch (error) {
        if (error instanceof TypeError)
          throw new Error(
            "Could not reach Folio. Check your connection and try again.",
          );
        throw error;
      } finally {
        requests.current.delete(controller);
      }
    },
    [clearSession],
  );

  const loadSession = useCallback(async () => {
    const next = await api<Session>("/auth/session", {}, false);
    sessionRef.current = next;
    setSession(next);
    return next;
  }, [api]);

  const bootstrap = useCallback(async () => {
    setBooting(true);
    setBootError("");
    const results = await Promise.allSettled([
      api<Providers>("/auth/providers", {}, false),
      loadSession(),
    ]);
    if (results[0].status === "fulfilled") setProviders(results[0].value);
    if (
      results[1].status === "rejected" &&
      !isAborted(results[1].reason) &&
      !(
        results[1].reason instanceof ApiError &&
        results[1].reason.status === 401
      )
    )
      setBootError(errorText(results[1].reason));
    setBooting(false);
  }, [api, loadSession]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const oauthError = params.get("auth_error");
    if (oauthError)
      setNotice(
        OAUTH_ERRORS[oauthError] ||
          "Sign-in could not be completed. Please try again.",
      );
    if (params.has("auth") || params.has("auth_error")) {
      params.delete("auth");
      params.delete("auth_error");
      history.replaceState(
        null,
        "",
        `${location.pathname}${params.size ? `?${params}` : ""}${location.hash}`,
      );
    }
    void bootstrap();
    const onHashChange = () =>
      setPage(
        ["documents", "account", "admin"].includes(location.hash.slice(1))
          ? (location.hash.slice(1) as Page)
          : "workspace",
      );
    window.addEventListener("hashchange", onHashChange);
    return () => {
      window.removeEventListener("hashchange", onHashChange);
      requests.current.forEach((controller) => controller.abort());
    };
  }, [bootstrap]);

  const refreshDocuments = useCallback(
    async (showLoading = false) => {
      if (showLoading) setDocumentsLoading(true);
      try {
        const list = await api<Document[]>("/documents");
        setDocuments(list);
        setDocumentsError("");
      } catch (error) {
        if (!isAborted(error)) setDocumentsError(errorText(error));
      } finally {
        if (sessionRef.current) setDocumentsLoading(false);
      }
    },
    [api],
  );

  useEffect(() => {
    if (session) void refreshDocuments(true);
  }, [session?.user.id, refreshDocuments]);
  useEffect(() => {
    if (session && page === "admin" && !session.user.is_admin) {
      setPage("workspace");
      history.replaceState(null, "", `${location.pathname}${location.search}#workspace`);
    }
  }, [session, page]);

  const pendingDocuments = documents.some(
    (document) =>
      document.status === "queued" || document.status === "processing",
  );
  useEffect(() => {
    if (!session || !pendingDocuments) return;
    const timer = window.setInterval(() => void refreshDocuments(), 3000);
    return () => clearInterval(timer);
  }, [session, pendingDocuments, refreshDocuments]);

  const login = async (email: string, password: string, register: boolean) => {
    if (register)
      await api(
        "/auth/register",
        { method: "POST", body: JSON.stringify({ email, password }) },
        false,
      );
    await api(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ email, password }) },
      false,
    );
    generation.current += 1;
    setDocuments([]);
    setExchanges([]);
    setNotice("");
    await loadSession();
  };

  const ask = async (question: string) => {
    const id = Date.now();
    setAsking(true);
    setExchanges((previous) => [...previous, { id, question }]);
    try {
      const result = await api<Answer>("/ask", {
        method: "POST",
        body: JSON.stringify({ query: question, top_k: 5 }),
      });
      setExchanges((previous) =>
        previous.map((exchange) =>
          exchange.id === id ? { ...exchange, result } : exchange,
        ),
      );
    } catch (error) {
      if (!isAborted(error))
        setExchanges((previous) =>
          previous.map((exchange) =>
            exchange.id === id
              ? { ...exchange, error: errorText(error) }
              : exchange,
          ),
        );
    } finally {
      if (sessionRef.current) setAsking(false);
    }
  };

  if (booting)
    return (
      <div className="boot-screen">
        <Brand />
        <Spinner size={26} />
        <p>Opening your workspace…</p>
      </div>
    );
  if (bootError)
    return (
      <div className="boot-screen">
        <Brand />
        <Notice>{bootError}</Notice>
        <button className="button primary" onClick={() => void bootstrap()}>
          Try again <ArrowRight size={17} />
        </button>
      </div>
    );
  if (!session)
    return (
      <AuthPage
        providers={providers}
        onLogin={login}
        notice={notice}
        dismissNotice={() => setNotice("")}
      />
    );

  const readyCount = documents.filter(
    (document) => document.status === "ready",
  ).length;
  return (
    <div className="app-shell">
      <a
        className="skip-link"
        href="#main"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        Skip to content
      </a>
      <aside className="sidebar">
        <Brand />
        <div className="workspace-label">YOUR WORKSPACE</div>
        <nav aria-label="Main navigation">
          <a
            className={page === "workspace" ? "nav-item active" : "nav-item"}
            href="#workspace"
            aria-current={page === "workspace" ? "page" : undefined}
          >
            <MessageSquare size={19} /> Ask Folio <span className="nav-dot" />
          </a>
          <a
            className={page === "documents" ? "nav-item active" : "nav-item"}
            href="#documents"
            aria-current={page === "documents" ? "page" : undefined}
          >
            <FolderOpen size={19} /> Documents{" "}
            <span className="nav-count">{documents.length}</span>
          </a>
          {session.user.is_admin && (
            <a
              className={page === "admin" ? "nav-item active" : "nav-item"}
              href="#admin"
              aria-current={page === "admin" ? "page" : undefined}
            >
              <ChartNoAxesCombined size={19} /> Admin
            </a>
          )}
        </nav>
        <div className="sidebar-bottom">
          <a
            href="#account"
            aria-label="Account settings"
            aria-current={page === "account" ? "page" : undefined}
            className={`profile-link ${page === "account" ? "selected" : ""}`}
          >
            <span className="avatar">{initials(session.user)}</span>
            <span>
              <strong>
                {session.user.email?.split("@")[0] || session.user.username}
              </strong>
              <small>Personal workspace</small>
            </span>
            <ChevronDown size={15} />
          </a>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <span>/</span>{" "}
            <strong>
              {page === "documents"
                ? "Documents"
                : page === "account"
                  ? "Account"
                  : page === "admin"
                    ? "Admin"
                    : "Ask Folio"}
            </strong>
          </div>
        </header>
        <main
          id="main"
          tabIndex={-1}
          className={`main-content ${page === "workspace" ? "chat-main" : ""}`}
        >
          {notice && <Notice onClose={() => setNotice("")}>{notice}</Notice>}
          {page === "workspace" && (
            <ChatWorkspace
              exchanges={exchanges}
              asking={asking}
              onAsk={ask}
              readyCount={readyCount}
              documentsLoading={documentsLoading}
              documentsError={documentsError}
              onRetry={() => void refreshDocuments(true)}
              onClear={() => setExchanges([])}
            />
          )}
          {page === "documents" && (
            <DocumentLibrary
              documents={documents}
              loading={documentsLoading}
              error={documentsError}
              onRefresh={() => refreshDocuments(true)}
              upload={async (file) => {
                const body = new FormData();
                body.append("file", file);
                const result = await api<{ filename: string; status: string }>(
                  "/documents",
                  { method: "POST", body },
                );
                await refreshDocuments();
                return result.status;
              }}
            />
          )}
          {page === "admin" && session.user.is_admin && (
            <Suspense fallback={<div className="empty-state" role="status"><Spinner size={26} /><p>Opening admin…</p></div>}>
              <AdminPanel api={api} />
            </Suspense>
          )}
          {page === "account" && (
            <Account
              session={session}
              providers={providers}
              onLink={async (provider) => {
                const result = await api<{ authorization_url: string }>(
                  `/auth/oauth/${provider}/link`,
                  { method: "POST" },
                );
                const url = new URL(result.authorization_url);
                const allowed =
                  provider === "google"
                    ? url.hostname === "accounts.google.com"
                    : url.hostname === "github.com";
                if (url.protocol !== "https:" || !allowed)
                  throw new Error(
                    "The provider returned an invalid connection link.",
                  );
                location.assign(url.href);
              }}
              onLogout={async () => {
                await api("/auth/logout", { method: "POST" });
                clearSession();
              }}
            />
          )}
        </main>
      </div>
    </div>
  );
}

function AuthPage({
  providers,
  onLogin,
  notice,
  dismissNotice,
}: {
  providers: Providers;
  onLogin: (
    email: string,
    password: string,
    register: boolean,
  ) => Promise<void>;
  notice: string;
  dismissNotice: () => void;
}) {
  const [register, setRegister] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await onLogin(email.trim(), password, register);
    } catch (error) {
      if (!isAborted(error)) setError(errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="auth-layout">
      <section className="auth-story">
        <Brand light />
        <div className="auth-story-content">
          <span className="eyebrow light">
            <span /> YOUR KNOWLEDGE, CONNECTED
          </span>
          <h1>
            Less searching.
            <br />
            More <span>understanding.</span>
          </h1>
          <p>
            Bring your documents together.
            <br />
            Find the answers that move you forward.
          </p>
          <div className="document-art" aria-hidden="true">
            <div className="art-orbit orbit-one" />
            <div className="art-orbit orbit-two" />
            <div className="art-sheet sheet-back">
              <div />
              <div />
              <div />
            </div>
            <div className="art-sheet sheet-middle">
              <span className="art-pdf">PDF</span>
              <div />
              <div />
              <div />
              <div />
            </div>
            <div className="art-sheet sheet-front">
              <span className="art-file">
                <FileText size={23} />
              </span>
              <div className="art-line long" />
              <div className="art-line" />
              <div className="art-highlight">
                <span />
                <span />
              </div>
              <div className="art-line" />
              <div className="art-line short" />
              <div className="art-answer">
                <Sparkles size={16} />
                <span>A clearer perspective.</span>
                <Check size={15} />
              </div>
            </div>
            <span className="art-spark spark-one">
              <Plus size={20} />
            </span>
            <span className="art-spark spark-two">
              <Plus size={15} />
            </span>
            <div className="art-citation">
              <span>
                <CheckCheck size={16} />
              </span>
              Grounded in your sources
            </div>
          </div>
        </div>
        
      </section>
      <section className="auth-form-side">
        <div className="mobile-brand">
          <Brand />
        </div>
        <div className="auth-form-wrap">
          <h2>
            {register
              ? "Make room for your ideas."
              : "Welcome to your workspace."}
          </h2>
          {notice && <Notice onClose={dismissNotice}>{notice}</Notice>}
          {error && <Notice>{error}</Notice>}
          <div className="social-buttons">
            <button
              className="button social"
              disabled={!providers.google || busy}
              onClick={() => location.assign("/api/auth/oauth/google/start")}
              title={
                !providers.google
                  ? "Google sign-in is not configured"
                  : undefined
              }
            >
              <GoogleIcon /> Google
            </button>
            <button
              className="button social"
              disabled={!providers.github || busy}
              onClick={() => location.assign("/api/auth/oauth/github/start")}
              title={
                !providers.github
                  ? "GitHub sign-in is not configured"
                  : undefined
              }
            >
              <GitHubIcon size={19} /> GitHub
            </button>
          </div>
          {(!providers.google || !providers.github) && (
            <p className="provider-hint">
              {!providers.google && !providers.github
                ? "Social sign-in is not configured yet. Use your email below."
                : `${!providers.google ? "Google" : "GitHub"} sign-in is not configured yet.`}
            </p>
          )}
          <div className="form-divider">
            <span />
            or continue with email
            <span />
          </div>
          <form onSubmit={submit}>
            <label htmlFor="email">
              {register ? "Email address" : "Email or username"}
            </label>
            <input
              id="email"
              type={register ? "email" : "text"}
              inputMode="email"
              autoComplete={register ? "email" : "username"}
              placeholder={register ? "you@example.com" : "you@example.com or username"}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              maxLength={254}
              required
              disabled={busy}
            />
            <label htmlFor="password">Password</label>
            <div className="password-input">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                autoComplete={register ? "new-password" : "current-password"}
                placeholder={
                  register ? "Create a strong password" : "Enter your password"
                }
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                minLength={register ? 12 : undefined}
                maxLength={128}
                required
                disabled={busy}
                aria-describedby={register ? "password-hint" : undefined}
              />
              <button
                type="button"
                className="icon-button"
                onClick={() => setShowPassword(!showPassword)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? <EyeOff size={19} /> : <Eye size={19} />}
              </button>
            </div>
            {register && (
              <p className="field-hint" id="password-hint">
                Use 12–128 characters for your password.
              </p>
            )}
            <button
              type="submit"
              className="button primary auth-submit"
              disabled={busy}
            >
              {busy ? (
                <>
                  <Spinner />
                  {register ? "Creating your workspace…" : "Signing in…"}
                </>
              ) : (
                <>
                  {register ? "Create account" : "Sign in"}
                  <ArrowRight size={18} />
                </>
              )}
            </button>
          </form>
          <p className="auth-switch">
            {register ? "Already have an account?" : "New to Folio?"}{" "}
            <button
              onClick={() => {
                setRegister(!register);
                setError("");
              }}
              disabled={busy}
            >
              {register ? "Sign in" : "Create an account"}{" "}
              <ArrowRight size={14} />
            </button>
          </p>
          
        </div>
      </section>
    </div>
  );
}

function StatusBadge({ status }: { status: Document["status"] }) {
  return (
    <span className={`status-badge ${status}`}>
      {status === "ready" ? (
        <Check size={12} />
      ) : status === "failed" ? (
        <AlertCircle size={12} />
      ) : (
        <Spinner size={12} />
      )}
      {status === "ready"
        ? "Ready"
        : status === "failed"
          ? "Failed"
          : status === "queued"
            ? "Queued"
            : "Processing"}
    </span>
  );
}

function DocumentLibrary({
  documents,
  loading,
  error,
  onRefresh,
  upload,
}: {
  documents: Document[];
  loading: boolean;
  error: string;
  onRefresh: () => Promise<void>;
  upload: (file: File) => Promise<string>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [success, setSuccess] = useState("");
  const [dragging, setDragging] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const chooseFile = async (file?: File) => {
    if (!file || uploading) return;
    setUploadError("");
    setSuccess("");
    if (
      !file.name.toLowerCase().endsWith(".pdf") ||
      (file.type && file.type !== "application/pdf")
    ) {
      setUploadError("Choose a PDF document to upload.");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setUploadError("That PDF is over 10 MB. Choose a smaller file.");
      return;
    }
    if (!file.size) {
      setUploadError("That file is empty. Choose a PDF with content.");
      return;
    }
    setUploading(true);
    try {
      const status = await upload(file);
      setSuccess(
        status === "ready"
          ? `${file.name} is already in your library and ready to use.`
          : `${file.name} was uploaded. We’re preparing it for your questions.`,
      );
    } catch (error) {
      if (!isAborted(error))
        setUploadError(
          error instanceof ApiError && error.status === 409
            ? "This PDF could not be prepared in a previous upload. Please try a different PDF document."
            : errorText(error),
        );
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  };
  const matching = documents.filter(
    (document) =>
      document.original_filename.toLowerCase().includes(search.toLowerCase()) &&
      (filter === "all" || document.status === filter),
  );
  return (
    <div className="library-page">
      <div className="page-heading">
        <div>
          <h1>
            Document library<span className="heading-dot">.</span>
          </h1>
        </div>
        <button
          className="button primary"
          disabled={uploading}
          onClick={() => input.current?.click()}
        >
          {uploading ? <Spinner /> : <Plus size={18} />}
          {uploading ? "Uploading…" : "Upload document"}
        </button>
      </div>
      <input
        className="visually-hidden"
        type="file"
        ref={input}
        accept="application/pdf,.pdf"
        onChange={(event: ChangeEvent<HTMLInputElement>) =>
          void chooseFile(event.target.files?.[0])
        }
        aria-label="Choose a PDF to upload"
        tabIndex={-1}
      />
      {uploadError && (
        <Notice onClose={() => setUploadError("")}>{uploadError}</Notice>
      )}
      {success && (
        <Notice success onClose={() => setSuccess("")}>
          {success}
        </Notice>
      )}
      <button
        className={`upload-zone ${dragging ? "dragging" : ""}`}
        disabled={uploading}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void chooseFile(event.dataTransfer.files[0]);
        }}
        onClick={() => input.current?.click()}
      >
        <span className="upload-symbol">
          {uploading ? (
            <Spinner size={24} />
          ) : (
            <Upload size={24} strokeWidth={1.6} />
          )}
        </span>
        <span>
          <strong>
            {uploading
              ? "Bringing your document in…"
              : "Drop a little knowledge here"}
          </strong>
          <span>
            Drag a PDF here, or <b>browse files</b>
          </span>
        </span>
        <span className="upload-limit">
          PDF only <span>·</span> Up to 10 MB
        </span>
      </button>
      <div className="library-toolbar">
        <div className="library-count">
          <h2>Your documents</h2>
          <span>{documents.length}</span>
        </div>
        <div className="library-filters">
          <label className="search-input">
            <Search size={17} />
            <input
              aria-label="Search documents"
              placeholder="Search documents…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label className="status-filter">
            <span className="visually-hidden">Filter by status</span>
            <select
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="all">All statuses</option>
              <option value="ready">Ready</option>
              <option value="processing">Processing</option>
              <option value="queued">Queued</option>
              <option value="failed">Failed</option>
            </select>
            <ChevronDown size={15} />
          </label>
        </div>
      </div>
      {error && (
        <Notice>
          {error}
          <button className="text-button" onClick={() => void onRefresh()}>
            Retry
          </button>
        </Notice>
      )}
      <div className="document-table">
        {loading && !documents.length ? (
          <div className="empty-state">
            <Spinner size={28} />
            <h3>Opening your library…</h3>
          </div>
        ) : !matching.length ? (
          <div className="empty-state">
            <span className="empty-icon">
              <FolderOpen size={31} strokeWidth={1.4} />
            </span>
            <h3>
              {documents.length
                ? "No documents match your search."
                : "Great answers start with a document."}
            </h3>
            <p>
              {documents.length
                ? "Try a different name or status."
                : "Upload your first PDF. Folio will get it ready for your questions."}
            </p>
            {!documents.length && (
              <button
                className="button secondary"
                disabled={uploading}
                onClick={() => input.current?.click()}
              >
                <Plus size={17} />
                Add your first document
              </button>
            )}
          </div>
        ) : (
          <>
            <div className="document-row table-label">
              <span>DOCUMENT NAME</span>
              <span>ADDED</span>
              <span>STATUS</span>
              <span />
            </div>
            {matching.map((document) => (
              <div className="document-row" key={document.id}>
                <div className="document-name">
                  <span className="pdf-icon">
                    <FileText size={21} strokeWidth={1.6} />
                    <small>PDF</small>
                  </span>
                  <div>
                    <strong>{document.original_filename}</strong>
                    <small>
                      {document.status === "failed"
                        ? "Could not prepare this document."
                        : document.status === "ready"
                          ? "Available for your questions"
                          : "Preparing your document…"}
                    </small>
                  </div>
                </div>
                <span className="document-date">
                  {formatDate(document.created_at)}
                </span>
                <StatusBadge status={document.status} />
                <a
                  className="icon-button open-document"
                  href={`/api/documents/${document.id}/file`}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`Open ${document.original_filename}`}
                >
                  <ExternalLink size={17} />
                </a>
              </div>
            ))}
          </>
        )}
      </div>
      
    </div>
  );
}

function ChatWorkspace({
  exchanges,
  asking,
  onAsk,
  readyCount,
  documentsLoading,
  documentsError,
  onRetry,
  onClear,
}: {
  exchanges: Exchange[];
  asking: boolean;
  onAsk: (query: string) => Promise<void>;
  readyCount: number;
  documentsLoading: boolean;
  documentsError: string;
  onRetry: () => void;
  onClear: () => void;
}) {
  const [query, setQuery] = useState("");
  const [activeSource, setActiveSource] = useState<number | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const latest = [...exchanges]
    .reverse()
    .find((exchange) => exchange.result)?.result;
  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const value = query.trim();
    if (!value || asking || !readyCount) return;
    setQuery("");
    setActiveSource(null);
    void onAsk(value);
  };
  useEffect(() => {
    if (exchanges.length)
      bottom.current?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
        block: "end",
      });
  }, [exchanges, asking]);
  return (
    <div
      className={`chat-layout ${exchanges.length ? "has-conversation" : ""}`}
    >
      <section className="chat-column">
        <div className="chat-heading">
          <div className="eyebrow">
            <span className="small-blue-dot" /> ASK YOUR DOCUMENTS
          </div>
          {exchanges.length > 0 && (
            <button
              className="button quiet small"
              disabled={asking}
              onClick={() => {
                onClear();
                setActiveSource(null);
              }}
            >
              <Plus size={16} />
              New conversation
            </button>
          )}
        </div>
        {!exchanges.length ? (
          <div className="chat-welcome">
            <div className="welcome-symbol">
              <Layers3 size={33} strokeWidth={1.4} />
              <span>
                <Sparkles size={14} />
              </span>
            </div>
            <h1>
              Your documents.
              <br />A clearer <span>answer.</span>
            </h1>
            <div className="suggestions">
              {[
                {
                  title: "See the bigger picture",
                  query: "Summarize the key ideas across my documents.",
                  icon: BookOpen,
                },
                {
                  title: "Find what matters",
                  query:
                    "What are the most important takeaways from my documents?",
                  icon: Search,
                },
                {
                  title: "Connect the dots",
                  query: "What common themes appear across my documents?",
                  icon: Link2,
                },
              ].map(({ title, query, icon: Icon }) => (
                <button
                  key={title}
                  disabled={!readyCount || asking}
                  onClick={() => {
                    setQuery(query);
                    textarea.current?.focus();
                  }}
                >
                  <Icon size={18} />
                  <span>{title}</span>
                  <ArrowUp size={15} />
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div
            className="conversation"
            aria-live="polite"
            aria-relevant="additions text"
          >
            {exchanges.map((exchange) => (
              <article className="exchange" key={exchange.id}>
                <div className="question-row">
                  <span className="question-marker">YOU</span>
                  <h2>{exchange.question}</h2>
                </div>
                <div className="answer-row">
                  <span className="answer-logo">
                    <Layers3 size={18} />
                  </span>
                  <div className="answer-content">
                    <div className="answer-label">
                      Folio <span>FROM YOUR DOCUMENTS</span>
                    </div>
                    {exchange.result ? (
                      <>
                        <div className="markdown">
                          <ReactMarkdown disallowedElements={["img"]}>
                            {exchange.result.answer}
                          </ReactMarkdown>
                        </div>
                        {exchange.result.sources.length > 0 && (
                          <div className="inline-sources">
                            <span>
                              <CheckCheck size={15} />
                              {exchange.result.sources.length} source
                              {exchange.result.sources.length === 1 ? "" : "s"}
                            </span>
                            {exchange.result.sources.map((source, index) => (
                              <details key={`${source.chunk_id}-${index}`}>
                                <summary>
                                  <span>{index + 1}</span>
                                  <span>{source.filename}</span>
                                  <ChevronDown size={13} />
                                </summary>
                                <div>
                                  <p>{source.text}</p>
                                  <span className="source-score">
                                    Reranker score:{" "}
                                    {Number.isFinite(source.reranking_score)
                                      ? source.reranking_score.toFixed(3)
                                      : "Unavailable"}
                                  </span>
                                  <a
                                    href={`/api/documents/${source.document_id}/file`}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                  >
                                    Open document <ExternalLink size={13} />
                                  </a>
                                </div>
                              </details>
                            ))}
                          </div>
                        )}
                      </>
                    ) : exchange.error ? (
                      <Notice>
                        {exchange.error}
                        <button
                          className="text-button"
                          disabled={asking}
                          onClick={() => void onAsk(exchange.question)}
                        >
                          Try again
                        </button>
                      </Notice>
                    ) : (
                      <div className="thinking">
                        <Spinner size={17} />
                        <span>Reading between the lines…</span>
                      </div>
                    )}
                  </div>
                </div>
              </article>
            ))}
            <div ref={bottom} />
          </div>
        )}
        <div className="composer-area">
          {documentsError ? (
            <Notice>
              {documentsError}
              <button className="text-button" onClick={onRetry}>
                Retry
              </button>
            </Notice>
          ) : !readyCount && !documentsLoading ? (
            <div className="composer-notice">
              <FileText size={17} />
              <span>Upload a PDF to give your questions a starting point.</span>
              <a href="#documents">
                Add a document <ArrowRight size={14} />
              </a>
            </div>
          ) : null}
          <form className="composer" onSubmit={submit}>
            <label htmlFor="question" className="visually-hidden">
              Ask a question about your documents
            </label>
            <textarea
              ref={textarea}
              id="question"
              rows={2}
              placeholder={
                documentsLoading
                  ? "Opening your document library…"
                  : !readyCount
                    ? "Your next good question starts with a document…"
                    : "What would you like to understand?"
              }
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              disabled={!readyCount || documentsLoading}
              maxLength={8000}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  submit();
                }
              }}
            />
            <div className="composer-bottom">
              <span>
                <FolderOpen size={14} />
                {documentsLoading
                  ? "Loading documents"
                  : `${readyCount} document${readyCount === 1 ? "" : "s"} in context`}
              </span>
              <button
                type="submit"
                aria-label="Send question"
                disabled={
                  !query.trim() || asking || !readyCount || documentsLoading
                }
              >
                {asking ? <Spinner size={19} /> : <ArrowUp size={20} />}
              </button>
            </div>
          </form>
        </div>
      </section>
      <aside className="source-rail">
        <div className="source-rail-heading">
          <h2>
            <BookOpen size={17} />
            Sources
          </h2>
          <span>{latest?.sources.length || 0}</span>
        </div>
        {latest?.sources.length ? (
          <>
            <p className="rail-intro">
              The passages behind your latest answer.
            </p>
            <div className="rail-sources">
              {latest.sources.map((source, index) => (
                <div
                  className={`source-card ${activeSource === index ? "expanded" : ""}`}
                  key={`${source.chunk_id}-${index}`}
                >
                  <button
                    className="source-card-toggle"
                    aria-expanded={activeSource === index}
                    onClick={() =>
                      setActiveSource(activeSource === index ? null : index)
                    }
                  >
                    <span className="source-number">{index + 1}</span>
                    <span><strong>{source.filename}</strong></span>
                    <ChevronDown size={14} />
                  </button>
                  <p
                    className={
                      activeSource === index
                        ? "source-excerpt full"
                        : "source-excerpt"
                    }
                  >
                    {source.text}
                  </p>
                  {activeSource === index && (
                    <div className="source-card-detail">
                      <span>
                        Reranker score:{" "}
                        {Number.isFinite(source.reranking_score)
                          ? source.reranking_score.toFixed(3)
                          : "Unavailable"}
                      </span>
                      <a
                        href={`/api/documents/${source.document_id}/file`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Open PDF <ExternalLink size={12} />
                      </a>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="source-empty">
            <div className="source-empty-art" aria-hidden="true">
              <span />
              <span />
              <BookOpen size={25} strokeWidth={1.4} />
            </div>
            <h3>No sources yet.</h3>
          </div>
        )}
        
      </aside>
    </div>
  );
}

function Account({
  session,
  providers,
  onLink,
  onLogout,
}: {
  session: Session;
  providers: Providers;
  onLink: (provider: Provider) => Promise<void>;
  onLogout: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const perform = async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setError("");
    try {
      await action();
    } catch (error) {
      if (!isAborted(error)) setError(errorText(error));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="account-page">
      <div className="page-heading">
        <div>
          <h1>
            Account<span className="heading-dot">.</span>
          </h1>
        </div>
      </div>
      {error && <Notice>{error}</Notice>}
      <section className="account-card">
        <div className="account-card-heading">
          <UserRound size={20} />
          <h2>Your profile</h2>
        </div>
        <div className="profile-details">
          <span className="avatar large">{initials(session.user)}</span>
          <div>
            <strong>{session.user.email || session.user.username}</strong>
            <p>Personal workspace</p>
          </div>

        </div>
      </section>
      <section className="account-card">
        <div className="account-card-heading">
          <Link2 size={20} />
          <h2>Connected accounts</h2>
        </div>
        <p className="account-description">
          Connect a provider to use it the next time you sign in.
        </p>
        {(["google", "github"] as Provider[]).map((provider) => {
          const linked = session.providers.includes(provider);
          return (
            <div className="provider-row" key={provider}>
              <span className="provider-logo">
                {provider === "google" ? (
                  <GoogleIcon />
                ) : (
                  <GitHubIcon size={23} />
                )}
              </span>
              <div>
                <strong>{provider === "google" ? "Google" : "GitHub"}</strong>
                <p>
                  {linked
                    ? "Connected to your account"
                    : providers[provider]
                      ? "A simpler way to sign in"
                      : "Not configured on this workspace"}
                </p>
              </div>
              {linked ? (
                <span className="connected-label">
                  <Check size={14} />
                  Connected
                </span>
              ) : (
                <button
                  className="button secondary small"
                  disabled={!!busy || !providers[provider]}
                  onClick={() => void perform(provider, () => onLink(provider))}
                >
                  {busy === provider ? (
                    <Spinner size={15} />
                  ) : (
                    <Plus size={15} />
                  )}
                  Connect
                </button>
              )}
            </div>
          );
        })}
      </section>
      <section className="account-card session-card">
        <div>
          <h2>Your session</h2>
          <p>Sign out of Folio on this browser.</p>
        </div>
        <button
          className="button secondary"
          disabled={!!busy}
          onClick={() => void perform("logout", onLogout)}
        >
          {busy === "logout" ? <Spinner size={16} /> : <LogOut size={16} />}Sign
          out
        </button>
      </section>
      
    </div>
  );
}
