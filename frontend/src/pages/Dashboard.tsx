import { useEffect, useState } from "react";
import { Link } from "react-router";
import {
  Alert, Box, Card, Chip, LinearProgress, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, Tooltip, Typography,
} from "@mui/material";
import TaskAltIcon from "@mui/icons-material/TaskAlt";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { api } from "../dataProvider";
import { severityColor, FONT } from "../theme";
import { ENTITIES, useEntityScope } from "../entityScope";

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

interface Problem {
  id: number;
  rule_code: string;
  severity: string;
  summary: string;
  count: number;
  exceptions: number;
  age_days: number;
}

interface Assurance {
  entity: string | null;
  as_at: string | null;
  as_at_age_days: number;
  window_days: number;
  controls: {
    total: number;
    assured: number;
    not_assured: { code: string; name: string; status: string; validation_status: string }[];
    not_assured_count: number;
  };
  days: DayRow[];
  offenders: ControlRow[];
  gaps: ControlRow[];
  overdue: ControlRow[];
  open: {
    findings: number;
    exceptions: number;
    oldest_days: number;
    by_severity: { severity: string; findings: number; exceptions: number }[];
  };
  longest_open: Problem[];
  movement: {
    raised_findings: number;
    raised_exceptions: number;
    acknowledged_findings: number;
    ack_days: number | null;
  };
}

/* ================================================================== */
/* Vocabulary                                                          */
/* ================================================================== */
const PASSED = "#2e7d32";
const BREACH = "#c62828";
const ERRORED = "#ef6c00";
/** Deliberately the colour of an absence: nothing happened here, not "good". */
const UNCHECKED = "#c7d0de";
const ABSENT = "#b6c0cf";

const num = (n: number) => n.toLocaleString("en-GB");

const day = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
    weekday: "short", day: "2-digit", month: "short",
  });
const longDate = (iso: string | null) =>
  iso
    ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
        day: "numeric", month: "long", year: "numeric",
      })
    : "—";

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/**
 * The three words this page uses about a data date.
 *
 * The backend sends four states - passed, breach, errored, unchecked - because
 * those partition the rules exactly and that is worth asserting on. But they
 * are not words a head of audit thinks in, and "no opinion" is the internal
 * distinction leaking onto the screen. `passed + breach` is simply "the rules
 * that ran"; whatever is left over did not run at all.
 *
 * The VOCABULARY, since four words for related things is the actual problem:
 *
 *   rule             the thing that runs          LAD-CA-03
 *   audit observation  one rule found something    1 per rule, per day
 *   exception line     one item it flagged        119 in that same day
 *   ticket            what an auditor acknowledges one per observation
 *
 * An observation is counted per RULE, never per line. Read the other way it
 * turns "2 rules found something" into "119 findings" and loses the only figure
 * that says how much of the rule estate is engaged.
 */
const checkedOf = (d: DayRow) => d.passed + d.breach;

