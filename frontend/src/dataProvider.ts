/**
 * Data provider.
 *
 * This is the ONLY module that talks to the backend. Everything else in the UI
 * goes through react-admin. That matters for the Power Apps migration: the
 * connector will consume the same REST endpoints, so swapping this provider for
 * an OData one later does not touch a single screen.
 */
import type { DataProvider, GetListParams, GetListResult } from "react-admin";

const TOKEN_KEY = "audit.token";
const USER_KEY = "audit.user";

/**
 * Fired whenever the session changes - a sign-in, a sign-out, or a 401
 * anywhere in the app clearing it.
 *
 * A provider that reads the token once at mount would keep asking for
 * things as a stranger after the user signed out, and would keep waiting for
 * a list it can never get. The session is a moment, not a state, and this is
 * how the rest of the app hears about it.
 */
export const SESSION_EVENT = "audit:session";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const getStoredUser = () => {
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
};
export const storeSession = (token: string, user: unknown) => {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  window.dispatchEvent(new Event(SESSION_EVENT));
};
/**
 * Replace the cached user without touching the token.
 *
 * Used when the server tells us who the session belongs to - which is how a
 * role changed by an administrator reaches the browser that is holding the
 * old answer. The event fires because everything that reads the cached user
 * (the permission list, the identity) has to hear about it.
 */
export const setStoredUser = (user: unknown) => {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  window.dispatchEvent(new Event(SESSION_EVENT));
};
export const clearSession = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  window.dispatchEvent(new Event(SESSION_EVENT));
};

/**
 * Where to go once the visitor has signed in.
 *
 * A deep link is a link somebody was sent, and it is the reason the session
 * existed at all - so signing in should return to it rather than to the
 * dashboard. The app has to remember it itself, because it decides which
 * route to show before the sign-in has happened.
 */
const RETURN_TO_KEY = "audit.returnTo";

/** Remember the route the visitor was trying to reach, if it is worth returning to. */
export const rememberReturnTo = (hash: string) => {
  const path = hash.replace(/^#/, "");
  if (!path || path.startsWith("/login")) return;
  try {
    sessionStorage.setItem(RETURN_TO_KEY, path);
  } catch {
    /* private browsing: the visitor lands on the dashboard instead */
  }
};

/** The remembered route, and forget it - a destination is used once. */
export const takeReturnTo = (): string | undefined => {
  try {
    const path = sessionStorage.getItem(RETURN_TO_KEY) ?? undefined;
    sessionStorage.removeItem(RETURN_TO_KEY);
    return path;
  } catch {
    return undefined;
  }
};

/**
 * Put the app on the login route.
 *
 * There is one rule here - no session means the login form is what should be
 * on screen - and it is enforced in two places, because it has to hold at two
 * moments: before the first render, and when the session goes away while the
 * app is open.
 *
 * It is worth being precise about why the app is put on the login route
 * rather than being left to bounce off the protected routes. react-admin
 * signs an anonymous visitor out of the protected branch, the sign-out clears
 * the query cache, clearing the cache re-runs the auth check, the check
 * fails, and the branch signs the visitor out again - fifty times over, which
 * is where React's "maximum update depth exceeded" comes from. The login route
 * is not that branch, so arriving on it is the one way in that does not loop.
 */
export const goToLogin = () => {
  if (window.location.hash.includes("/login")) return;
  window.location.hash = "#/login";
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * A query-parameter bag. Numbers are allowed because a page size or a window is
 * genuinely numeric, and stringifying at the boundary beats casting at 12 call
 * sites.
 */
type Query = Record<string, string | number | undefined>;

/** Drop empty values so a filter object can be passed straight through. */
const compact = (params: Query) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, String(v));
  return q.toString();
};

/**
 * The same, ready to append to a path.
 *
 * `compact` returns a bare query string with no leading `?`, so writing
 * `` `/api/dashboard${compact(...)}` `` produced `/api/dashboardentity=SCOPE` and a
 * 404 that looked like a routing problem. Everything here goes through this.
 */
