import type { DataProvider, GetListParams, GetListResult } from "react-admin";

const TOKEN_KEY = "audit.token";
const USER_KEY = "audit.user";

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
export const setStoredUser = (user: unknown) => {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  window.dispatchEvent(new Event(SESSION_EVENT));
};
export const clearSession = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  window.dispatchEvent(new Event(SESSION_EVENT));
};

const RETURN_TO_KEY = "audit.returnTo";

export const rememberReturnTo = (hash: string) => {
  const path = hash.replace(/^#/, "");
  if (!path || path.startsWith("/login")) return;
  try {
    sessionStorage.setItem(RETURN_TO_KEY, path);
  } catch {
    /* private browsing: the visitor lands on the dashboard instead */
  }
};

export const takeReturnTo = (): string | undefined => {
  try {
    const path = sessionStorage.getItem(RETURN_TO_KEY) ?? undefined;
    sessionStorage.removeItem(RETURN_TO_KEY);
    return path;
  } catch {
    return undefined;
  }
};

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

type Query = Record<string, string | number | undefined>;

const compact = (params: Query) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, String(v));
  return q.toString();
};

const qs = (params: Query) => {
  const s = compact(params);
  return s ? `?${s}` : "";
};

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

const identifyRules = (records: any[]) =>
  records.map((r) => ({ ...r, id: r.code }));

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

export interface DemoAccount {
  username: string;
  password: string;
  role: string;
  blurb: string;
}

export interface UserRow {
  id: number;
  username: string;
  full_name: string;
  role: string;
  role_label: string;
  is_active: boolean;
  permissions: string[];
}

export interface RoleOption {
  name: string;
  label: string;
  description: string;
  capabilities: string[];
}

export interface CapabilityOption {
  name: string;
  label: string;
  group: string;
  group_label: string;
}

export interface RoleMatrix {
  capabilities: CapabilityOption[];
  roles: RoleOption[];
}

export interface DatasourceRow {
  id: number;
  name: string;
  entity: string;
  system: string;
  description: string;
  path_template: string;
  grain: string;
  columns: Record<string, string>;
  used_by: string[];
  landing: "landed" | "absent";
  row_count: number | null;
  last_seen: string | null;
}

export interface DatasourcePreview {
  entity: string;
  name: string;
  data_date: string;
  total_rows: number | null;
  columns: Record<string, string>;
  rows: Record<string, string | null>[];
  limit: number;
}

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

export interface GenerationAttempt {
  n: number;
  ok: boolean;
  error: string | null;
  traceback: string | null;
}

/**
 * What a rule's source declares and reads.
 *
 * The server works this out with the AST, because the server is what decides
 * whether a rule may be activated. The browser used to answer the same
 * question with a regex, and the two could disagree about the same file.
 */
export interface SourceBinding {
  declared: string[];
  referenced: string[];
  unreadable: boolean;
  undeclared: string[];
  unused: string[];
  declaration: string;
  source_code: string;
}

export interface TrialResult {
  status: string;
  ok: boolean;
  outcome: "pass" | "fail" | null;
  summary: string;
  rows: Record<string, any>[];
  metrics: Record<string, any>;
  error: string | null;
  duration_ms: number;
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
  explanation: ExplanationDraft | null;
  trace: AgentStep[];
}

export interface AgentStep {
  tool: string;
  args: Record<string, any>;
  summary: string;
}

export interface ExplanationStep {
  n: number;
  text: string;
  lines: number[];
  explainable: boolean;
}

export interface ExplanationDraft {
  breach_condition: string;
  steps: { n: number; text: string }[];
  outputs: string[];
  origin: string;
  generated_by: string;
  model: string;
  prompt_version: string;
}

export interface ExplanationGate {
  name: string;
  ok: boolean;
  detail: string;
  blocking: boolean;
}

export interface RuleExplanation {
  code: string;
  version: number;
  source_sha256: string;
  origin: "none" | "ai" | "edited";
  stored: boolean;
  stale: boolean;
  llm_configured: boolean;
  purpose: string;
  breach_condition: string;
  steps: ExplanationStep[];
  outputs: string[];
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

  demoAccounts: () => request<DemoAccount[]>("/api/auth/demo-accounts"),

