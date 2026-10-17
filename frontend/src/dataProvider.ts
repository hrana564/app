/**
 * Data provider.
 *
 * This is the ONLY module that talks to the backend. Everything else in the UI
 * goes through react-admin. That matters for the Power Apps migration: the
 * connector will consume the same REST endpoints, so swapping this provider for
 * an OData one later does not touch a single screen.
 */
import type { DataProvider, GetListParams, GetListResult } from "react-admin";

const TOKEN_KEY = "abm.token";
const USER_KEY = "abm.user";

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

const base = (resource: string) => `/api/${resource}`;

/** react-admin list params -> query string. */
function listQuery(resource: string, params: Partial<GetListParams>): string {
  const q = new URLSearchParams();
  const p = params.pagination as { page: number; perPage: number };
  q.set("page", String(p.page ?? 1));
  q.set("perPage", String(p.perPage ?? 25));
  if (params.sort) {
    const [field, order] = String(params.sort).split(" ");
    q.set("sort", field);
    q.set("order", (order ?? "ASC").toUpperCase());
  }
  const filter = params.filter ?? {};
  for (const [key, value] of Object.entries(filter)) {
    if (value === undefined || value === null || value === "") continue;
    if (key === "q") {
      q.set("q", String(value));
    } else if (["status", "severity", "rule_id", "entity", "enabled"].includes(key)) {
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
    return {
      data: result.data,
      total: result.total ?? result.data.length,
    } as GetListResult;
  },

  getOne: async (resource, params) => {
    const record = await request<any>(`${base(resource)}/${params.id}`);
    return { data: record };
  },

  getMany: async (resource, params) => {
    const ids = params.ids as (string | number)[];
    if (!ids.length) return { data: [] };
    // Fetch one page wide enough for the requested ids and filter locally.
    const result = await request<{ data: any[]; total?: number }>(
      `${base(resource)}?perPage=500`,
    );
    const wanted = new Set(ids.map(String));
    return { data: result.data.filter((r) => wanted.has(String(r.id))) };
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
    return { data: { ...params.data, ...record } };
  },

  update: async (resource, params) => {
    const { id, data } = params;
    const record = await request<any>(`${base(resource)}/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
    return { data: { ...data, ...record, id } };
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

// --- custom endpoints used by the UI ---------------------------------------
export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; expires_in: number; user: any }>(
      "/api/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
    ),

  me: () => request<any>("/api/auth/me"),

  dashboard: () => request<any>("/api/dashboard"),

  health: () => request<any>("/health"),

  ticketSummary: () => request<any>("/api/tickets/summary"),
  runSummary: () => request<any>("/api/runs/summary"),

  acknowledgeTicket: (id: number | string, status: string, note: string) =>
    request<any>(`/api/tickets/${id}/acknowledge`, {
      method: "POST",
      body: JSON.stringify({ status, note }),
    }),

  reopenTicket: (id: number | string) =>
    request<any>(`/api/tickets/${id}/reopen`, { method: "POST" }),

  ruleSource: (id: number | string) =>
    request<any>(`/api/rules/${id}/source`),

  ruleVersions: (id: number | string) =>
    request<any>(`/api/rules/${id}/versions`),

  testRule: (id: number | string, data_date: string | null, shadow = false) =>
    request<any>(`/api/rules/${id}/test`, {
      method: "POST",
      body: JSON.stringify({ data_date, shadow }),
    }),

  rerun: (id: number | string) => request<any>(`/api/runs/${id}/rerun`, { method: "POST" }),

  generateStatus: () => request<any>("/api/rules/generate/status"),
  generateContext: (entity: string) =>
    request<any>(`/api/rules/generate/context?entity=${entity}`),
  generateRule: (prompt: string, entity: string) =>
    request<any>("/api/rules/generate", {
      method: "POST",
      body: JSON.stringify({ prompt, entity }),
    }),

  updateRule: (id: number, payload: Record<string, unknown>) =>
    request<any>(`/api/rules/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  entities: () => request<any>("/api/datasources/entities"),
  availableDates: (entity: string) =>
    request<any>(`/api/datasources/available-dates?entity=${entity}`),

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
  listSchedules: (ruleId: number) =>
    request<{ data: any[] }>(`/api/rule-schedules?rule_id=${ruleId}`),
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

  // --- rule lifecycle ---
  validateRule: (id: number) =>
    request<any>(`/api/rules/${id}/validate`, { method: "POST" }),
  validationReport: (id: number) =>
    request<any>(`/api/rules/${id}/validate-report`),
};