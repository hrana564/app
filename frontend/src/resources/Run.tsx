import { useEffect } from "react";
import {
  Datagrid, List, Pagination, ShowBase,
  useNotify, useRecordContext,
} from "react-admin";
import {
  Alert, Box, Card, CardContent, Chip, Stack,
  Tooltip, Typography, useTheme,
} from "@mui/material";
import ReplayIcon from "@mui/icons-material/Replay";
import { api } from "../dataProvider";
import {
  IdField, JsonBlock, RowsTable, ActionIcon,
} from "../components/ui";
import { TextLike, listTableSx } from "../components/listTable";
import { runOutcomeColor, runStatusColor } from "../theme";
import { useEntityScope } from "../entityScope";
import { can, usePermissions } from "../permissions";

const shortenSummary = (text?: string | null, cap = 120): string => {
  if (!text) return "";
  let s = text.split(/\.\s+/)[0];
  const lead = s.split(/\s+[-–—]\s+/)[0];
  if (lead.length >= 32) s = lead;
  s = s.trim().replace(/[\s.,;:]+$/, "");
  if (s.length > cap) {
    s = s.slice(0, cap - 1);
    const cut = s.lastIndexOf(" ");
    if (cut > cap * 0.6) s = s.slice(0, cut);
    s += "…";
  }
  return s;
};

const duration = (ms?: number) =>
  ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;

const TRIGGER_LABEL: Record<string, string> = {
  manual_test: "manual",
  schedule: "scheduled",
};

const OUTCOME_LABEL: Record<string, string> = {
  fail: "Findings raised",
  pass: "No findings",
};

const stamp = (v?: string) =>
  v
    ? new Date(v).toLocaleString(undefined, {
        day: "2-digit", month: "short",
        hour: "2-digit", minute: "2-digit", hour12: false,
      })
    : "—";

/* ================= Runs ================= */
const RerunButton = ({ label }: { label?: string }) => {
  const record = useRecordContext<any>();
  const notify = useNotify();
  const perms = usePermissions();
  const mayRerun = can(perms, "runs.rerun");
  if (!mayRerun) {
    return (
      <Tooltip title="Re-running needs the runs.rerun capability">
        <span>
          <ActionIcon
            title="Re-running needs the runs.rerun capability"
            icon={<ReplayIcon fontSize="small" />}
            disabled
          />
        </span>
      </Tooltip>
    );
  }
  return (
    <ActionIcon
      title={`Re-run rule for ${record?.data_date ?? "today"}`}
      icon={<ReplayIcon fontSize="small" />}
      onClick={async (e) => {
        e.stopPropagation();
        try {
          const run = await api.rerun(record.id);
          notify(`Re-run finished: ${run.status} / ${run.outcome ?? "no outcome"}`, {
            type: run.status === "success" ? "success" : "error",
          });
        } catch (err: any) {
          notify(`Re-run failed: ${err.message}`, { type: "error" });
        }
      }}
    />
  );
};

export const RunStatusCell = ({ source }: { source: string; label?: string }) => {
  const record = useRecordContext<any>();
  const theme = useTheme();
  const status = record?.[source];
  const palette = theme.palette as unknown as Record<string, { main?: string } | undefined>;
  return (
    <Typography
      variant="body2"
      sx={{
        color: palette[runStatusColor[status] ?? "info"]?.main ?? "#8d99ae",
        fontWeight: 700,
        textTransform: "capitalize",
      }}
    >
      {status ?? "—"}
    </Typography>
  );
};

export const RunOutcomeCell = ({ source }: { source: string; label?: string }) => {
  const record = useRecordContext<any>();
  const theme = useTheme();
  const outcome = record?.[source];
  const palette = theme.palette as unknown as Record<string, { main?: string } | undefined>;
  if (!outcome) {
    // No outcome because the rule never got to one. Saying nothing is honest;
    // printing "pass" here would mean the exact opposite of what happened.
    return (
      <Typography variant="body2" sx={{ color: "text.disabled" }}>
        &mdash;
      </Typography>
    );
  }
  return (
    <Typography
      variant="body2"
      noWrap
      sx={{
        color: palette[runOutcomeColor[outcome] ?? "info"]?.main ?? "#8d99ae",
        fontWeight: 700,
      }}
    >
      {OUTCOME_LABEL[outcome] ?? outcome}
    </Typography>
  );
};

const RunSummary = ({ source }: { source: string; label?: string }) => {
  const record = useRecordContext<any>();
  const full = record?.[source];
  return (
    <Tooltip
      title={full || ""}
      placement="top-start"
      slotProps={{ tooltip: { sx: { maxWidth: 620, whiteSpace: "normal" } } }}
    >
      <Typography
        variant="body2"
        noWrap
        sx={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          color: record?.status === "failed" ? "error.main" : "text.primary",
        }}
      >
        {shortenSummary(full)}
      </Typography>
    </Tooltip>
  );
};

const FIXED_WIDTHS: Array<[number, number]> = [
  [1, 58], [2, 112], [3, 98], [4, 148], [5, 150], [7, 70], [8, 82], [9, 124], [10, 50],
];