  dashboard: (entity?: string, windowDays?: number) =>
    request<any>(`/api/dashboard${qs({ entity, window: windowDays })}`),

  listTickets: (params: Record<string, string | undefined>) =>
    request<{ data: any[]; total: number }>(
      `/api/tickets${qs({ ...params, sort: "created_at", order: "DESC" })}`,
    ),

  groupedTickets: (params: Record<string, string | undefined>) =>
    request<{
      groups: any[];
      total_groups: number;
      total_tickets: number;
      scanned: number;
      truncated: boolean;
      scan_limit: number;
    }>(`/api/tickets/grouped${qs(params)}`),

  listRules: () => request<{ data: any[] }>("/api/rules?perPage=200&sort=code&order=ASC"),

  acknowledgeTicket: (id: number | string, status: string, note: string) =>
    request<any>(`/api/tickets/${id}/acknowledge`, {
      method: "POST",
      body: JSON.stringify({ status, note }),
    }),

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
  ruleSource: (code: string, version?: number | null) =>
    request<any>(`/api/rules/${code}/source${version ? `?version=${version}` : ""}`),

  /**
   * What a source declares and reads. With `names`, the source comes back
   * with its DATASOURCES declaration set to them.
   */
  sourceBinding: (source_code: string, names?: string[]) =>
    request<SourceBinding>("/api/source/binding", {
      method: "POST",
      body: JSON.stringify({ source_code, names: names ?? null }),
    }),

  ruleVersions: (code: string) => request<any>(`/api/rules/${code}/versions`),

  testRule: (code: string, data_date: string | null, version?: number | null) =>
    request<any>(`/api/rules/${code}/test`, {
      method: "POST",
      body: JSON.stringify({ data_date, version: version ?? null }),
    }),

  updateRule: (code: string, payload: Record<string, unknown>) =>
    request<any>(`/api/rules/${code}`, { method: "PUT", body: JSON.stringify(payload) }),

  deleteRule: (code: string) =>
    request<void>(`/api/rules/${code}`, { method: "DELETE" }),

  discardDraft: (code: string) =>
    request<void>(`/api/rules/${code}/draft`, { method: "DELETE" }),

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

  validateRule: (code: string, version?: number | null) =>
    request<any>(
      `/api/rules/${code}/validate${version ? `?version=${version}` : ""}`,
      { method: "POST" },
    ),

  validationReport: (code: string, version?: number | null) =>
    request<any>(
      `/api/rules/${code}/validate-report${version ? `?version=${version}` : ""}`,
    ),

  explanation: (code: string, version?: number | null) =>
    request<RuleExplanation>(
      `/api/rules/${code}/explain${version ? `?version=${version}` : ""}`,
    ),

  generateExplanation: (code: string, version?: number | null) =>
    request<RuleExplanation>(
      `/api/rules/${code}/explain${version ? `?version=${version}` : ""}`,
      { method: "POST" },
    ),

  editExplanation: (
    code: string,
    payload: { breach_condition: string; steps: { n: number; text: string }[] },
    version?: number | null,
  ) =>
    request<RuleExplanation>(
      `/api/rules/${code}/explain${version ? `?version=${version}` : ""}`,
      { method: "PUT", body: JSON.stringify(payload) },
    ),

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

  publishRule: (code: string, version: number) => {
    if (version == null) {
      throw new Error(`publishRule: no version given for ${code}`);
    }
    return request<{ rule: any; validation: any; activated: boolean }>(
      `/api/rules/${code}/publish`,
      { method: "POST", body: JSON.stringify({ version }) },
    );
  },

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
    explanation?: ExplanationDraft | null;
  }) =>
    request<any>("/api/rules", { method: "POST", body: JSON.stringify(payload) }),

  entities: () =>
    request<{ entities: EntityOut[] }>("/api/datasources/entities").then((d) => d.entities),

  datasourceOverview: (entity: string, dataDate?: string) =>
    request<DatasourceOverview>(
      `/api/datasources/overview?entity=${entity}` +
        (dataDate ? `&data_date=${dataDate}` : ""),
    ),

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
  changeOwnPassword: (currentPassword: string, newPassword: string) =>
    request<UserRow>("/api/users/me/password", {
      method: "PATCH",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),

  roleMatrix: () => request<RoleMatrix>("/api/roles"),
};