/**
 * Friendly frequency picker.
 *
 * The user never sees a cron expression. They pick a cadence kind, fill in the
 * obvious fields, and read back a plain-English label plus the real fire times.
 * The derived cron is shown only as a secondary, muted line for engineers
 * debugging the scheduler.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert, Box, Button, Checkbox, CircularProgress, FormControlLabel, Grid,
  ListItemText, MenuItem, Stack, TextField, Typography, Divider, Chip,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import { api } from "../dataProvider";

export interface CadenceConfig {
  kind: string;
  config: Record<string, any>;
  timezone?: string;
}

const WEEKDAYS = [
  { value: 1, label: "Mon" },
  { value: 2, label: "Tue" },
  { value: 3, label: "Wed" },
  { value: 4, label: "Thu" },
  { value: 5, label: "Fri" },
  { value: 6, label: "Sat" },
  { value: 0, label: "Sun" },
];

const KINDS = [
  { value: "HOURLY", label: "Every few hours" },
  { value: "DAILY", label: "Every day" },
  { value: "WEEKDAYS", label: "Weekdays (Mon–Fri)" },
  { value: "WEEKLY", label: "Specific days of the week" },
  { value: "MONTHLY", label: "Once a month" },
];

interface Props {
  value: CadenceConfig;
  onChange: (next: CadenceConfig) => void;
}

export const CadenceEditor = ({ value, onChange }: Props) => {
  const [options, setOptions] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [checking, setChecking] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(value.kind === "CUSTOM");

  useEffect(() => {
    api
      .cadenceOptions()
      .then(setOptions)
      .catch(() => setOptions({ presets: [], hourly_intervals: [1, 4, 6, 12], max_day_of_month: 28 }));
  }, []);

  // Debounced preview so the user sees the consequence before saving.
  useEffect(() => {
    if (!value.kind) return;
    let cancelled = false;
    setChecking(true);
    const handle = setTimeout(async () => {
      try {
        const result = await api.cadencePreview({
          kind: value.kind,
          config: value.config,
          timezone: value.timezone ?? "UTC",
          count: 5,
        });
        if (!cancelled) setPreview(result);
      } catch {
        if (!cancelled) setPreview(null);
      } finally {
        if (!cancelled) setChecking(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [value.kind, value.config, value.timezone]);

  const setKind = (kind: string) => {
    onChange({
      ...value,
      kind,
      config:
        kind === "HOURLY"
          ? { time_of_day: "00:00", interval_hours: 6 }
          : kind === "WEEKLY"
            ? { time_of_day: "07:00", days_of_week: [1] }
            : kind === "MONTHLY"
              ? { time_of_day: "07:00", day_of_month: 1 }
              : { time_of_day: "06:30" },
    });
  };

  const setConfig = (patch: Record<string, any>) =>
    onChange({ ...value, config: { ...value.config, ...patch } });

  const toggleDay = (day: number) => {
    const current: number[] = value.config.days_of_week ?? [];
    const next = current.includes(day)
      ? current.filter((d) => d !== day)
      : [...current, day].sort((a, b) => ((a + 6) % 7) - ((b + 6) % 7));
    setConfig({ days_of_week: next });
  };

  const intervals = options?.hourly_intervals ?? [1, 2, 3, 4, 6, 8, 12];
  const maxDom = options?.max_day_of_month ?? 28;

  const problems = preview?.problems ?? [];
  const zone = value.timezone ?? "UTC";
  const upcoming: string[] = useMemo(
    () =>
      (preview?.upcoming ?? []).map((v: string) =>
        // Render in the SCHEDULE's timezone, not the browser's. Otherwise a
        // "Daily at 06:30 UTC" schedule reads as "12:00 PM" on a Dubai laptop.
        new Date(v).toLocaleString("en-GB", {
          timeZone: zone,
          weekday: "short",
          day: "2-digit",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }),
      ),
    [preview, zone],
  );

  return (
    <Box>
      {/* One-click starting points */}
      {options?.presets?.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary" }}>
            COMMON
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 0.5, flexWrap: "wrap", gap: 0.75 }}>
            {options.presets.map((p: any) => (
              <Chip
                key={p.id}
                label={p.label}
                size="small"
                variant="outlined"
                clickable
                onClick={() => onChange({ ...value, kind: p.kind, config: p.config })}
              />
            ))}
          </Stack>
        </Box>
      )}

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, sm: 6 }}>
          <TextField
            select
            fullWidth
            size="small"
            label="How often"
            value={value.kind === "CUSTOM" ? "" : value.kind}
            onChange={(e) => setKind(e.target.value)}
          >
            {KINDS.map((k) => (
              <MenuItem key={k.value} value={k.value}>{k.label}</MenuItem>
            ))}
          </TextField>
        </Grid>

        <Grid size={{ xs: 12, sm: 6 }}>
          <TextField
            select
            fullWidth
            size="small"
            label="Timezone"
            value={value.timezone ?? "UTC"}
            onChange={(e) => onChange({ ...value, timezone: e.target.value })}
          >
            {["UTC", "Asia/Dubai", "Asia/Riyadh", "Europe/London", "Asia/Kolkata"].map((tz) => (
              <MenuItem key={tz} value={tz}>{tz}</MenuItem>
            ))}
          </TextField>
        </Grid>

        {value.kind === "HOURLY" && (
          <>
            <Grid size={{ xs: 12, sm: 6 }}>
              <TextField
                select fullWidth size="small" label="Every"
                value={value.config.interval_hours ?? 6}
                onChange={(e) => setConfig({ interval_hours: Number(e.target.value) })}
              >
                {intervals.map((n: number) => (
                  <MenuItem key={n} value={n}>
                    {n === 1 ? "1 hour" : `${n} hours`}
                  </MenuItem>
                ))}
              </TextField>
            </Grid>
            <Grid size={{ xs: 12, sm: 6 }}>
              <TextField
                size="small" fullWidth label="At minute"
                value={value.config.time_of_day ?? "00:00"}
                onChange={(e) => setConfig({ time_of_day: e.target.value })}
                placeholder="HH:MM"
              />
            </Grid>
          </>
        )}

        {["DAILY", "WEEKDAYS", "WEEKLY", "MONTHLY"].includes(value.kind) && (
          <Grid size={{ xs: 12, sm: 6 }}>
            <TextField
              size="small" fullWidth label="At time"
              value={value.config.time_of_day ?? "06:30"}
              onChange={(e) => setConfig({ time_of_day: e.target.value })}
              placeholder="HH:MM"
              helperText="24-hour clock"
            />
          </Grid>
        )}

        {value.kind === "WEEKLY" && (
          <Grid size={{ xs: 12 }}>
            <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary" }}>
              ON THESE DAYS
            </Typography>
            <Stack direction="row" sx={{ mt: 0.5, flexWrap: "wrap", gap: 0.5 }}>
              {WEEKDAYS.map((d) => (
                <Button
                  key={d.value}
                  size="small"
                  variant={(value.config.days_of_week ?? []).includes(d.value) ? "contained" : "outlined"}
                  onClick={() => toggleDay(d.value)}
                >
                  {d.label}
                </Button>
              ))}
            </Stack>
          </Grid>
        )}

        {value.kind === "MONTHLY" && (
          <Grid size={{ xs: 12, sm: 6 }}>
            <TextField
              select fullWidth size="small" label="On day of month"
              value={value.config.day_of_month ?? 1}
              onChange={(e) => setConfig({ day_of_month: Number(e.target.value) })}
            >
              {Array.from({ length: maxDom }, (_, i) => i + 1).map((d) => (
                <MenuItem key={d} value={d}>
                  Day {d}
                  {d === maxDom ? " (last day that exists in every month)" : ""}
                </MenuItem>
              ))}
            </TextField>
          </Grid>
        )}
      </Grid>

      <Divider sx={{ my: 2 }} />

      {/* What this actually means */}
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="subtitle2">
          {preview?.cadence_label ?? "Choose a frequency"}
        </Typography>
        {checking && <CircularProgress size={14} />}
      </Stack>

      {problems.length > 0 && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {problems.map((p: string) => <div key={p}>{p}</div>)}
        </Alert>
      )}

      {!problems.length && upcoming.length > 0 && (
        <Box sx={{ mt: 1 }}>
          <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary" }}>
            NEXT RUNS ({zone})
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 0.5, flexWrap: "wrap", gap: 0.75 }}>
            {upcoming.map((t) => (
              <Chip key={t} size="small" variant="outlined" label={t} />
            ))}
          </Stack>
          {preview?.cron && (
            <Typography
              variant="caption"
              sx={{ display: "block", mt: 1, color: "text.secondary", fontFamily: "monospace" }}
            >
              derived cron: {preview.cron}
            </Typography>
          )}
        </Box>
      )}

      <Box sx={{ mt: 1.5 }}>
        <FormControlLabel
          control={
            <Checkbox
              size="small"
              checked={showAdvanced}
              onChange={(e) => {
                setShowAdvanced(e.target.checked);
                if (e.target.checked) onChange({ ...value, kind: "CUSTOM", config: { cron_override: "" } });
                else setKind("DAILY");
              }}
            />
          }
          label={<Typography variant="caption">Advanced: enter a raw cron expression</Typography>}
        />
      </Box>

      {showAdvanced && value.kind === "CUSTOM" && (
        <TextField
          fullWidth size="small" sx={{ mt: 1 }}
          label="Cron expression (5 fields)"
          value={value.config.cron_override ?? ""}
          onChange={(e) => setConfig({ cron_override: e.target.value })}
          placeholder="30 6 * * 1-5"
          helperText="Minute Hour DayOfMonth Month DayOfWeek — validated before it is saved."
        />
      )}
    </Box>
  );
};