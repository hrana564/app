import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import {
  Alert, Box, Card, Chip, LinearProgress, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, Tooltip, Typography, useTheme,
} from "@mui/material";
import TaskAltIcon from "@mui/icons-material/TaskAlt";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import ArrowUpwardIcon from "@mui/icons-material/ArrowUpward";
import ArrowDownwardIcon from "@mui/icons-material/ArrowDownward";
import RemoveIcon from "@mui/icons-material/Remove";
import { api } from "../dataProvider";
import { severityColor, SEVERITIES, FONT } from "../theme";
import { useEntityScope } from "../entityScope";
import { ruleShowPath } from "../rulePaths";

/* ================================================================== */
/* The payload                                                         */
/* ================================================================== */
interface DayRow {
  date: string;
  passed: number;
  breach: number;
  errored: number;
  unchecked: number;
  assured: number;
  reported: number;
  exceptions: number;
  findings: number;
}

interface AgeBand {
  key: string;
  label: string;
  findings: number;
  exceptions: number;
}

interface ControlRow {
  code: string;
  name: string;
  severity: string;
  cadence: string;
  scheduled: boolean;
  breached_days: number;
  reported_days: number;
  exceptions: number;
  open_findings: number;
  last_breach: string | null;
  missing_days: number;
  missing_dates: string[];
}

interface Finding {
  id: number;
  rule_code: string;
  severity: string;
  summary: string;
  count: number;
  exceptions: number;
  age_days: number;
  oldest_at: string;
  newest_at: string;
  repeat: boolean;
}

type Rating = "red" | "amber" | "green";

interface Assurance {
  entity: string | null;
  as_at: string | null;
  as_at_age_days: number;
  window_days: number;
  verdict: { rating: Rating; reason: string };
  controls: {
    total: number;
    assured: number;
    not_assured: { code: string; name: string; status: string; validation_status: string }[];
    not_assured_count: number;
  };
  days: DayRow[];
  offenders: ControlRow[];
  gaps: ControlRow[];
  gaps_count: number;
  overdue: ControlRow[];
  overdue_count: number;
  open: {
    findings: number;
    exceptions: number;
    oldest_days: number;
    by_severity: { severity: string; findings: number; exceptions: number }[];
    aging: AgeBand[];
    by_status: Record<string, number>;
  };
  priority: Finding[];
  movement: {
    raised_findings: number;
    raised_exceptions: number;
    previous_raised_findings: number;
    previous_raised_exceptions: number;
    acknowledged_findings: number;
    ack_days: number | null;
    has_previous: boolean;
  };
}

/* ================================================================== */
/* Vocabulary                                                          */
/* ================================================================== */
const PASSED = "#2e7d32";
const BREACH = "#c62828";
const ERRORED = "#ef6c00";
const UNCHECKED = "#c7d0de";
const ABSENT = "#b6c0cf";
const NEUTRAL = "#5b6b82";

const RATING: Record<Rating, { word: string; colour: string; tint: string; rule: string }> = {
  red: {
    word: "Needs attention",
    colour: BREACH,
    tint: "#fdf3f3",
    rule: "Red: a critical finding is open, or a scheduled control missed a data day.",
  },
  amber: {
    word: "Monitor",
    colour: ERRORED,
    tint: "#fff8f1",
    rule: "Amber: something is open, a control owes an opinion, or a control failed to run.",
  },
  green: {
    word: "Assured",
    colour: PASSED,
    tint: "#f3f8f4",
    rule: "Green: every control ran and nothing is open.",
  },
};

const num = (n: number) => n.toLocaleString("en-GB");
const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

const day = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
    weekday: "short", day: "2-digit", month: "short",
  });
const longDate = (iso: string | null) =>
  iso
    ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
        day: "numeric", month: "long", year: "numeric",
      })
    : "\u2014";

const checkedOf = (d: DayRow) => d.passed + d.breach;

const shortDay = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

const useSeverityTone = () => {
  const theme = useTheme();
  return (level: string) => theme.palette[severityColor[level] ?? "info"].main;
};