/* ================================================================== */
/* Shared furniture                                                    */
/* ================================================================== */
const SectionTitle = ({ children, aside }: { children: string; aside?: string }) => (
  <Stack direction="row" justifyContent="space-between" alignItems="baseline" sx={{ mb: 1.5 }}>
    <Typography variant="overline" color="text.secondary" sx={{ letterSpacing: 0.8 }}>
      {children}
    </Typography>
    {aside && <Typography variant="caption" color="text.secondary">{aside}</Typography>}
  </Stack>
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

const bodyCell = { py: 0.8, px: 1.5, borderColor: "#eef1f6", lineHeight: 1.4 };

const Sev = ({ level }: { level: string }) => (
  <Chip
    size="small" label={level} variant="outlined"
    color={severityColor[level] ?? "info"} sx={{ textTransform: "capitalize" }}
  />
);

/** A number that is zero reads as absent, not as a finding. */
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

/** Two lines, then an ellipsis. A finding summary is a sentence, not a cell. */
const Clamp = ({ children, lines = 2 }: { children: string; lines?: number }) => (
  <Typography
    variant="caption" color="text.secondary"
    sx={{
      display: "-webkit-box", WebkitLineClamp: lines, WebkitBoxOrient: "vertical",
      overflow: "hidden", lineHeight: 1.35,
    }}
  >
    {children}
  </Typography>
);

/**
 * How many of the client's rules actually looked at this day's data.
 *
 * "2 of 3" says in two words what three columns used to say in six: two ran,
 * one did not. The shortfall is amber and bold, because a rule that never ran
 * is the thing on this page most likely to go unnoticed.
 */
const RulesRan = ({ d }: { d: DayRow }) => {
  const ran = checkedOf(d);
  const short = d.assured - ran;
  return (
    <Stack
      direction="row" spacing={0.4} justifyContent="flex-end" alignItems="baseline"
      sx={{ fontVariantNumeric: "tabular-nums" }}
    >
      <Box component="span" sx={{ fontWeight: short ? 700 : 600 }}>
        {ran}
      </Box>
      <Typography
        component="span" variant="body2"
        sx={{ color: short ? "warning.main" : ABSENT }}
      >
        of {d.assured}
      </Typography>
    </Stack>
  );
};

/**
 * How many rules reported an audit observation that day.
 *
 * ONE PER RULE, not per exception line. A rule that flagged 119 items raised
 * one observation, and it happens to carry 119 exception lines - which is why
 * both columns survive. Reading an observation as a line would turn
 * "2 rules found something" into "119 findings" and lose the only figure that
 * says how much of the control estate is engaged.
 *
 * The colour rule is the other half: GREEN is reserved for a day where every
 * rule ran AND found nothing. A day where some rule never ran cannot go green
 * however clean the rest of it was, because "clean" is a claim about the rules
 * and an unrun rule has made none. That false clean is what this page exists to
 * refuse.
 */
const Observations = ({ d }: { d: DayRow }) => {
  if (d.breach > 0) {
    return (
      <Typography variant="body2" sx={{ fontWeight: 700, color: BREACH }}>
        {d.breach}
      </Typography>
    );
  }
  const complete = checkedOf(d) === d.assured;
  return (
    <Typography
      variant="body2"
      sx={{ color: complete ? PASSED : "text.secondary", fontWeight: complete ? 600 : 400 }}
    >
      None
    </Typography>
  );
};

/**
 * One data date as four segments. It is the only chart on the page, because it
 * is the only one a head of audit needs: green that the rule ran and found
 * nothing, red that it raised an observation, grey that it did not run at all.
 */
const DayBar = ({ d }: { d: DayRow }) => {
  const seg = (n: number, colour: string, label: string) =>
    n > 0 ? (
      <Tooltip key={label} title={`${n} ${plural(n, "rule", "rules")} — ${label}`}>
        <Box sx={{ width: `${(n / d.assured) * 100}%`, bgcolor: colour }} />
      </Tooltip>
    ) : null;
  // Only the parts that happened, so the tooltip reads as a sentence about this
  // day rather than as four numbers including the zeroes.
  const what = [
    d.passed && `${d.passed} ran, no observations`,
    d.breach && `${d.breach} raised an observation`,
    d.errored && `${d.errored} failed to run`,
    d.unchecked && `${d.unchecked} did not run`,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <Tooltip
      title={`${longDate(d.date)} — ${what || "no rules ran"} (of ${d.assured})`}
    >
      <Box sx={{ display: "flex", height: 9, borderRadius: 5, overflow: "hidden", bgcolor: "#eef1f6" }}>
        {seg(d.passed, PASSED, "ran, no observations")}
        {seg(d.breach, BREACH, "raised an observation")}
        {seg(d.errored, ERRORED, "failed to run")}
        {seg(d.unchecked, UNCHECKED, "did not run")}
      </Box>
    </Tooltip>
  );
};

/* ================================================================== */
/* The screen                                                          */
/* ================================================================== */
export const Dashboard = () => {
  const [data, setData] = useState<Assurance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { entity } = useEntityScope();
  const current = ENTITIES.find((e) => e.code === entity);

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
        Could not load the assurance position{error ? ` — ${error}` : ""}. Refusing
        to draw a partial picture that would read as a real count.
      </Alert>
    );
  }

  if (!data.window_days) {
    return (
      <Alert severity="info">
        No rule has run for {current?.name ?? entity} yet, so there is no
        assurance position to report. It appears here as soon as a rule executes.
      </Alert>
    );
  }

  // Most recent first. A briefing reads from the top, and the newest day is the
  // only one still open to change.
  const days = [...data.days].reverse();
  const latest = days[0];
  // The headline and the grid must count the same thing, or the top of the page
  // contradicts the table under it. "Ran" is passed+breach: a rule that tried to
  // run and failed has not checked anything, so it belongs in the shortfall,
  // exactly where the grid puts it.
  const missing = latest.assured - checkedOf(latest);
  const net = data.movement.raised_findings - data.movement.acknowledged_findings;
  const critical = data.open.by_severity.find((s) => s.severity === "critical");

  const Position = ({
    label, value, unit, sentence, tone,
  }: {
    label: string; value: string; unit?: string; sentence: string;
    tone?: "error" | "warning" | "success";
  }) => (
    <Box sx={{ px: 3, py: 2.25, flex: 1, minWidth: 210 }}>
      <Typography variant="overline" color="text.secondary">{label}</Typography>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mt: 0.25 }}>
        <Typography
          sx={{
            fontSize: "2.125rem", fontWeight: 700, lineHeight: 1.15, letterSpacing: -0.5,
            color:
              tone === "error" ? "error.main"
              : tone === "warning" ? "warning.main"
              : "text.primary",
          }}
        >
          {value}
        </Typography>
        {unit && <Typography variant="body2" color="text.secondary">{unit}</Typography>}
      </Stack>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
        {sentence}
      </Typography>
    </Box>
  );

  return (
    <Box>
      {/* --- header ------------------------------------------------------- */}
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" sx={{ mb: 2.5 }}>
        <Box>
          <Typography variant="h5">{current?.name ?? entity} — assurance position</Typography>
          <Typography variant="body2" color="text.secondary">
            {data.controls.assured} {plural(data.controls.assured, "rule", "rules")} in scope ·{" "}
            last {data.window_days} data {plural(data.window_days, "day", "days")}
          </Typography>
        </Box>
        <Box sx={{ textAlign: "right", flexShrink: 0 }}>
          <Typography variant="overline" color="text.secondary">Last checked</Typography>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>{longDate(data.as_at)}</Typography>
          {data.as_at_age_days > 1 && (
            <Typography variant="caption" color="warning.main" sx={{ display: "block" }}>
              {data.as_at_age_days} days ago — stale
            </Typography>
          )}
        </Box>
      </Stack>

      {/* --- the three questions ------------------------------------------ */}
      <Card sx={{ mb: 2.5 }}>
        <Box sx={{ display: "flex", flexWrap: "wrap", "& > * + *": { borderLeft: "1px solid #eef1f6" } }}>
          <Position
            label="Rules ran"
            // Always "N of M", never "All N" - "All 1" reads as a mistake, and
            // the colour already carries whether the two numbers agree.
            value={`${checkedOf(latest)} of ${latest.assured}`}
            tone={missing === 0 ? "success" : "warning"}
            sentence={
              missing === 0
                ? `every rule ran on ${day(latest.date)}`
                : `${missing} ${plural(missing, "rule did", "rules did")} not run on ${day(latest.date)}`
            }
          />
          <Position
            label="Open exposure"
            value={num(data.open.exceptions)}
            unit="exception lines"
            tone={data.open.findings ? "error" : "success"}
            sentence={
              data.open.findings
                ? `across ${num(data.open.findings)} open ${plural(data.open.findings, "ticket", "tickets")}` +
                  `${critical ? ` · ${num(critical.exceptions)} from critical` : ""}` +
                  ` · oldest ${data.open.oldest_days} ${plural(data.open.oldest_days, "day", "days")}`
                : "nothing outstanding"
            }
          />
          <Position
            label="Movement"
            value={`${net > 0 ? "+" : net < 0 ? "−" : ""}${num(Math.abs(net))}`}
            unit={plural(Math.abs(net), "ticket", "tickets")}
            tone={net > 0 ? "warning" : "success"}
            sentence={
              `${num(data.movement.raised_findings)} raised, ` +
              `${num(data.movement.acknowledged_findings)} acknowledged` +
              (data.movement.ack_days !== null
                ? ` · ${data.movement.ack_days}d to acknowledge`
                : " in the window")
            }
          />
        </Box>
      </Card>

      {/* --- only rendered when there is something to say --------------------
           Above the grid, not below it. A missed control is the first thing a
           head of audit needs, and the last thing they should have to scroll for. */}
      {data.overdue.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2, alignItems: "center" }}>
          <WarningAmberIcon sx={{ mr: 1.5, flexShrink: 0 }} />
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 700 }}>
              {data.overdue.length} scheduled {plural(data.overdue.length, "rule has", "rules have")} missed a data day
            </Typography>
            <Typography variant="caption" sx={{ display: "block" }}>
              {data.overdue
                .map((o) => `${o.code} did not run on ${o.missing_days} of the last ${data.window_days} days`)
                .join(" · ")}
              . Those days were not checked — which is not the same as clean.
            </Typography>
          </Box>
        </Alert>
      )}

      {data.controls.not_assured_count > 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <Typography variant="body2" sx={{ fontWeight: 700 }}>
            {data.controls.not_assured_count} {plural(data.controls.not_assured_count, "rule is", "rules are")} not yet assuring
          </Typography>
          <Typography variant="caption" sx={{ display: "block" }}>
            {data.controls.not_assured
              .map((c) => `${c.code} (${c.status}, ${c.validation_status})`)
              .join(" · ")}
            . Excluded from the figures above: a rule that was never switched
            on is not expected to have checked anything.
          </Typography>
        </Alert>
      )}

      {data.gaps.length > 0 && data.overdue.length === 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <Typography variant="body2" sx={{ fontWeight: 700 }}>On-demand coverage</Typography>
          <Typography variant="caption" sx={{ display: "block" }}>
            {data.gaps
              .map((o) => `${o.code} (${o.cadence.toLowerCase()}, covered ${o.reported_days} of ${data.window_days})`)
              .join(" · ")}
            . None has a cadence, so the days it missed are coverage notes rather
            than missed deadlines.
          </Typography>
        </Alert>
      )}

      {/* --- question 1: did the controls look? ----------------------------- */}
      <Box sx={{ mb: 3 }}>
        <SectionTitle aside={`${data.window_days} data days, most recent first`}>
          Daily assurance — which rules ran each day, and what they raised
        </SectionTitle>
        <Card>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ ...headCell, width: 170 }}>Date</TableCell>
                <TableCell sx={{ ...headCell, width: 110, textAlign: "right" }}>Rules ran</TableCell>
                <TableCell sx={{ ...headCell, width: 170 }}>Audit observations</TableCell>
                <TableCell sx={{ ...headCell, width: 150, textAlign: "right" }}>Exception lines</TableCell>
                <TableCell sx={{ ...headCell, width: 180 }}>Rule run coverage</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {days.map((d) => (
                <TableRow key={d.date} sx={{ bgcolor: d.date === latest.date ? "#f6faf9" : undefined }}>
                  <TableCell sx={{ ...bodyCell, fontWeight: 600, whiteSpace: "nowrap" }}>
                    {day(d.date)}
                    {d.date === latest.date && (
                      <Chip size="small" label="latest" sx={{ ml: 1, height: 20 }} />
                    )}
                  </TableCell>
                  <TableCell sx={bodyCell}><RulesRan d={d} /></TableCell>
                  <TableCell sx={bodyCell}><Observations d={d} /></TableCell>
                  <TableCell sx={{ ...bodyCell, textAlign: "right" }}>
                    <Maybe n={d.exceptions} emphasise />
                  </TableCell>
                  <TableCell sx={bodyCell}>
                    <DayBar d={d} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Stack
            direction="row" spacing={2.5} flexWrap="wrap" useFlexGap
            sx={{ px: 2, py: 1, borderTop: "1px solid #eef1f6" }}
          >
            {([[PASSED, "ran, no observations"], [BREACH, "raised an observation"],
               [ERRORED, "failed to run"], [UNCHECKED, "did not run"]] as const).map(
              ([colour, label]) => (
                <Stack key={label} direction="row" spacing={0.75} alignItems="center">
                  <Box sx={{ width: 10, height: 10, borderRadius: 3, bgcolor: colour, flexShrink: 0 }} />
                  <Typography variant="caption" color="text.secondary">{label}</Typography>
                </Stack>
              ),
            )}
          </Stack>
        </Card>
      </Box>

      {/* --- questions 2 and 3: what was found, and where to look first ------- */}
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", xl: "5fr 7fr" }, gap: 3 }}>
        <Box sx={{ minWidth: 0 }}>
          <SectionTitle aside="most observations first">Rules with observations</SectionTitle>
          <Card sx={{ height: "100%" }}>
            {data.offenders.length === 0 ? (
              <Stack direction="row" spacing={1} alignItems="center" sx={{ p: 2.5, color: "success.main" }}>
                <TaskAltIcon />
                <Typography variant="body2">
                  No rule raised an observation in this window.
                </Typography>
              </Stack>
            ) : (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell sx={headCell}>Rule</TableCell>
                    <TableCell sx={{ ...headCell, width: 92, textAlign: "right" }}>Days hit</TableCell>
                    <TableCell sx={{ ...headCell, width: 150, textAlign: "right" }}>Exception lines</TableCell>
                    <TableCell sx={{ ...headCell, width: 66, textAlign: "right" }}>Open</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {data.offenders.map((o) => (
                    <TableRow key={o.code}>
                      <TableCell sx={bodyCell}>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Sev level={o.severity} />
                          <Box sx={{ minWidth: 0 }}>
                            <Typography
                              component={Link} to={`/rules/${o.code}`}
                              variant="body2"
                              sx={{ fontWeight: 600, color: "primary.main", textDecoration: "none", fontFamily: "monospace" }}
                            >
                              {o.code}
                            </Typography>
                            <Clamp lines={1}>{o.name.replace(/^\S+\s+/, "")}</Clamp>
                          </Box>
                        </Stack>
                      </TableCell>
                      <TableCell sx={{ ...bodyCell, textAlign: "right", color: o.breached_days ? BREACH : ABSENT }}>
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
            )}
          </Card>
        </Box>

        <Box sx={{ minWidth: 0 }}>
          <SectionTitle aside="oldest first, repeats grouped">
            Open problems, longest waiting
          </SectionTitle>
          <Card sx={{ height: "100%" }}>
            {data.longest_open.length === 0 ? (
              <Stack direction="row" spacing={1} alignItems="center" sx={{ p: 2.5, color: "success.main" }}>
                <TaskAltIcon />
                <Typography variant="body2">Nothing open. The queue is clear.</Typography>
              </Stack>
            ) : (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ ...headCell, width: 84 }}>Severity</TableCell>
                    <TableCell sx={{ ...headCell, width: 80 }}>Rule</TableCell>
                    <TableCell sx={{ ...headCell, width: 64, textAlign: "right" }}>Tickets</TableCell>
                    <TableCell sx={{ ...headCell, width: 62, textAlign: "right" }}>Age</TableCell>
                    <TableCell sx={headCell}>What it found</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {data.longest_open.map((p) => (
                    <TableRow key={`${p.rule_code}-${p.id}`}>
                      <TableCell sx={bodyCell}><Sev level={p.severity} /></TableCell>
                      <TableCell sx={{ ...bodyCell, whiteSpace: "nowrap" }}>
                        <Typography
                          component={Link} to={`/tickets/${p.id}/show`}
                          variant="body2"
                          sx={{ fontWeight: 600, color: "primary.main", textDecoration: "none", fontFamily: "monospace" }}
                        >
                          {p.rule_code}
                        </Typography>
                      </TableCell>
                      <TableCell sx={{ ...bodyCell, textAlign: "right" }}>
                        {p.count > 1 ? (
                          <Tooltip title={`${p.count} separate ${plural(p.count, "ticket", "tickets")} — one per rule that raised an observation`}>
                            <Chip size="small" label={`×${p.count}`} sx={{ height: 20 }} />
                          </Tooltip>
                        ) : (
                          <Maybe n={1} />
                        )}
                      </TableCell>
                      <TableCell sx={{ ...bodyCell, textAlign: "right", fontWeight: 700, whiteSpace: "nowrap" }}>
                        {p.age_days}d
                      </TableCell>
                      <TableCell sx={bodyCell}><Clamp>{p.summary}</Clamp></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Card>
        </Box>
      </Box>
    </Box>
  );
};