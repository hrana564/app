import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Box, Button, ButtonBase, Card, CardContent, CircularProgress,
  Collapse, Dialog, DialogActions, DialogContent, DialogTitle, Divider,
  LinearProgress, Stack, TextField, Tooltip, Typography,
} from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import CodeIcon from "@mui/icons-material/Code";
import EditNoteIcon from "@mui/icons-material/EditNote";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import ReportProblemIcon from "@mui/icons-material/ReportProblem";
import VerifiedIcon from "@mui/icons-material/Verified";
import { useNotify } from "react-admin";
import { api, type RuleExplanation } from "../dataProvider";
import { PythonBlock } from "./ui";
import { FONT } from "../theme";

/**
 * What a rule does, for the person who is answerable for it.
 *
 * The page used to open with sixty lines of Python and a row of chips naming
 * `manual`, `T-1 to T` and `anchor day`. This replaces that with the two things
 * a head of audit actually needs, side by side:
 *
 *   left   what the control checks, in sentences, and the condition that
 *          raises an exception - stated first, because that is the control;
 *   right  the code, collapsed, so it is available for checking a claim and
 *          not in the way of reading one.
 *
 * The words are derived from the file on every request, and a model may reword
 * them only after the server's gates have passed. So the page can say where a
 * sentence came from, which is the difference between a summary and a claim.
 */
