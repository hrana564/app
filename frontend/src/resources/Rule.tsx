import { useEffect, useState } from "react";
import {
  Datagrid, List, ListButton, Pagination, ShowBase, TextField, TopToolbar,
  useListContext, useNotify, useRecordContext, useRefresh,
} from "react-admin";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, Divider, Grid, IconButton,
  MenuItem, Stack, TextField as MuiTextField, Tooltip, Typography,
} from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import BugReportIcon from "@mui/icons-material/BugReport";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import ScheduleIcon from "@mui/icons-material/Schedule";
import { api } from "../dataProvider";
import { CadenceEditor, type CadenceConfig } from "../components/CadenceEditor";
import {
  CodeBlock, EntityField, IdField, JsonBlock, RowsTable, SeverityField,
  StatusField, RowCountField,
} from "../components/ui";

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

const RuleToolbar = () => {
  const { setFilters, filterValues } = useListContext<any>();
  const [q, setQ] = useState<string>(filterValues?.q ?? "");

  useEffect(() => {
    const handle = setTimeout(() => setFilters({ q: q || undefined }, "ignored"), 300);
    return () => clearTimeout(handle);
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <TopToolbar>
      <MuiTextField size="small" placeholder="Search rule code or name…" value={q}
        onChange={(e) => setQ(e.target.value)} sx={{ minWidth: 280 }} />
      <GenerateRuleDialog />
    </TopToolbar>
  );
};

const ValidationField = ({ source }: { source: string }) => {
  const record = useRecordContext<any>();
  const v = record?.[source];
  if (v === "valid") return <Chip size="small" label="validated" color="success" />;
  if (v === "invalid") return <Chip size="small" label="failed" color="error" />;
  return <Chip size="small" label="not validated" color="warning" />;
};

/**
 * Shows a rule's cadences in plain English, e.g. "Daily at 06:30" or
 * "Daily at 06:30 · Every 6 hours". The raw cron is never the primary label.
 */
const CadenceCell = () => {
  const record = useRecordContext<any>();
  const [schedules, setSchedules] = useState<any[]>([]);
  const [loadedFor, setLoadedFor] = useState<number | null>(null);

  useEffect(() => {
    if (!record?.code || loadedFor === record.code) return;
    setLoadedFor(record.code);
    fetch(`/api/rule-schedules?rule_code=${record.code}`, {
      headers: { Authorization: `Bearer ${localStorage.getItem("abm.token")}` },
    })
      .then((r) => r.json())
      .then((d) => setSchedules(d.data ?? []))
      .catch(() => setSchedules([]));
  }, [record?.id, loadedFor]);

  if (!schedules.length) {
    return <Typography variant="caption" color="text.secondary">manual only</Typography>;
  }
  return (
    <Stack spacing={0.25}>
      {schedules.slice(0, 3).map((s) => (
        <Stack key={s.id} direction="row" spacing={0.5} alignItems="center">
          <ScheduleIcon sx={{ fontSize: 14, color: "text.secondary" }} />
          <Typography variant="caption">{s.cadence_label}</Typography>
          {s.next_run_relative && (
            <Typography variant="caption" color="text.secondary">· {s.next_run_relative}</Typography>
          )}
        </Stack>
      ))}
      {schedules.length > 3 && (
        <Typography variant="caption" color="text.secondary">
          +{schedules.length - 3} more
        </Typography>
      )}
    </Stack>
  );
};

/* ================================================================== */
/* Test run dialog                                                     */
/* ================================================================== */
const TestRuleButton = () => {
  const record = useRecordContext<any>();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [dates, setDates] = useState<string[]>([]);
  const [day, setDay] = useState<string>("");
  const notify = useNotify();

  const openDialog = async () => {
    setOpen(true); setOut(null); setError(null);
    try {
      const res = await api.availableDates(record.entity);
      setDates(res.dates);
      if (res.dates.length) setDay(res.dates[res.dates.length - 1]);
    } catch (e: any) { setError(`Could not list dates: ${e.message}`); }
  };

  const run = async () => {
    setBusy(true); setError(null);
    try { setOut(await api.testRule(record.code, day, false)); }
    catch (e: any) { setError(e.message); notify(`Run failed: ${e.message}`, { type: "error" }); }
    finally { setBusy(false); }
  };

  return (
    <>
      <Button size="small" startIcon={<PlayArrowIcon />}
        onClick={(e) => { e.stopPropagation(); openDialog(); }}>
        Test run
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="lg" fullWidth>
        <DialogTitle>
          Test run — {record?.code}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Executes against real data. A failing run also raises a ticket.
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Stack direction="row" spacing={2} alignItems="center">
              <MuiTextField select size="small" label="Data date" value={day}
                onChange={(e) => setDay(e.target.value)} sx={{ minWidth: 200 }}>
                {dates.map((d) => <MenuItem key={d} value={d}>{d}</MenuItem>)}
              </MuiTextField>
              <Button variant="contained" onClick={run} disabled={busy || !day}
                startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}>
                Run
              </Button>
            </Stack>
            {error && <Alert severity="error">{error}</Alert>}
            {out && (
              <>
                <Stack direction="row" spacing={1} alignItems="center">
                  <StatusField source="status" />
                  <Typography variant="body2">{out.summary}</Typography>
                </Stack>
                {out.status === "error" && (
                  <Alert severity="error" icon={<BugReportIcon />}>
                    <Box component="pre" sx={{ m: 0, whiteSpace: "pre-wrap", fontSize: 12 }}>
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
              </>
            )}
          </Stack>
        </DialogContent>
        <DialogActions><Button onClick={() => setOpen(false)}>Close</Button></DialogActions>
      </Dialog>
    </>
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
  const [role, setRole] = useState<string>("");
  const notify = useNotify();
  const refresh = useRefresh();

  const isAdmin = role === "admin";

  useEffect(() => {
    setRole(JSON.parse(localStorage.getItem("abm.user") ?? "{}").role);
  }, []);

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
            <Chip size="small" variant="outlined" label={record.query_mode} />
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
                <TestRuleButton />
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

      {/* Alert policy */}
      <Card>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1.5 }}>Alert policy</Typography>
          <Grid container spacing={2}>
            <Grid size={{ xs: 12, sm: 3 }}>
              <Typography variant="caption" color="text.secondary">TICKETS PER RUN</Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>{record.ticket_mode}</Typography>
            </Grid>
            <Grid size={{ xs: 12, sm: 3 }}>
              <Typography variant="caption" color="text.secondary">GROUP BY</Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>{record.group_by || "—"}</Typography>
            </Grid>
            <Grid size={{ xs: 12, sm: 3 }}>
              <Typography variant="caption" color="text.secondary">DUPLICATE HANDLING</Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>{record.dedupe_mode}</Typography>
            </Grid>
            <Grid size={{ xs: 12, sm: 3 }}>
              <Typography variant="caption" color="text.secondary">ALERTING</Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {record.alerting_enabled ? "On" : "Muted"}
              </Typography>
            </Grid>
          </Grid>
          {record.dedupe_mode === "PER_DATA_DATE" && (
            <Alert severity="info" sx={{ mt: 2 }}>
              Repeat triggers against the same data date bump the existing ticket’s
              occurrence count rather than flooding the inbox. If an auditor has already
              acknowledged it, the next trigger opens a fresh ticket — a breach that is
              still happening should not hide on a closed ticket.
            </Alert>
          )}
        </CardContent>
      </Card>

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
            {t.occurrences > 1 && (
              <Chip size="small" color="warning" label={`seen ${t.occurrences}×`} />
            )}
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

/** Search lives in the custom actions toolbar (see RuleToolbar). */
export const RuleList = () => (
  <List actions={<RuleToolbar />} pagination={<Pagination rowsPerPageOptions={[10, 25, 50]} />}>
    <Datagrid rowClick="show" bulkActionButtons={false}>
      <TextField source="code" label="Code" />
      <EntityField source="entity" />
      <TextField source="name" label="Rule" />
      <SeverityField source="severity" />
      <ValidationField source="validation_status" />
      <CadenceCell />
      <StatusField source="status" />
      <TextField source="version" label="Ver" />
      <ListButton to="/rule-schedules">Schedules</ListButton>
      <TestRuleButton />
    </Datagrid>
  </List>
);