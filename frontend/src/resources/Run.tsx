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
import { statusColor, FONT } from "../theme";

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
          notify(`Re-run finished: ${run.status}`, {
            type: run.status === "pass" ? "success" : "warning",
          });
        } catch (err: any) {
          notify(`Re-run failed: ${err.message}`, { type: "error" });
        }
      }}
    />
  );
};

/**
 * Result as coloured words rather than a filled chip.
 *
 * A chip per row is a block of colour the size of a postage stamp, and on a list
 * of 100 runs it out-shouts the one thing being scanned for. The word still
 * carries the colour, so it is not weaker - it just stops competing.
 */
const RunResult = ({ source }: { source: string; label?: string }) => {
  const record = useRecordContext<any>();
  const theme = useTheme();
  const status = record?.[source];
  const palette = theme.palette as unknown as Record<string, { main?: string } | undefined>;
  return (
    <Typography
      variant="body2"
      sx={{
        color: palette[statusColor[status] ?? "info"]?.main ?? "#8d99ae",
        fontWeight: 700,
        textTransform: "capitalize",
      }}
    >
      {status ?? "—"}
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
          color: record?.status === "error" ? "error.main" : "text.primary",
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
  [1, 60], [2, 116], [3, 100], [4, 78], [6, 72], [7, 84], [8, 126], [9, 52],
];

const widthSx = FIXED_WIDTHS.reduce<Record<string, { width: number }>>(
  (acc, [i, w]) => Object.assign(acc, columnWidth(i, w)),
  {},
);

export const RunList = () => (
  <List
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
      <RunResult source="status" label="Result" />
      <RunSummary source="summary" label="Finding" />
      <TextLike source="duration_ms" label="Took" render={duration} secondary />
      <TextLike source="triggered_by" label="By" render={(v: string) => TRIGGER_LABEL[v] ?? v} secondary />
      <TextLike source="started_at" label="Started" render={stamp} secondary />
      <RerunButton label="" />
    </Datagrid>
  </List>
);

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
          <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: "wrap", gap: 1 }}>
            <StatusField source="status" />
            <Chip size="small" variant="outlined" label={`rule v${record.rule_version}`} />
            <Chip
              size="small"
              variant="outlined"
              label={record.schedule_id ? `schedule #${record.schedule_id}` : "manual"}
            />
            <Chip size="small" variant="outlined" label={`${record.duration_ms} ms`} />
          </Stack>
          <Typography variant="body1" sx={{ fontWeight: 600 }}>{record.summary}</Typography>
          <Typography variant="caption" color="text.secondary">
            Data date {record.data_date} · triggered by {record.triggered_by} ·{" "}
            {new Date(record.started_at).toLocaleString()}
          </Typography>
          {record.status === "error" && (
            <Alert severity="error" sx={{ mt: 2 }}>
              <Box component="pre" sx={{ m: 0, whiteSpace: "pre-wrap", fontSize: 12 }}>
                {record.error_detail}
              </Box>
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
    const headers = { Authorization: `Bearer ${localStorage.getItem("abm.token")}` };
    fetch("/api/rule-schedules", { headers })
      .then((r) => r.json())
      .then((d) => setRows(d.data ?? []))
      .catch(() => setRows([]));
    fetch("/api/rules?perPage=500", { headers })
      .then((r) => r.json())
      .then((d) => {
        const map: Record<number, any> = {};
        (d.data ?? []).forEach((x: any) => (map[x.id] = x));
        setRules(map);
      })
      .catch(() => {});
    api.schedulerJobs()
      .then((j) => setJobs((j.jobs ?? []).map((x: any) => x.id)))
      .catch(() => setJobs([]));
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