export const RuleExplainer = ({
  ruleCode,
  version,
  source,
  mayWrite,
  mayReview,
  readOnly,
  onChanged,
}: {
  ruleCode: string;
  /** The version row being read. Every call below is about that one row. */
  version?: number;
  source: string;
  mayWrite: boolean;
  mayReview: boolean;
  /** An older version: its words are a record, not something to change. */
  readOnly?: boolean;
  onChanged?: () => void;
}) => {
  const [data, setData] = useState<RuleExplanation | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<"generate" | "review" | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [showCode, setShowCode] = useState(false);
  const [active, setActive] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const codeRef = useRef<HTMLDivElement | null>(null);
  const notify = useNotify();

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .explanation(ruleCode, version)
      .then((d) => live && setData(d))
      .catch((e) => live && setFailed(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [ruleCode, version]);

  const lineCount = useMemo(() => (source || "").split("\n").length, [source]);
  const activeStep = data?.steps.find((s) => s.n === active) ?? null;
  const highlight =
    activeStep && activeStep.lines.length === 2
      ? ([activeStep.lines[0], activeStep.lines[1]] as [number, number])
      : null;

  const pick = (n: number) => {
    setActive((current) => (current === n ? null : n));
    // Opening the code is the point of picking a step: the reader asked to see
    // where this sentence comes from, so the answer has to be on screen.
    setShowCode(true);
    window.setTimeout(() => {
      const row = codeRef.current?.querySelector(`[data-line="${data?.steps.find((s) => s.n === n)?.lines[0] ?? 1}"]`);
      row?.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 60);
  };

  const generate = async () => {
    setWorking("generate");
    try {
      const next = await api.generateExplanation(ruleCode, version);
      setData(next);
      notify(
        next.origin === "ai"
          ? "Description written and checked"
          : "No model answered, so the description was read from the code",
        { type: next.origin === "ai" ? "success" : "info" },
      );
      onChanged?.();
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setWorking(null);
    }
  };

  const review = async (approved: boolean) => {
    setWorking("review");
    try {
      setData(await api.reviewExplanation(ruleCode, approved, "", version));
      notify(approved ? "Description signed off" : "Sign-off withdrawn", {
        type: "success",
      });
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setWorking(null);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent>
          <CircularProgress size={22} />
        </CardContent>
      </Card>
    );
  }

  if (failed || !data) {
    return (
      <Alert severity="warning">
        The description could not be loaded: {failed ?? "no answer"}
      </Alert>
    );
  }

  return (
    <Card>
      <CardContent sx={{ pb: 1.5 }}>
        {/* The label names what is behind it. "More" and "Advanced" are doors
            nobody opens, because they say nothing about what is on the other
            side - and this one is opened by people who came here to check a
            claim, not to browse. */}
        <Box
          sx={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            gap: 2, mb: 1.25, flexWrap: "wrap",
          }}
        >
          <Typography
            variant="caption"
            sx={{
              fontWeight: 700, letterSpacing: 0.6,
              textTransform: "uppercase", color: "text.secondary",
            }}
          >
            What this control does
          </Typography>
          <Button
            size="small"
            startIcon={showCode ? <KeyboardArrowDownIcon /> : <KeyboardArrowRightIcon />}
            endIcon={<CodeIcon sx={{ fontSize: 16 }} />}
            onClick={() => setShowCode((s) => !s)}
            sx={{ textTransform: "none" }}
          >
            {showCode
              ? "Hide the code"
              : `Show the code behind this description (${lineCount} lines)`}
          </Button>
        </Box>

        <Box
          sx={{
            display: "grid",
            // One column until the code is asked for. The split only exists
            // when it earns its keep - which is when a reader is comparing a
            // sentence against the lines it came from. A permanently reserved
            // second column is mostly whitespace.
            gridTemplateColumns: {
              xs: "1fr",
              md: showCode ? "minmax(0, 1.15fr) minmax(0, 1fr)" : "1fr",
            },
            columnGap: 3,
            rowGap: 2,
            alignItems: "start",
            transition: "grid-template-columns 120ms ease",
          }}
        >
          {/* ---------------------------------------------------------- */}
          {/* What it does                                               */}
          {/* ---------------------------------------------------------- */}
          <Box data-pane="read">
            {!data.breach_condition && data.purpose && (
              <Typography sx={{ fontSize: "1.0625rem", lineHeight: 1.55, fontWeight: 500, mb: 1.5 }}>
                {data.purpose}
              </Typography>
            )}

            {/* The one sentence that matters most, given the most weight: what
                makes this control raise anything at all. */}
            {data.breach_condition && (
              <Box
                sx={{
                  mt: 1.75, p: 1.5, borderRadius: 1.5,
                  bgcolor: "#fff6e9", border: "1px solid #f3d9b5",
                  display: "grid", gridTemplateColumns: "auto 1fr", columnGap: 1.25,
                }}
              >
                <ReportProblemIcon sx={{ color: "#a15c00", fontSize: 20, mt: 0.15 }} />
                <Box>
                  <Typography
                    variant="caption"
                    sx={{ fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase", color: "#8a4b00" }}
                  >
                    Raises an exception when
                  </Typography>
                  <Typography sx={{ fontSize: "0.9688rem", lineHeight: 1.5, color: "#4a2c00" }}>
                    {stripLead(data.breach_condition)}
                  </Typography>
                </Box>
              </Box>
            )}

            {/* The steps, one per thing the rule actually does. Each is a
                button because each points at a range of the code. */}
            <Box sx={{ mt: 2 }}>
              <Typography
                variant="caption"
                sx={{
                  display: "block", fontWeight: 700, letterSpacing: 0.6,
                  textTransform: "uppercase", color: "text.secondary", mb: 0.75,
                }}
              >
                What it does, step by step
              </Typography>
              <Stack spacing={0.25}>
                {data.steps.map((step) => {
                  const on = step.n === active;
                  return (
                    <ButtonBase
                      key={step.n}
                      data-step={step.n}
                      onClick={() => pick(step.n)}
                      sx={{
                        display: "grid",
                        gridTemplateColumns: "1.75rem 1fr auto",
                        columnGap: 1.25,
                        alignItems: "start",
                        textAlign: "left",
                        px: 1, py: 0.75,
                        borderRadius: 1.25,
                        bgcolor: on ? "#eef6ff" : "transparent",
                        "&:hover": { bgcolor: on ? "#eef6ff" : "#f5f8fc" },
                      }}
                    >
                      <Box
                        sx={{
                          width: 22, height: 22, borderRadius: "50%",
                          display: "grid", placeItems: "center",
                          fontSize: FONT.label, fontWeight: 700,
                          bgcolor: on ? "primary.main" : "#e8eef7",
                          color: on ? "#fff" : "text.secondary",
                        }}
                      >
                        {step.n}
                      </Box>
                      <Typography
                        sx={{
                          fontSize: "0.9375rem", lineHeight: 1.5,
                          color: step.explainable ? "text.primary" : "text.secondary",
                          fontStyle: step.explainable ? "normal" : "italic",
                        }}
                      >
                        {step.text}
                      </Typography>
                      <Typography
                        variant="caption"
                        sx={{ color: on ? "primary.main" : "text.disabled", fontVariantNumeric: "tabular-nums", mt: 0.25 }}
                      >
                        {step.lines.length === 2
                          ? step.lines[0] === step.lines[1]
                            ? `line ${step.lines[0]}`
                            : `lines ${step.lines[0]}–${step.lines[1]}`
                          : ""}
                      </Typography>
                    </ButtonBase>
                  );
                })}
              </Stack>
              <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 0.75, ml: 1 }}>
                Pick a step to see the lines of code it came from.
              </Typography>
            </Box>

            {/* The numbers the control is tuned with. The constant name is kept
                as a caption rather than a label: the value is what a reader
                decides on, and the identifier is what a maintainer edits. */}
            {data.limits.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Typography
                  variant="caption"
                  sx={{
                    display: "block", fontWeight: 700, letterSpacing: 0.6,
                    textTransform: "uppercase", color: "text.secondary", mb: 0.75,
                  }}
                >
                  The limits it applies
                </Typography>
                <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1 }}>
                  {data.limits.map((limit) => (
                    <Tooltip key={limit.constant} title={`Set by ${limit.constant} in the rule file`}>
                      <Box
                        sx={{
                          px: 1.25, py: 0.75, borderRadius: 1.25,
                          bgcolor: "#f7f9fc", border: "1px solid #e9edf4",
                        }}
                      >
                        <Typography sx={{ fontSize: "1rem", fontWeight: 700, lineHeight: 1.2 }}>
                          {limit.label}
                        </Typography>
                        <Typography variant="caption" sx={{ color: "text.secondary" }}>
                          {limit.constant}
                        </Typography>
                      </Box>
                    </Tooltip>
                  ))}
                </Stack>
              </Box>
            )}
          </Box>

          {/* ---------------------------------------------------------- */}
          {/* The code, only when it has been asked for                   */}
          {/* ---------------------------------------------------------- */}
          <Collapse in={showCode} timeout="auto" unmountOnExit sx={{ gridColumn: { xs: "auto", md: "2" } }}>
            <Box ref={codeRef} data-pane="code">
              {source ? (
                <PythonBlock code={source} highlight={highlight} />
              ) : (
                <Alert severity="warning">The source file could not be read.</Alert>
              )}
            </Box>
          </Collapse>
        </Box>
      </CardContent>

      <Divider />

      {/* ---------------------------------------------------------- */}
      {/* Who says so                                                */}
      {/* ---------------------------------------------------------- */}
      <Box
        sx={{
          display: "flex", flexWrap: "wrap", gap: 1.5,
          alignItems: "center", justifyContent: "space-between",
          px: 2, py: 1.25, bgcolor: "#fbfcfe",
          borderBottomLeftRadius: 8, borderBottomRightRadius: 8,
        }}
      >
        <Provenance data={data} />

        <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 1 }}>
          {mayWrite && !readOnly && (
            <Button
              size="small"
              startIcon={working === "generate" ? <CircularProgress size={14} /> : <AutoAwesomeIcon />}
              onClick={generate}
              disabled={working !== null}
            >
              {working === "generate" ? "Writing…" : data.stored ? "Rewrite in plain English" : "Write in plain English"}
            </Button>
          )}
          {mayWrite && !readOnly && data.stored && (
            <Button size="small" startIcon={<EditNoteIcon />} onClick={() => setEditing(true)}
              disabled={working !== null}>
              Edit the wording
            </Button>
          )}
          {mayReview && !readOnly && data.stored && (
            data.reviewed_by ? (
              <Button size="small" color="inherit" onClick={() => review(false)}
                disabled={working !== null}>
                Withdraw sign-off
              </Button>
            ) : (
              <Button size="small" variant="outlined" startIcon={<VerifiedIcon />}
                onClick={() => review(true)} disabled={working !== null || data.stale}>
                Sign off
              </Button>
            )
          )}
        </Stack>
      </Box>

      {working === "generate" && <LinearProgress />}

      <EditDialog
        open={editing}
        onClose={() => setEditing(false)}
        data={data}
        ruleCode={ruleCode}
        version={version}
        onSaved={(next) => {
          setData(next);
          setEditing(false);
          notify("Wording saved", { type: "success" });
        }}
      />
    </Card>
  );
};

