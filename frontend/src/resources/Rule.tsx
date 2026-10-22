import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  Datagrid, Edit, List, Pagination, ShowBase, TextField, TopToolbar,
  useListContext, useNotify, useRecordContext, useRefresh, useRequireAccess,
  useShowContext,
} from "react-admin";
import { useNavigate, useParams } from "react-router";
import {
  Alert, Autocomplete, Box, Button, ButtonBase, Card, CardContent, Chip,
  CircularProgress, Collapse,
  Dialog, DialogActions, DialogContent, DialogTitle, Divider, IconButton,
  Menu, MenuItem, Stack,
  TextField as MuiTextField, Tooltip, Typography,
} from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import PublishIcon from "@mui/icons-material/Publish";
import BugReportIcon from "@mui/icons-material/BugReport";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import EditIcon from "@mui/icons-material/Edit";
import HistoryIcon from "@mui/icons-material/History";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";
import ScheduleIcon from "@mui/icons-material/Schedule";
import {
  api, type DatasourceOverview, type DatasourceRow,
  type RuleGenerateResponse,
} from "../dataProvider";
import { CadenceEditor, type CadenceConfig } from "../components/CadenceEditor";
import { DatasourcePicker } from "../components/DatasourcePicker";
import { RuleDiff } from "../components/RuleDiff";
import { inspectBinding } from "../components/ruleSource";
import { RuleExplainer } from "../components/RuleExplainer";
import { TEMPLATES, templatePrompt } from "../components/rulePrompts";
import { RunOutcomeCell, RunStatusCell } from "./Run";
import {
  ActionIcon, CodeBlock, DotLabel, EntityField, fieldFromRecord, IdField, JsonBlock,
  RowsTable, SeverityChipField, StatusChipField, RowCountField,
} from "../components/ui";
import { TextLike, LIST_HEADER_SX, listTableSx } from "../components/listTable";
import { severityColor, statusColor, SEVERITIES_ASC, FONT } from "../theme";
import { can, usePermissions } from "../permissions";
import { useEntityScope } from "../entityScope";
import { ruleShowPath, ruleEditPath, versionFromParam } from "../rulePaths";

/* ================================================================== */
/* Filter vocabulary - mirrors the Literals in backend/app/schemas.py */
/* ================================================================== */
const STATUS_OPTIONS = ["draft", "active", "paused", "retired"] as const;
// Ascending here, worst-first in the inbox: a "set the severity of a new rule"
// dropdown should start at the least alarming option, because that is the
// starting position, not the interesting one.
const SEVERITY_OPTIONS = SEVERITIES_ASC;

/* ================================================================== */
/* Rule toolbar: search + prompt-to-rule                               */
/* ================================================================== */

/**
 * Ask for a rule, and get back one that has been run.
 *
 * The dialog has four states, and the order matters: what to build, the wait,
 * the review, and the failure. Nothing is written until the review.
 *
 * THE REVIEW IS NOT A FORMALITY
 * -----------------------------
 * The agent writes a rule, runs it against the bytes the estate actually
 * holds, and repairs it if it does not run. What comes back is the source and
 * the verdict on it - including how many exception rows it found, which is
 * the difference between a control that works and a control that is quiet.
 *
 * A rule that found nothing is not a failure, but it is worth a word: it may
 * be correct, or it may be too loose to catch anything. The screen says which
 * it cannot tell.
 */
