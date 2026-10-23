import { useEffect, useState } from "react";
import { Edit, useNotify, useRecordContext, useRequireAccess } from "react-admin";
import { useNavigate } from "react-router";
import {
  Alert, Box, Button, Card, CardContent, CircularProgress, MenuItem, Stack,
  TextField as MuiTextField, Typography,
} from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import { api, type ExplanationDraft, type RuleGenerateResponse, type SourceBinding } from "../../dataProvider";
import { DatasourcePicker } from "../../components/DatasourcePicker";
import { RuleDiff } from "../../components/RuleDiff";
import { RuleExplainer } from "../../components/RuleExplainer";
import { can, usePermissions } from "../../permissions";
import { ruleShowPath } from "../../rulePaths";
import { SEVERITY_OPTIONS } from "./options";

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
  // The description of the revision. The agent wrote it while the source was
  // still the one it had just written, so it travels with the source and the
  // draft's words always match the draft's code.
  const [explanation, setExplanation] = useState<ExplanationDraft | null>(null);
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
  // shows and what the agent is told cannot drift. The server reads the
  // candidate with the AST, which is the same answer the validator gets.
  const [binding, setBinding] = useState<SourceBinding | null>(null);
  useEffect(() => {
    let live = true;
    api
      .sourceBinding(working)
      .then((b) => live && setBinding(b))
      .catch(() => live && setBinding(null));
    return () => { live = false; };
  }, [working]);
  const selection = binding
    ? binding.unreadable
      ? binding.referenced
      : binding.declared
    : [];

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
    // The description of the revision, when the agent brought one. Saved with
    // the source, so the draft's words are always about the draft's code.
    explanation: revised ? explanation : null,
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
        setExplanation(g.explanation ?? null);
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
  //
  // No model is called here. The description of a rule is written when the
  // rule is written, so by the time this runs there is nothing left to write
  // and the operator is not waiting on a model to find out whether a control
  // went live.
  const publish = async () => {
    setBusy(true);
    try {
      // Publish the working draft. `workingDraftVersion` is the version an edit
      // lands on - the head when a rule was never published, otherwise the
      // candidate - and falls back to minting the next number only when there is
      // no draft. Coalescing null and undefined here matters: the server sends
      // `pending_version: null` for a never-published rule, and `null === undefined`
      // is false, which is what posted `{version: null}` and earned a 422.
      let version: number;
      if (changed || workingDraftVersion == null) {
        const saved = await api.updateRule(code, payload());
        version = Number(saved.version);
      } else {
        version = workingDraftVersion;
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

export const RuleEdit = () => {
  useRequireAccess({ resource: "rules", action: "edit" });
  return (
    <Edit mutationMode="pessimistic">
      <RuleEditForm />
    </Edit>
  );
};
