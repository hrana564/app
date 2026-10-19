import { useEffect, useState } from "react";
import {
  Datagrid, List, Pagination, ShowBase,
  useNotify, useRecordContext,
} from "react-admin";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Stack,
  Tooltip, Typography, useTheme,
} from "@mui/material";
import ReplayIcon from "@mui/icons-material/Replay";
import { api } from "../dataProvider";
import {
  IdField, JsonBlock, RowsTable, StatusField, ActionIcon,
} from "../components/ui";
import { runOutcomeColor, runStatusColor, FONT } from "../theme";
import { useEntityScope } from "../entityScope";

/**
 * A run summary is written for a detail page, so it runs to 160 characters and
 * wraps a list cell onto three lines - which is what turns a scannable table
 * into a scroll.
 *
 * Only the FIRST clause carries the finding. What follows it is commentary
 * ("Payments to these vendors require review") or a worked example ("Worst:
 * Garry Vaughan (1 txns, last active ...)"), and the row either way is decided
 * by the Result column beside it. So: cut at the first sentence, cut again at
 * the first dash if what remains is still substantial, and only truncate at a
 * word boundary as a last resort. The full text stays on the tooltip and on
 * the run page.
 */
export const shortenSummary = (text?: string | null, cap = 120): string => {
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

/**
 * Did it run, and what did it find - as two columns.
 *
 * These were one column reading "Fail", which is why a runs list looked like a
 * wall of faults. A red `fail` here means the control worked and caught
 * something; it is not a broken system. Only `failed` is a broken system, and
 * it now sits in the column that actually answers that question.
 */
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
      <Typography variant="body2" color="text.disabled">
        &mdash;
      </Typography>
    );
  }
  return (
    <Typography
      variant="body2"
      sx={{
        color: palette[runOutcomeColor[outcome] ?? "info"]?.main ?? "#8d99ae",
        fontWeight: 700,
        textTransform: "capitalize",
      }}
    >
      {outcome}
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

const cellSx = {
  py: 0.65,
  px: 1.25,
  borderColor: "#eef1f6",
  overflow: "hidden",
  lineHeight: 1.5,
};

/**
 * A one-line text cell. `mono` for identifiers, `secondary` for the values that
 * should recede beside the finding, `render` to format on the way out.
 */
const TextLike = ({
  source, mono, secondary, render, label,
}: {
  source: string;
  label?: string;
  mono?: boolean;
  secondary?: boolean;
  render?: (value: any) => string;
}) => {
  const record = useRecordContext<any>();
  const raw = record?.[source];
  return (
    <Typography
      variant="body2"
      noWrap
      sx={{
        fontFamily: mono ? "monospace" : undefined,
        fontWeight: mono ? 700 : undefined,
        color: secondary ? "text.secondary" : "text.primary",
      }}
    >
      {render ? render(raw) : (raw ?? "—")}
    </Typography>
  );
};

/**
 * Fixed widths for the columns around the finding, so the finding absorbs
 * whatever width is left instead of the browser redistributing against the
 * longest cell in the row. Column 5 is deliberately absent - that is the one
 * that takes the remainder.
 */
const columnWidth = (i: number, w: number) => ({
  [`& .RaDatagrid-headerCell:nth-of-type(${i}), & .RaDatagrid-rowCell:nth-of-type(${i})`]:
    { width: w },
});

const FIXED_WIDTHS: Array<[number, number]> = [
  [1, 58], [2, 112], [3, 98], [4, 82], [5, 82], [7, 70], [8, 82], [9, 124], [10, 50],
];

const widthSx = FIXED_WIDTHS.reduce<Record<string, { width: number }>>(
  (acc, [i, w]) => Object.assign(acc, columnWidth(i, w)),
  {},
);

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
        sx={{
          // Fixed layout is what makes "one row, one line" enforceable: columns
          // take their declared width and the summary absorbs the remainder
          // instead of the browser redistributing it against the longest cell.
          "& .RaDatagrid-table": { tableLayout: "fixed" },
          "& .RaDatagrid-headerCell": {
            py: 0.75,
            px: 1.25,
            fontSize: FONT.label,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: 0.5,
            color: "#5b6b82",
            backgroundColor: "#fafbfe",
            whiteSpace: "nowrap",
            overflow: "hidden",
          },
          "& .RaDatagrid-rowCell": cellSx,
          "& .RaDatagrid-headerRow:hover": { backgroundColor: "#f3f6fb" },
          ...widthSx,
        }}
      >
        <IdField source="id" label="#" />
        <TextLike source="rule_code" label="Rule" mono />
        <TextLike source="data_date" label="Data date" secondary />
        <RunStatusCell source="status" label="Ran" />
        <RunOutcomeCell source="outcome" label="Outcome" />
        <RunSummary source="summary" label="Finding" />
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
    if (record?.id) document.title = `Run #${record.id} · ABM Audit Portal`;
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
              <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>
                Execution
              </Typography>
              <Typography variant="body1" sx={{ fontWeight: 600, textTransform: "capitalize" }}>
                {record.status}
              </Typography>
              <Typography variant="caption" color="text.secondary" display="block">
                {record.status === "success"
                  ? "the rule executed"
                  : record.status === "failed"
                    ? "the rule never completed"
                    : "in progress"}
              </Typography>
            </Box>
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>
                Control outcome
              </Typography>
              <Typography variant="body1" sx={{ fontWeight: 600, textTransform: "capitalize" }}>
                {record.outcome ?? "none"}
              </Typography>
              <Typography variant="caption" color="text.secondary" display="block">
                {!record.outcome
                  ? "the control never got an answer"
                  : record.outcome === "fail"
                    ? "breach found - a ticket was raised"
                    : "no breach found"}
              </Typography>
            </Box>
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>
                Exception rows
              </Typography>
              <Typography variant="body1" sx={{ fontWeight: 600 }}>
                {record.rows?.length ?? 0}
              </Typography>
            </Box>
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>
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
          <Typography variant="caption" color="text.secondary">
            Data date {record.data_date} ·{" "}
            {new Date(record.started_at).toLocaleString()}
          </Typography>

          {/* Only a run that failed to execute has a fault to report. A breach
              is the product working and is not shown as an error. */}
          {record.status === "failed" && (
            <Alert severity="error" sx={{ mt: 2 }}>
              <Typography variant="body2" sx={{ fontWeight: 700, mb: 0.5 }}>
                This rule did not complete
              </Typography>
              <Box component="pre" sx={{ m: 0, whiteSpace: "pre-wrap", fontSize: 12 }}>
                {record.error_detail || "no detail recorded"}
              </Box>
              <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                No ticket was raised: a rule that never ran has no finding to report.
                This day is unchecked, not clean.
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
            Exception rows ({record.rows?.length ?? 0})
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

/* ================= Schedules (read-only overview across all rules) ========= */
export const ScheduleList = () => (
  <ScheduleOverview />
);

/**
 * A flat view of every schedule in the system, grouped by cadence label.
 * Editing happens on the rule page, where the cadence editor lives.
 */
const ScheduleOverview = () => {
  const [rows, setRows] = useState<any[] | null>(null);
  const [jobs, setJobs] = useState<string[]>([]);
  const [rules, setRules] = useState<Record<number, any>>({});
  const notify = useNotify();

  useEffect(() => {
    let live = true;
    api.listSchedules()
      .then((d) => live && setRows(d.data ?? []))
      .catch(() => live && setRows([]));
    api.listRules()
      .then((d) => {
        if (!live) return;
        const map: Record<number, any> = {};
        d.data.forEach((x: any) => (map[x.id] = x));
        setRules(map);
      })
      .catch(() => {});
    api.schedulerJobs()
      .then((j) => live && setJobs((j.jobs ?? []).map((x: any) => x.id)))
      .catch(() => live && setJobs([]));
    return () => { live = false; };
  }, []);

  if (!rows) return <CircularProgress size={24} />;

  if (rows.length === 0) {
    return (
      <Alert severity="info">
        No schedules configured. Open a rule and add a schedule to have it run
        automatically.
      </Alert>
    );
  }

  return (
    <Stack spacing={1.5}>
      <Typography variant="body2" color="text.secondary">
        {rows.length} schedule{rows.length === 1 ? "" : "s"} across{" "}
        {new Set(rows.map((r) => r.rule_code)).size} rule(s). A schedule only fires
        while its rule is <strong>active</strong>.
      </Typography>
      {rows.map((s) => {
        const rule = rules[s.rule_code];
        const live = jobs.includes(`rule_schedule_${s.id}`);
        return (
          <Card key={s.id}>
            <CardContent>
              <Stack direction="row" justifyContent="space-between" alignItems="center"
                flexWrap="wrap" gap={1}>
                <Box>
                  <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" gap={1}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                      {s.cadence_label}
                    </Typography>
                    {rule && (
                      <Chip size="small" variant="outlined" label={rule.code} />
                    )}
                    {rule && (
                      <Chip
                        size="small"
                        label={rule.status.toUpperCase()}
                        color={rule.status === "active" ? "success" : "default"}
                      />
                    )}
                    <Chip size="small" variant="outlined" label={s.timezone} />
                    <Chip size="small" variant="outlined"
                      label={s.data_lag_days === 1 ? "yesterday" : `${s.data_lag_days}d lag`} />
                    <Chip
                      size="small"
                      label={live ? "REGISTERED" : "NOT SCHEDULED"}
                      color={live ? "success" : "warning"}
                    />
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    next {s.next_run_relative ?? "unknown"}
                    {s.last_run_at && ` · last ran ${new Date(s.last_run_at).toLocaleString()}`}
                  </Typography>
                  <Typography variant="caption" sx={{ display: "block", color: "#8d99ae", fontFamily: "monospace" }}>
                    cron: {s.cron}
                  </Typography>
                </Box>
                {rule && (
                  <Button size="small" onClick={() => { window.location.hash = `#/rules/${rule.id}/show`; }}>
                    Open rule
                  </Button>
                )}
              </Stack>
            </CardContent>
          </Card>
        );
      })}
    </Stack>
  );
};