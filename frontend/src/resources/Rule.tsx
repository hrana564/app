import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Datagrid, Edit, List, Pagination, ShowBase, TextField, TopToolbar,
  useListContext, useNotify, useRecordContext, useRefresh,
} from "react-admin";
import { useNavigate } from "react-router-dom";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, Divider, IconButton,
  MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead,
  TableRow, TextField as MuiTextField, Tooltip, Typography,
} from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import BugReportIcon from "@mui/icons-material/BugReport";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import EditIcon from "@mui/icons-material/Edit";
import VisibilityIcon from "@mui/icons-material/Visibility";
import HistoryIcon from "@mui/icons-material/History";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ArrowUpwardIcon from "@mui/icons-material/ArrowUpward";
import ArrowDownwardIcon from "@mui/icons-material/ArrowDownward";
import ScheduleIcon from "@mui/icons-material/Schedule";
import { api, getStoredUser } from "../dataProvider";
import { CadenceEditor, type CadenceConfig } from "../components/CadenceEditor";
import {
  ActionIcon, CodeBlock, DotLabel, EntityField, IdField, JsonBlock, RowsTable,
  RuleStateCell, SeverityField, StatusField, RowCountField,
} from "../components/ui";
import { severityColor, statusColor, FONT } from "../theme";

/* ================================================================== */
/* Filter vocabulary - mirrors the Literals in backend/app/schemas.py */
/* ================================================================== */
const STATUS_OPTIONS = ["draft", "active", "paused", "retired"] as const;
const SEVERITY_OPTIONS = ["low", "medium", "high", "critical"] as const;
const ENTITY_OPTIONS = ["LAD", "ABM"] as const;

/** One row of a rule's history. RuleOut, minus the fields the table ignores. */
interface VersionRow {
  code: string;
  version: number;
  is_version_active: boolean;
  status: string;
  validation_status: string;
  severity: string;
  change_note: string;
  created_by: string;
  created_at: string;
  source_file: string;
}

const isAdminUser = () => getStoredUser()?.role === "admin";

