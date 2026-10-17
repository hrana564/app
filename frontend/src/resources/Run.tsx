import { useEffect, useState } from "react";
import {
  Datagrid, DateField, EditButton, List, Pagination, ShowBase,
  TextField, useNotify, useRecordContext,
} from "react-admin";
import {
  Alert, Box, Button, Card, CardContent, Chip, Stack, Typography,
} from "@mui/material";
import ReplayIcon from "@mui/icons-material/Replay";
import { api } from "../dataProvider";
import {
  BooleanDotField, IdField, JsonBlock, RowsTable, StatusField,
} from "../components/ui";

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
      <TextField source="rule_id" label="Rule" />
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

/* ================= Schedules ================= */
export const ScheduleList = () => (
  <List pagination={<Pagination rowsPerPageOptions={[10, 25, 50]} />}>
    <Datagrid rowClick="show" bulkActionButtons={false}>
      <IdField source="id" />
      <TextField source="rule_id" label="Rule" />
      <TextField source="cron" label="Cron" />
      <TextField source="timezone" label="Timezone" />
      <BooleanDotField source="enabled" />
      <DateField source="last_run_at" label="Last run" showTime />
      <DateField source="next_run_at" label="Next run" showTime />
      <EditButton />
    </Datagrid>
  </List>
);

const ScheduleDetails = () => {
  const record = useRecordContext<any>();
  const [jobs, setJobs] = useState<any[] | null>(null);

  useEffect(() => {
    api.schedulerJobs()
      .then((j) => setJobs(j.jobs ?? []))
      .catch(() => setJobs([]));
  }, []);

  if (!record) return null;
  const live = jobs?.find((j) => j.id === `schedule_${record.id}`);

  return (
    <Box>
      <Card>
        <CardContent>
          <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: "wrap", gap: 1 }}>
            <Chip
              size="small"
              label={record.enabled ? "ENABLED" : "DISABLED"}
              color={record.enabled ? "success" : "default"}
            />
            <Chip size="small" variant="outlined" label={record.timezone} />
          </Stack>
          <Typography variant="h6">Rule #{record.rule_id} · {record.cron}</Typography>
          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
            Created by {record.created_by}
          </Typography>
          <Stack direction="row" spacing={3} sx={{ mt: 2 }}>
            <Box>
              <Typography variant="caption" color="text.secondary">LAST RUN</Typography>
              <Typography variant="body2">
                {record.last_run_at ? new Date(record.last_run_at).toLocaleString() : "never"}
              </Typography>
            </Box>
            <Box>
              <Typography variant="caption" color="text.secondary">NEXT RUN</Typography>
              <Typography variant="body2">
                {record.next_run_at ? new Date(record.next_run_at).toLocaleString() : "—"}
              </Typography>
            </Box>
          </Stack>
          <Alert severity={live ? "success" : "warning"} sx={{ mt: 2 }}>
            {jobs === null
              ? "Checking APScheduler…"
              : live
                ? `Job registered with APScheduler. Next fire: ${live.next_run_time}`
                : "Not currently registered with APScheduler."}
          </Alert>
        </CardContent>
      </Card>
    </Box>
  );
};

export const ScheduleShow = () => (
  <ShowBase>
    <ScheduleDetails />
  </ShowBase>
);