/* ================================================================== */
/* Furniture                                                           */
/* ================================================================== */
const Frame = ({ title, aside, children, flush = false, className }: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) => (
  <Card
    className={className}
    sx={{
      // No `height: 100%` here. Inside the grids below, `align-items: stretch`
      // already makes the frames in a row match. On the frames that are direct
      // children of the page - the daily evidence - `100%` resolves against the
      // whole page and the frame swallows it.
      display: "flex",
      flexDirection: "column",
      overflow: flush ? "hidden" : undefined,
    }}
  >
    <Stack
      direction="row"
      spacing={1}
      sx={{
        px: 2, pt: 1.75, pb: flush ? 1 : 0,
        justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap",
      }}
    >
      <Typography variant="overline" sx={{ letterSpacing: 0.8, color: "text.secondary" }}>
        {title}
      </Typography>
      {aside && (
        <Typography component="div" variant="caption" sx={{ color: "text.secondary" }}>
          {aside}
        </Typography>
      )}
    </Stack>
    <Box
      sx={{
        flex: 1,
        minWidth: 0,
        px: flush ? 0 : 2,
        pb: flush ? 0 : 1.75,
        display: "flex",
        flexDirection: "column",
        justifyContent: flush ? "flex-start" : "center",
      }}
    >
      {children}
    </Box>
  </Card>
);

const headCell = {
  fontWeight: 700,
  fontSize: FONT.label,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  color: "#5b6b82",
  backgroundColor: "#fafbfe",
  py: 0.75,
  px: 1.5,
  whiteSpace: "nowrap" as const,
};

const bodyCell = { py: 0.6, px: 1.5, borderColor: "#eef1f6", lineHeight: 1.4 };

const flushTable = {
  "& th:first-of-type, & td:first-of-type": { pl: 2 },
  "& th:last-of-type, & td:last-of-type": { pr: 2 },
};

const Sev = ({ level }: { level: string }) => (
  <Chip
    size="small" label={level} variant="outlined"
    color={severityColor[level] ?? "info"} sx={{ textTransform: "capitalize" }}
  />
);

const Maybe = ({ n, emphasise }: { n: number; emphasise?: boolean }) => (
  <Box
    component="span"
    sx={{
      color: n ? "text.primary" : ABSENT,
      fontWeight: n && emphasise ? 700 : 400,
      fontVariantNumeric: "tabular-nums",
    }}
  >
    {n}
  </Box>
);

const Clamp = ({ children, lines = 2 }: { children: string; lines?: number }) => (
  <Typography
    variant="caption"
    sx={{ display: "-webkit-box", WebkitLineClamp: lines, WebkitBoxOrient: "vertical", overflow: "hidden", lineHeight: 1.35, color: "text.secondary" }}
  >
    {children}
  </Typography>
);

interface BarRow {
  key: string;
  label: string;
  value: number;
  colour: string;
  note: string;
}

const BarRows = ({ rows, total, labelWidth }: { rows: BarRow[]; total: number; labelWidth: number }) => (
  <Stack spacing={0.4} sx={{ width: "100%" }}>
    {rows.map((r) => (
      <Tooltip key={r.key} title={r.note}>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Typography
            variant="caption"
            noWrap
            sx={{
              width: labelWidth, flexShrink: 0, textTransform: "capitalize",
              color: r.value ? "text.secondary" : ABSENT,
            }}
          >
            {r.label}
          </Typography>
          <Box sx={{ flex: 1, minWidth: 32, height: 8, borderRadius: 4, bgcolor: "#eef1f6", overflow: "hidden" }}>
            <Box
              sx={{
                width: `${total ? (r.value / total) * 100 : 0}%`,
                height: "100%",
                bgcolor: r.value ? r.colour : "transparent",
              }}
            />
          </Box>
          <Typography
            variant="caption"
            sx={{
              width: 22, flexShrink: 0, textAlign: "right", fontWeight: 700,
              fontVariantNumeric: "tabular-nums",
              color: r.value ? "text.primary" : ABSENT,
            }}
          >
            {r.value}
          </Typography>
        </Stack>
      </Tooltip>
    ))}
  </Stack>
);

