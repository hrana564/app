import { useEffect, useState } from "react";
import {
  Datagrid, EditButton, List, Pagination, ShowBase,
  TextField, TopToolbar, useListContext, useNotify, useRecordContext, useRefresh,
} from "react-admin";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, FormControl, InputLabel,
  MenuItem, Stack, TextField as MuiTextField, Typography,
  // MuiTextField for plain inputs; react-admin's TextField needs a `source`.
} from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import BugReportIcon from "@mui/icons-material/BugReport";
import { api } from "../dataProvider";
import {
  CodeBlock, EntityChip, EntityField, JsonBlock, RowsTable, SeverityChip,
  SeverityField, StatusChip, StatusField,
} from "../components/ui";

/* ------------------------------------------------------------------ */
/* Generate a rule from a natural-language prompt (admin only)         */
/* ------------------------------------------------------------------ */
const GenerateRuleDialog = () => {
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
      const status = await api.generateStatus();
      setConfigured(status.configured);
    } catch {
      setConfigured(false);
    }
  };

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      const generated = await api.generateRule(prompt, entity);
      setResult(generated);
      notify(`Draft rule ${generated.rule.code} created - review before activating`, {
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
              No LLM endpoint configured. Set <code>LLM_BASE_URL</code>, <code>LLM_API_KEY</code>{" "}
              and <code>LLM_MODEL</code> in <code>backend/.env</code>, then restart the API.
              Any OpenAI-compatible endpoint works (Azure AI Foundry, Azure OpenAI, Ollama,
              MiniMax…).
            </Alert>
          ) : result ? (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <Alert severity="success">
                Draft <strong>{result.rule.code}</strong> created with status{" "}
                <strong>draft</strong>. Read the code, run it against a real day, then
                promote to <strong>active</strong> when you are satisfied.
              </Alert>
              <Box>
                <Typography variant="caption" sx={{ fontWeight: 700 }}>DATASOURCES USED</Typography>
                <Stack direction="row" spacing={1} sx={{ mt: 0.5, flexWrap: "wrap", gap: 0.5 }}>
                  {result.datasources_used.length === 0
                    ? <Chip size="small" label="none detected" />
                    : result.datasources_used.map((d: string) => (
                        <Chip key={d} size="small" label={d} color="secondary" />
                      ))}
                </Stack>
              </Box>
              <CodeBlock code={result.source_code} />
            </Stack>
          ) : (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <FormControl fullWidth>
                <InputLabel>Client entity</InputLabel>
                <MuiTextField
                  select
                  label="Client entity"
                  value={entity}
                  onChange={(e) => setEntity(e.target.value)}
                >
                  <MenuItem value="LAD">Laduree (iPOS SaaS, Azure Synapse, HRMS)</MenuItem>
                  <MenuItem value="ABM">Al Boom Marine (iPOS, HRMS, Business Central)</MenuItem>
                </MuiTextField>
              </FormControl>
              <MuiTextField
                label="Describe the audit control"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                multiline
                minRows={4}
                fullWidth
                placeholder="e.g. Flag POS transactions settled by an employee who has exited the company in the last 30 days"
                helperText="Plain language. The model only sees the datasources registered for the selected entity, so it cannot invent columns."
              />
              {error && <Alert severity="error">{error}</Alert>}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Close</Button>
          {!result && configured !== false && (
            <Button
              onClick={generate}
              variant="contained"
              disabled={busy || prompt.trim().length < 5}
              startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}
            >
              Generate draft
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </>
  );
};

/* ------------------------------------------------------------------ */
/* Dry-run a rule against a chosen date                                */
/* ------------------------------------------------------------------ */
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
    setOpen(true);
    setOut(null);
    setError(null);
    try {
      const res = await api.availableDates(record.entity);
      setDates(res.dates);
      if (res.dates.length) setDay(res.dates[res.dates.length - 1]);
    } catch (e: any) {
      setError(`Could not list dates: ${e.message}`);
    }
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.testRule(record.id, day, false);
      setOut(result);
    } catch (e: any) {
      setError(e.message ?? String(e));
      notify(`Run failed: ${e.message}`, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button size="small" startIcon={<PlayArrowIcon />} onClick={(e) => { e.stopPropagation(); openDialog(); }}>
        Test run
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="lg" fullWidth>
        <DialogTitle>
          Test run - {record?.code}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Executes against a copy of the real data. A failing run also raises a ticket.
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Stack direction="row" spacing={2} alignItems="center">
              <FormControl sx={{ minWidth: 200 }}>
                <InputLabel>Data date</InputLabel>
                <MuiTextField
                  select
                  label="Data date"
                  value={day}
                  onChange={(e) => setDay(e.target.value)}
                >
                  {dates.map((d) => <MenuItem key={d} value={d}>{d}</MenuItem>)}
                </MuiTextField>
              </FormControl>
              <Button
                variant="contained"
                onClick={run}
                disabled={busy || !day}
                startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}
              >
                Run
              </Button>
            </Stack>

            {error && <Alert severity="error">{error}</Alert>}

            {out && (
              <>
                <Stack direction="row" spacing={1} alignItems="center">
                  <StatusChip value={out.status} />
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
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

/* ------------------------------------------------------------------ */
/* List                                                                */
/* ------------------------------------------------------------------ */
/**
 * SearchInput cannot stand alone in react-admin - it reads the registered
 * filter names, so it must be wrapped in a Filter.
 */
/**
 * This list overrides `actions`, which replaces react-admin's whole toolbar -
 * including the filter bar. So the search is driven explicitly against the
 * list context rather than relying on <SearchInput>.
 */
const RuleToolbar = () => {
  const { setFilters, filterValues } = useListContext<any>();
  const [q, setQ] = useState<string>(filterValues?.q ?? "");

  useEffect(() => {
    const handle = setTimeout(() => {
      setFilters({ q: q || undefined }, "ignored");
    }, 300);
    return () => clearTimeout(handle);
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <TopToolbar>
      <MuiTextField
        size="small"
        placeholder="Search rule code or name…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        sx={{ minWidth: 280 }}
      />
      <GenerateRuleDialog />
    </TopToolbar>
  );
};

/** Search lives in the custom actions toolbar (see RuleToolbar). */
export const RuleList = () => (
  <List
    actions={<RuleToolbar />}
    pagination={<Pagination rowsPerPageOptions={[10, 25, 50]} />}
  >
    <Datagrid rowClick="show" bulkActionButtons={false}>
      <TextField source="code" label="Code" />
      <EntityField source="entity" />
      <TextField source="name" label="Rule" />
      <SeverityField source="severity" />
      <TextField source="query_mode" label="Mode" />
      <StatusField source="status" />
      <TextField source="current_version" label="v" />
      <TestRuleButton />
      <EditButton />
    </Datagrid>
  </List>
);

/* ------------------------------------------------------------------ */
/* Show                                                                */
/* ------------------------------------------------------------------ */
const RuleDetails = () => {
  const record = useRecordContext<any>();
  const [source, setSource] = useState<string>("");
  const [versions, setVersions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    if (!record?.id) return;
    setLoading(true);
    Promise.all([api.ruleSource(record.id), api.ruleVersions(record.id)])
      .then(([s, v]) => {
        setSource(s.source_code ?? "");
        setVersions(v ?? []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(load, [record?.id, record?.current_version]);

  if (!record) return null;

  return (
    <Box>
      <Card>
        <CardContent>
          <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: "wrap", gap: 1 }}>
            <EntityChip value={record.entity} />
            <SeverityChip value={record.severity} />
            <StatusChip value={record.status} />
            <Chip size="small" variant="outlined" label={record.query_mode} />
            <Chip size="small" variant="outlined" label={`v${record.current_version}`} />
            <Chip size="small" variant="outlined" label={record.origin} />
          </Stack>
          <Typography variant="h6" sx={{ mb: 0.5 }}>{record.code}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            {record.description}
          </Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" gap={1}>
            <Chip size="small" label={`file: ${record.source_file}`} />
            {record.datasource_hint && (
              <Chip size="small" color="secondary" label={`reads: ${record.datasource_hint}`} />
            )}
            <Chip size="small" label={`by ${record.created_by}`} />
          </Stack>
        </CardContent>
      </Card>

      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="h6">Python source</Typography>
            <TestRuleButton />
          </Stack>
          {loading ? <CircularProgress size={24} /> : <CodeBlock code={source} />}
        </CardContent>
      </Card>

      {versions.length > 0 && (
        <Card sx={{ mt: 2 }}>
          <CardContent>
            <Typography variant="h6" sx={{ mb: 1 }}>Version history</Typography>
            <Stack spacing={1}>
              {versions.map((v) => (
                <Box
                  key={v.id}
                  sx={{ p: 1.25, bgcolor: v.version === record.current_version ? "#eef6ff" : "#fafbfe",
                        borderRadius: 1.5, border: "1px solid #e9edf4" }}
                >
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Chip size="small" label={`v${v.version}`} color={v.version === record.current_version ? "primary" : "default"} />
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
    </Box>
  );
};

export const RuleShow = () => (
  <ShowBase>
    <RuleDetails />
  </ShowBase>
);

export { GenerateRuleDialog };