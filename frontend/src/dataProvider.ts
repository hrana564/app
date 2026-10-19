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

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const getStoredUser = () => {
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
};
export const storeSession = (token: string, user: unknown) => {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
};
export const clearSession = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
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

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const response = await fetch(path, { ...options, headers });

  if (response.status === 401) {
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
    const record = await request<any>(`${base(resource)}/${params.id}`);
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

// --- custom endpoints used by the UI ---------------------------------------
export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; expires_in: number; user: any }>(
      "/api/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
    ),

  me: () => request<any>("/api/auth/me"),

  /**
   * The assurance read model behind the landing page.
   *
   * One payload, derived on read from the ledger - there is no summary table
   * that can fall out of step with `rule_runs` and `tickets`.
   */
  dashboard: (entity?: string, windowDays?: number) =>
    request<any>(`/api/dashboard${qs({ entity, window: windowDays })}`),

  health: () => request<any>("/health"),

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
    request<{ groups: any[]; total_groups: number; total_tickets: number }>(
      `/api/tickets/grouped${qs(params)}`,
    ),

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
  ruleSource: (code: string) => request<any>(`/api/rules/${code}/source`),

  ruleVersions: (code: string) => request<any>(`/api/rules/${code}/versions`),

  /**
   * Every version of every rule, metadata only.
   *
   * The list endpoint is used deliberately rather than /versions: RuleOut
   * omits `source_code`, while RuleVersionOut embeds the full source of every
   * version in the tree. One call therefore gives the rules screen its whole
   * drill-down history without downloading every rule's code.
   */
  allRuleVersions: () =>
    request<{ data: any[]; total: number }>(
      "/api/rules?all_versions=true&perPage=500&sort=code&order=ASC",
    ),

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
    request<any>(`/api/rules/${code}/rollback/${version}`, { method: "POST" }),

  validateRule: (code: string) =>
    request<any>(`/api/rules/${code}/validate`, { method: "POST" }),

  validationReport: (code: string) =>
    request<any>(`/api/rules/${code}/validate-report`),

  rerun: (id: number | string) => request<any>(`/api/runs/${id}/rerun`, { method: "POST" }),

  generateStatus: () => request<any>("/api/rules/generate/status"),
  generateContext: (entity: string) =>
    request<any>(`/api/rules/generate/context?entity=${entity}`),
  generateRule: (prompt: string, entity: string) =>
    request<any>("/api/rules/generate", {
      method: "POST",
      body: JSON.stringify({ prompt, entity }),
    }),

  /**
   * The two client entities and the systems each one draws from.
   *
   * The endpoint wraps its list in `{ entities: [...] }`. The unwrap belongs
   * here so a caller cannot treat the envelope as the list - which is how this
   * read `entities.map is not a function`.
   */
  entities: () =>
    request<{ entities: EntityOut[] }>("/api/datasources/entities").then((d) => d.entities),

  listDatasources: () => request<any[]>("/api/datasources"),

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
  schedulerJobs: () => request<any>("/api/rule-schedules/cron/jobs"),
};