/* ================================================================== */
/* 1. The verdict, as one line                                         */
/* ================================================================== */
const VerdictStrip = ({ verdict }: { verdict: Assurance["verdict"] }) => {
  const tone = RATING[verdict.rating];
  return (
    <Card sx={{ mb: 2, borderLeft: `5px solid ${tone.colour}`, bgcolor: tone.tint }}>
      <Stack
        direction="row"
        spacing={1.25}
        sx={{ px: 2, py: 1.1, alignItems: "center", flexWrap: "wrap", rowGap: 0.25 }}
      >
        <Tooltip title={tone.rule}>
          <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", cursor: "help" }}>
            <Box sx={{ width: 10, height: 10, borderRadius: 5, bgcolor: tone.colour, flexShrink: 0 }} />
            <Typography
              variant="body1"
              sx={{
                fontWeight: 700, color: tone.colour, whiteSpace: "nowrap",
                textDecoration: "underline dotted", textUnderlineOffset: 3,
              }}
            >
              {tone.word}
            </Typography>
          </Stack>
        </Tooltip>
        <Typography variant="body2" sx={{ color: "text.secondary", minWidth: 0 }}>
          {cap(verdict.reason)}.
        </Typography>
      </Stack>
    </Card>
  );
};

/* ================================================================== */
/* 2. What, how old, which way - one row                               */
/* ================================================================== */
const SeverityChart = ({ open }: { open: Assurance["open"] }) => {
  const tone = useSeverityTone();
  const rows: BarRow[] = SEVERITIES.map((severity) => {
    const hit = open.by_severity.find((r) => r.severity === severity);
    const findings = hit?.findings ?? 0;
    const exceptions = hit?.exceptions ?? 0;
    return {
      key: severity,
      label: severity,
      value: findings,
      colour: tone(severity),
      note:
        `${findings} open ${plural(findings, "finding", "findings")} · ` +
        `${num(exceptions)} exception ${plural(exceptions, "line", "lines")}` +
        (open.findings ? ` · ${Math.round((findings / open.findings) * 100)}% of the open backlog` : ""),
    };
  });
  return <BarRows rows={rows} total={open.findings} labelWidth={66} />;
};

const AgeChart = ({ open }: { open: Assurance["open"] }) => {
  const rows: BarRow[] = open.aging.map((band) => ({
    key: band.key,
    // The band's own key, because the frame's title already says these are days
    // and "Over 90 days" is four times the width of "90+" for no more meaning.
    // The full label is in the tooltip.
    label: band.key.replace("-", "\u2013"),
    value: band.findings,
    // The open-ended band is the one that matters, so it is the one that is
    // coloured. Everything under 90 days is grey: neither a pass nor a fault.
    colour: band.key === "90+" ? BREACH : NEUTRAL,
    note:
      `${band.label} · ${band.findings} open ${plural(band.findings, "finding", "findings")} · ` +
      `${num(band.exceptions)} exception ${plural(band.exceptions, "line", "lines")}`,
  }));
  return <BarRows rows={rows} total={open.findings} labelWidth={46} />;
};

const Spark = ({ days }: { days: DayRow[] }) => {
  const peak = Math.max(1, ...days.map((d) => d.findings));
  const first = days[0];
  const last = days[days.length - 1];
  return (
    <Stack direction="row" spacing={1.5} sx={{ alignItems: "flex-end" }}>
      <Stack direction="row" spacing={0.4} sx={{ alignItems: "flex-end", height: 30 }}>
        {days.map((d) => (
          <Tooltip
            key={d.date}
            title={`${day(d.date)} \u2014 ${d.findings} ${plural(d.findings, "finding", "findings")} raised, ${num(d.exceptions)} exception ${plural(d.exceptions, "line", "lines")}`}
          >
            <Box
              sx={{
                width: 12,
                height: `${Math.max(8, (d.findings / peak) * 100)}%`,
                bgcolor: d.findings ? BREACH : UNCHECKED,
                borderRadius: 0.5,
              }}
            />
          </Tooltip>
        ))}
      </Stack>
      {first && last && (
        <Typography variant="caption" sx={{ color: "text.secondary", whiteSpace: "nowrap", pb: 0.25 }}>
          {shortDay(first.date)} – {shortDay(last.date)}
        </Typography>
      )}
    </Stack>
  );
};

