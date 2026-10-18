import { useEffect, useState } from "react";
import {
  Datagrid, DateField, List, Pagination, ShowBase,
  TextField, useNotify, useRecordContext,
} from "react-admin";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Stack, Typography,
} from "@mui/material";
import ReplayIcon from "@mui/icons-material/Replay";
import { api } from "../dataProvider";
import { IdField, JsonBlock, RowsTable, StatusField } from "../components/ui";

/* ================= Runs ================= */
const RerunButton = () => {
  const record = useRecordContext<any>();
  const notify = useNotify();
  return (
    <Button
      size="small"
      startIcon={<ReplayIcon />}
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
    >
      Re-run
    </Button>
  );
};

export const RunList = () => (
  <List
    pagination={<Pagination rowsPerPageOptions={[10, 25, 50, 100]} />}
    sort={{ field: "started_at", order: "DESC" }}
  >
    <Datagrid rowClick="show" bulkActionButtons={false}>
      <IdField source="id" />
      <TextField source="rule_code" label="Rule" />
      <TextField source="data_date" label="Data date" />
      <StatusField source="status" />
      <TextField source="summary" label="Summary" />
      <TextField source="rows_scanned" label="Rows" />
      <TextField source="duration_ms" label="ms" />
      <TextField source="triggered_by" label="Triggered by" />
      <DateField source="started_at" label="Started" showTime />
      <RerunButton />
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
            <Chip size="small" variant="outlined" label={`${record.rows_scanned} rows scanned`} />
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