/* ------------------------------------------------------------------ */
/* Where the words came from                                           */
/* ------------------------------------------------------------------ *//**
 * The condition without the frame the page already supplies.
 *
 * `breach_condition` is a sentence so that anything else reading the API gets
 * one, and the callout above it already says "Raises an exception when". Two
 * frames stacked reads as a stutter, so the known lead-ins are dropped here -
 * both the model's, which the prompt fixes, and the derivation's, which is
 * written by `explain.py`.
 */
const stripLead = (text: string) =>
  text
    .replace(
      /^(?:(?:the|this) control )?raises? an exception (?:when|if)\s+/i,
      "",
    )
    .replace(/^(?:the day is flagged when|a finding is raised when)\s+/i, "")
    // Only when the word is an ordinary one: "An employee" becomes "an
    // employee", while "VAT" and "IBAN" are left exactly as written.
    .replace(/^([A-Z][a-z])/, (m) => m.toLowerCase());

/**
 * Three states, said out loud.
 *
 * "AI generated" on its own tells a reviewer nothing about whether anybody has
 * looked at it, and an unreviewed machine-written account of a control is the
 * one thing an auditor must not mistake for a signed-off one. So the page says
 * which of the three it is, and a model change resets the sign-off rather than
 * inheriting it.
 */
