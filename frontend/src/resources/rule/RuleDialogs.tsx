import { useEffect, useState } from "react";
import { useNotify } from "react-admin";
import {
  Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent,
  DialogTitle, MenuItem, Stack, TextField as MuiTextField, Typography,
} from "@mui/material";
import BugReportIcon from "@mui/icons-material/BugReport";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import { api } from "../../dataProvider";
import { DotLabel, JsonBlock, RowsTable } from "../../components/ui";
import { FONT, statusColor } from "../../theme";

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
/* Delete - typed confirmation                                         */
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
  const notify = useNotify();

  useEffect(() => {
    setTyped(""); setError(null);
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
      // A genuine failure - the rule vanished under us, the session expired -
      // is shown as it comes. The estate no longer refuses a delete.
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
            This removes the rule and all {rule.version} of its versions, the
            Python source files on disk, the runs it performed and the findings
            those runs raised. It cannot be undone.
          </Typography>

          <Alert severity="warning">
            A deletion takes the findings with it. To stop the control but keep
            its history, retire the rule instead.
          </Alert>

          <MuiTextField
            size="small"
            autoFocus
            label={`Type ${rule.code} to confirm`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={rule.code}
          />

          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button
          variant="contained"
          color="error"
          onClick={remove}
          disabled={busy || !confirmed}
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}
        >
          Delete
        </Button>
      </DialogActions>
    </Dialog>
  );
};

/* ================================================================== */
/* Discard the working draft                                           */
/* ================================================================== */
export const DiscardDraftDialog = ({
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