export const RunList = () => {
  const { entity } = useEntityScope();
  return (
    <List
      // The app bar owns the client scope; see RuleList for why it is a
      // permanent filter rather than another dropdown on this toolbar.
      filter={{ entity }}
      pagination={<Pagination rowsPerPageOptions={[10, 25, 50, 100]} />}
      sort={{ field: "started_at", order: "DESC" }}
    >
      <Datagrid
        rowClick="show"
        bulkActionButtons={false}
        size="small"
        sx={listTableSx(FIXED_WIDTHS)}
      >
        <IdField source="id" label="#" />
        <TextLike source="rule_code" label="Rule" mono />
        <TextLike source="data_date" label="Data date" secondary />
        <RunStatusCell source="status" label="Rule Execution" />
        <RunOutcomeCell source="outcome" label="Outcome" />
        <RunSummary source="summary" label="What it found" />
        <TextLike source="duration_ms" label="Took" render={duration} secondary />
        <TextLike source="triggered_by" label="By" render={(v: string) => TRIGGER_LABEL[v] ?? v} secondary />
        <TextLike source="started_at" label="Started" render={stamp} secondary />
        <RerunButton label="" />
      </Datagrid>
    </List>
  );
};

const RunDetails = () => {
  const record = useRecordContext<any>();

  useEffect(() => {
    if (record?.id) document.title = `Run #${record.id} · Audit Portal`;
  }, [record?.id]);

  if (!record) return null;
  return (
    <Box>
      <Card>
        <CardContent>
          {/* The two answers, side by side, never merged into one word. */}
          <Box
            sx={{
              display: "grid", gap: 1.5, mb: 2,
              gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(4, 1fr)" },
            }}
          >
            <Box>
              <Typography
                variant="caption"
                sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, color: "text.secondary" }}
              >
                Execution
              </Typography>
              <Typography variant="body1" sx={{ fontWeight: 600, textTransform: "capitalize" }}>
                {record.status}
              </Typography>
              <Typography variant="caption" sx={{ color: "text.secondary", display: "block" }}>
                {record.status === "success"
                  ? "the rule executed"
                  : record.status === "failed"
                    ? "the rule never completed"
                    : "in progress"}
              </Typography>
            </Box>
            <Box>
              <Typography
                variant="caption"
                sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, color: "text.secondary" }}
              >
                Rule outcome
              </Typography>
              <Typography variant="body1" sx={{ fontWeight: 600 }}>
                {record.outcome ? OUTCOME_LABEL[record.outcome] ?? record.outcome : "none"}
              </Typography>
              {!record.outcome && (
                <Typography variant="caption" sx={{ color: "text.secondary", display: "block" }}>
                  the rule never got an answer
                </Typography>
              )}
            </Box>
            <Box>
              <Typography
                variant="caption"
                sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, color: "text.secondary" }}
              >
                Exception lines
              </Typography>
              <Typography variant="body1" sx={{ fontWeight: 600 }}>
                {record.rows?.length ?? 0}
              </Typography>
            </Box>
            <Box>
              <Typography
                variant="caption"
                sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, color: "text.secondary" }}
              >
                Duration
              </Typography>
              <Typography variant="body1" sx={{ fontWeight: 600 }}>
                {duration(record.duration_ms)}
              </Typography>
            </Box>
          </Box>

          <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: "wrap", gap: 1 }}>
            <Chip size="small" variant="outlined" label={`rule v${record.rule_version}`} />
            <Chip
              size="small"
              variant="outlined"
              label={record.schedule_id ? `schedule #${record.schedule_id}` : "manual"}
            />
            <Chip size="small" variant="outlined" label={record.triggered_by} />
          </Stack>
          <Typography variant="body1" sx={{ fontWeight: 600 }}>{record.summary}</Typography>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            Data date {record.data_date} ·{" "}
            {new Date(record.started_at).toLocaleString()}
          </Typography>

          {/* Only a run that failed to execute has a fault to report. A raised
              observation is the product working and is not shown as an error. */}
          {record.status === "failed" && (
            <Alert severity="error" sx={{ mt: 2 }}>
              <Typography variant="body2" sx={{ fontWeight: 700, mb: 0.5 }}>
                This rule did not complete
              </Typography>
              <Box component="pre" sx={{ m: 0, whiteSpace: "pre-wrap", fontSize: 12 }}>
                {record.error_detail || "no detail recorded"}
              </Box>
              <Typography variant="caption" sx={{ mt: 1, display: "block" }}>
                No finding was raised: a rule that never ran has no opinion to
                report. This day is unchecked, not clean.
              </Typography>
            </Alert>
          )}
        </CardContent>
      </Card>

      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1.5 }}>Metrics</Typography>
          <JsonBlock value={record.metrics} />
        </CardContent>
      </Card>

      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1.5 }}>
            Exception lines ({record.rows?.length ?? 0})
          </Typography>
          <RowsTable rows={record.rows ?? []} />
        </CardContent>
      </Card>
    </Box>
  );
};

export const RunShow = () => (
  <ShowBase>
    <RunDetails />
  </ShowBase>
);