const Provenance = ({ data }: { data: RuleExplanation }) => {
  const when = data.generated_at ? new Date(data.generated_at).toLocaleDateString() : "";
  const reviewed = data.reviewed_at ? new Date(data.reviewed_at).toLocaleDateString() : "";

  let text: string;
  let tone: "ok" | "warn" | "plain" = "plain";

  if (data.stale) {
    text =
      "The code has changed since this was written, so these words are a fresh " +
      "reading of the code rather than the stored description.";
    tone = "warn";
  } else if (!data.stored && data.origin === "derived") {
    text =
      "Read directly from the code. No model was used and nobody has signed " +
      "this off.";
  } else if (data.origin === "ai" && data.reviewed_by) {
    text = `Written by an AI model${data.model ? ` (${data.model})` : ""} and signed off by ${data.reviewed_by} on ${reviewed}.`;
    tone = "ok";
  } else if (data.origin === "ai") {
    text = `Written by an AI model${data.model ? ` (${data.model})` : ""} on ${when}. Checked against the code, not yet signed off by a person.`;
    tone = "warn";
  } else if (data.origin === "edited") {
    text = `Written by ${data.generated_by || "a person"} on ${when}.`;
    tone = "ok";
  } else {
    text = "Read directly from the code. Not yet signed off by a person.";
  }

  const dot = tone === "ok" ? "#2e7d32" : tone === "warn" ? "#b26a00" : "#8a97ab";

  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: "flex-start", maxWidth: { xs: "100%", md: "58%" } }}>
      <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: dot, mt: 0.75, flexShrink: 0 }} />
      <Box>
        <Typography variant="caption" sx={{ color: "text.secondary", lineHeight: 1.45, display: "block" }}>
          {text}
        </Typography>
        {data.gates.some((g) => !g.ok) && (
          <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 0.25 }}>
            The last machine-written attempt was not used:{" "}
            {data.gates.filter((g) => !g.ok).map((g) => `${g.name} (${g.detail})`).join("; ")}
          </Typography>
        )}
      </Box>
    </Stack>
  );
};

/* ------------------------------------------------------------------ */
/* Correcting the words                                                */
/* ------------------------------------------------------------------ */
const EditDialog = ({
  open,
  onClose,
  data,
  ruleCode,
  version,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  data: RuleExplanation;
  ruleCode: string;
  version?: number;
  onSaved: (next: RuleExplanation) => void;
}) => {
  const notify = useNotify();
  const [breach, setBreach] = useState(data.breach_condition);
  const [steps, setSteps] = useState(data.steps.map((s) => ({ n: s.n, text: s.text })));
  const [saving, setSaving] = useState(false);

  // Reopening the dialog after a rewrite must show the current words, not the
  // ones that were on screen when it was first mounted.
  useEffect(() => {
    setBreach(data.breach_condition);
    setSteps(data.steps.map((s) => ({ n: s.n, text: s.text })));
  }, [data.breach_condition, data.steps]);

  const save = async () => {
    setSaving(true);
    try {
      onSaved(await api.editExplanation(ruleCode, { breach_condition: breach, steps }, version));
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Edit the description</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ color: "text.secondary", mb: 2 }}>
          Your words replace the model's, and the page will say a person wrote
          them. The line references stay tied to the code, and this clears any
          sign-off, because a review was given to the previous text.
        </Typography>
        <TextField
          label="What makes this control raise an exception"
          value={breach}
          onChange={(e) => setBreach(e.target.value)}
          fullWidth
          multiline
          minRows={2}
          sx={{ mb: 2 }}
        />
        <Stack spacing={1.5}>
          {data.steps.map((step, i) => (
            <Box key={step.n}>
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                Step {step.n} · lines {step.lines[0]}–{step.lines[1]}
              </Typography>
              <TextField
                value={steps[i]?.text ?? ""}
                onChange={(e) =>
                  setSteps((all) =>
                    all.map((s) => (s.n === step.n ? { ...s, text: e.target.value } : s)),
                  )
                }
                fullWidth
                multiline
                size="small"
              />
            </Box>
          ))}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button onClick={save} variant="contained" disabled={saving || !breach.trim()}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