const TravelChart = ({ movement, days, backlog }: {
  movement: Assurance["movement"]; days: DayRow[]; backlog: Record<string, number>;
}) => {
  const raised = movement.raised_findings;
  const delta = raised - movement.previous_raised_findings;

  // No baseline is not the same as no movement. With one window of history
  // every estate looks like it is rising from nothing, which is a statement
  // about how long the portal has been running, not about the controls.
  const direction = !movement.has_previous
    ? { word: "No prior period", colour: "text.secondary", Icon: RemoveIcon }
    : delta > 0
      ? { word: "Rising", colour: BREACH, Icon: ArrowUpwardIcon }
      : delta < 0
        ? { word: "Receding", colour: PASSED, Icon: ArrowDownwardIcon }
        : { word: "Level", colour: "text.secondary", Icon: RemoveIcon };

  const line = { color: "text.secondary", display: "block" };

  return (
    <Stack spacing={0.6} sx={{ width: "100%" }}>
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
        <direction.Icon sx={{ fontSize: 17, color: direction.colour }} />
        <Typography variant="body2" sx={{ fontWeight: 700, color: direction.colour }}>
          {direction.word}
        </Typography>
      </Stack>
      <Spark days={days} />
      <Tooltip title={`${num(movement.raised_exceptions)} exception ${plural(movement.raised_exceptions, "line", "lines")} in the same window`}>
        <Typography variant="caption" sx={{ ...line, cursor: "help" }}>
          <strong>{num(raised)}</strong> raised ·{" "}
          <strong>{num(movement.acknowledged_findings)}</strong> acknowledged
          {movement.has_previous
            ? ` · ${num(movement.previous_raised_findings)} the period before`
            : ""}
        </Typography>
      </Tooltip>
      <Typography variant="caption" sx={line}>
        Backlog <strong>{num(backlog.open ?? 0)}</strong> open ·{" "}
        <strong>{num(backlog.acknowledged ?? 0)}</strong> acknowledged ·{" "}
        <strong>{num(backlog.dismissed ?? 0)}</strong> dismissed
      </Typography>
    </Stack>
  );
};

/* ================================================================== */
/* 3. Did the controls look?                                           */
/* ================================================================== */
const NOT_RUN = "A control that did not run has no opinion about the day. It is not a day it found nothing.";
const FAILED = "The control errored before it could answer, so it has no opinion about the day either.";
const NOT_ASSURING = "Never switched on, or not yet past the validation gate. It owes no opinion yet, so it is not counted as a gap.";

const Figure = ({ value, label, tone, hint }: {
  value: string; label: string; tone?: string; hint?: string;
}) => {
  const body = (
    <Typography variant="body2" sx={{ color: "text.secondary", whiteSpace: "nowrap" }}>
      <Box
        component="span"
        sx={{ fontWeight: 700, color: tone ?? "text.primary", fontVariantNumeric: "tabular-nums" }}
      >
        {value}
      </Box>{" "}
      {label}
    </Typography>
  );
  return hint ? <Tooltip title={hint}><Box component="span" sx={{ cursor: "help" }}>{body}</Box></Tooltip> : body;
};

