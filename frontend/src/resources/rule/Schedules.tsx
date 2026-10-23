import { useEffect, useState } from "react";
import { useNotify } from "react-admin";
import {
  Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, IconButton, MenuItem, Stack,
  TextField as MuiTextField, Tooltip, Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import ScheduleIcon from "@mui/icons-material/Schedule";
import { api } from "../../dataProvider";
import { CadenceEditor, type CadenceConfig } from "../../components/CadenceEditor";

/* ================================================================== */
/* Shared index                                                        */
/* ================================================================== */
export const useScheduleIndex = () => {
  const [index, setIndex] = useState<Record<string, any[]>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listSchedules()
      .then((r) => {
        const byCode: Record<string, any[]> = {};
        for (const s of r.data ?? []) (byCode[s.rule_code] ??= []).push(s);
        setIndex(byCode);
      })
      .catch(() => setIndex({}))
      .finally(() => setLoading(false));
  }, []);

  return { index, loading };
};

export const ScheduleSummary = ({ schedules }: { schedules: any[] }) => {
  if (!schedules?.length) {
    return (
      <Typography variant="body2" sx={{ color: "text.secondary" }}>
        manual only
      </Typography>
    );
  }
  return (
    <Tooltip
      // The friendly cadences only. `frequency.py` is the only thing that knows
      // a cron expression, and users never see one - which is why the tooltip
      // says "Daily at 06:30 · Every 6 hours" and not the expression behind it.
      title={schedules.map((s) => s.cadence_label).join(" · ")}
      placement="top-start"
      slotProps={{ tooltip: { sx: { maxWidth: 420, whiteSpace: "normal" } } }}
    >
      <Typography variant="body2" noWrap>
        {schedules[0].cadence_label}
        {schedules.length > 1 && (
          <Typography component="span" variant="caption" sx={{ color: "text.secondary" }}>
            {" "}+{schedules.length - 1}
          </Typography>
        )}
      </Typography>
    </Tooltip>
  );
};
/* ================================================================== */
/* Schedules section                                                   */
/* ================================================================== */
const ScheduleDialog = ({
  ruleCode, schedule, onClose,
}: {
  ruleCode: string;
  schedule: any | null;
  onClose: () => void;
}) => {
  const isEdit = Boolean(schedule);
  const [kind, setKind] = useState(schedule?.kind ?? "DAILY");
  const [config, setConfig] = useState<Record<string, any>>(schedule?.config ?? { time_of_day: "06:30" });
  const [timezone, setTimezone] = useState(schedule?.timezone ?? "UTC");
  const [label, setLabel] = useState(schedule?.label ?? "");
  const [dataLag, setDataLag] = useState(schedule?.data_lag_days ?? 1);
  const [busy, setBusy] = useState(false);
  const notify = useNotify();

  const save = async () => {
    setBusy(true);
    try {
      const payload = { label, kind, config, timezone, data_lag_days: dataLag };
      if (isEdit) await api.updateSchedule(schedule.id, payload);
      else await api.createSchedule({ ...payload, rule_code: ruleCode });
      notify(isEdit ? "Schedule updated" : "Schedule added", { type: "success" });
      onClose();
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{isEdit ? "Edit schedule" : "Add a schedule"}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <MuiTextField size="small" fullWidth label="Label (optional)"
            value={label} onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Daily control" />
          <CadenceEditor value={{ kind, config, timezone }} onChange={(v: CadenceConfig) => {
            setKind(v.kind); setConfig(v.config); setTimezone(v.timezone ?? "UTC");
          }} />
          <MuiTextField select size="small" fullWidth label="Audit which day's data"
            value={dataLag} onChange={(e) => setDataLag(Number(e.target.value))}
            helperText="Yesterday is the usual choice — a day's CSVs are rarely complete when the job fires.">
            <MenuItem value={0}>Same day</MenuItem>
            <MenuItem value={1}>Yesterday</MenuItem>
            <MenuItem value={2}>2 days ago</MenuItem>
            <MenuItem value={7}>7 days ago</MenuItem>
          </MuiTextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" onClick={save} disabled={busy}
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}>
          {isEdit ? "Save" : "Add schedule"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export const SchedulesSection = ({ ruleCode, canWrite }: { ruleCode: string; canWrite: boolean }) => {
  const [schedules, setSchedules] = useState<any[] | null>(null);
  const [editing, setEditing] = useState<any | null>(null);
  const [adding, setAdding] = useState(false);
  const notify = useNotify();

  const load = () =>
    api.listSchedules(ruleCode).then((r) => setSchedules(r.data ?? [])).catch(() => setSchedules([]));

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [ruleCode]);

  const remove = async (id: number) => {
    try {
      await api.deleteSchedule(id);
      notify("Schedule removed", { type: "info" });
      load();
    } catch (e: any) {
      notify(e.message, { type: "error" });
    }
  };

  return (
    <Card>
      <CardContent>
        <Stack
          direction="row"
          sx={{ mb: 1.5, justifyContent: "space-between", alignItems: "center" }}
        >
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <ScheduleIcon color="action" />
            <Typography variant="h6">Schedules</Typography>
            <Chip size="small" label={schedules?.length ?? 0} />
          </Stack>
          {canWrite && (
            <Button size="small" startIcon={<AddIcon />} onClick={() => setAdding(true)}>
              Add schedule
            </Button>
          )}
        </Stack>

        {!schedules && <CircularProgress size={22} />}

        {schedules?.length === 0 && (
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            No schedules. A rule with no schedule will only ever run when triggered manually.
          </Typography>
        )}

        <Stack spacing={1}>
          {schedules?.map((s) => (
            <Box key={s.id} sx={{ p: 1.5, border: "1px solid #e9edf4", borderRadius: 2, bgcolor: "#fcfdff" }}>
              <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
                <Box>
                  <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                    <Typography variant="body2" sx={{ fontWeight: 700 }}>{s.cadence_label}</Typography>
                    {s.label && s.label !== s.cadence_label && (
                      <Chip size="small" variant="outlined" label={s.label} />
                    )}
                    <Chip size="small" label={s.timezone} />
                    <Chip size="small" variant="outlined"
                      label={s.data_lag_days === 1 ? "yesterday's data" : `${s.data_lag_days}d lag`} />
                  </Stack>
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    next {s.next_run_relative ?? "unknown"}
                    {s.upcoming_runs?.[1] &&
                      ` · then ${new Date(s.upcoming_runs[1]).toLocaleString()}`}
                    {s.last_run_at && ` · last ran ${new Date(s.last_run_at).toLocaleString()}`}
                  </Typography>
                  <Typography variant="caption" sx={{ display: "block", color: "#8d99ae", fontFamily: "monospace" }}>
                    cron: {s.cron}
                  </Typography>
                </Box>
                {canWrite && (
                  <Stack direction="row" spacing={0.5}>
                    <Button size="small" onClick={() => setEditing(s)}>Edit</Button>
                    <Tooltip title="Remove schedule">
                      <IconButton size="small" onClick={() => remove(s.id)}>
                        <DeleteOutlinedIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                )}
              </Stack>
            </Box>
          ))}
        </Stack>

        {(adding || editing) && (
          <ScheduleDialog
            ruleCode={ruleCode}
            schedule={editing}
            onClose={() => { setAdding(false); setEditing(null); load(); }}
          />
        )}
      </CardContent>
    </Card>
  );
};
