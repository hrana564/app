import { useMemo, useState } from "react";
import { useNotify, useRefresh } from "react-admin";
import { useNavigate } from "react-router";
import {
  Alert, Autocomplete, Box, Button, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, Menu, MenuItem, Stack,
  TextField as MuiTextField, Tooltip, Typography,
} from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import {
  api, type DatasourceOverview, type DatasourceRow, type RuleGenerateResponse,
} from "../../dataProvider";
import { CodeBlock } from "../../components/ui";
import { TEMPLATES, templatePrompt } from "../../components/rulePrompts";
import { can, usePermissions } from "../../permissions";
import { useEntityScope } from "../../entityScope";
import { ruleShowPath } from "../../rulePaths";
import { SEVERITY_OPTIONS } from "./options";

/* ================================================================== */
/* Rule toolbar: search + prompt-to-rule                               */
/* ================================================================== */

export const GenerateRuleDialog = () => {
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
        // The description came with the source, so it is saved with the rule.
        // The draft is born complete: a reviewer can read what the control
        // does before deciding whether it goes live.
        explanation: result.explanation ?? null,
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