const CoverageStrip = ({ data }: { data: Assurance }) => {
  const latest = data.days[data.days.length - 1];
  const ran = checkedOf(latest);
  const onDemand = data.gaps.filter((g) => !g.scheduled);

  const notes: ReactNode[] = [];
  if (data.overdue_count > 0) {
    notes.push(
      <Box component="span" key="overdue" sx={{ color: "warning.main", fontWeight: 600 }}>
        <WarningAmberIcon sx={{ fontSize: 15, mr: 0.5, verticalAlign: "middle" }} />
        {data.overdue_count} scheduled {plural(data.overdue_count, "control", "controls")} missed a day
        {data.overdue.length
          ? ` \u2014 ${data.overdue.map((o) => `${o.code} on ${o.missing_days} of ${data.window_days}`).join(", ")}`
          : ""}
        {data.overdue_count > data.overdue.length ? ` (worst ${data.overdue.length} shown)` : ""}
      </Box>,
    );
  }
  if (onDemand.length) {
    notes.push(
      <Box component="span" key="ondemand">
        on demand: {onDemand.map((o) => `${o.code} ${o.reported_days}/${data.window_days}`).join(", ")} —
        no cadence, so a coverage note rather than a missed deadline
      </Box>,
    );
  }
  if (data.controls.not_assured_count) {
    notes.push(
      <Box component="span" key="notassuring">
        not yet assuring: {data.controls.not_assured.map((c) => c.code).join(", ")}
        {data.controls.not_assured_count > data.controls.not_assured.length
          ? ` and ${data.controls.not_assured_count - data.controls.not_assured.length} more`
          : ""}
      </Box>,
    );
  }

  return (
    <Card sx={{ mb: 2, px: 2, py: 1.1 }}>
      <Stack direction="row" spacing={2.5} sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 0.5 }}>
        <Typography variant="overline" sx={{ color: "text.secondary", letterSpacing: 0.8 }}>
          Control coverage
        </Typography>
        <Figure
          value={`${ran} of ${latest.assured}`}
          label={`ran on ${day(latest.date)}`}
          tone={ran === latest.assured ? PASSED : ERRORED}
        />
        <Figure value={num(latest.errored)} label="failed to run" tone={latest.errored ? ERRORED : undefined} hint={FAILED} />
        <Figure value={num(latest.unchecked)} label="did not run" tone={latest.unchecked ? ERRORED : undefined} hint={NOT_RUN} />
        <Figure
          value={`${num(data.controls.not_assured_count)} of ${data.controls.total}`}
          label="not yet assuring"
          hint={NOT_ASSURING}
        />
      </Stack>
      {notes.length > 0 && (
        <Typography variant="caption" sx={{ display: "block", mt: 0.6, color: "text.secondary" }}>
          {notes.map((n, i) => (
            <Box component="span" key={i}>
              {i > 0 && " \u00b7 "}
              {n}
            </Box>
          ))}
        </Typography>
      )}
    </Card>
  );
};

/* ================================================================== */
/* 4. What needs a decision                                            */
/* ================================================================== */
const PriorityTable = ({ findings }: { findings: Finding[] }) => (
  <Table size="small" sx={flushTable}>
    <TableHead>
      <TableRow>
        <TableCell sx={{ ...headCell, width: 84 }}>Severity</TableCell>
        <TableCell sx={{ ...headCell, width: 112 }}>Control</TableCell>
        <TableCell sx={{ ...headCell, width: 56, textAlign: "right" }}>Age</TableCell>
        <TableCell sx={{ ...headCell, width: 62, textAlign: "right" }}>Findings</TableCell>
        <TableCell sx={headCell}>What it found</TableCell>
      </TableRow>
    </TableHead>
    <TableBody>
      {findings.map((f) => (
        <TableRow key={`${f.rule_code}-${f.id}`}>
          <TableCell sx={bodyCell}>
            <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
              <Sev level={f.severity} />
              {f.repeat && (
                <Tooltip title={`${f.count} findings raised by this control, one per day it breached - none of them closed`}>
                  <Chip size="small" label="repeat" variant="outlined" color="warning" />
                </Tooltip>
              )}
            </Stack>
          </TableCell>
          <TableCell sx={bodyCell}>
            <Typography
              component={Link} to={`/tickets/${f.id}/show`}
              variant="body2"
              noWrap
              sx={{ fontWeight: 600, color: "primary.main", textDecoration: "none", fontFamily: "monospace" }}
            >
              {f.rule_code}
            </Typography>
          </TableCell>
          <TableCell sx={{ ...bodyCell, textAlign: "right", fontWeight: 700, whiteSpace: "nowrap" }}>
            {f.age_days}d
          </TableCell>
          <TableCell sx={{ ...bodyCell, textAlign: "right" }}>
            {f.count > 1 ? (
              <Tooltip title={`${f.count} separate findings - the control raised one on each day it breached`}>
                <Chip size="small" label={`\u00d7${f.count}`} sx={{ height: 20 }} />
              </Tooltip>
            ) : (
              <Maybe n={1} />
            )}
          </TableCell>
          <TableCell sx={bodyCell}><Clamp>{f.summary}</Clamp></TableCell>
        </TableRow>
      ))}
    </TableBody>
  </Table>
);

