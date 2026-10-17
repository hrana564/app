import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Box, Card, CardContent, Chip, Grid, LinearProgress, Stack, Typography, Tooltip,
} from "@mui/material";
import RuleIcon from "@mui/icons-material/Rule";
import ScheduleIcon from "@mui/icons-material/Schedule";
import PlayCircleIcon from "@mui/icons-material/PlayCircle";
import ReportProblemIcon from "@mui/icons-material/ReportProblem";
import TaskAltIcon from "@mui/icons-material/TaskAlt";
import { api } from "../dataProvider";
import { severityColor } from "../theme";

interface Dashboard {
  rules: { total: number; active: number; draft: number; paused: number; validated: number };
  schedules: { total: number; live: number };
  runs: { total: number; pass: number; fail: number; error: number };
  tickets: { total: number; open: number; acknowledged: number; dismissed: number };
  scheduler: { running: boolean; jobs: number };
}

const Stat = ({
  title, value, caption, icon, color, to,
}: {
  title: string; value: number | string; caption: string;
  icon: React.ReactNode; color: string; to?: string;
}) => (
  <Card sx={{ height: "100%" }}>
    <CardContent>
      <Stack direction="row" alignItems="flex-start" justifyContent="space-between" spacing={2}>
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 700, letterSpacing: 0.6 }}>
            {title}
          </Typography>
          <Typography variant="h4" sx={{ mt: 0.5, fontWeight: 700 }}>
            {value}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {caption}
          </Typography>
        </Box>
        <Box
          sx={{
            width: 44, height: 44, borderRadius: 2, bgcolor: `${color}14`, color,
            display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
          }}
        >
          {icon}
        </Box>
      </Stack>
      {to && (
        <Typography
          component={Link}
          to={to}
          variant="caption"
          sx={{ display: "inline-block", mt: 1.5, fontWeight: 700, textDecoration: "none", color: "primary.main" }}
        >
          View →
        </Typography>
      )}
    </CardContent>
  </Card>
);

export const Dashboard = () => {
  const [data, setData] = useState<Dashboard | null>(null);
  const [sev, setSev] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.dashboard(), api.ticketSummary()])
      .then(([d, s]: [any, any]) => {
        setData(d);
        setSev(s.open_by_severity ?? {});
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading || !data) return <LinearProgress />;

  const runTotal = Math.max(1, data.runs.total);
  const ticketTotal = Math.max(1, data.tickets.total);

  return (
    <Box>
      <Typography variant="h5" sx={{ mb: 0.5 }}>Continuous Auditing Overview</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Rules, schedules, execution history and the open ticket queue across both client entities.
      </Typography>

      <Grid container spacing={2.5}>
        <Grid size={{ xs: 12, sm: 6, lg: 3 }}>
          <Stat
            title="RULES" value={data.rules.total} color="#123a63" to="/rules"
            caption={`${data.rules.active} active · ${data.rules.draft} draft · ${data.rules.validated ?? 0} validated`}
            icon={<RuleIcon />}
          />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, lg: 3 }}>
          <Stat
            title="SCHEDULES" value={data.schedules.total} color="#0f8a80" to="/schedules"
            caption={`${data.schedules.live} firing · scheduler ${data.scheduler.running ? "running" : "stopped"}`}
            icon={<ScheduleIcon />}
          />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, lg: 3 }}>
          <Stat
            title="RUNS" value={data.runs.total} color="#0277bd" to="/runs"
            caption={`${data.runs.pass} pass · ${data.runs.fail} fail · ${data.runs.error} error`}
            icon={<PlayCircleIcon />}
          />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, lg: 3 }}>
          <Stat
            title="OPEN TICKETS" value={data.tickets.open} color="#c62828" to="/tickets?filter=%7B%22status%22%3A%22open%22%7D"
            caption={`${data.tickets.acknowledged} acknowledged · ${data.tickets.dismissed} dismissed`}
            icon={<ReportProblemIcon />}
          />
        </Grid>

        <Grid size={{ xs: 12, lg: 7 }}>
          <Card sx={{ height: "100%" }}>
            <CardContent>
              <Typography variant="h6" sx={{ mb: 2 }}>Run outcomes</Typography>
              {(["pass", "fail", "error"] as const).map((key) => {
                const value = data.runs[key];
                const pct = Math.round((value / runTotal) * 100);
                const colors: Record<string, string> = { pass: "#2e7d32", fail: "#c62828", error: "#ef6c00" };
                return (
                  <Box key={key} sx={{ mb: 2 }}>
                    <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
                      <Typography variant="body2" sx={{ textTransform: "capitalize", fontWeight: 600 }}>
                        {key}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {value} ({pct}%)
                      </Typography>
                    </Stack>
                    <LinearProgress
                      variant="determinate"
                      value={pct}
                      sx={{
                        height: 8, borderRadius: 4,
                        bgcolor: "#eef1f6",
                        "& .MuiLinearProgress-bar": { bgcolor: colors[key], borderRadius: 4 },
                      }}
                    />
                  </Box>
                );
              })}

              <Typography variant="h6" sx={{ mt: 3, mb: 1 }}>Ticket queue</Typography>
              {(["open", "acknowledged", "dismissed"] as const).map((key) => {
                const value = data.tickets[key];
                const pct = Math.round((value / ticketTotal) * 100);
                const colors: Record<string, string> = { open: "#c62828", acknowledged: "#2e7d32", dismissed: "#8d99ae" };
                return (
                  <Box key={key} sx={{ mb: 1.5 }}>
                    <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
                      <Typography variant="body2" sx={{ textTransform: "capitalize", fontWeight: 600 }}>
                        {key}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">{value} ({pct}%)</Typography>
                    </Stack>
                    <LinearProgress
                      variant="determinate"
                      value={pct}
                      sx={{
                        height: 8, borderRadius: 4, bgcolor: "#eef1f6",
                        "& .MuiLinearProgress-bar": { bgcolor: colors[key], borderRadius: 4 },
                      }}
                    />
                  </Box>
                );
              })}
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, lg: 5 }}>
          <Stack spacing={2.5}>
            <Card>
              <CardContent>
                <Typography variant="h6" sx={{ mb: 1.5 }}>Open tickets by severity</Typography>
                {Object.keys(sev).length === 0 ? (
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ color: "success.main" }}>
                    <TaskAltIcon />
                    <Typography variant="body2">Nothing open - queue is clear.</Typography>
                  </Stack>
                ) : (
                  Object.entries(sev).map(([level, count]) => (
                    <Stack key={level} direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
                      <Chip size="small" label={level} color={severityColor[level] ?? "info"} />
                      <Typography variant="h6">{count}</Typography>
                    </Stack>
                  ))
                )}
              </CardContent>
            </Card>

            <Card>
              <CardContent>
                <Typography variant="h6" sx={{ mb: 1.5 }}>Scheduler</Typography>
                <Stack direction="row" justifyContent="space-between" alignItems="center">
                  <Box>
                    <Typography variant="body2" color="text.secondary">APScheduler</Typography>
                    <Typography variant="h6">{data.scheduler.jobs} job(s) loaded</Typography>
                  </Box>
                  <Chip
                    label={data.scheduler.running ? "RUNNING" : "STOPPED"}
                    color={data.scheduler.running ? "success" : "error"}
                  />
                </Stack>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.5 }}>
                  In production this maps to a Power Automate Cloud Flow firing the Python
                  audit engine per rule cadence.
                </Typography>
              </CardContent>
            </Card>
          </Stack>
        </Grid>
      </Grid>
    </Box>
  );
};