/* ================================================================== */
/* Rule toolbar: search + prompt-to-rule                               */
/* ================================================================== */
export const GenerateRuleDialog = () => {
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [entity, setEntity] = useState("LAD");
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const notify = useNotify();
  const refresh = useRefresh();

  const openDialog = async () => {
    setOpen(true);
    setResult(null);
    setError(null);
    try {
      const s = await api.generateStatus();
      setConfigured(s.configured);
    } catch {
      setConfigured(false);
    }
  };

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      const g = await api.generateRule(prompt, entity);
      setResult(g);
      notify(`Draft rule ${g.rule.code} created — validate it before activating`, {
        type: "success",
      });
      refresh();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button variant="contained" startIcon={<AutoAwesomeIcon />} onClick={openDialog}>
        Generate rule from prompt
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Generate a rule from a natural-language prompt</DialogTitle>
        <DialogContent>
          {configured === false ? (
            <Alert severity="warning" sx={{ mt: 1 }}>
              No LLM endpoint configured. Set <code>LLM_BASE_URL</code>,{" "}
              <code>LLM_API_KEY</code> and <code>LLM_MODEL</code> in{" "}
              <code>backend/.env</code>, then restart the API. Any OpenAI-compatible
              endpoint works.
            </Alert>
          ) : result ? (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <Alert severity="success">
                Draft <strong>{result.rule.code}</strong> created with status{" "}
                <strong>draft</strong>. Read the code, run <strong>Validate</strong>,
                then promote to <strong>active</strong>.
              </Alert>
              <Box>
                <Typography variant="caption" sx={{ fontWeight: 700 }}>
                  DATASOURCES USED
                </Typography>
                <Stack direction="row" spacing={1} sx={{ mt: 0.5, flexWrap: "wrap", gap: 0.5 }}>
                  {result.datasources_used.length === 0 ? (
                    <Chip size="small" label="none detected" />
                  ) : (
                    result.datasources_used.map((d: string) => (
                      <Chip key={d} size="small" label={d} color="secondary" />
                    ))
                  )}
                </Stack>
              </Box>
              <CodeBlock code={result.source_code} />
            </Stack>
          ) : (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <MuiTextField select fullWidth size="small" label="Client entity"
                value={entity} onChange={(e) => setEntity(e.target.value)}>
                <MenuItem value="LAD">Laduree (iPOS SaaS, Azure Synapse, HRMS)</MenuItem>
                <MenuItem value="ABM">Al Boom Marine (iPOS, HRMS, Business Central)</MenuItem>
              </MuiTextField>
              <MuiTextField label="Describe the audit control" value={prompt}
                onChange={(e) => setPrompt(e.target.value)} multiline minRows={4} fullWidth
                placeholder="e.g. Flag POS transactions settled by an employee who exited the company in the last 30 days"
                helperText="Plain language. The model only sees the datasources registered for the selected entity." />
              {error && <Alert severity="error">{error}</Alert>}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Close</Button>
          {!result && configured !== false && (
            <Button onClick={generate} variant="contained" disabled={busy || prompt.trim().length < 5}
              startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}>
              Generate draft
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </>
  );
};

/**
 * Search plus three dropdowns, held in one local object and pushed in a single
 * debounced effect. Two separate effects would race: typing in the search box
 * calls setFilters with just `q`, which drops the dropdowns.
 *
 * Two details are load-bearing. `setFilters` and `filterValues` are read through
 * refs because setFilters changes identity every time the store does - depending
 * on it re-runs this effect on every render. And the push is guarded by a real
 * comparison, because react-admin builds a fresh `filterValues` object on every
 * call even when nothing changed, so an unguarded call never converges and loops.
 */
const RuleToolbar = () => {
  const { setFilters, filterValues } = useListContext<any>();
  const [filters, setLocal] = useState({
    q: "",
    status: "",
    severity: "",
    entity: "",
  });

  const applyRef = useRef({ setFilters, filterValues });
  applyRef.current = { setFilters, filterValues };

  useEffect(() => {
    const handle = setTimeout(() => {
      const next: Record<string, string> = {};
      (Object.keys(filters) as (keyof typeof filters)[]).forEach((k) => {
        if (filters[k]) next[k] = filters[k];
      });

      const { setFilters: apply, filterValues: current } = applyRef.current;
      const live: Record<string, unknown> = current ?? {};
      const changed =
        Object.keys(next).length !== Object.keys(live).length ||
        Object.keys(next).some((k) => live[k] !== next[k]);

      if (changed) apply(next, "ignored");
    }, 300);
    return () => clearTimeout(handle);
  }, [filters]);

  const set = (patch: Partial<typeof filters>) => setLocal((f) => ({ ...f, ...patch }));
  const active = Object.values(filters).some(Boolean);

  const FilterSelect = ({
    label, options, value,
  }: { label: string; options: readonly string[]; value: string }) => (
    <MuiTextField
      select
      size="small"
      label={label}
      value={value}
      onChange={(e) => set({ [label]: e.target.value } as Partial<typeof filters>)}
      sx={{ minWidth: 130 }}
    >
      <MenuItem value="">
        <em>All</em>
      </MenuItem>
      {options.map((o) => (
        <MenuItem key={o} value={o} sx={{ textTransform: "capitalize" }}>
          {o}
        </MenuItem>
      ))}
    </MuiTextField>
  );

  return (
    <TopToolbar>
      <MuiTextField size="small" placeholder="Search rule code or name…" value={filters.q}
        onChange={(e) => set({ q: e.target.value })} sx={{ minWidth: 220 }} />
      <FilterSelect label="status" options={STATUS_OPTIONS} value={filters.status} />
      <FilterSelect label="severity" options={SEVERITY_OPTIONS} value={filters.severity} />
      <FilterSelect label="entity" options={ENTITY_OPTIONS} value={filters.entity} />
      {active && (
        <Button
          size="small"
          onClick={() => setLocal({ q: "", status: "", severity: "", entity: "" })}
        >
          Clear
        </Button>
      )}
      <Box sx={{ flex: 1 }} />
      <GenerateRuleDialog />
    </TopToolbar>
  );
};

/* ================================================================== */
/* Shared indexes                                                      */
/* ================================================================== */
/**
 * Every version of every rule, in ONE request.
 *
 * The old screen fetched a rule's schedules inside each row, so ten rows meant
 * ten round trips. Both indexes are now fetched once and read synchronously.
 */
const useVersionIndex = () => {
  const [index, setIndex] = useState<Record<string, VersionRow[]>>({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    api.allRuleVersions()
      .then((r) => {
        const byCode: Record<string, VersionRow[]> = {};
        for (const row of (r.data ?? []) as VersionRow[]) {
          (byCode[row.code] ??= []).push(row);
        }
        for (const code of Object.keys(byCode)) {
          byCode[code].sort((a, b) => b.version - a.version);
        }
        setIndex(byCode);
      })
      .catch(() => setIndex({}))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);
  return { index, loading, reload: load };
};

const useScheduleIndex = () => {
  const [index, setIndex] = useState<Record<string, any[]>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listSchedules()
      .then((r) => {
        const byCode: Record<string, any[]> = {};
        for (const s of r.data ?? []) (byCode[s.rule_code] ??= []).push(s);
        setIndex(byCode);
      })
      .catch(() => setIndex({}))
      .finally(() => setLoading(false));
  }, []);

  return { index, loading };
};

/** Plain-English schedule summary, or "manual only" when there is none. */
const ScheduleSummary = ({ schedules }: { schedules: any[] }) => {
  if (!schedules?.length) {
    return (
      <Typography variant="body2" color="text.secondary">
        manual only
      </Typography>
    );
  }
  return (
    <Stack spacing={0.15} sx={{ minWidth: 0 }}>
      {schedules.slice(0, 2).map((s) => (
        <Typography key={s.id} variant="body2" noWrap title={s.cron}>
          {s.cadence_label}
        </Typography>
      ))}
      {schedules.length > 2 && (
        <Typography variant="caption" color="text.secondary">
          +{schedules.length - 2} more
        </Typography>
      )}
    </Stack>
  );
};

/* ================================================================== */
/* Run - asks first, then shows what happened                          */
/* ================================================================== */
export const RunRuleDialog = ({
  rule, onClose, onDone,
}: {
  rule: any | null;
  onClose: () => void;
  onDone: () => void;
}) => {
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [dates, setDates] = useState<string[]>([]);
  const [day, setDay] = useState<string>("");

  useEffect(() => {
    if (!rule) return;
    setOut(null);
    setError(null);
    api.availableDates(rule.entity)
      .then((res) => {
        setDates(res.dates ?? []);
        // Latest available day - yesterday's CSVs are what a schedule would use.
        if (res.dates?.length) setDay(res.dates[res.dates.length - 1]);
        else setError("No data is available for this entity yet.");
      })
      .catch((e: any) => setError(`Could not list data dates: ${e.message}`));
  }, [rule]);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setOut(await api.testRule(rule.code, day));
      onDone();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!rule) return null;

  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="lg" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        Run {rule.code}
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          {rule.name}
        </Typography>
      </DialogTitle>

      <DialogContent>
        {!out ? (
          <Stack spacing={2} sx={{ mt: 1 }}>
            <MuiTextField
              select
              size="small"
              label="Run against data from"
              value={day}
              onChange={(e) => setDay(e.target.value)}
              sx={{ minWidth: 220 }}
            >
              {dates.map((d) => (
                <MenuItem key={d} value={d}>{d}</MenuItem>
              ))}
            </MuiTextField>

            <Alert severity="warning">
              This executes the rule against real staged data and is recorded as
              a run. <strong>If it finds a breach, a ticket is raised.</strong> It
              is not a dry run.
            </Alert>

            <Typography variant="body2">
              Run <strong>{rule.code}</strong> now, against{" "}
              <strong>{day || "—"}</strong>?
            </Typography>

            {error && <Alert severity="error">{error}</Alert>}
          </Stack>
        ) : (
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Stack direction="row" spacing={1} alignItems="center">
              <DotLabel value={out.status} tone={statusColor[out.status] ?? "default"} />
              <Typography variant="body2">{out.summary}</Typography>
            </Stack>

            {out.status === "error" && (
              <Alert severity="error" icon={<BugReportIcon />}>
                <Box component="pre" sx={{ m: 0, whiteSpace: "pre-wrap", fontSize: FONT.label }}>
                  {out.error_detail}
                </Box>
              </Alert>
            )}

            <Box>
              <Typography variant="caption" sx={{ fontWeight: 700 }}>METRICS</Typography>
              <Box sx={{ mt: 0.5 }}><JsonBlock value={out.metrics} /></Box>
            </Box>

            <Box>
              <Typography variant="caption" sx={{ fontWeight: 700 }}>
                EXCEPTION ROWS ({out.rows?.length ?? 0}) · {out.duration_ms} ms
              </Typography>
              <Box sx={{ mt: 0.5 }}><RowsTable rows={out.rows ?? []} /></Box>
            </Box>
          </Stack>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {out ? "Close" : "Cancel"}
        </Button>
        {!out && (
          <Button
            variant="contained"
            color="warning"
            onClick={run}
            disabled={busy || !day}
            startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <PlayArrowIcon />}
          >
            {busy ? "Running…" : "Run now"}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

/* ================================================================== */
/* Delete - typed confirmation, and a way out when the API refuses     */
/* ================================================================== */
export const DeleteRuleDialog = ({
  rule, onClose, onDone,
}: {
  rule: any | null;
  onClose: () => void;
  onDone: () => void;
}) => {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const notify = useNotify();

  useEffect(() => {
    setTyped(""); setError(null); setRefused(null);
  }, [rule]);

  if (!rule) return null;
  const confirmed = typed.trim().toUpperCase() === rule.code.toUpperCase();

  const remove = async () => {
    setBusy(true); setError(null);
    try {
      await api.deleteRule(rule.code);
      notify(`${rule.code} deleted`, { type: "success" });
      onDone();
      onClose();
    } catch (e: any) {
      // A rule with runs or tickets cannot be deleted - the backend says so, and
      // says what to do instead. Show its sentence rather than a generic error.
      setRefused(e.message);
    } finally {
      setBusy(false);
    }
  };

  const retire = async () => {
    setBusy(true);
    try {
      await api.setRuleStatus(rule.code, "retired");
      notify(`${rule.code} retired - its history is preserved`, { type: "success" });
      onDone();
      onClose();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Delete {rule.code}?</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Typography variant="body2">
            This removes the rule and all {rule.version} of its versions, including
            the Python source files on disk. It cannot be undone.
          </Typography>

          {refused ? (
            <>
              <Alert severity="warning">{refused}</Alert>
              <Button onClick={retire} disabled={busy} startIcon={<HistoryIcon />}>
                Retire instead
              </Button>
            </>
          ) : (
            <MuiTextField
              size="small"
              autoFocus
              label={`Type ${rule.code} to confirm`}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={rule.code}
            />
          )}

          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        {!refused && (
          <Button
            variant="contained"
            color="error"
            onClick={remove}
            disabled={busy || !confirmed}
            startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}
          >
            Delete
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

/* ================================================================== */
/* One version's source, read-only                                      */
/* ================================================================== */
export const VersionSourceDialog = ({
  code, version, onClose,
}: {
  code: string | null;
  version: number | null;
  onClose: () => void;
}) => {
  const [found, setFound] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const notify = useNotify();
  const refresh = useRefresh();

  useEffect(() => {
    if (!code || version === null) return;
    setFound(null);
    api.ruleVersions(code)
      .then((list: any[]) => setFound(list.find((v) => v.version === version) ?? null))
      .catch(() => setFound(null));
  }, [code, version]);

  if (!code || version === null) return null;

  const restore = async () => {
    setBusy(true);
    try {
      const next = await api.rollbackRule(code, version);
      notify(`${code} v${next.version} created from v${version} as draft - validate it to activate`, {
        type: "success",
      });
      refresh();
      onClose();
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        {code} · version {version}
        {found && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {found.change_note || "(no note)"} · {found.created_by} ·{" "}
            {new Date(found.created_at).toLocaleString()}
          </Typography>
        )}
      </DialogTitle>
      <DialogContent>
        {!found ? (
          <Box sx={{ py: 4, textAlign: "center" }}>
            <CircularProgress size={24} />
          </Box>
        ) : (
          <CodeBlock code={found.source_code} />
        )}
      </DialogContent>
      <DialogActions>
        {/* Restore copies v(n) FORWARD into a new version - it does not rewind. */}
        {isAdminUser() && (
          <Tooltip title="Copies this source forward into a new draft version. It does not rewind history.">
            <span>
              <Button onClick={restore} disabled={busy || !found} startIcon={<HistoryIcon />}>
                {busy ? "Restoring…" : "Restore this version"}
              </Button>
            </span>
          </Tooltip>
        )}
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};

/* ================================================================== */
/* Schedules section                                                   */
/* ================================================================== */
const ScheduleDialog = ({
  ruleCode, schedule, onClose,
}: {
  ruleCode: string;
  schedule: any | null;
  onClose: () => void;
}) => {
  const isEdit = Boolean(schedule);
  const [kind, setKind] = useState(schedule?.kind ?? "DAILY");
  const [config, setConfig] = useState<Record<string, any>>(schedule?.config ?? { time_of_day: "06:30" });
  const [timezone, setTimezone] = useState(schedule?.timezone ?? "UTC");
  const [label, setLabel] = useState(schedule?.label ?? "");
  const [dataLag, setDataLag] = useState(schedule?.data_lag_days ?? 1);
  const [busy, setBusy] = useState(false);
  const notify = useNotify();

  const save = async () => {
    setBusy(true);
    try {
      const payload = { label, kind, config, timezone, data_lag_days: dataLag };
      if (isEdit) await api.updateSchedule(schedule.id, payload);
      else await api.createSchedule({ ...payload, rule_code: ruleCode });
      notify(isEdit ? "Schedule updated" : "Schedule added", { type: "success" });
      onClose();
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{isEdit ? "Edit schedule" : "Add a schedule"}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <MuiTextField size="small" fullWidth label="Label (optional)"
            value={label} onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Daily control" />
          <CadenceEditor value={{ kind, config, timezone }} onChange={(v: CadenceConfig) => {
            setKind(v.kind); setConfig(v.config); setTimezone(v.timezone ?? "UTC");
          }} />
          <MuiTextField select size="small" fullWidth label="Audit which day's data"
            value={dataLag} onChange={(e) => setDataLag(Number(e.target.value))}
            helperText="Yesterday is the usual choice — a day's CSVs are rarely complete when the job fires.">
            <MenuItem value={0}>Same day</MenuItem>
            <MenuItem value={1}>Yesterday</MenuItem>
            <MenuItem value={2}>2 days ago</MenuItem>
            <MenuItem value={7}>7 days ago</MenuItem>
          </MuiTextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" onClick={save} disabled={busy}
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}>
          {isEdit ? "Save" : "Add schedule"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

const SchedulesSection = ({ ruleCode, isAdmin }: { ruleCode: string; isAdmin: boolean }) => {
  const [schedules, setSchedules] = useState<any[] | null>(null);
  const [editing, setEditing] = useState<any | null>(null);
  const [adding, setAdding] = useState(false);
  const notify = useNotify();

  const load = () =>
    api.listSchedules(ruleCode).then((r) => setSchedules(r.data ?? [])).catch(() => setSchedules([]));

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [ruleCode]);

  const remove = async (id: number) => {
    try {
      await api.deleteSchedule(id);
      notify("Schedule removed", { type: "info" });
      load();
    } catch (e: any) {
      notify(e.message, { type: "error" });
    }
  };

  return (
    <Card>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <ScheduleIcon color="action" />
            <Typography variant="h6">Schedules</Typography>
            <Chip size="small" label={schedules?.length ?? 0} />
          </Stack>
          {isAdmin && (
            <Button size="small" startIcon={<AddIcon />} onClick={() => setAdding(true)}>
              Add schedule
            </Button>
          )}
        </Stack>

        {!schedules && <CircularProgress size={22} />}

        {schedules?.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            No schedules. A rule with no schedule will only ever run when triggered manually.
          </Typography>
        )}

        <Stack spacing={1}>
          {schedules?.map((s) => (
            <Box key={s.id} sx={{ p: 1.5, border: "1px solid #e9edf4", borderRadius: 2, bgcolor: "#fcfdff" }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Box>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="body2" sx={{ fontWeight: 700 }}>{s.cadence_label}</Typography>
                    {s.label && s.label !== s.cadence_label && (
                      <Chip size="small" variant="outlined" label={s.label} />
                    )}
                    <Chip size="small" label={s.timezone} />
                    <Chip size="small" variant="outlined"
                      label={s.data_lag_days === 1 ? "yesterday's data" : `${s.data_lag_days}d lag`} />
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    next {s.next_run_relative ?? "unknown"}
                    {s.upcoming_runs?.[1] &&
                      ` · then ${new Date(s.upcoming_runs[1]).toLocaleString()}`}
                    {s.last_run_at && ` · last ran ${new Date(s.last_run_at).toLocaleString()}`}
                  </Typography>
                  <Typography variant="caption" sx={{ display: "block", color: "#8d99ae", fontFamily: "monospace" }}>
                    cron: {s.cron}
                  </Typography>
                </Box>
                {isAdmin && (
                  <Stack direction="row" spacing={0.5}>
                    <Button size="small" onClick={() => setEditing(s)}>Edit</Button>
                    <Tooltip title="Remove schedule">
                      <IconButton size="small" onClick={() => remove(s.id)}>
                        <DeleteOutlineIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                )}
              </Stack>
            </Box>
          ))}
        </Stack>

        {(adding || editing) && (
          <ScheduleDialog
            ruleCode={ruleCode}
            schedule={editing}
            onClose={() => { setAdding(false); setEditing(null); load(); }}
          />
        )}
      </CardContent>
    </Card>
  );
};

/* ================================================================== */
/* Rule detail                                                         */
/* ================================================================== */
const RuleDetails = () => {
  const record = useRecordContext<any>();
  const [source, setSource] = useState("");
  const [versions, setVersions] = useState<any[]>([]);
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [validating, setValidating] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const notify = useNotify();
  const refresh = useRefresh();

  const isAdmin = isAdminUser();

  // react-admin does not export a title hook, so set the document title directly.
  useEffect(() => {
    if (record?.code) document.title = `${record.code} · ABM Audit Portal`;
  }, [record?.code]);

  const load = () => {
    if (!record?.id) return;
    setLoading(true);
    Promise.all([api.ruleSource(record.code), api.ruleVersions(record.code), api.validationReport(record.code)])
      .then(([s, v, r]) => {
        setSource(s.source_code ?? "");
        setVersions(v ?? []);
        setReport(r);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(load, [record?.code, record?.version, record?.validation_status]);

  const runValidation = async () => {
    setValidating(true);
    try {
      const r = await api.validateRule(record.code);
      setReport(r);
      notify(r.status === "valid" ? "Validation passed" : `${r.errors.length} error(s)`,
        { type: r.status === "valid" ? "success" : "error" });
      refresh();
    } catch (e: any) {
      notify(`Validation failed: ${e.message}`, { type: "error" });
    } finally {
      setValidating(false);
    }
  };

  const setStatus = async (status: string) => {
    try {
      await api.setRuleStatus(record.code, status);
      notify(`Rule is now ${status}`, { type: "success" });
      refresh();
    } catch (e: any) {
      notify(e.message, { type: "error" });
    }
  };

  if (!record) return null;

  const errors = report?.errors ?? [];
  const warnings = report?.warnings ?? [];

  return (
    <Stack spacing={2}>
      {/* Overview */}
      <Card>
        <CardContent>
          <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: "wrap", gap: 1 }}>
            <EntityField source="entity" />
            <SeverityField source="severity" />
            <StatusField source="status" />
            <Chip size="small" label={`v${record.version}`} />
            <Chip
              size="small"
              variant="outlined"
              label={record.lookback_days === 1
                ? "anchor day"
                : `T-${record.lookback_days - 1} to T`}
            />
            <Chip size="small" variant="outlined" label={record.origin} />
            <Chip
              size="small"
              label={
                record.validation_status === "valid" ? "VALIDATED"
                : record.validation_status === "invalid" ? "VALIDATION FAILED"
                : "NOT VALIDATED"
              }
              color={record.validation_status === "valid" ? "success"
                : record.validation_status === "invalid" ? "error" : "warning"}
            />
          </Stack>

          <Typography variant="h6" sx={{ mb: 0.5 }}>{record.code}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            {record.description}
          </Typography>

          <Stack direction="row" spacing={1} flexWrap="wrap" gap={1}>
            <Chip size="small" label={`file: ${record.source_file}`} />
            {record.declared_datasources?.length > 0 && (
              <Chip size="small" color="secondary"
                label={`reads: ${record.declared_datasources.join(", ")}`} />
            )}
            <Chip size="small" label={`by ${record.created_by}`} />
          </Stack>

          {isAdmin && (
            <>
              <Divider sx={{ my: 2 }} />
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" gap={1}>
                <Button size="small" startIcon={<FactCheckIcon />} onClick={runValidation}
                  disabled={validating}>
                  {validating ? "Validating…" : "Validate"}
                </Button>
                <Button
                  size="small"
                  startIcon={<PlayArrowIcon />}
                  onClick={() => setRunOpen(true)}
                >
                  Run now
                </Button>
                {record.status === "active" ? (
                  <Button size="small" color="warning" onClick={() => setStatus("paused")}>
                    Pause rule
                  </Button>
                ) : (
                  <Button size="small" variant="contained" disabled={record.validation_status !== "valid"}
                    onClick={() => setStatus("active")}>
                    Activate
                  </Button>
                )}
                {record.validation_status !== "valid" && record.status !== "active" && (
                  <Typography variant="caption" color="text.secondary">
                    Activation requires a passing validation.
                  </Typography>
                )}
              </Stack>
            </>
          )}
        </CardContent>
      </Card>

      {/* Validation report */}
      {(errors.length > 0 || warnings.length > 0) && (
        <Alert severity={errors.length ? "error" : "warning"}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
            {errors.length ? "Validation errors" : "Validation warnings"}
          </Typography>
          {errors.map((e: string) => (
            <Typography key={e} variant="body2">• {e}</Typography>
          ))}
          {warnings.map((w: string) => (
            <Typography key={w} variant="body2">• {w}</Typography>
          ))}
        </Alert>
      )}

      {/* Schedules */}
      <SchedulesSection ruleCode={record.code} isAdmin={isAdmin} />

      {/* Code */}
      <Card>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1 }}>Python source</Typography>
          {loading ? <CircularProgress size={24} /> : <CodeBlock code={source} />}
        </CardContent>
      </Card>

      {/* Version history */}
      {versions.length > 0 && (
        <Card>
          <CardContent>
            <Typography variant="h6" sx={{ mb: 1 }}>Version history</Typography>
            <Stack spacing={1}>
              {versions.map((v) => (
                <Box key={v.id}
                  sx={{ p: 1.25, borderRadius: 1.5, border: "1px solid #e9edf4",
                        bgcolor: v.version === record.version ? "#eef6ff" : "#fafbfe" }}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Chip size="small" label={`v${v.version}`}
                      color={v.version === record.version ? "primary" : "default"} />
                    <Typography variant="body2">{v.change_note || "(no note)"}</Typography>
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    {v.created_by} · {new Date(v.created_at).toLocaleString()}
                  </Typography>
                </Box>
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* Tabs: runs and alerts raised by this rule */}
      <RuleActivity ruleCode={record.code} />

      <RunRuleDialog rule={runOpen ? record : null} onClose={() => setRunOpen(false)} onDone={refresh} />
    </Stack>
  );
};

const RuleActivity = ({ ruleCode }: { ruleCode: string }) => {
  const [tab, setTab] = useState<"runs" | "tickets">("runs");
  return (
    <Card>
      <CardContent>
        <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
          {(["runs", "tickets"] as const).map((t) => (
            <Button key={t} size="small" variant={tab === t ? "contained" : "outlined"}
              onClick={() => setTab(t)} sx={{ textTransform: "capitalize" }}>
              {t === "runs" ? "Runs" : "Alerts raised"}
            </Button>
          ))}
        </Stack>
        {tab === "runs" ? (
          <Box sx={{ "& .RaDatagrid-root": { boxShadow: "none" } }}>
            <List
              resource="runs"
              perPage={10}
              filterDefaultValues={{ rule_code: ruleCode }}
            >
              <Datagrid bulkActionButtons={false} rowClick="show">
                <IdField source="id" />
                <TextField source="data_date" label="Data date" />
                <StatusField source="status" />
                <TextField source="summary" label="Summary" />
                <RowCountField source="rows" />
                <TextField source="triggered_by" label="Triggered by" />
              </Datagrid>
              <Pagination rowsPerPageOptions={[10, 25]} />
            </List>
          </Box>
        ) : (
          <Box>
            <TicketsForRule ruleCode={ruleCode} />
          </Box>
        )}
      </CardContent>
    </Card>
  );
};

const TicketsForRule = ({ ruleCode }: { ruleCode: string }) => {
  const [tickets, setTickets] = useState<any[] | null>(null);
  useEffect(() => {
    fetch(`/api/tickets?rule_code=${ruleCode}&perPage=50`, {
      headers: { Authorization: `Bearer ${localStorage.getItem("abm.token")}` },
    })
      .then((r) => r.json())
      .then((d) => setTickets(d.data ?? []))
      .catch(() => setTickets([]));
  }, [ruleCode]);

  if (!tickets) return <CircularProgress size={22} />;
  if (!tickets.length) {
    return <Typography variant="body2" color="text.secondary">This rule has not raised any alerts.</Typography>;
  }
  return (
    <Stack spacing={1}>
      {tickets.map((t) => (
        <Box key={t.id} sx={{ p: 1.25, border: "1px solid #e9edf4", borderRadius: 1.5 }}>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" gap={1}>
            <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 700 }}>
              {t.ticket_no}
            </Typography>
            <SeverityField source="severity" />
            <StatusField source="status" />
          </Stack>
          <Typography variant="body2" color="text.secondary">{t.summary}</Typography>
        </Box>
      ))}
    </Stack>
  );
};

export const RuleShow = () => (
  <ShowBase>
    <RuleDetails />
  </ShowBase>
);

/* ================================================================== */
/* The rules table - one row per rule, versions nested beneath          */
/* ================================================================== */
const SortableHeader = ({
  field, label, sort, setSort, sx,
}: {
  field: string;
  label: string;
  sort: { field: string; order: string };
  setSort: (s: any) => void;
  sx?: any;
}) => {
  const active = sort.field === field;
  const ascending = sort.order !== "DESC";
  return (
    <TableCell
      onClick={() => setSort({ field, order: active && !ascending ? "DESC" : "ASC" })}
      sx={{ cursor: "pointer", userSelect: "none", ...sx }}
    >
      {/* A flex Stack INSIDE the cell. Setting display:flex on a td would drop
          it out of the table layout and break column alignment. */}
      <Stack direction="row" spacing={0.25} alignItems="center" sx={{ width: "fit-content" }}>
        <span>{label}</span>
        <Box component="span" sx={{ display: "inline-flex", opacity: active ? 0.85 : 0.25 }}>
          {ascending ? (
            <ArrowUpwardIcon sx={{ fontSize: 14 }} />
          ) : (
            <ArrowDownwardIcon sx={{ fontSize: 14 }} />
          )}
        </Box>
      </Stack>
    </TableCell>
  );
};

const headCellSx = {
  fontWeight: 700,
  fontSize: FONT.label,
  textTransform: "uppercase" as const,
  letterSpacing: 0.5,
  color: "#5b6b82",
  backgroundColor: "#fafbfe",
  py: 1.25,
  whiteSpace: "nowrap" as const,
};

const RuleTable = () => {
  const { data, isPending, sort, setSort } = useListContext<any>();
  const { index: versionIndex, loading: versionsLoading } = useVersionIndex();

  /**
   * react-admin keeps sort as the string "field ORDER". Read it once, here,
   * instead of assuming an object - the same normalising the dataProvider does
   * on the way out, because the shape has been seen both ways.
   */
  const currentSort = useMemo(() => {
    const s: any = sort;
    if (typeof s === "string") {
      const [field, order] = s.split(" ");
      return { field: field ?? "", order: order ?? "ASC" };
    }
    return { field: s?.field ?? "", order: s?.order ?? "ASC" };
  }, [sort]);
  const { index: scheduleIndex } = useScheduleIndex();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [paged, setPaged] = useState<Record<string, VersionRow[]>>({});
  const [runFor, setRunFor] = useState<any>(null);
  const [deleteFor, setDeleteFor] = useState<any>(null);
  const [viewVersion, setViewVersion] = useState<{ code: string; version: number } | null>(null);
  const navigate = useNavigate();
  const refresh = useRefresh();
  const admin = isAdminUser();

  const rules: any[] = useMemo(() => data ?? [], [data]);

  /**
   * The shared index is capped at 500 rows. A rule whose history came back
   * shorter than its own version number was truncated, so fetch it properly -
   * a partial history must never render as a complete one.
   */
  useEffect(() => {
    if (versionsLoading) return;
    const missing = rules.filter(
      (r) => r.code && !paged[r.code] && (versionIndex[r.code]?.length ?? 0) < (r.version ?? 0),
    );
    if (!missing.length) return;
    let cancelled = false;
    Promise.all(
      missing.map((r) =>
        api
          .ruleVersions(r.code)
          .then((list: any[]) => [r.code, list] as const)
          .catch(() => [r.code, versionIndex[r.code] ?? []] as const),
      ),
    ).then((pairs) => {
      if (!cancelled) setPaged((prev) => ({ ...prev, ...Object.fromEntries(pairs) }));
    });
    return () => {
      cancelled = true;
    };
  }, [rules, versionIndex, versionsLoading, paged]);

  const historyFor = (rule: any): VersionRow[] => {
    const fromIndex = (versionIndex[rule.code] ?? []).filter((v) => !v.is_version_active);
    const fetched = paged[rule.code];
    if (!fetched?.length) return fromIndex;
    // /versions omits `code` and `severity`; backfill them from the index so a
    // repaired history is not missing values the index already knew.
    return fetched
      .filter((v) => !v.is_version_active)
      .map((v) => ({
        ...v,
        code: v.code ?? rule.code,
        severity:
          v.severity ?? fromIndex.find((i) => i.version === v.version)?.severity ?? "",
      }));
  };

  const toggle = (code: string) =>
    setExpanded((prev) => ({ ...prev, [code]: !prev[code] }));

  if (isPending) {
    return (
      <Box sx={{ p: 4, textAlign: "center" }}>
        <CircularProgress size={26} />
      </Box>
    );
  }

  if (!rules.length) {
    return (
      <Box sx={{ p: 6, textAlign: "center" }}>
        <Typography variant="body1" color="text.secondary">
          No rules match these filters.
        </Typography>
      </Box>
    );
  }

  const openShow = (code: string) => navigate(`/rules/${encodeURIComponent(code)}/show`);
  const openEdit = (code: string) => navigate(`/rules/${encodeURIComponent(code)}/edit`);

  return (
    <>
      <TableContainer>
        <Table size="small" sx={{ "& td, & th": { borderColor: "#eef1f6" } }}>
          <TableHead>
            <TableRow>
              <SortableHeader field="code" label="Rule" sort={currentSort} setSort={setSort} sx={headCellSx} />
              <SortableHeader field="severity" label="Severity" sort={currentSort} setSort={setSort}
                sx={{ ...headCellSx, width: 110 }} />
              <SortableHeader field="status" label="State" sort={currentSort} setSort={setSort}
                sx={{ ...headCellSx, width: 140 }} />
              <TableCell sx={{ ...headCellSx, width: 170 }}>Schedule</TableCell>
              <TableCell align="right" sx={{ ...headCellSx, width: 130 }}>
                Actions
              </TableCell>
            </TableRow>
          </TableHead>

          <TableBody>
            {rules.map((rule) => {
              const history = historyFor(rule);
              const open = Boolean(expanded[rule.code]);
              const totalVersions = history.length + 1;

              return [
                <TableRow
                  key={rule.code}
                  hover
                  onClick={() => openShow(rule.code)}
                  sx={{ cursor: "pointer", "& td": { py: 1.5 } }}
                >
                  {/* Rule */}
                  <TableCell sx={{ width: "38%" }}>
                    <Stack direction="row" spacing={0} alignItems="flex-start">
                      <Box
                        onClick={(e) => { e.stopPropagation(); toggle(rule.code); }}
                        role="button"
                        aria-label={open ? "Hide versions" : "Show versions"}
                        sx={{
                          width: 26, height: 26, mr: 0.5, display: "flex",
                          alignItems: "center", justifyContent: "center",
                          borderRadius: 1, cursor: "pointer", color: "text.secondary",
                          "&:hover": { bgcolor: "action.hover" },
                        }}
                      >
                        {open ? (
                          <ExpandMoreIcon fontSize="small" />
                        ) : (
                          <ChevronRightIcon fontSize="small" />
                        )}
                      </Box>

                      <Box sx={{ minWidth: 0 }}>
                        <Stack direction="row" spacing={0.75} alignItems="baseline">
                          <Typography
                            variant="body2"
                            sx={{ fontFamily: "monospace", fontWeight: 700 }}
                          >
                            {rule.code}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {rule.entity}
                          </Typography>
                          {totalVersions > 1 && (
                            <Typography variant="caption" color="text.secondary">
                              · {totalVersions} versions
                            </Typography>
                          )}
                        </Stack>
                        <Typography variant="body2" color="text.secondary" noWrap>
                          {rule.name}
                        </Typography>
                      </Box>
                    </Stack>
                  </TableCell>

                  {/* Severity */}
                  <TableCell>
                    <DotLabel value={rule.severity} tone={severityColor[rule.severity] ?? "default"} />
                  </TableCell>

                  {/* State */}
                  <TableCell>
                    <RuleStateCell record={rule} />
                  </TableCell>

                  {/* Schedule */}
                  <TableCell>
                    <ScheduleSummary schedules={scheduleIndex[rule.code] ?? []} />
                  </TableCell>

                  {/* Actions */}
                  <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                    <Stack direction="row" spacing={0} justifyContent="flex-end">
                      <ActionIcon
                        title="Run this rule now"
                        color="primary"
                        icon={<PlayArrowIcon fontSize="small" />}
                        onClick={() => setRunFor(rule)}
                      />
                      <ActionIcon
                        title={admin ? "Edit rule" : "Editing is admin only"}
                        icon={<EditIcon fontSize="small" />}
                        disabled={!admin}
                        onClick={() => openEdit(rule.code)}
                      />
                      <ActionIcon
                        title={admin ? "Delete rule" : "Deleting is admin only"}
                        icon={<DeleteOutlineIcon fontSize="small" />}
                        color="error"
                        disabled={!admin}
                        onClick={() => setDeleteFor(rule)}
                      />
                    </Stack>
                  </TableCell>
                </TableRow>,

                ...(open
                  ? history.map((v) => (
                      <TableRow
                        key={`${rule.code}-v${v.version}`}
                        sx={{ bgcolor: "#fafbfe", "&:hover": { bgcolor: "#f4f7fc" } }}
                      >
                        <TableCell sx={{ pl: 6, py: 1 }}>
                          <Stack direction="row" spacing={1} alignItems="baseline">
                            <Typography
                              variant="body2"
                              sx={{ fontFamily: "monospace", fontWeight: 700, color: "text.secondary" }}
                            >
                              v{v.version}
                            </Typography>
                            <Typography variant="body2" noWrap>
                              {v.change_note || "(no note)"}
                            </Typography>
                            <Typography variant="caption" color="text.secondary" noWrap>
                              · {v.created_by} · {new Date(v.created_at).toLocaleDateString()}
                            </Typography>
                          </Stack>
                        </TableCell>
                        <TableCell>
                          <DotLabel value={v.severity}
                            tone={severityColor[v.severity] ?? "default"} />
                        </TableCell>
                        <TableCell>
                          <RuleStateCell record={v} />
                        </TableCell>
                        <TableCell />
                        <TableCell align="right">
                          <Stack direction="row" spacing={0} justifyContent="flex-end">
                            <ActionIcon
                              title="View this version's source"
                              icon={<VisibilityIcon fontSize="small" />}
                              onClick={() =>
                                setViewVersion({ code: rule.code, version: v.version })
                              }
                            />
                          </Stack>
                        </TableCell>
                      </TableRow>
                    ))
                  : []),
              ];
            })}
          </TableBody>
        </Table>
      </TableContainer>

      <RunRuleDialog rule={runFor} onClose={() => setRunFor(null)} onDone={refresh} />
      <DeleteRuleDialog
        rule={deleteFor}
        onClose={() => setDeleteFor(null)}
        onDone={refresh}
      />
      <VersionSourceDialog
        code={viewVersion?.code ?? null}
        version={viewVersion?.version ?? null}
        onClose={() => setViewVersion(null)}
      />
    </>
  );
};

/** Search and filters live in the custom actions toolbar (see RuleToolbar). */
export const RuleList = () => (
  <List
    actions={<RuleToolbar />}
    sort={{ field: "code", order: "ASC" }}
    perPage={10}
    pagination={<Pagination rowsPerPageOptions={[10, 25, 50]} />}
  >
    <RuleTable />
  </List>
);

/* ================================================================== */
/* Edit - every save publishes a new draft version                     */
/* ================================================================== */
const RuleEditForm = () => {
  const record = useRecordContext<any>();
  const notify = useNotify();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState("");
  const [loadedSource, setLoadedSource] = useState("");
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({
    name: "",
    description: "",
    severity: "medium",
    lookback_days: 1,
    change_note: "",
  });

  useEffect(() => {
    if (!record?.code) return;
    setLoading(true);
    setForm({
      name: record.name ?? "",
      description: record.description ?? "",
      severity: record.severity ?? "medium",
      lookback_days: record.lookback_days ?? 1,
      change_note: "",
    });
    api.ruleSource(record.code)
      .then((r) => {
        setSource(r.source_code ?? "");
        setLoadedSource(r.source_code ?? "");
      })
      .catch((e: any) => notify(`Could not load source: ${e.message}`, { type: "error" }))
      .finally(() => setLoading(false));
  }, [record?.code]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!record) return null;
  const nextVersion = (record.version ?? 1) + 1;
  const dirty =
    form.name !== (record.name ?? "") ||
    form.description !== (record.description ?? "") ||
    form.severity !== record.severity ||
    form.lookback_days !== record.lookback_days ||
    source !== loadedSource;

  const save = async () => {
    setBusy(true);
    try {
      const updated = await api.updateRule(record.code, { ...form, source_code: source });
      notify(`${record.code} v${updated.version} published as draft - validate it to activate`, {
        type: "success",
      });
      navigate(`/rules/${encodeURIComponent(record.code)}/show`);
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack spacing={2}>
      <Alert severity="info">
        Saving publishes <strong>version {nextVersion}</strong> of{" "}
        <strong>{record.code}</strong> as a <strong>draft</strong> and resets its
        validation. The current version stays live until this one is validated and
        activated.
      </Alert>

      <Card>
        <CardContent>
          <Stack spacing={2}>
            <MuiTextField
              label="Name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              fullWidth
            />
            <MuiTextField
              label="Description"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              multiline
              minRows={2}
              fullWidth
            />
            <Stack direction="row" spacing={2}>
              <MuiTextField
                select
                label="Severity"
                value={form.severity}
                onChange={(e) => setForm({ ...form, severity: e.target.value })}
                sx={{ minWidth: 160 }}
              >
                {SEVERITY_OPTIONS.map((s) => (
                  <MenuItem key={s} value={s} sx={{ textTransform: "capitalize" }}>{s}</MenuItem>
                ))}
              </MuiTextField>
              <MuiTextField
                label="Lookback days"
                type="number"
                value={form.lookback_days}
                onChange={(e) =>
                  setForm({ ...form, lookback_days: Math.max(1, Number(e.target.value) || 1) })
                }
                sx={{ minWidth: 160 }}
                helperText="1 = the anchor day only"
              />
            </Stack>
            <MuiTextField
              label="What changed?"
              value={form.change_note}
              onChange={(e) => setForm({ ...form, change_note: e.target.value })}
              placeholder="e.g. tighten the last-hour limit"
              helperText="Shown in the version history."
              fullWidth
            />
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="h6">Python source</Typography>
            <Typography variant="caption" color="text.secondary">
              {record.source_file} &rarr; v{nextVersion} on save
            </Typography>
          </Stack>
          {loading ? (
            <CircularProgress size={24} />
          ) : (
            <Box
              component="textarea"
              value={source}
              onChange={(e: any) => setSource(e.target.value)}
              onKeyDown={(e: any) => {
                if (e.key === "Tab") {
                  e.preventDefault();
                  const el = e.target;
                  const { selectionStart, selectionEnd } = el;
                  const next = `${source.slice(0, selectionStart)}    ${source.slice(selectionEnd)}`;
                  setSource(next);
                  requestAnimationFrame(() => el.setSelectionRange(selectionStart + 4, selectionStart + 4));
                }
              }}
              spellCheck={false}
              rows={26}
              sx={{
                width: "100%",
                fontFamily: '"JetBrains Mono", Consolas, "Courier New", monospace',
                fontSize: FONT.bodyAlt,
                lineHeight: 1.6,
                p: 2,
                border: "1px solid #d9e0ea",
                borderRadius: 2,
                resize: "vertical",
                outline: "none",
                "&:focus": { borderColor: "primary.main" },
              }}
            />
          )}
        </CardContent>
      </Card>

      <Stack direction="row" spacing={1} justifyContent="flex-end">
        <Button
          onClick={() => navigate(`/rules/${encodeURIComponent(record.code)}/show`)}
          disabled={busy}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={save}
          disabled={busy || !dirty || !form.name.trim()}
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}
        >
          {busy ? "Publishing…" : `Publish v${nextVersion}`}
        </Button>
      </Stack>
    </Stack>
  );
};

export const RuleEdit = () => (
  <Edit mutationMode="pessimistic">
    <RuleEditForm />
  </Edit>
);