const ControlsTable = ({ rows }: { rows: ControlRow[] }) => {
  const tone = useSeverityTone();
  return (
    <Table size="small" sx={flushTable}>
      <TableHead>
        <TableRow>
          <TableCell sx={headCell}>Control</TableCell>
          {/* Tighter than the priority table's columns on purpose. Side by side
              at 1366 this card is ~450px, and at its old widths the control code
              had ~176px for a chip, the code and the name - so the code itself
              wrapped onto two lines, which reads as a different control. */}
          <TableCell sx={{ ...headCell, width: 64, textAlign: "right" }}>Days hit</TableCell>
          <TableCell sx={{ ...headCell, width: 84, textAlign: "right" }}>Exceptions</TableCell>
          <TableCell sx={{ ...headCell, width: 52, textAlign: "right" }}>Open</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map((o) => (
          <TableRow key={o.code}>
            <TableCell sx={bodyCell}>
              <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                <Sev level={o.severity} />
                <Box sx={{ minWidth: 0 }}>
                  <Typography
                    component={Link} to={ruleShowPath(o.code)}
                    variant="body2"
                    noWrap
                    sx={{ fontWeight: 600, color: "primary.main", textDecoration: "none", fontFamily: "monospace" }}
                  >
                    {o.code}
                  </Typography>
                  <Clamp lines={1}>{o.name.replace(/^\S+\s+/, "")}</Clamp>
                </Box>
              </Stack>
            </TableCell>
            <TableCell sx={{ ...bodyCell, textAlign: "right", color: o.breached_days ? tone(o.severity) : ABSENT }}>
              <Maybe n={o.breached_days} emphasise />
            </TableCell>
            <TableCell sx={{ ...bodyCell, textAlign: "right" }}>
              <Maybe n={o.exceptions} emphasise />
            </TableCell>
            <TableCell sx={{ ...bodyCell, textAlign: "right" }}>
              <Maybe n={o.open_findings} emphasise />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
};

/* ================================================================== */
/* 5. The evidence                                                     */
/* ================================================================== */
const KEY: ReadonlyArray<readonly [string, string]> = [
  [PASSED, "ran, nothing found"],
  [BREACH, "raised a finding"],
  [ERRORED, "failed to run"],
  [UNCHECKED, "did not run"],
];

const Key = () => (
  <Stack direction="row" spacing={1.5} useFlexGap sx={{ flexWrap: "wrap", alignItems: "center", rowGap: 0.25 }}>
    {KEY.map(([colour, label]) => (
      <Stack key={label} direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
        <Box sx={{ width: 9, height: 9, borderRadius: 2, bgcolor: colour, flexShrink: 0 }} />
        <Typography variant="caption" sx={{ color: "text.secondary", whiteSpace: "nowrap" }}>
          {label}
        </Typography>
      </Stack>
    ))}
  </Stack>
);

const DayBar = ({ d }: { d: DayRow }) => {
  const seg = (n: number, colour: string, label: string) =>
    n > 0 ? (
      <Tooltip key={label} title={`${n} ${plural(n, "control", "controls")} \u2014 ${label}`}>
        <Box sx={{ width: `${(n / d.assured) * 100}%`, bgcolor: colour }} />
      </Tooltip>
    ) : null;
  const what = [
    d.passed && `${d.passed} ran, nothing found`,
    d.breach && `${d.breach} raised a finding`,
    d.errored && `${d.errored} failed to run`,
    d.unchecked && `${d.unchecked} did not run`,
  ]
    .filter(Boolean)
    .join(" \u00b7 ");
  return (
    <Tooltip title={`${longDate(d.date)} \u2014 ${what || "nothing ran"} (of ${d.assured})`}>
      <Box sx={{ display: "flex", height: 9, borderRadius: 5, overflow: "hidden", bgcolor: "#eef1f6" }}>
        {seg(d.passed, PASSED, "ran, nothing found")}
        {seg(d.breach, BREACH, "raised a finding")}
        {seg(d.errored, ERRORED, "failed to run")}
        {seg(d.unchecked, UNCHECKED, "did not run")}
      </Box>
    </Tooltip>
  );
};

const DailyGrid = ({ days }: { days: DayRow[] }) => {
  const latest = days[0];
  return (
    <Table size="small" sx={flushTable}>
      <TableHead>
        <TableRow>
          <TableCell sx={{ ...headCell, width: 180 }}>Data date</TableCell>
          <TableCell sx={{ ...headCell, width: 120, textAlign: "right" }}>Controls ran</TableCell>
          <TableCell sx={{ ...headCell, width: 160, textAlign: "right" }}>Findings raised</TableCell>
          <TableCell sx={{ ...headCell, width: 140, textAlign: "right" }}>Exceptions</TableCell>
          <TableCell sx={{ ...headCell, width: 220 }}>Coverage</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {days.map((d) => {
          const ran = checkedOf(d);
          const short = d.assured - ran;
          return (
            <TableRow key={d.date} sx={{ bgcolor: d.date === latest.date ? "#f6faf9" : undefined }}>
              <TableCell sx={{ ...bodyCell, fontWeight: 600, whiteSpace: "nowrap" }}>
                {day(d.date)}
                {d.date === latest.date && (
                  <Chip size="small" label="latest" sx={{ ml: 1, height: 20 }} />
                )}
              </TableCell>
              <TableCell sx={{ ...bodyCell, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                <Box component="span" sx={{ fontWeight: short ? 700 : 600 }}>{ran}</Box>
                <Typography component="span" variant="body2" sx={{ color: short ? "warning.main" : ABSENT }}>
                  {" "}of {d.assured}
                </Typography>
              </TableCell>
              <TableCell sx={{ ...bodyCell, textAlign: "right" }}>
                {d.breach > 0 ? (
                  <Typography variant="body2" sx={{ fontWeight: 700, color: BREACH }}>{d.breach}</Typography>
                ) : (
                  <Typography
                    variant="body2"
                    sx={{
                      color: ran === d.assured ? PASSED : "text.secondary",
                      fontWeight: ran === d.assured ? 600 : 400,
                    }}
                  >
                    None
                  </Typography>
                )}
              </TableCell>
              <TableCell sx={{ ...bodyCell, textAlign: "right" }}>
                <Maybe n={d.exceptions} emphasise />
              </TableCell>
              <TableCell sx={bodyCell}><DayBar d={d} /></TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
};

/* ================================================================== */
/* The screen                                                          */
/* ================================================================== */
export const Dashboard = () => {
  const [data, setData] = useState<Assurance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { entity, entities } = useEntityScope();
  const current = entities.find((e) => e.code === entity);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    api
      .dashboard(entity)
      .then((d: Assurance) => live && setData(d))
      .catch((e: Error) => live && setError(e.message))
      // Without this a failed load left `loading` true forever: a blank page
      // under a progress bar that never stops. A page that cannot load says so.
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [entity]);

  if (loading) return <LinearProgress />;

  if (!data) {
    return (
      <Alert severity="error">
        Could not load the assurance position{error ? ` \u2014 ${error}` : ""}. Refusing
        to draw a partial picture that would read as a real count.
      </Alert>
    );
  }

  if (!data.window_days) {
    return (
      <Alert severity="info">
        No control has run for {current?.name ?? entity} yet, so there is no
        assurance position to report. It appears here as soon as a control executes.
      </Alert>
    );
  }

  // Most recent first for the grid - a briefing reads from the top. The
  // sparkline wants the opposite, so it is given the payload's own order.
  const days = [...data.days].reverse();

  return (
    <Box>
      {/* --- header ------------------------------------------------------- */}
      <Stack
        direction="row"
        sx={{ mb: 1.5, justifyContent: "space-between", alignItems: "flex-start" }}
      >
        <Box>
          <Typography variant="h5">{current?.name ?? entity} — assurance position</Typography>
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            {data.controls.assured} {plural(data.controls.assured, "control", "controls")} assuring ·{" "}
            last {data.window_days} data {plural(data.window_days, "day", "days")} · Part of SquareOne Holdings
          </Typography>
        </Box>
        <Box sx={{ textAlign: "right", flexShrink: 0 }}>
          <Typography variant="overline" sx={{ color: "text.secondary" }}>Last checked</Typography>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>{longDate(data.as_at)}</Typography>
          {data.as_at_age_days > 1 && (
            <Typography variant="caption" sx={{ display: "block", color: "warning.main" }}>
              {data.as_at_age_days} days ago — stale
            </Typography>
          )}
        </Box>
      </Stack>

      {/* --- 1. the verdict, one line -------------------------------------- */}
      <VerdictStrip verdict={data.verdict} />

      {/* --- 2. what, how old, which way ----------------------------------- */}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", lg: "repeat(3, 1fr)" },
          gap: 2,
          mb: 2,
          alignItems: "stretch",
        }}
      >
        <Frame title="Exposure by severity" aside="open findings, worst first">
          <SeverityChart open={data.open} />
        </Frame>
        <Frame
          title="Age of open findings"
          aside={`oldest ${data.open.oldest_days} ${plural(data.open.oldest_days, "day", "days")}`}
        >
          <AgeChart open={data.open} />
        </Frame>
        <Frame title="Direction of travel" aside={`${data.window_days} data days`}>
          <TravelChart movement={data.movement} days={data.days} backlog={data.open.by_status} />
        </Frame>
      </Box>

      {/* --- 3. did the controls look? ------------------------------------- */}
      <CoverageStrip data={data} />

      {/* --- 4. what needs a decision -------------------------------------- */}
      {/* Two columns from `lg`, not `xl`: at 1366 the briefings the two tables
          hold are the last thing above the fold, and stacking them there put
          the second one 350px below it. */}
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "7fr 5fr" }, gap: 2, mb: 2 }}>
        <Frame
          title="Findings that need a decision"
          aside="worst first, oldest first within a severity"
          flush
        >
          {data.priority.length === 0 ? (
            <Stack direction="row" spacing={1} sx={{ px: 2, pb: 2, color: "success.main", alignItems: "center" }}>
              <TaskAltIcon />
              <Typography variant="body2">Nothing open. The queue is clear.</Typography>
            </Stack>
          ) : (
            <PriorityTable findings={data.priority} />
          )}
        </Frame>

        <Frame title="Controls that found something" aside="most days breached first" flush>
          {data.offenders.length === 0 ? (
            <Stack direction="row" spacing={1} sx={{ px: 2, pb: 2, color: "success.main", alignItems: "center" }}>
              <TaskAltIcon />
              <Typography variant="body2">
                No control raised a finding in this window.
              </Typography>
            </Stack>
          ) : (
            <ControlsTable rows={data.offenders} />
          )}
        </Frame>
      </Box>

      {/* --- 5. the evidence ------------------------------------------------ */}
      <Frame
        title="Daily evidence — which controls ran, and what they found"
        aside={<Key />}
        flush
        className="no-print"
      >
        <DailyGrid days={days} />
      </Frame>
    </Box>
  );
};