const qs = (params: Query) => {
  const s = compact(params);
  return s ? `?${s}` : "";
};

/**
 * One request, one answer.
 *
 * `unauthorizedIsAnAnswer` says that a 401 here is the response the caller
 * asked for rather than an expired session. The sign-in endpoint is the case
 * that matters: wrong credentials are what a 401 means there, and treating it
 * as an expiry would report "your session has expired" to somebody who never
 * had one, and clear a session they were trying to start.
 */
async function request<T>(
  path: string,
  options: RequestInit = {},
  unauthorizedIsAnAnswer = false,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const response = await fetch(path, { ...options, headers });

  if (response.status === 401 && !unauthorizedIsAnAnswer) {
    clearSession();
    // Bounce to login without a full page reload loop.
    if (!window.location.hash.includes("/login")) {
      window.location.hash = "#/login";
    }
    throw new ApiError(401, "Session expired - please sign in again");
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const API_MAP: Record<string, string> = { runs: "rule-runs" };

const base = (resource: string) => `/api/${API_MAP[resource] ?? resource}`;

/**
 * Rules are addressed by their permanent `code`, not the numeric row id - a
 * row id identifies one VERSION, while the code identifies the rule. So for the
 * `rules` resource react-admin's `id` is set to the code, which makes every URL
 * (getOne, update, delete) resolve with no per-screen special-casing.
 */
const identifyRules = (records: any[]) =>
  records.map((r) => ({ ...r, id: r.code }));

/** react-admin list params -> query string. */
function listQuery(resource: string, params: Partial<GetListParams>): string {
  const q = new URLSearchParams();
  const p = params.pagination as { page: number; perPage: number };
  q.set("page", String(p.page ?? 1));
  q.set("perPage", String(p.perPage ?? 25));
  if (params.sort) {
    // react-admin hands sort over as "field ORDER", but an object has been seen
    // reaching here too - a plain String(sort) produced the literal
    // "sort=[object Object]&order=OBJECT" on the wire. Accept either shape.
    const raw: any = params.sort;
    const parts = typeof raw === "string" ? raw.split(" ") : [];
    const field = typeof raw === "string" ? parts[0] : raw.field;
    const order = typeof raw === "string" ? parts[1] : raw.order;
    if (field) {
      q.set("sort", String(field));
      q.set("order", String(order ?? "ASC").toUpperCase());
    }
  }
  const filter = params.filter ?? {};
  for (const [key, value] of Object.entries(filter)) {
    if (value === undefined || value === null || value === "") continue;
    if (key === "q") {
      q.set("q", String(value));
    } else if (["status", "severity", "rule_code", "entity"].includes(key)) {
      q.set(key, String(value));
    }
  }
  return `${base(resource)}?${q.toString()}`;
}

export const dataProvider: DataProvider = {
  getList: async (resource, params) => {
    const result = await request<{ data: any[]; total?: number }>(
      listQuery(resource, params),
    );
    const data = resource === "rules" ? identifyRules(result.data) : result.data;
    return { data, total: result.total ?? data.length } as GetListResult;
  },

  getOne: async (resource, params) => {
    // A rule is a chain of versions; the page asks for one of them by number.
    // `meta.version` is how a route segment reaches the request without the
    // provider having to know which screen asked.
    const version = (params.meta as { version?: number } | undefined)?.version;
    const query = version ? `?version=${version}` : "";
    const record = await request<any>(`${base(resource)}/${params.id}${query}`);
    // Rules must report `id` as the code, because that is what was requested.
    return { data: resource === "rules" ? identifyRules([record])[0] : record };
  },

  getMany: async (resource, params) => {
    const ids = params.ids as (string | number)[];
    if (!ids.length) return { data: [] };
    // Fetch one page wide enough for the requested ids and filter locally.
    const result = await request<{ data: any[]; total?: number }>(
      `${base(resource)}?perPage=500`,
    );
    const wanted = new Set(ids.map(String));
    const rows = result.data.filter((r) => wanted.has(String(resource === "rules" ? r.code : r.id)));
    return { data: resource === "rules" ? identifyRules(rows) : rows };
  },

  getManyReference: async (resource, params) => {
    const result = await request<{ data: any[]; total?: number }>(
      `${listQuery(resource, params)}&${params.target}=${params.id}`,
    );
    return { data: result.data, total: result.total ?? result.data.length };
  },

  create: async (resource, params) => {
    const record = await request<any>(base(resource), {
      method: "POST",
      body: JSON.stringify(params.data),
    });
    const merged = { ...params.data, ...record };
    return { data: resource === "rules" ? identifyRules([merged])[0] : merged };
  },

  update: async (resource, params) => {
    const { id, data } = params;
    const record = await request<any>(`${base(resource)}/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
    const merged = { ...data, ...record, id };
    return { data: resource === "rules" ? identifyRules([merged])[0] : merged };
  },

  updateMany: async (resource, params) => {
    await Promise.all(
      params.ids.map((id) =>
        request(`${base(resource)}/${id}`, {
          method: "PUT",
          body: JSON.stringify(params.data),
        }),
      ),
    );
    return { data: params.ids };
  },

  delete: async (resource, params) => {
    await request(`${base(resource)}/${params.id}`, { method: "DELETE" });
    return { data: params.previousData as any };
  },

  deleteMany: async (resource, params) => {
    await Promise.all(
      params.ids.map((id) => request(`${base(resource)}/${id}`, { method: "DELETE" })),
    );
    return { data: params.ids };
  },
};

export interface EntityOut {
  code: string;
  name: string;
  systems: string[];
}

/**
 * One account the sign-in screen offers.
 *
 * The password is in it because the panel exists so a reviewer can try each
 * role without being given accounts by hand.
 */
export interface DemoAccount {
  username: string;
  password: string;
  role: string;
  blurb: string;
}

/**
 * One account.
 *
 * `permissions` is the resolved capability list, derived by the server from
 * the role. It travels with the user so the frontend never has to know what a
 * role is allowed to do - the role string is for display, the list is for
 * decisions.
 */
export interface UserRow {
  id: number;
  username: string;
  full_name: string;
  role: string;
  /** What that role is called, derived by the server. A label, not a value. */
  role_label: string;
  is_active: boolean;
  permissions: string[];
}

/**
 * One role, and what it holds.
 *
 * Served by the server, because the matrix that decides this is the server's.
 * The frontend asks "what roles are there, and what does each one do?" and is
 * answered - it does not keep its own copy of the answer.
 */
export interface RoleOption {
  name: string;
  label: string;
  description: string;
  capabilities: string[];
}

/** One capability, named the way a screen shows it. */
export interface CapabilityOption {
  name: string;
  label: string;
  group: string;
  group_label: string;
}

/** The whole matrix: the rows, the columns, and which crosses where. */
export interface RoleMatrix {
  capabilities: CapabilityOption[];
  roles: RoleOption[];
}

/** One registered table, plus what we know about it right now. */
export interface DatasourceRow {
  id: number;
  name: string;
  entity: string;
  system: string;
  description: string;
  path_template: string;
  grain: string;
  columns: Record<string, string>;
  /** Active rules that read this table. The answer to "what breaks if I remove it". */
  used_by: string[];
  /** "landed" when the ETL delivered the file for the requested day, else "absent". */
  landing: "landed" | "absent";
  row_count: number | null;
  last_seen: string | null;
}

/** The first rows of one landed file. */
export interface DatasourcePreview {
  entity: string;
  name: string;
  data_date: string;
  /** Rows in the whole file, so the screen can say "10 of 4,318". */
  total_rows: number | null;
  /** Column name -> type, read from the file. Shown under each header. */
  columns: Record<string, string>;
  rows: Record<string, string | null>[];
  limit: number;
}

/** One CSV on disk, described entirely by reading the file. */
export interface DiscoveredFile {
  name: string;
  entity: string;
  data_date: string;
  columns: Record<string, string>;
  column_count: number;
  row_count: number;
  size_bytes: number;
  modified_at: string;
  state: "new" | "registered";
}

export interface DatasourceOverview {
  entity: string;
  entity_name: string;
  systems: string[];
  data_date: string;
  dates: string[];
  registered: DatasourceRow[];
  discovered: DiscoveredFile[];
}

/** One attempt at a working rule, and how it ended. */
export interface GenerationAttempt {
  n: number;
  ok: boolean;
  error: string | null;
  traceback: string | null;
}

/** What the trial found when the candidate was run against real data. */
export interface TrialResult {
  status: string;
  ok: boolean;
  outcome: "pass" | "fail" | null;
  summary: string;
  rows: Record<string, any>[];
  metrics: Record<string, any>;
  error: string | null;
  duration_ms: number;
  /** Values the rule tests a column for that the data does not hold. */
  warnings: string[];
}

export interface RuleGenerateResponse {
  ok: boolean;
  entity: string;
  data_date: string;
  lookback_days: number;
  source_code: string | null;
  name: string | null;
  description: string | null;
  datasources_used: string[];
  trial: TrialResult | null;
  attempts: GenerationAttempt[];
  suggested_code: string | null;
  error: string | null;
  trace: AgentStep[];
}

/** One tool the agent used, and what it learned. */
export interface AgentStep {
  tool: string;
  args: Record<string, any>;
  summary: string;
}

/**
 * One step of a rule, in the words somebody wrote for it.
 *
 * `lines` always comes from the rule's own source, so the reference beside a
 * step lands on the code that step describes even after a model has rewritten
 * the sentence.
 */
export interface ExplanationStep {
  n: number;
  text: string;
  lines: number[];
  kind: string;
  explainable: boolean;
}

/** One check a description had to pass before it was shown. */
export interface ExplanationGate {
  name: string;
  ok: boolean;
  detail: string;
}

/**
 * What a rule does, and who says so.
 *
 * `origin` is the question an auditor asks before trusting the words:
 * `derived` was read off the source, `ai` was written by a model and gated,
 * `edited` was written by a person. `stale` means the code has moved since -
 * the words are withheld rather than shown out of date.
 */
export interface RuleExplanation {
  code: string;
  version: number;
  source_sha256: string;
  origin: "derived" | "ai" | "edited";
  stored: boolean;
  stale: boolean;
  llm_configured: boolean;
  purpose: string;
  breach_condition: string;
  steps: ExplanationStep[];
  outputs: string[];
  cannot_explain: { n: number; reason: string }[];
  gates: ExplanationGate[];
  generated_by: string;
  model: string;
  prompt_version: string;
  generated_at: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

// --- custom endpoints used by the UI ---------------------------------------
export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; expires_in: number; user: any }>(
      "/api/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
      // A refused sign-in is a 401, and it is the answer to the question that
      // was asked - not an expired session.
      true,
    ),

  me: () => request<any>("/api/auth/me"),

  /**
   * The accounts the sign-in screen offers, with their passwords.
   *
   * Served by the API rather than hardcoded here, because the screen and the
   * seed are then one list rather than two - and two copies of a list of
   * passwords drift the first time somebody changes one.
   *
   * Empty unless the deployment has opted in, which is why the panel is
   * allowed to render nothing.
   */
  demoAccounts: () => request<DemoAccount[]>("/api/auth/demo-accounts"),

  /**
   * The assurance read model behind the landing page.
   *
   * One payload, derived on read from the ledger - there is no summary table
   * that can fall out of step with `rule_runs` and `tickets`.
   */
  dashboard: (entity?: string, windowDays?: number) =>
    request<any>(`/api/dashboard${qs({ entity, window: windowDays })}`),

  /**
   * Flat ticket list, for the ungrouped inbox view.
   *
   * `/api/tickets/summary` and `/api/rule-runs/summary` are still served and
   * still documented - they are what the Power Apps connector consumes - but no
   * screen calls them. The landing page reads the assurance payload instead,
   * and a wrapper nothing calls is a wrapper that quietly rots.
   */
  listTickets: (params: Record<string, string | undefined>) =>
    request<{ data: any[]; total: number }>(
      `/api/tickets${qs({ ...params, sort: "created_at", order: "DESC" })}`,
    ),

  /**
   * Tickets grouped across the WHOLE filtered set, paginated by group.
   *
   * Grouping a page of tickets fragments a rule across pages, so its count on
   * each page is wrong - and a bulk acknowledge behind that count then touches
   * more tickets than the header said. Counts here are computed before paging.
   */
  groupedTickets: (params: Record<string, string | undefined>) =>
    request<{
      groups: any[];
      total_groups: number;
      /** The true number of tickets matching the filter, counted by the server. */
      total_tickets: number;
      /** How many the scan actually read. Less than total_tickets when `truncated`. */
      scanned: number;
      /** True when the server's scan ceiling hid older tickets from the groups. */
      truncated: boolean;
      scan_limit: number;
    }>(`/api/tickets/grouped${qs(params)}`),

  listRules: () => request<{ data: any[] }>("/api/rules?perPage=200&sort=code&order=ASC"),

  acknowledgeTicket: (id: number | string, status: string, note: string) =>
    request<any>(`/api/tickets/${id}/acknowledge`, {
      method: "POST",
      body: JSON.stringify({ status, note }),
    }),

  /**
   * Acknowledge every OPEN ticket matching the given filters, server-side.
   *
   * The filters are the ones the inbox is showing, so the action can never
   * reach a ticket the auditor cannot see - `entity` included, because the app
   * bar's client scope is part of what is on screen. Doing this from the
   * browser would mean one request per ticket.
   */
  acknowledgeAllTickets: (payload: {
    status?: "acknowledged" | "dismissed";
    note?: string;
    rule_code?: string;
    severity?: string;
    q?: string;
    entity?: string;
  }) =>
    request<any>("/api/tickets/acknowledge-all", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  reopenTicket: (id: number | string) =>
    request<any>(`/api/tickets/${id}/reopen`, { method: "POST" }),

  // --- rules (addressed by code) ----------------------------------------
  /**
   * One version's source, or the head's when no version is named.
   *
   * The version is not a nicety: each row has its own file on disk, so this is
   * what makes "the code pane is showing v1" true rather than hopeful.
   */
  ruleSource: (code: string, version?: number | null) =>
    request<any>(`/api/rules/${code}/source${version ? `?version=${version}` : ""}`),

  ruleVersions: (code: string) => request<any>(`/api/rules/${code}/versions`),

  testRule: (code: string, data_date: string | null) =>
    request<any>(`/api/rules/${code}/test`, {
      method: "POST",
      body: JSON.stringify({ data_date }),
    }),

  updateRule: (code: string, payload: Record<string, unknown>) =>
    request<any>(`/api/rules/${code}`, { method: "PUT", body: JSON.stringify(payload) }),

  deleteRule: (code: string) =>
    request<void>(`/api/rules/${code}`, { method: "DELETE" }),

  setRuleStatus: (code: string, status: string) =>
    request<any>(`/api/rules/${code}/status`, {
      method: "PUT",
      body: JSON.stringify({ status }),
    }),

  rollbackRule: (code: string, version: number) =>
    request<{ rule: any; validation: any; activated: boolean }>(
      `/api/rules/${code}/rollback/${version}`,
      { method: "POST" },
    ),

  validateRule: (code: string) =>
    request<any>(`/api/rules/${code}/validate`, { method: "POST" }),

  validationReport: (code: string, version?: number | null) =>
    request<any>(
      `/api/rules/${code}/validate-report${version ? `?version=${version}` : ""}`,
    ),

  /**
   * What this control does, in plain English.
   *
   * The structure comes from the rule's own source on every call, so a page
   * that has been open since before an edit cannot describe code that is no
   * longer there. Reading costs nothing - a GET never calls a model.
   */
  explanation: (code: string, version?: number | null) =>
    request<RuleExplanation>(
      `/api/rules/${code}/explain${version ? `?version=${version}` : ""}`,
    ),

  /** Write (or rewrite) the description. Costs a model call; stored on the rule. */
  generateExplanation: (code: string, version?: number | null) =>
    request<RuleExplanation>(
      `/api/rules/${code}/explain${version ? `?version=${version}` : ""}`,
      { method: "POST" },
    ),

  /** A person's correction. The words are theirs from then on. */
  editExplanation: (
    code: string,
    payload: { breach_condition: string; steps: { n: number; text: string }[] },
    version?: number | null,
  ) =>
    request<RuleExplanation>(
      `/api/rules/${code}/explain${version ? `?version=${version}` : ""}`,
      { method: "PUT", body: JSON.stringify(payload) },
    ),

  /** Sign the description off, or withdraw the sign-off. */
  reviewExplanation: (
    code: string,
    approved: boolean,
    note = "",
    version?: number | null,
  ) =>
    request<RuleExplanation>(
      `/api/rules/${code}/explain/review${version ? `?version=${version}` : ""}`,
      { method: "POST", body: JSON.stringify({ approved, note }) },
    ),

  rerun: (id: number | string) =>
    request<any>(`/api/rule-runs/${id}/rerun`, { method: "POST" }),

  generateStatus: () => request<any>("/api/rules/generate/status"),

  /**
   * Ask for a rule, and get back one that has been run.
   *
   * The selection binds: the model is shown only `datasources`, and a rule
   * that reads any other table is rejected before it executes. Nothing is
   * created by this call - the rule row and the file are written when the
   * admin presses Save, so nothing reaches the catalogue that nobody read.
   */
  generateRule: (payload: {
    prompt: string;
    entity: string;
    datasources: string[];
    lookback_days: number;
    data_date?: string;
  }) =>
    request<RuleGenerateResponse>("/api/rules/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /**
   * Rewrite an existing rule from a plain-language change request.
   *
   * The same agent as `generateRule`, given the rule's current source and told
   * to change only what the request asks. Nothing is written: it returns a
   * candidate and the verdict on it, and the candidate becomes a draft version
   * when the admin saves it.
   */
  reviseRule: (
    code: string,
    payload: {
      prompt: string;
      datasources: string[];
      lookback_days?: number;
      data_date?: string;
    },
  ) =>
    request<RuleGenerateResponse>(`/api/rules/${code}/revise`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /**
   * Put a draft version on the air.
   *
   * The draft is validated first and only promoted if it passes, so a candidate
   * that cannot read the estate cannot replace a working control.
   */
  publishRule: (code: string, version: number) =>
    request<{ rule: any; validation: any; activated: boolean }>(
      `/api/rules/${code}/publish`,
      { method: "POST", body: JSON.stringify({ version }) },
    ),

  createRule: (payload: {
    code: string;
    name: string;
    entity: string;
    description?: string;
    severity?: string;
    lookback_days?: number;
    source_code: string;
    status?: string;
    origin?: string;
  }) =>
    request<any>("/api/rules", { method: "POST", body: JSON.stringify(payload) }),

  /**
   * The configured clients and the systems each one draws from.
   *
   * The endpoint wraps its list in `{ entities: [...] }`. The unwrap belongs
   * here so a caller cannot treat the envelope as the list - which is how this
   * read `entities.map is not a function`.
   *
   * How many there are is the deployment's business, not ours. It comes from
   * CLIENT_ENTITIES via the API; the app bar and the entity scope both read
   * this one call rather than keeping a list of their own.
   */
  entities: () =>
    request<{ entities: EntityOut[] }>("/api/datasources/entities").then((d) => d.entities),

  /**
   * Everything the datasources screen shows, in ONE call: what is registered,
   * what the ETL delivered today, and which rules read each table.
   *
   * One request rather than one per table - the same N+1 that used to make the
   * rules screen issue a schedule fetch per row.
   */
  datasourceOverview: (entity: string, dataDate?: string) =>
    request<DatasourceOverview>(
      `/api/datasources/overview?entity=${entity}` +
        (dataDate ? `&data_date=${dataDate}` : ""),
    ),

  /**
   * The first rows of one landed file.
   *
   * Read-only and catalogue-free: it works on any CSV the ETL dropped, including
   * one nobody has registered, because deciding whether to register it is exactly
   * when an admin needs to see what is in it.
   */
  datasourcePreview: (entity: string, name: string, dataDate?: string, limit = 10) =>
    request<DatasourcePreview>(
      `/api/datasources/preview?entity=${encodeURIComponent(entity)}` +
        `&name=${encodeURIComponent(name)}` +
        (dataDate ? `&data_date=${encodeURIComponent(dataDate)}` : "") +
        `&limit=${limit}`,
    ),

  registerDatasource: (payload: {
    name: string;
    entity: string;
    system?: string;
    description?: string;
    grain?: string;
    columns?: Record<string, string>;
  }) =>
    request<any>("/api/datasources", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /**
   * Remove a registration. Refused with 409 while an active rule still reads the
   * table, naming the rules - the caller surfaces that, so the refusal is a
   * decision rather than a surprise.
   */
  deleteDatasource: (id: number) =>
    request<void>(`/api/datasources/${id}`, { method: "DELETE" }),

  availableDates: (entity: string) =>
    request<{ dates: string[]; root: string; latest: string | null }>(
      `/api/datasources/available-dates?entity=${entity}`,
    ),

  // --- rule schedules (friendly cadence, never raw cron) ---
  cadenceOptions: () => request<any>("/api/rule-schedules/cadence-options"),
  cadencePreview: (payload: {
    kind: string;
    config: Record<string, any>;
    timezone: string;
    count?: number;
  }) =>
    request<any>("/api/rule-schedules/cadence-preview", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Omit `ruleCode` to get every schedule in one call. The rules screen does
   * exactly that - fetching per row meant one request per row.
   */
  listSchedules: (ruleCode?: string) =>
    request<{ data: any[] }>(
      ruleCode ? `/api/rule-schedules?rule_code=${ruleCode}` : "/api/rule-schedules",
    ),
  createSchedule: (payload: any) =>
    request<any>("/api/rule-schedules", { method: "POST", body: JSON.stringify(payload) }),
  updateSchedule: (id: number, payload: any) =>
    request<any>(`/api/rule-schedules/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteSchedule: (id: number) =>
    request<void>(`/api/rule-schedules/${id}`, { method: "DELETE" }),

  // --- users ---------------------------------------------------------------
  // The accounts, and the roles that decide what they may do. The list comes
  // back as a bare array rather than the `{data, total}` envelope the paged
  // endpoints use - there are a handful of people, not an estate.
  listUsers: () => request<UserRow[]>("/api/users"),
  createUser: (payload: {
    username: string;
    password: string;
    full_name?: string;
    role?: string;
  }) =>
    request<UserRow>("/api/users", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateUser: (
    id: number,
    payload: {
      full_name?: string;
      role?: string;
      is_active?: boolean;
      password?: string;
    },
  ) =>
    request<UserRow>(`/api/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteUser: (id: number) =>
    request<void>(`/api/users/${id}`, { method: "DELETE" }),
  /** Change your own password. Nobody else can set it. */
  changeOwnPassword: (currentPassword: string, newPassword: string) =>
    request<UserRow>("/api/users/me/password", {
      method: "PATCH",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),

  /**
   * The role matrix: which role holds which capability.
   *
   * Read-only, and it is the SERVER's matrix rather than a copy of one. The
   * settings screen draws it, and the account editor's role picker is built
   * from the same list - so a role added on the server appears in both
   * without anything here being edited.
   */
  roleMatrix: () => request<RoleMatrix>("/api/roles"),
};