const GenerateRuleDialog = () => {
  const perms = usePermissions();
  const mayGenerate = can(perms, "rules.generate");
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<"setup" | "working" | "review" | "failed">("setup");
  const [prompt, setPrompt] = useState("");
  const [tables, setTables] = useState<string[]>([]);
  const [lookback, setLookback] = useState(1);
  const [testDate, setTestDate] = useState("");
  const [starterMenu, setStarterMenu] = useState<HTMLElement | null>(null);
  const [overview, setOverview] = useState<DatasourceOverview | null>(null);
  const [result, setResult] = useState<RuleGenerateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [severity, setSeverity] = useState("medium");
  const notify = useNotify();
  const refresh = useRefresh();
  const navigate = useNavigate();

  // A generated rule belongs to the client the portal is scoped to. The model
  // is only shown that client's registered datasources, so a second selector here
  // would be asking the same question in two places with two answers.
  const { entity, entities } = useEntityScope();
  const current = entities.find((e) => e.code === entity);

  const openDialog = async () => {
    setOpen(true);
    setStep("setup");
    setResult(null);
    setError(null);
    setPrompt("");
    setTables([]);
    setLookback(1);
    setTestDate("");
    setName("");
    setCode("");
    setSeverity("medium");
    try {
      const s = await api.generateStatus();
      setConfigured(s.configured);
    } catch {
      setConfigured(false);
    }
    try {
      const ov = await api.datasourceOverview(entity);
      setOverview(ov);
      setTestDate(ov.data_date);
    } catch {
      setOverview(null);
    }
  };

  const generate = async () => {
    setBusy(true);
    setError(null);
    setStep("working");
    try {
      const g = await api.generateRule({
        prompt,
        entity,
        datasources: tables,
        lookback_days: lookback,
        data_date: testDate || undefined,
      });
      setResult(g);
      setName(g.name ?? "");
      setCode(g.suggested_code ?? "");
      setStep(g.ok ? "review" : "failed");
    } catch (e: any) {
      setError(e.message ?? String(e));
      setStep("failed");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!result?.source_code) return;
    setBusy(true);
    try {
      const created = await api.createRule({
        code,
        name: name || code,
        entity,
        description: result.description || prompt,
        severity,
        lookback_days: result.lookback_days,
        source_code: result.source_code,
        status: "draft",
        origin: "llm",
      });
      // The server prefixes the code with the client's code, so the code that
      // was typed is not always the code that was filed. Everything after
      // this point uses the one the server recorded.
      const filed = created.code;
      // The agent proved the rule runs; the validator proves it is allowed to.
      // Both are cheap, and a rule that has not been validated cannot be
      // activated - so it is validated now rather than leaving that to
      // somebody to remember.
      const report = await api.validateRule(filed);
      if (report?.ok === false) {
        notify(
          `${filed} was created, but validation found problems: ${report.errors?.join("; ") ?? "see the rule page"}`,
          { type: "warning" },
        );
      } else {
        notify(`${filed} created and validated as a draft - activate it when you are ready`, {
          type: "success",
        });
      }
      refresh();
      setOpen(false);
      // A brand new rule has exactly one version, so the page it lands on can
      // name it rather than making the doorway do it.
      navigate(ruleShowPath(filed, created.version));
    } catch (e: any) {
      notify(e.message ?? String(e), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const options = useMemo(
    () => overview?.registered ?? [],
    [overview],
  );

  // A table whose file has not landed cannot be tested, and the trial will
  // say so - but it is better to say it before the model has been paid for.
  const absent = useMemo(
    () => tables.filter((t) => options.find((o) => o.name === t)?.landing === "absent"),
    [tables, options],
  );

  const trial = result?.trial;

  // The button stays in place and says why, rather than disappearing: a
  // missing button is a question the user has to ask. The server refuses the
  // call either way - this is the screen agreeing with it in advance.
  if (!mayGenerate) {
    return (
      <Tooltip title="Generating a rule needs the rules.generate capability">
        <span>
          <Button variant="contained" startIcon={<AutoAwesomeIcon />} disabled>
            Create a rule
          </Button>
        </span>
      </Tooltip>
    );
  }

  return (
    <>
      <Button variant="contained" startIcon={<AutoAwesomeIcon />} onClick={openDialog}>
        Create a rule
      </Button>
      <Dialog open={open} onClose={() => !busy && setOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>Create a rule</DialogTitle>
        <DialogContent>
          {configured === false ? (
            <Alert severity="warning" sx={{ mt: 1 }}>
              No LLM endpoint configured. Set <code>LLM_BASE_URL</code>,{" "}
              <code>LLM_API_KEY</code> and <code>LLM_MODEL</code> in{" "}
              <code>backend/.env</code>, then restart the API. Any OpenAI-compatible
              endpoint works.
            </Alert>
          ) : step === "working" ? (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                <CircularProgress size={20} />
                <Typography variant="body1">
                  Writing the rule, then running it against real data
                </Typography>
              </Stack>
              <Typography variant="body2" sx={{ color: "text.secondary" }}>
                The agent checks its own work by executing the rule, and repairs it
                if it does not run. A reasoning model can take a minute or two for
                this - it is thinking, not hanging.
              </Typography>
            </Stack>
          ) : step === "review" && result ? (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <Alert severity={trial?.outcome === "fail" ? "warning" : "success"}>
                {trial?.outcome === "fail" ? (
                  <>
                    The rule ran and found{" "}
                    <strong>{trial.rows.length}</strong> exception row
                    {trial.rows.length === 1 ? "" : "s"} on{" "}
                    {result.data_date}.
                  </>
                ) : (
                  <>
                    The rule ran and found nothing on {result.data_date}. That may be
                    correct - but if the control should have caught something, the
                    logic is too loose.
                  </>
                )}
                {result.attempts.length > 1 && (
                  <>
                    {" "}It took <strong>{result.attempts.length}</strong> attempts
                    to get there.
                  </>
                )}
              </Alert>

              <Stack direction="row" spacing={2}>
                <MuiTextField label="Rule name" value={name} onChange={(e) => setName(e.target.value)} fullWidth />
                <MuiTextField label="Rule code" value={code} onChange={(e) => setCode(e.target.value)} fullWidth />
                <MuiTextField select label="Severity" value={severity}
                  onChange={(e) => setSeverity(e.target.value)} sx={{ minWidth: 140 }}>
                  {SEVERITY_OPTIONS.map((s) => (
                    <MenuItem key={s} value={s}>{s}</MenuItem>
                  ))}
                </MuiTextField>
              </Stack>

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

              {trial?.summary && (
                <Alert severity="info">{trial.summary}</Alert>
              )}

              {trial?.warnings?.length ? (
                <Alert severity="warning">
                  <strong>The rule tests values the data does not hold.</strong>{" "}
                  A wrong value matches nothing, so the control would find
                  nothing and still look clean. Check these before saving.
                  <Box component="ul" sx={{ mt: 0.5, mb: 0, pl: 2 }}>
                    {trial.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </Box>
                </Alert>
              ) : null}

              {result.trace?.length ? (
                <Box>
                  <Typography variant="caption" sx={{ fontWeight: 700 }}>
                    WHAT THE AGENT CHECKED
                  </Typography>
                  <Stack spacing={0.25} sx={{ mt: 0.5 }}>
                    {result.trace.map((step, i) => (
                      <Typography
                        key={`${step.tool}-${i}`}
                        variant="body2"
                        sx={{ color: "text.secondary" }}
                      >
                        <Box component="span" sx={{ fontFamily: "monospace" }}>
                          {step.tool}
                        </Box>
                        {step.summary ? ` — ${step.summary}` : ""}
                      </Typography>
                    ))}
                  </Stack>
                </Box>
              ) : null}

              <CodeBlock code={result.source_code ?? ""} />
            </Stack>
          ) : step === "failed" && result ? (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <Alert severity="error">
                <strong>No working rule was produced.</strong>{" "}
                {result.error}
              </Alert>
              {result.attempts.length > 0 && (
                <Box>
                  <Typography variant="caption" sx={{ fontWeight: 700 }}>
                    WHAT IT TRIED
                  </Typography>
                  <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                    {result.attempts.map((a) => (
                      <Typography key={a.n} variant="body2" sx={{ color: "text.secondary" }}>
                        Attempt {a.n}:{" "}
                        {a.ok ? "ran cleanly" : (a.error ?? "failed").split("\n")[0]}
                      </Typography>
                    ))}
                  </Stack>
                </Box>
              )}
              {result.source_code && (
                <>
                  <Typography variant="caption" sx={{ fontWeight: 700 }}>
                    THE LAST ATTEMPT
                  </Typography>
                  <CodeBlock code={result.source_code} />
                </>
              )}
              <Typography variant="body2" sx={{ color: "text.secondary" }}>
                Nothing was created. Try a narrower prompt, or add the datasource the
                rule needs to the selection.
              </Typography>
            </Stack>
          ) : (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <Alert severity="info">
                The rule will belong to{" "}
                <strong>{current?.name ?? entity}</strong> - the client the app
                bar is scoped to. Change it there to create for the other one.
              </Alert>

              <MuiTextField label="Describe the audit control" value={prompt}
                onChange={(e) => setPrompt(e.target.value)} multiline minRows={7} fullWidth
                placeholder="e.g. Flag POS transactions settled by an employee who exited the company in the last 30 days"
                helperText="Plain language. Say what should not happen, and what the finding should show. Anything in angle brackets is a question for you to answer." />

              <Stack
                direction="row"
                spacing={1}
                sx={{ alignItems: "center", flexWrap: "wrap", gap: 1 }}
              >
                <Button size="small" variant="outlined" startIcon={<AutoAwesomeIcon />}
                  onClick={(e) => setStarterMenu(e.currentTarget)}>
                  Start from a template
                </Button>
                <Menu anchorEl={starterMenu} open={Boolean(starterMenu)} onClose={() => setStarterMenu(null)}>
                  {TEMPLATES.map((t) => {
                    // A control that reads a feed the client has not registered
                    // cannot be written, so the template says which feed is
                    // missing rather than offering a prompt that will fail.
                    const missing = t.tables.filter(
                      (name) => !options.some((o) => o.name === name),
                    );
                    return (
                      <MenuItem
                        key={t.id}
                        disabled={missing.length > 0}
                        onClick={() => {
                          // The template brings its own tables: a control reads
                          // the data it is about, and the selection binds what
                          // the model may read.
                          setTables(t.tables);
                          setPrompt(
                            templatePrompt(
                              t.id,
                              t.tables.map((name) =>
                                options.find((o) => o.name === name),
                              ).filter((o): o is DatasourceRow => Boolean(o))
                                .map((o) => ({
                                  name: o.name,
                                  system: o.system,
                                  description: o.description,
                                  grain: o.grain,
                                  columns: o.columns ?? {},
                                })),
                            ),
                          );
                          setStarterMenu(null);
                        }}
                      >
                        <Box>
                          <Typography variant="body2">{t.label}</Typography>
                          <Typography
                            variant="caption"
                            sx={{ color: "text.secondary", display: "block" }}
                          >
                            {missing.length > 0
                              ? `needs ${missing.join(", ")} — not registered for this client`
                              : t.programme}
                          </Typography>
                        </Box>
                      </MenuItem>
                    );
                  })}
                </Menu>
                <Typography variant="caption" sx={{ color: "text.secondary" }}>
                  A template fills in what the catalogue already knows - the
                  table, and what one row in it is - and leaves you the
                  judgement.
                </Typography>
              </Stack>

              <Autocomplete multiple options={options}
                value={tables.map((n) => options.find((o) => o.name === n)).filter(Boolean) as DatasourceRow[]}
                onChange={(_, next) => setTables(next.map((o) => o.name))}
                getOptionLabel={(o) => o.name}
                isOptionEqualToValue={(a, b) => a.name === b.name}
                renderValue={(value, getItemProps) =>
                  value.map((option, i) => (
                    <Chip {...getItemProps({ index: i })} key={option.name} label={option.name}
                      size="small" variant="outlined" sx={{ fontFamily: "monospace" }} />
                  ))
                }
                renderOption={(props, option) => (
                  <li {...props} key={option.name}>
                    <Box>
                      <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 600 }}>
                        {option.name}
                      </Typography>
                      <Typography
                        variant="caption"
                        sx={{ color: "text.secondary", display: "block" }}
                      >
                        {option.grain || option.description}
                        {Object.keys(option.columns ?? {}).length
                          ? ` - ${Object.keys(option.columns).length} columns`
                          : ""}
                      </Typography>
                    </Box>
                  </li>
                )}
                renderInput={(params) => (
                  <MuiTextField {...params} size="small"
                    label="Datasources the rule may read"
                    placeholder="Select the tables this rule reads" />
                )}
              />

              <Stack direction="row" spacing={2} sx={{ alignItems: "flex-start" }}>
                <MuiTextField type="number" label="Lookback days" value={lookback}
                  onChange={(e) => setLookback(Math.max(1, Number(e.target.value) || 1))}
                  slotProps={{ htmlInput: { min: 1, max: 365 } }} sx={{ width: 160 }}
                  helperText={
                    lookback === 1
                      ? "That day alone"
                      : `T-${lookback - 1} to T, ${lookback} days staged per run`
                  }
                />
                <MuiTextField select label="Test on" value={testDate}
                  onChange={(e) => setTestDate(e.target.value)}
                  sx={{ width: 190 }}
                  helperText="The date the rule is run against">
                  {(overview?.dates ?? []).map((d) => (
                    <MenuItem key={d} value={d}>{d}</MenuItem>
                  ))}
                </MuiTextField>
                <Box sx={{ pt: 0.5 }}>
                  <Typography variant="caption" sx={{ color: "text.secondary", display: "block" }}>
                    {overview
                      ? `Tested against ${current?.name ?? entity} data for ${testDate}.`
                      : "Loading the available datasources..."}
                  </Typography>
                  {absent.length > 0 && (
                    <Alert severity="warning" sx={{ mt: 1 }}>
                      {absent.join(", ")} has not been delivered for the test date.
                      The rule cannot be tested until the ETL lands it.
                    </Alert>
                  )}
                </Box>
              </Stack>

              {error && <Alert severity="error">{error}</Alert>}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)} disabled={busy}>
            {step === "review" ? "Cancel" : "Close"}
          </Button>
          {step === "setup" && configured !== false && (
            <Button onClick={generate} variant="contained"
              disabled={busy || prompt.trim().length < 5 || tables.length === 0}
              startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}>
              Write and test the rule
            </Button>
          )}
          {step === "review" && (
            <Button onClick={save} variant="contained" disabled={busy || !code.trim()}>
              Save as draft
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </>
  );
};

/**
 * Search plus two dropdowns, held in one local object and pushed in a single
 * debounced effect. Two separate effects would race: typing in the search box
 * calls setFilters with just `q`, which drops the dropdowns.
 *
 * Two details are load-bearing. `setFilters` and `filterValues` are read through
 * refs because setFilters changes identity every time the store does - depending
 * on it re-runs this effect on every render. And the push is guarded by a real
 * comparison, because react-admin builds a fresh `filterValues` object on every
 * call even when nothing changed, so an unguarded call never converges and loops.
 *
 * There is no entity dropdown here. The client scope belongs to the app bar and
 * arrives as a permanent filter on <List>, so it cannot be cleared from here and
 * cannot disagree with the screen above it.
 */
const RuleToolbar = () => {
  const { setFilters, filterValues } = useListContext<any>();
  const [filters, setLocal] = useState({
    q: "",
    status: "",
    severity: "",
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
      {active && (
        <Button
          size="small"
          onClick={() => setLocal({ q: "", status: "", severity: "" })}
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
/* Shared index                                                        */
/* ================================================================== */
/**
 * Every rule's schedules, in ONE request, read synchronously per row.
 *
 * The alternative is a request inside each row, so ten rows mean ten round
 * trips.
 */
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

/**
 * The rule's cadence, on one line.
 *
 * A rule can carry several schedules - a daily run and an hourly one - and
 * this used to stack them, which made that one row twice the height of every
 * other and cost the table its rhythm. The list names the first and counts the
 * rest; the rule page lists them all.
 */
const ScheduleSummary = ({ schedules }: { schedules: any[] }) => {
  if (!schedules?.length) {
    return (
      <Typography variant="body2" sx={{ color: "text.secondary" }}>
        manual only
      </Typography>
    );
  }
  return (
    <Tooltip
      // The friendly cadences only. `frequency.py` is the only thing that knows
      // a cron expression, and users never see one - which is why the tooltip
      // says "Daily at 06:30 · Every 6 hours" and not the expression behind it.
      title={schedules.map((s) => s.cadence_label).join(" · ")}
      placement="top-start"
      slotProps={{ tooltip: { sx: { maxWidth: 420, whiteSpace: "normal" } } }}
    >
      <Typography variant="body2" noWrap>
        {schedules[0].cadence_label}
        {schedules.length > 1 && (
          <Typography component="span" variant="caption" sx={{ color: "text.secondary" }}>
            {" "}+{schedules.length - 1}
          </Typography>
        )}
      </Typography>
    </Tooltip>
  );
};

/* ================================================================== */
/* Run - asks first, then shows what happened                          */
/* ================================================================== */
const RunRuleDialog = ({
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
      setOut(await api.testRule(rule.code, day, rule.version));
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
        <Typography variant="body2" sx={{ mt: 0.5, color: "text.secondary" }}>
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
              a run. <strong>If it finds something, a finding is raised.</strong> It
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
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
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
                Exception lines ({out.rows?.length ?? 0}) · {out.duration_ms} ms
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
const DeleteRuleDialog = ({
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
/* Discard the working draft                                           */
/* ================================================================== */
/**
 * Remove the one draft an edit rewrites.
 *
 * A rule has at most one, and it is either a candidate beside a live version -
 * in which case only the draft goes - or the only version there is, when the
 * rule was never published. The latter is a delete, and the dialog says so.
 */
const DiscardDraftDialog = ({
  rule, onClose, onDone,
}: {
  rule: any | null;
  onClose: () => void;
  onDone: () => void;
}) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const notify = useNotify();

  useEffect(() => { setError(null); }, [rule]);

  if (!rule) return null;

  // A draft that is the head is the whole rule: there is nothing published for
  // it to sit beside.
  const wholeRule = Boolean(rule.is_version_active && rule.status === "draft");

  const discard = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.discardDraft(rule.code);
      notify(
        wholeRule
          ? `${rule.code} deleted - it had never been published`
          : `Draft v${rule.version} discarded`,
        { type: "success" },
      );
      onDone();
      onClose();
    } catch (e: any) {
      // The server refuses when the draft carries a finding; show its sentence.
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {wholeRule ? `Delete ${rule.code}?` : `Discard draft v${rule.version}?`}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {wholeRule ? (
            <Typography variant="body2">
              This rule was never published, so the draft is all there is.
              Discarding it removes the rule and the Python source file on disk.
              It cannot be undone.
            </Typography>
          ) : (
            <Typography variant="body2">
              The live version keeps running. Only draft{" "}
              <strong>v{rule.version}</strong> and its source file are removed.
            </Typography>
          )}

          {wholeRule && (
            <Alert severity="warning">
              Any manual test runs against this rule go with it. Nothing that
              raised a finding can be discarded.
            </Alert>
          )}

          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button
          variant="contained"
          color="error"
          onClick={discard}
          disabled={busy}
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <DeleteOutlinedIcon />}
        >
          {busy ? "Discarding…" : wholeRule ? "Delete rule" : "Discard draft"}
        </Button>
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

const SchedulesSection = ({ ruleCode, canWrite }: { ruleCode: string; canWrite: boolean }) => {
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
        <Stack
          direction="row"
          sx={{ mb: 1.5, justifyContent: "space-between", alignItems: "center" }}
        >
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <ScheduleIcon color="action" />
            <Typography variant="h6">Schedules</Typography>
            <Chip size="small" label={schedules?.length ?? 0} />
          </Stack>
          {canWrite && (
            <Button size="small" startIcon={<AddIcon />} onClick={() => setAdding(true)}>
              Add schedule
            </Button>
          )}
        </Stack>

        {!schedules && <CircularProgress size={22} />}

        {schedules?.length === 0 && (
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            No schedules. A rule with no schedule will only ever run when triggered manually.
          </Typography>
        )}

        <Stack spacing={1}>
          {schedules?.map((s) => (
            <Box key={s.id} sx={{ p: 1.5, border: "1px solid #e9edf4", borderRadius: 2, bgcolor: "#fcfdff" }}>
              <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
                <Box>
                  <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                    <Typography variant="body2" sx={{ fontWeight: 700 }}>{s.cadence_label}</Typography>
                    {s.label && s.label !== s.cadence_label && (
                      <Chip size="small" variant="outlined" label={s.label} />
                    )}
                    <Chip size="small" label={s.timezone} />
                    <Chip size="small" variant="outlined"
                      label={s.data_lag_days === 1 ? "yesterday's data" : `${s.data_lag_days}d lag`} />
                  </Stack>
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    next {s.next_run_relative ?? "unknown"}
                    {s.upcoming_runs?.[1] &&
                      ` · then ${new Date(s.upcoming_runs[1]).toLocaleString()}`}
                    {s.last_run_at && ` · last ran ${new Date(s.last_run_at).toLocaleString()}`}
                  </Typography>
                  <Typography variant="caption" sx={{ display: "block", color: "#8d99ae", fontFamily: "monospace" }}>
                    cron: {s.cron}
                  </Typography>
                </Box>
                {canWrite && (
                  <Stack direction="row" spacing={0.5}>
                    <Button size="small" onClick={() => setEditing(s)}>Edit</Button>
                    <Tooltip title="Remove schedule">
                      <IconButton size="small" onClick={() => remove(s.id)}>
                        <DeleteOutlinedIcon fontSize="small" />
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
const RuleDetails = ({ asked }: { asked?: number }) => {
  const record = useRecordContext<any>();
  const navigate = useNavigate();
  const [source, setSource] = useState("");
  const [versions, setVersions] = useState<any[]>([]);
  const [report, setReport] = useState<any>(null);
  const [validating, setValidating] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const notify = useNotify();
  const refresh = useRefresh();

  // Four separate questions, because they are four separate permissions.
  // Running a control is a read of the data; validating one writes a verdict
  // to its row; changing its lifecycle is a change to the estate; and only
  // the first of them is the auditor's.
  const perms = usePermissions();
  const mayTest = can(perms, "rules.test");
  const mayWrite = can(perms, "rules.write");
  const mayActivate = can(perms, "rules.activate");
  const mayWriteSchedules = can(perms, "schedules.write");

  // react-admin does not export a title hook, so set the document title directly.
  useEffect(() => {
    if (record?.code) document.title = `${record.code} · Audit Portal`;
  }, [record?.code]);

  const load = () => {
    if (!record?.id) return;
    Promise.all([
      api.ruleSource(record.code, record.version),
      api.ruleVersions(record.code),
      api.validationReport(record.code, record.version),
    ])
      .then(([s, v, r]) => {
        setSource(s.source_code ?? "");
        setVersions(v ?? []);
        setReport(r);
      })
      .catch(() => {});
  };

  useEffect(load, [record?.code, record?.version, record?.validation_status]);

  // `/rules/X/show` is a doorway, not a page: it lands on the head and replaces
  // the URL with the version it landed on, so the address bar always names the
  // row on screen. Links written before versions were in the URL - a ticket, a
  // run, a bookmark - therefore still work and end up canonical.
  useEffect(() => {
    if (asked == null && record?.code && record?.version) {
      navigate(ruleShowPath(record.code, record.version), { replace: true });
    }
  }, [asked, record?.code, record?.version, navigate]);

  const runValidation = async () => {
    setValidating(true);
    try {
      const r = await api.validateRule(record.code, record.version);
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

  /**
   * Put an old version back on the air.
   *
   * It is a copy forward, never a rewind: the server publishes the next version
   * number with this source, validates it, and only then activates it. The
   * version history keeps saying what actually happened.
   */
  const restore = async () => {
    setRestoring(true);
    try {
      const result = await api.rollbackRule(record.code, record.version);
      const next = result.rule;
      notify(
        result.activated
          ? `v${next.version} created from v${record.version} and activated`
          : `v${next.version} created from v${record.version}, but it did not validate - nothing was activated`,
        { type: result.activated ? "success" : "warning" },
      );
      setRestoreOpen(false);
      navigate(ruleShowPath(next.code, next.version));
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setRestoring(false);
    }
  };

  /**
   * Put a draft candidate on the air.
   *
   * The server validates it first and only promotes it if it passes, so a draft
   * that cannot read the estate leaves the live control untouched. The live
   * version stops running only once this succeeds.
   */
  const publishDraft = async () => {
    setPublishing(true);
    try {
      const outcome = await api.publishRule(record.code, record.version);
      if (!outcome.activated) {
        notify(
          `v${record.version} did not pass validation, so nothing went live: ` +
            `${outcome.validation?.errors?.join("; ") ?? "see the errors below"}`,
          { type: "warning" },
        );
        if (outcome.validation) setReport(outcome.validation);
        return;
      }
      // The words for the version that is now live. Written here, not left to a
      // page visit: a GET must not spend money at a model, and a page that fell
      // back to the code silently is exactly what this avoids.
      try {
        await api.generateExplanation(record.code, record.version);
      } catch (e: any) {
        notify(
          `v${record.version} is live, but its description could not be written: ${e.message}`,
          { type: "warning" },
        );
      }
      notify(`v${record.version} is now live`, { type: "success" });
      refresh();
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setPublishing(false);
    }
  };

  if (!record) return null;

  // The head is the version the scheduler runs. `status === "draft"` identifies
  // the working draft - the version an edit rewrites. There is normally exactly
  // one, whether it is the head (a rule never published, where the draft is all
  // there is) or a candidate beside the live version; taking the NEWEST draft
  // also resolves a rule an older build left with several.
  const headVersion =
    versions.find((v) => v.is_version_active)?.version ?? record.version;
  const liveVersion = versions.find((v) => v.is_version_active)?.version ?? null;
  const draftVersions = versions.filter((v) => v.status === "draft");
  const workingDraftVersion = draftVersions.length
    ? Math.max(...draftVersions.map((v) => v.version))
    : null;
  const isDraft = record.status === "draft" && record.version === workingDraftVersion;
  // A version that has ever been published carries active/paused/retired. Any
  // such row means something is on the air for a draft to sit beside.
  const hasPublished = versions.some((v) => v.status !== "draft");
  const isLive = record.is_version_active && !isDraft;
  // Everything else - a superseded version, or a stale draft head an older build
  // left behind - is a read-only record.
  const isRecord = !isDraft && !isLive;
  const draftHasLive = hasPublished;

  const errors = report?.errors ?? [];
  const warnings = report?.warnings ?? [];

  // One sentence, in the words a person uses. It replaces four chips -
  // `VALIDATED`, `v1`, `manual` and `anchor day` - which said the same things
  // in a vocabulary only the rule's author shared.
  const health = [
    record.status === "active"
      ? "Active"
      : record.status === "paused"
        ? "Paused"
        : record.status === "retired"
          ? "Retired"
          : "In draft",
    record.validation_status === "valid"
      ? "validated"
      : record.validation_status === "invalid"
        ? "failed validation"
        : "not yet validated",
    record.lookback_days === 1
      ? "reading the day's own data"
      : `reading the last ${record.lookback_days} days of data`,
  ].join(", ");

  return (
    // `minWidth: 0` is load-bearing. The tabs below embed react-admin <List>s,
    // whose tables refuse to be narrower than their columns; without this the
    // flex item's automatic minimum width lets that table set the width of the
    // whole page, and at 1366 the rule page scrolled sideways by 225px.
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      {/* Say which of the three this page is, before the title, so it is read
          before somebody presses something. */}
      {isRecord && (
        <Alert
          severity="info"
          action={
            headVersion !== record.version && (
              <Button
                color="inherit"
                size="small"
                onClick={() => navigate(ruleShowPath(record.code, headVersion))}
              >
                Open v{headVersion}
              </Button>
            )
          }
        >
          You are reading <strong>v{record.version}</strong>. v{headVersion} is the
          current version, and the scheduler runs that one. This page is a record
          of what v{record.version} did, and cannot be edited.
        </Alert>
      )}

      {isDraft && (
        <Alert
          severity="warning"
          action={
            draftHasLive && liveVersion !== null && (
              <Button
                color="inherit"
                size="small"
                onClick={() => navigate(ruleShowPath(record.code, liveVersion))}
              >
                Open v{liveVersion}
              </Button>
            )
          }
        >
          <strong>Draft v{record.version}.</strong>{" "}
          {draftHasLive
            ? `This is not the live control - v${liveVersion} keeps running until this is published.`
            : "Nothing is published yet, so this draft is the whole rule."}{" "}
          Editing rewrites this version; the number only moves when it goes live.
        </Alert>
      )}

      {/* The live version, when a replacement is waiting. The draft is the
          reason somebody is on this page, so it is said here rather than only
          on the version history lower down. */}
      {isLive && record.pending_version && (
        <Alert
          severity="info"
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => navigate(ruleShowPath(record.code, record.pending_version))}
            >
              Open the draft
            </Button>
          }
        >
          Draft <strong>v{record.pending_version}</strong> is waiting to be
          published. This version keeps running until it is.
        </Alert>
      )}

      {/* Overview */}
      <Card>
        <CardContent>
          <Stack
            direction="row"
            sx={{ alignItems: "flex-start", justifyContent: "space-between", gap: 2, flexWrap: "wrap" }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", gap: 1, mb: 0.75 }}>
                <Typography sx={{ fontSize: "1.375rem", fontWeight: 700, lineHeight: 1.2 }}>
                  {record.name || record.code}
                </Typography>
                <Chip size="small" variant="outlined" label={record.code} />
              </Stack>
              <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", flexWrap: "wrap", gap: 0.75 }}>
                <EntityField source="entity" />
                <SeverityChipField source="severity" record={record} />
                <StatusChipField source="status" record={record} />
                <Typography variant="body2" sx={{ color: "text.secondary" }}>
                  {health}
                </Typography>
              </Stack>
            </Box>

            {isRecord ? (
              mayActivate && (
                <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", gap: 1 }}>
                  <Button
                    size="small"
                    variant="contained"
                    startIcon={<HistoryIcon />}
                    onClick={() => setRestoreOpen(true)}
                  >
                    Restore v{record.version} as the current version
                  </Button>
                </Stack>
              )
            ) : isDraft ? (
              (mayTest || mayWrite || mayActivate) && (
                <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", gap: 1 }}>
                  {mayWrite && (
                    <Button size="small" startIcon={<EditIcon />}
                      onClick={() => navigate(ruleEditPath(record.code))}>
                      Edit
                    </Button>
                  )}
                  {mayActivate && (
                    <Button
                      size="small"
                      variant="contained"
                      startIcon={publishing ? <CircularProgress size={14} color="inherit" /> : <PublishIcon />}
                      onClick={publishDraft}
                      disabled={publishing}
                    >
                      {publishing ? "Publishing…" : `Publish v${record.version}`}
                    </Button>
                  )}
                  {mayWrite && (
                    <Button size="small" startIcon={<FactCheckIcon />} onClick={runValidation}
                      disabled={validating}>
                      {validating ? "Validating…" : "Validate"}
                    </Button>
                  )}
                  {mayTest && (
                    <Button
                      size="small"
                      startIcon={<PlayArrowIcon />}
                      onClick={() => setRunOpen(true)}
                    >
                      Run now
                    </Button>
                  )}
                  {mayWrite && (
                    <Button size="small" color="error" startIcon={<DeleteOutlinedIcon />}
                      onClick={() => setDiscardOpen(true)}>
                      Discard draft
                    </Button>
                  )}
                </Stack>
              )
            ) : (
              (mayTest || mayWrite || mayActivate) && (
                <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", gap: 1 }}>
                  {mayWrite && (
                    <Button size="small" startIcon={<FactCheckIcon />} onClick={runValidation}
                      disabled={validating}>
                      {validating ? "Validating…" : "Validate"}
                    </Button>
                  )}
                  {mayTest && (
                    <Button
                      size="small"
                      startIcon={<PlayArrowIcon />}
                      onClick={() => setRunOpen(true)}
                    >
                      Run now
                    </Button>
                  )}
                  {mayActivate &&
                    (record.status === "active" ? (
                      <Button size="small" color="warning" onClick={() => setStatus("paused")}>
                        Pause rule
                      </Button>
                    ) : (
                      <Button size="small" variant="contained" disabled={record.validation_status !== "valid"}
                        onClick={() => setStatus("active")}>
                        Activate
                      </Button>
                    ))}
                </Stack>
              )
            )}
          </Stack>

          {!isRecord && mayActivate && record.validation_status !== "valid" && record.status !== "active" && (
            <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 1 }}>
              Activation requires a passing validation.
            </Typography>
          )}

          {record.description && (
            <Typography sx={{ mt: 1.5, color: "text.secondary", fontSize: "0.9375rem", lineHeight: 1.5 }}>
              {record.description}
            </Typography>
          )}

          <RuleDatasources entity={record.entity} names={record.declared_datasources ?? []} />

          {/* Everything a maintainer needs and nobody else does. Off the page
              until asked for: a file name, a version and an author are not what
              a head of audit opened this screen to read. */}
          <TechnicalDetails record={record} />
        </CardContent>
      </Card>

      {/* Validation report */}
      {(errors.length > 0 || warnings.length > 0) && (
        <Alert severity={errors.length ? "error" : "warning"}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
            {errors.length ? "Validation errors" : "Validation warnings"}
          </Typography>
          {errors.map((e: string, i: number) => (
            <Typography key={`${i}-${e}`} variant="body2">• {e}</Typography>
          ))}
          {warnings.map((w: string, i: number) => (
            <Typography key={`${i}-${w}`} variant="body2">• {w}</Typography>
          ))}
        </Alert>
      )}

      {/* What it does, and the code it does it in */}
      <RuleExplainer
        ruleCode={record.code}
        version={record.version}
        source={source}
        mayWrite={mayWrite}
        mayReview={can(perms, "rules.explain_review")}
        readOnly={isRecord}
        onChanged={refresh}
      />

      {/* Schedules belong to the rule, not to one version, but a page about an
          old version is not the place to change when the control runs. */}
      <SchedulesSection ruleCode={record.code} canWrite={mayWriteSchedules && !isRecord} />

      {/* Version history */}
      {versions.length > 0 && (
        <Card>
          <CardContent>
            <Typography variant="h6" sx={{ mb: 1 }}>Version history</Typography>
            <Stack spacing={1}>
              {versions.map((v) => (
                // The version, not the row id: RuleVersionOut carries no id,
                // so `v.id` was undefined - which React reads as *no key at
                // all*, and warns about once per version. The warning is
                // harmless and the list still renders, which is why it went
                // unnoticed: a console error that changes nothing is easy to
                // mistake for noise from someone else's library.
                <Box
                  key={v.version}
                  role="link"
                  tabIndex={0}
                  onClick={() => navigate(ruleShowPath(record.code, v.version))}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      navigate(ruleShowPath(record.code, v.version));
                    }
                  }}
                  sx={{
                    p: 1.25, borderRadius: 1.5, border: "1px solid #e9edf4",
                    cursor: "pointer",
                    bgcolor: v.version === record.version ? "#eef6ff" : "#fafbfe",
                    "&:hover": { borderColor: "#c3d4ee" },
                  }}
                >
                  <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap" }}>
                    <Chip size="small" label={`v${v.version}`}
                      color={v.version === record.version ? "primary" : "default"} />
                    {v.is_version_active && (
                      <Chip size="small" variant="outlined" color="success" label="current" />
                    )}
                    {v.status === "draft" && (
                      <Chip size="small" variant="outlined" color="warning" label="draft" />
                    )}
                    <Typography variant="body2">{v.change_note || "(no note)"}</Typography>
                  </Stack>
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
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

      <DiscardDraftDialog
        rule={discardOpen ? record : null}
        onClose={() => setDiscardOpen(false)}
        onDone={refresh}
      />

      <Dialog open={restoreOpen} onClose={() => setRestoreOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Restore v{record.version} as the current version?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 1.5 }}>
            This copies v{record.version}'s code forward into a new version and
            puts it live. History is not rewritten: the version number only
            moves forward, and v{record.version} keeps its place in it.
          </Typography>
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            The copy is validated first. If the estate has moved since
            v{record.version} ran - a table it read may have been removed - it
            stays a draft and nothing is activated.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRestoreOpen(false)} disabled={restoring}>Cancel</Button>
          <Button variant="contained" onClick={restore} disabled={restoring}>
            {restoring ? "Restoring…" : `Create v${headVersion + 1} and activate it`}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};

/* ================================================================== */
/* The facts a maintainer needs, a reader does not                      */
/* ================================================================== */
/**
 * The file name, the version, who created it and what wrote it.
 *
 * These were chips across the top of the page, at the same weight as the
 * control's state and before the reader knew what the control checked. They
 * are not noise - they are how you find the file when a rule misbehaves - so
 * they are kept, one click away, in the order somebody debugging would want
 * them.
 */
const TechnicalDetails = ({ record }: { record: any }) => {
  const [open, setOpen] = useState(false);
  return (
    <Box sx={{ mt: 1.5 }}>
      <ButtonBase
        onClick={() => setOpen((o) => !o)}
        sx={{
          display: "inline-grid", gridTemplateColumns: "auto auto",
          columnGap: 0.5, alignItems: "center", px: 0.5, py: 0.25,
          borderRadius: 1, "&:hover": { bgcolor: "#f2f5fa" },
        }}
      >
        {open ? (
          <KeyboardArrowDownIcon sx={{ fontSize: 18, color: "text.secondary" }} />
        ) : (
          <KeyboardArrowRightIcon sx={{ fontSize: 18, color: "text.secondary" }} />
        )}
        <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 600 }}>
          Technical details
        </Typography>
      </ButtonBase>

      <Collapse in={open} timeout="auto" unmountOnExit>
        <Box
          sx={{
            mt: 0.75, p: 1.5, borderRadius: 1.5,
            bgcolor: "#fafbfe", border: "1px solid #e9edf4",
            display: "grid", rowGap: 0.75,
            gridTemplateColumns: { xs: "1fr", sm: "170px 1fr" },
            columnGap: 2, alignItems: "baseline",
          }}
        >
          {[
            ["Source file", record.source_file],
            ["Version", `v${record.version}${record.is_version_active ? " (current)" : ""}`],
            ["Created by", record.created_by || "—"],
            [
              "Written",
              record.origin === "llm" ? "by the rule assistant, read and saved by a person" : "by hand",
            ],
            ["Lookback", `${record.lookback_days} day(s)`],
            [
              "Last updated",
              record.updated_at ? new Date(record.updated_at).toLocaleString() : "—",
            ],
          ].map(([label, value]) => (
            <Box key={label} sx={{ display: "contents" }}>
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                {label}
              </Typography>
              <Typography variant="body2" sx={{ fontFamily: label === "Source file" ? "monospace" : undefined }}>
                {value}
              </Typography>
            </Box>
          ))}
        </Box>
      </Collapse>
    </Box>
  );
};

/* ================================================================== */
/* What this control reads                                              */
/* ================================================================== */
/**
 * The rule's inputs, one row per table, joined with the catalogue.
 *
 * `declared_datasources` is what the rule reads; the catalogue is what those
 * names currently resolve to. Neither is duplicated here - the join happens at
 * read time, so an admin who registers a system next month sees it appear on
 * this page without a migration.
 *
 * The system matters more than it looks. A cross-system control like
 * CA-22 joins the ERP to HRMS, and that fact was previously only
 * stated in the rule's Python docstring, where nobody looks.
 */
const RuleDatasources = ({ entity, names }: { entity: string; names: string[] }) => {
  const [registered, setRegistered] = useState<DatasourceRow[] | null>(null);
  const [failed, setFailed] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (!names.length) return;
    let live = true;
    api
      .datasourceOverview(entity)
      .then((o: DatasourceOverview) => live && setRegistered(o.registered ?? []))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [entity, names.length]);

  if (!names.length) {
    return (
      <Box sx={{ mt: 2 }}>
        <Divider sx={{ my: 1.5 }} />
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          This rule reads no data sources.
        </Typography>
      </Box>
    );
  }

  const missing = registered
    ? names.filter((n) => !registered.some((r) => r.name === n))
    : [];

  return (
    <Box sx={{ mt: 2 }}>
      <Divider sx={{ my: 1.5 }} />
      <Stack
        direction="row"
        sx={{ mb: 1, alignItems: "baseline", justifyContent: "space-between" }}
      >
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
          Data sources
          <Typography
            component="span"
            variant="caption"
            sx={{ ml: 1, fontWeight: 400, color: "text.secondary" }}
          >
            {names.length} table{names.length === 1 ? "" : "s"}
          </Typography>
        </Typography>
        <Button size="small" onClick={() => navigate("/datasources")}>
          Manage in Data Sources
        </Button>
      </Stack>

      {/* Three fixed columns rather than a flex row, so the systems line up and
          the eye can compare them down the page instead of re-reading each. */}
      <Box sx={{ display: "grid", rowGap: 0.5 }}>
        {names.map((name) => {
          const row = registered?.find((r) => r.name === name);
          return (
            <Box key={name}
              sx={{ display: "grid", alignItems: "baseline", columnGap: 2,
                    gridTemplateColumns: { xs: "1fr", sm: "200px 170px 1fr" } }}>
              <Typography variant="body2"
                sx={{ fontFamily: "monospace", fontWeight: 600, color: "primary.main" }}>
                {name}
              </Typography>
              <Typography variant="body2" sx={{ color: "text.secondary" }}>
                {registered === null ? "—" : row?.system || "—"}
              </Typography>
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                {registered === null
                  ? ""
                  : row?.grain || (row ? "" : "not registered")}
              </Typography>
            </Box>
          );
        })}
      </Box>

      {failed && (
        <Alert severity="warning" sx={{ mt: 1.5 }}>
          Could not load the datasource catalogue, so the systems above are
          blank. The table names are this rule&apos;s own declaration and are
          still correct.
        </Alert>
      )}

      {missing.length > 0 && (
        <Alert severity="error" sx={{ mt: 1.5 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
            {missing.length === 1 ? "1 table is" : `${missing.length} tables are`} no longer
            registered for {entity}
          </Typography>
          <Typography variant="body2" sx={{ mb: 1 }}>
            <strong>{missing.join(", ")}</strong> — this rule fails on its next
            run, not eventually. Remove it from the rule or register the table
            again.
          </Typography>
          <Button size="small" color="error" onClick={() => navigate("/datasources")}>
            Go to Data Sources
          </Button>
        </Alert>
      )}
    </Box>
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
              {t === "runs" ? "Runs" : "Findings raised"}
            </Button>
          ))}
        </Stack>
        {tab === "runs" ? (
          <Box sx={{ minWidth: 0, overflowX: "auto", contain: "inline-size", "& .RaDatagrid-root": { boxShadow: "none" } }}>
            {/* Pagination goes in as the `pagination` prop, not as a child.
                A <Pagination> child renders a second footer underneath the one
                the Datagrid already draws - two identical page pickers, stacked,
                which is what this tab showed. */}
            <List
              resource="runs"
              perPage={10}
              filterDefaultValues={{ rule_code: ruleCode }}
              pagination={<Pagination rowsPerPageOptions={[10, 25]} />}
              // The address is about this rule, and it names the version. A
              // list that syncs its filter, page and sort into the same address
              // overwrites that with another screen's state, so a tab inside a
              // page keeps its own.
              disableSyncWithLocation
            >
              <Datagrid bulkActionButtons={false} rowClick="show" size="small"
                sx={{
                  // The header is the list chrome, shared with every other
                  // list. The rows are not: this table deliberately does NOT
                  // use tableLayout:fixed, because pinning the columns leaves
                  // OBSERVATION a fixed box, which truncates the longest
                  // findings - and the finding is the reason anyone reads this
                  // table. Short rows leave a gap instead, which costs nothing.
                  "& .RaDatagrid-headerCell": LIST_HEADER_SX,
                  "& .RaDatagrid-rowCell": {
                    py: 0.75, px: 1.25, fontSize: FONT.body,
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                  },
                }}
              >
                <IdField source="id" label="#" />
                <TextField source="data_date" label="Data date" />
                {/* Two columns, because they answer different questions. One
                    column reading `status` showed a blue SUCCESS for a run that
                    found three breaches - a working control dressed as a clean
                    day, which is the one misreading the ledger split exists to
                    prevent. */}
                <RunStatusCell source="status" label="Rule Execution" />
                <RunOutcomeCell source="outcome" label="Outcome" />
                <TextField source="summary" label="Observation" />
                <RowCountField source="rows" />
                <TextField source="triggered_by" label="By" />
              </Datagrid>
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
    let live = true;
    api.listTickets({ rule_code: ruleCode, perPage: "50" })
      .then((d) => live && setTickets(d.data ?? []))
      .catch(() => live && setTickets([]));
    return () => { live = false; };
  }, [ruleCode]);

  if (!tickets) return <CircularProgress size={22} />;
  if (!tickets.length) {
    return <Typography variant="body2" sx={{ color: "text.secondary" }}>This rule has not raised any findings.</Typography>;
  }
  return (
    <Stack spacing={1}>
      {tickets.map((t) => (
        <Box key={t.id} sx={{ p: 1.25, border: "1px solid #e9edf4", borderRadius: 1.5 }}>
          <Stack
            direction="row"
            spacing={1}
            sx={{ alignItems: "center", flexWrap: "wrap", gap: 1 }}
          >
            <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 700 }}>
              {t.ticket_no}
            </Typography>
            <SeverityChipField source="severity" record={t} />
            <StatusChipField source="status" record={t} />
          </Stack>
          <Typography variant="body2" sx={{ color: "text.secondary" }}>{t.summary}</Typography>
        </Box>
      ))}
    </Stack>
  );
};

/**
 * A version that is not there.
 *
 * Left to react-admin, a bad version redirects to the rules list with no
 * explanation, and the reader is left wondering whether they mis-clicked. The
 * server knows exactly what is wrong - "it has v1 to v2" - so the page says it.
 */
const RuleVersionMissing = () => {
  const { id } = useParams();
  const { error } = useShowContext<any>();
  const navigate = useNavigate();
  const detail =
    // The server's refusal is more useful than "something went wrong": it says
    // which versions do exist.
    (error as any)?.body?.detail ??
    error?.message ??
    "This rule has no such version.";
  return (
    <Box sx={{ p: 3, maxWidth: 640 }}>
      <Typography variant="h6" sx={{ mb: 1 }}>That version does not exist</Typography>
      <Typography variant="body2" sx={{ color: "text.secondary", mb: 2 }}>
        {detail}
      </Typography>
      <Button variant="contained" onClick={() => navigate(ruleShowPath(id ?? ""))}>
        Open the current version
      </Button>
    </Box>
  );
};

export const RuleShow = () => {
  const { version } = useParams();
  const asked = versionFromParam(version);
  return (
    // `resource` because this page is reached by its own route, outside the
    // resource's generated ones - without it the show controller has no idea
    // which resource it is showing. `meta` is how the route segment reaches the
    // request: the provider turns it into `?version=n`, so the record on the
    // page IS the version in the URL.
    <ShowBase
      resource="rules"
      queryOptions={{ meta: asked ? { version: asked } : undefined }}
      error={<RuleVersionMissing />}
    >
      <RuleDetails asked={asked} />
    </ShowBase>
  );
};

/* ================================================================== */
/* The rules table - one row per rule; its versions are on its page     */
/* ================================================================== */
/**
 * Widths for the columns either side of the rule's name, which is the one
 * left unpinned so it absorbs whatever the window has left. The pinned widths
 * are the same on every list, because a column that resizes with the window is
 * a column the eye has to find again.
 */
const RULE_WIDTHS: Array<[number, number]> = [
  [1, 150], // the rule's code
  [3, 110], // severity
  [4, 110], // state
  [5, 130], // validation
  [6, 170], // schedule
  [7, 110], // actions
];

/**
 * What a rule row needs that is not on the rule.
 *
 * A `<Datagrid>` takes elements as children rather than a render function, so
 * a cell cannot be handed a prop by the component that holds the data. Two
 * things a row needs live outside it: the schedules, which arrive as one index
 * for the whole page rather than a request per row, and the two dialogs the
 * action buttons open, which belong to the table rather than to a row.
 */
const RuleRowContext = createContext<{
  schedules: Record<string, any[]>;
  run: (rule: any) => void;
  remove: (rule: any) => void;
}>({ schedules: {}, run: () => {}, remove: () => {} });

/** Severity, as the accent dot every other screen draws it with. */
const SeverityCell = fieldFromRecord(
  (value) => <DotLabel value={value} tone={severityColor[value] ?? "default"} />,
);

/**
 * The rule's lifecycle state, plus a marker when a draft is waiting.
 *
 * The state alone would read "active" while a replacement sits unpublished,
 * which is the one thing the reader needs to know: the thing on the air is not
 * the newest version.
 */
const StateCell = ({ source, label }: { source?: string; label?: string }) => {
  void source;
  void label;
  const record = useRecordContext<any>();
  return (
    <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
      <DotLabel value={record?.status} tone={statusColor[record?.status] ?? "default"} />
      {record?.pending_version ? (
        <Chip
          size="small"
          variant="outlined"
          color="warning"
          label={`draft v${record.pending_version}`}
          sx={{ height: 20, fontSize: FONT.label }}
        />
      ) : null}
    </Stack>
  );
};

/**
 * May this rule run at all? A second column because it is a second question:
 * an active rule that failed validation is a live control reading code the
 * validator refused, which one column reading "active" would hide.
 */
const validationLabel = (v?: string) =>
  v === "valid" ? "validated" : v === "invalid" ? "validation failed" : "not validated";

const validationTone = (v?: string) =>
  v === "valid" ? "success" : v === "invalid" ? "error" : "default";

const ValidationCell = fieldFromRecord((value) => (
  <DotLabel value={validationLabel(value)} tone={validationTone(value)} />
));

/**
 * The rule's schedules, read from the page's index rather than fetched per row.
 *
 * `label` is declared and ignored: the Datagrid titles the column from the
 * child element's props, not from anything the child renders.
 */
const ScheduleCell = ({ label }: { label?: string }) => {
  void label;
  const record = useRecordContext<any>();
  const { schedules } = useContext(RuleRowContext);
  return <ScheduleSummary schedules={schedules[record?.code] ?? []} />;
};

/**
 * Run, edit, delete. The click is stopped here so pressing an icon does not
 * also open the rule's page underneath it.
 */
const RuleActionsCell = ({ label }: { label?: string }) => {
  void label;
  const record = useRecordContext<any>();
  const perms = usePermissions();
  const { run, remove } = useContext(RuleRowContext);
  const navigate = useNavigate();
  const mayWrite = can(perms, "rules.write");
  const mayDelete = can(perms, "rules.delete");

  return (
    <Stack
      direction="row"
      spacing={0}
      sx={{ justifyContent: "flex-end" }}
      onClick={(e) => e.stopPropagation()}
    >
      <ActionIcon
        title="Run this rule now"
        color="primary"
        icon={<PlayArrowIcon fontSize="small" />}
        onClick={() => run(record)}
      />
      <ActionIcon
        title={mayWrite ? "Edit rule" : "Editing needs the rules.write capability"}
        icon={<EditIcon fontSize="small" />}
        disabled={!mayWrite}
        onClick={() => navigate(ruleEditPath(record.code))}
      />
      <ActionIcon
        title={mayDelete ? "Delete rule" : "Deleting needs the rules.delete capability"}
        icon={<DeleteOutlinedIcon fontSize="small" />}
        color="error"
        disabled={!mayDelete}
        onClick={() => remove(record)}
      />
    </Stack>
  );
};

const RuleTable = () => {
  const { index: schedules } = useScheduleIndex();
  const [runFor, setRunFor] = useState<any>(null);
  const [deleteFor, setDeleteFor] = useState<any>(null);
  const refresh = useRefresh();

  return (
    <>
      <RuleRowContext.Provider
        value={{ schedules, run: setRunFor, remove: setDeleteFor }}
      >
        <Datagrid
          // A rule page is addressed by code and version. Building the path
          // here is what keeps the list's own filter, page and sort out of it.
          rowClick={(_id, _resource, record) =>
            ruleShowPath(record.code, record.version)
          }
          bulkActionButtons={false}
          size="small"
          sx={listTableSx(RULE_WIDTHS)}
          empty={
            <Box sx={{ p: 6, textAlign: "center" }}>
              <Typography variant="body1" sx={{ color: "text.secondary" }}>
                No rules match these filters.
              </Typography>
            </Box>
          }
        >
          <TextLike source="code" label="Rule" mono />
          <TextLike source="name" label="Name" secondary />
          <SeverityCell source="severity" label="Severity" />
          <StateCell source="status" label="State" />
          <ValidationCell source="validation_status" label="Validation" />
          <ScheduleCell label="Schedule" />
          <RuleActionsCell label="Actions" />
        </Datagrid>
      </RuleRowContext.Provider>

      <RunRuleDialog rule={runFor} onClose={() => setRunFor(null)} onDone={refresh} />
      <DeleteRuleDialog
        rule={deleteFor}
        onClose={() => setDeleteFor(null)}
        onDone={refresh}
      />
    </>
  );
};

/** Search and filters live in the custom actions toolbar (see RuleToolbar). */
export const RuleList = () => {
  const { entity } = useEntityScope();
  return (
    <List
      actions={<RuleToolbar />}
      // The client scope is a permanent filter, so the toolbar's Clear button
      // cannot quietly drop it and leave the list showing both clients.
      filter={{ entity }}
      sort={{ field: "code", order: "ASC" }}
      pagination={<Pagination rowsPerPageOptions={[10, 25, 50, 100]} />}
    >
      <RuleTable />
    </List>
  );
};

/* ================================================================== */
/* Edit - every save publishes a new draft version                     */
/* ================================================================== */
const RuleEditForm = () => {
  const record = useRecordContext<any>();
  const notify = useNotify();
  const navigate = useNavigate();
  const perms = usePermissions();
  const mayPublish = can(perms, "rules.activate");

  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  // The live version's source, kept only as the left-hand side of the diff.
  const [liveSource, setLiveSource] = useState("");
  // What was on disk when the page opened - the candidate's starting point, and
  // the baseline for "has anything changed".
  const [loadedSource, setLoadedSource] = useState("");
  // The candidate. Only two things ever change it: the datasource picker and the
  // agent. There is no textarea, because the code is not the thing a person edits.
  const [working, setWorking] = useState("");
  const [request, setRequest] = useState("");
  const [revised, setRevised] = useState(false);
  const [step, setStep] = useState<"idle" | "working" | "review" | "failed">("idle");
  const [result, setResult] = useState<RuleGenerateResponse | null>(null);
  const [form, setForm] = useState({
    name: "",
    description: "",
    severity: "medium",
    lookback_days: 1,
    change_note: "",
  });

  const code = record?.code;

  useEffect(() => {
    if (!code) return;
    setLoading(true);
    setForm({
      name: record.name ?? "",
      description: record.description ?? "",
      severity: record.severity ?? "medium",
      lookback_days: record.lookback_days ?? 1,
      change_note: "",
    });
    // Start from the newest version: the draft being worked on if there is one,
    // otherwise the live code. The live source is fetched separately because it
    // is what the diff is measured against.
    const pending = record.pending_version as number | undefined;
    Promise.all([
      api.ruleSource(code),
      pending ? api.ruleSource(code, pending) : Promise.resolve(null),
    ])
      .then(([live, draft]) => {
        const base: string = draft?.source_code ?? live?.source_code ?? "";
        setLiveSource(live?.source_code ?? "");
        setLoadedSource(base);
        setWorking(base);
      })
      .catch((e: any) =>
        notify(`Could not load the rule's source: ${e.message}`, { type: "error" }),
      )
      .finally(() => setLoading(false));
  }, [code]); // eslint-disable-line react-hooks/exhaustive-deps

  // The tables the agent may read - taken from the candidate, so what the picker
  // shows and what the agent is told cannot drift.
  const binding = useMemo(() => inspectBinding(working), [working]);
  const selection = binding.unreadable ? binding.referenced : binding.declared;

  if (!record) return null;

  // The version an edit lands on. A published rule being rewritten has a draft
  // candidate; a rule that was never published IS a draft. Either way the save
  // rewrites that version. Only a published rule with no draft spends the next
  // number.
  const workingDraftVersion =
    (record.pending_version as number | undefined) ??
    (record.status === "draft" ? (record.version as number) : null);
  const createsNew = workingDraftVersion === null;
  const nextVersion = workingDraftVersion ?? ((record.version as number) + 1);
  const hasDraft = workingDraftVersion !== null;
  const changed =
    working !== loadedSource ||
    form.name !== (record.name ?? "") ||
    form.description !== (record.description ?? "") ||
    form.severity !== record.severity ||
    form.lookback_days !== record.lookback_days;

  const payload = () => ({
    ...form,
    source_code: working,
    change_note:
      form.change_note || request.trim().slice(0, 120) || "revised",
    // A version whose source the agent wrote is machine-written, whatever the
    // rule's first version was. A picker-only change leaves this unset, so the
    // row keeps saying the code was written by hand.
    origin: revised ? "llm" : undefined,
  });

  const rewrite = async () => {
    setBusy(true);
    setStep("working");
    setResult(null);
    try {
      const g = await api.reviseRule(code, {
        prompt: request,
        datasources: selection,
        lookback_days: form.lookback_days,
      });
      setResult(g);
      if (g.ok && g.source_code) {
        setWorking(g.source_code);
        setRevised(true);
      }
      setStep(g.ok ? "review" : "failed");
    } catch (e: any) {
      setResult(null);
      setStep("failed");
      notify(e.message ?? String(e), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    try {
      const saved = await api.updateRule(code, payload());
      notify(
        `${code} v${saved.version} saved as a draft - publish it to put it on the air`,
        { type: "success" },
      );
      navigate(ruleShowPath(code, saved.version));
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  // Publishing saves first if anything moved, then validates the draft and
  // promotes it. The server does the validating; if it fails the draft stays a
  // draft and nothing goes live, which is the point.
  const publish = async () => {
    setBusy(true);
    try {
      const pendingDraft = record.pending_version as number | undefined;
      let version: number;
      if (changed || pendingDraft === undefined) {
        const saved = await api.updateRule(code, payload());
        version = Number(saved.version);
      } else {
        version = pendingDraft;
      }
      const outcome = await api.publishRule(code, version);
      if (!outcome.activated) {
        notify(
          `v${version} did not pass validation, so nothing went live: ` +
            `${outcome.validation?.errors?.join("; ") ?? "see the rule page"}`,
          { type: "warning" },
        );
        navigate(ruleShowPath(code, version));
        return;
      }
      // The description of the version that is now live. Written here rather
      // than left to a page visit, because a GET must not spend money at a model
      // and a page that silently fell back to the code is the thing this avoids.
      try {
        await api.generateExplanation(code, version);
      } catch (e: any) {
        notify(
          `${code} v${version} is live, but its description could not be written: ${e.message}`,
          { type: "warning" },
        );
      }
      notify(`${code} v${version} is live`, { type: "success" });
      navigate(ruleShowPath(code, version));
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const trial = result?.trial;

  return (
    <Stack spacing={2}>
      <Alert severity="info">
        {createsNew ? (
          <>
            A change here creates <strong>draft version {nextVersion}</strong> of{" "}
            <strong>{record.code}</strong> and resets its validation.{" "}
            <strong>v{record.version} keeps running</strong> until the draft is
            published.
          </>
        ) : (
          <>
            A change here rewrites <strong>draft v{nextVersion}</strong> of{" "}
            <strong>{record.code}</strong> and resets its validation.{" "}
            {record.status === "draft" ? (
              "Nothing is published yet, so the version number does not move."
            ) : (
              <>
                <strong>v{record.version} keeps running</strong> until the draft
                is published.
              </>
            )}
          </>
        )}
      </Alert>

      {/* Read the control before changing it. Read-only: an old version's words
          are a record, and these are the live version's. */}
      {!loading && liveSource && (
        <RuleExplainer
          ruleCode={code}
          version={record.version}
          source={liveSource}
          mayWrite={false}
          mayReview={false}
          readOnly
        />
      )}

      <Card>
        <CardContent>
          <Stack spacing={2}>
            <MuiTextField
              label="Describe the change"
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              placeholder="e.g. also flag refunds over 500, and tighten the ticket window to one hour"
              helperText="Plain language. The assistant rewrites the rule to match - change only what you ask, and it leaves the rest alone."
              multiline
              minRows={4}
              fullWidth
            />
            <Stack direction="row" spacing={1.5} sx={{ alignItems: "center", flexWrap: "wrap" }}>
              <Button
                variant="contained"
                onClick={rewrite}
                disabled={busy || request.trim().length < 5 || selection.length === 0}
                startIcon={
                  busy && step === "working" ? (
                    <CircularProgress size={16} color="inherit" />
                  ) : (
                    <AutoAwesomeIcon />
                  )
                }
              >
                {busy && step === "working" ? "Rewriting…" : "Rewrite with the assistant"}
              </Button>
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                Reads the live data to settle anything the change depends on, then
                runs the result before offering it.
              </Typography>
            </Stack>

            {step === "review" && result && (
              <Stack spacing={1.5}>
                <Alert severity={trial?.outcome === "fail" ? "warning" : "success"}>
                  {trial?.outcome === "fail" ? (
                    <>
                      The rewritten rule ran and found{" "}
                      <strong>{trial.rows.length}</strong> exception row
                      {trial.rows.length === 1 ? "" : "s"} on {result.data_date}.
                    </>
                  ) : (
                    <>
                      The rewritten rule ran and found nothing on {result.data_date}.
                      That may be correct - but if the control should have caught
                      something, the change is too loose.
                    </>
                  )}
                </Alert>

                {trial?.warnings?.length ? (
                  <Alert severity="warning">
                    <strong>The rule tests values the data does not hold.</strong>{" "}
                    A wrong value matches nothing, so the control would find nothing
                    and still look clean.
                    <Box component="ul" sx={{ mt: 0.5, mb: 0, pl: 2 }}>
                      {trial.warnings.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </Box>
                  </Alert>
                ) : null}

                {result.trace?.length ? (
                  <Box>
                    <Typography variant="caption" sx={{ fontWeight: 700 }}>
                      WHAT THE ASSISTANT CHECKED
                    </Typography>
                    <Stack spacing={0.25} sx={{ mt: 0.5 }}>
                      {result.trace.map((s, i) => (
                        <Typography key={`${s.tool}-${i}`} variant="body2" sx={{ color: "text.secondary" }}>
                          <Box component="span" sx={{ fontFamily: "monospace" }}>
                            {s.tool}
                          </Box>
                          {s.summary ? ` — ${s.summary}` : ""}
                        </Typography>
                      ))}
                    </Stack>
                  </Box>
                ) : null}
              </Stack>
            )}

            {step === "failed" && (
              <Alert severity="error">
                <strong>No rewritten rule was produced.</strong>{" "}
                {result?.error ?? "The assistant could not be reached."}{" "}
                Nothing has changed - try a narrower request.
              </Alert>
            )}
          </Stack>
        </CardContent>
      </Card>

      {/* The binding is a decision; the code is the result. The picker writes
          DATASOURCES into the candidate, so it changes without the assistant. */}
      <Card>
        <CardContent>
          {loading ? (
            <CircularProgress size={22} />
          ) : (
            <>
              <DatasourcePicker
                entity={record.entity}
                source={working}
                onChange={(next) => setWorking(next)}
                disabled={busy}
              />
              <Typography variant="h6" sx={{ mt: 1, mb: 0.5 }}>
                What changed
              </Typography>
              <RuleDiff before={liveSource} after={working} />
            </>
          )}
        </CardContent>
      </Card>

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
                sx={{ minWidth: 200 }}
                helperText={
                  form.lookback_days === 1
                    ? "1 — the anchor day only"
                    : `${form.lookback_days} — T-${form.lookback_days - 1} to T, ${form.lookback_days} days of data staged per run`
                }
              />
            </Stack>
            <MuiTextField
              label="What changed?"
              value={form.change_note}
              onChange={(e) => setForm({ ...form, change_note: e.target.value })}
              placeholder="e.g. tighten the last-hour limit"
              helperText="Shown in the version history. Left blank, the request above is used."
              fullWidth
            />
          </Stack>
        </CardContent>
      </Card>

      <Stack direction="row" spacing={1} sx={{ justifyContent: "flex-end" }}>
        <Button onClick={() => navigate(ruleShowPath(record.code))} disabled={busy}>
          Cancel
        </Button>
        <Button onClick={save} disabled={busy || !changed}>
          {busy ? "Saving…" : `Save as draft v${nextVersion}`}
        </Button>
        {mayPublish && (
          <Button
            variant="contained"
            onClick={publish}
            disabled={busy || (!changed && !hasDraft)}
            startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}
          >
            {busy ? "Publishing…" : "Publish"}
          </Button>
        )}
      </Stack>
    </Stack>
  );
};

/**
 * The rule editor.
 *
 * Guarded on the route, not only on the button that leads to it. The list
 * disabled the edit icon for anyone without `rules.write`, but the URL was
 * open - so an auditor who typed it landed on a working form and only found
 * out on Save. `useRequireAccess` sends them to the access-denied page
 * instead, which says what happened rather than failing at the last moment.
 */
export const RuleEdit = () => {
  useRequireAccess({ resource: "rules", action: "edit" });
  return (
    <Edit mutationMode="pessimistic">
      <RuleEditForm />
    </Edit>
  );
};