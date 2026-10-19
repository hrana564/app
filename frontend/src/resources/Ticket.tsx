import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  List, Pagination, ShowBase, TopToolbar,
  useListContext, useNotify, useRecordContext, useRefresh,
} from "react-admin";
import { useNavigate } from "react-router-dom";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, Divider, MenuItem, Stack,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField as MuiTextField, Tooltip, Typography,
} from "@mui/material";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import ReplayIcon from "@mui/icons-material/Replay";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import DoneAllIcon from "@mui/icons-material/DoneAll";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import { api } from "../dataProvider";
import { DotLabel, RowsTable } from "../components/ui";
import { severityColor, statusColor, FONT } from "../theme";

const STATUS_OPTIONS = ["open", "acknowledged", "dismissed"] as const;
const SEVERITY_OPTIONS = ["critical", "high", "medium", "low"] as const;

/**
 * How the inbox can be grouped - the ServiceNow "Group by" pattern, with a
 * None that removes grouping entirely.
 */
const GROUPINGS = [
  { value: "rule_code", label: "Rule" },
  { value: "severity", label: "Severity" },
  { value: "status", label: "Status" },
  { value: "", label: "No grouping" },
] as const;

type GroupKey = (typeof GROUPINGS)[number]["value"];

/** Rule code -> name, so group headers can say what the rule actually is. */
const useRuleNames = () => {
  const [names, setNames] = useState<Record<string, string>>({});
  useEffect(() => {
    fetch("/api/rules?perPage=200&sort=code&order=ASC", {
      headers: { Authorization: `Bearer ${localStorage.getItem("abm.token")}` },
    })
      .then((r) => (r.ok ? r.json() : { data: [] }))
      .then((d) => {
        const map: Record<string, string> = {};
        (d.data ?? []).forEach((r: any) => {
          map[r.code] = r.name;
        });
        setNames(map);
      })
      .catch(() => setNames({}));
  }, []);
  return names;
};

const headCellSx = {
  fontWeight: 700,
  fontSize: FONT.label,
  textTransform: "uppercase" as const,
  letterSpacing: 0.5,
  color: "#5b6b82",
  backgroundColor: "#fafbfe",
  py: 1.25,
  whiteSpace: "nowrap" as const,
};

const SeverityDot = ({ value }: { value: string }) => (
  <DotLabel value={value} tone={severityColor[value] ?? "default"} />
);
const StatusDot = ({ value }: { value: string }) => (
  <DotLabel value={value} tone={statusColor[value] ?? "default"} />
);

/* ================================================================== */
/* Acknowledgement - the auditor's primary action                      */
/* ================================================================== */
const AcknowledgeButton = ({ record: explicit }: { record?: any }) => {
  const contextRecord = useRecordContext<any>();
  const record = explicit ?? contextRecord;
  const notify = useNotify();
  const refresh = useRefresh();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);

  if (!record || record.status !== "open") return null;

  const submit = async (status: "acknowledged" | "dismissed") => {
    setPending(true);
    try {
      await api.acknowledgeTicket(record.id, status, note);
      notify(`${record.ticket_no} ${status}`, { type: "success" });
      setOpen(false);
      setNote("");
      refresh();
    } catch (e: any) {
      notify(`Failed: ${e.message}`, { type: "error" });
    } finally {
      setPending(false);
    }
  };

  return (
    <>
      <Button
        size="small"
        variant="contained"
        startIcon={<CheckCircleOutlineIcon />}
        onClick={(e) => { e.stopPropagation(); setOpen(true); }}
      >
        Acknowledge
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>
          Acknowledge {record.ticket_no}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {record.summary}
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <MuiTextField
              label="Auditor note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              multiline
              minRows={3}
              fullWidth
              placeholder="What was the outcome of your review?"
              helperText="Recorded against your username with a timestamp - this is the audit trail."
            />
            <Alert severity="info">
              Acknowledging marks this ticket reviewed. It does not close the underlying
              control - the next scheduled run will still raise a ticket if the breach persists.
            </Alert>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)} disabled={pending}>Cancel</Button>
          <Button onClick={() => submit("dismissed")} disabled={pending} color="inherit">
            Dismiss
          </Button>
          <Button
            onClick={() => submit("acknowledged")}
            disabled={pending}
            variant="contained"
            startIcon={pending ? <CircularProgress size={16} color="inherit" /> : undefined}
          >
            Acknowledge
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

const ReopenButton = ({ record: explicit }: { record?: any }) => {
  const contextRecord = useRecordContext<any>();
  const record = explicit ?? contextRecord;
  const notify = useNotify();
  const refresh = useRefresh();
  if (!record || record.status === "open") return null;
  return (
    <Button
      size="small"
      startIcon={<ReplayIcon />}
      onClick={async (e) => {
        e.stopPropagation();
        try {
          await api.reopenTicket(record.id);
          notify(`${record.ticket_no} reopened`, { type: "info" });
          refresh();
        } catch (err: any) {
          notify(`Failed: ${err.message}`, { type: "error" });
        }
      }}
    >
      Reopen
    </Button>
  );
};

/* ================================================================== */
/* Bulk acknowledgement                                               */
/* ================================================================== */
export interface BulkScope {
  /** What the auditor is about to act on, in plain words. */
  describe: string;
  count: number;
  payload: { rule_code?: string; severity?: string; q?: string };
}

const BulkAckDialog = ({
  scope, onClose,
}: {
  scope: BulkScope | null;
  onClose: () => void;
}) => {
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const notify = useNotify();
  const refresh = useRefresh();

  useEffect(() => {
    setNote("");
  }, [scope]);

  if (!scope) return null;
  const nothingToDo = scope.count === 0;

  const run = async (status: "acknowledged" | "dismissed") => {
    setPending(true);
    try {
      const r = await api.acknowledgeAllTickets({ ...scope.payload, status, note });
      notify(
        `${r.acknowledged} ticket${r.acknowledged === 1 ? "" : "s"} ${status}`,
        { type: "success" },
      );
      refresh();
      onClose();
    } catch (e: any) {
      notify(`Failed: ${e.message}`, { type: "error" });
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open onClose={pending ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {scope.count} ticket{scope.count === 1 ? "" : "s"}
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          {scope.describe}
        </Typography>
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {nothingToDo ? (
            <Alert severity="info">
              Nothing is open in this scope, so there is nothing to acknowledge.
            </Alert>
          ) : (
            <>
              <Alert severity="warning">
                This records <strong>{scope.count}</strong>{" "}
                {scope.count === 1 ? "ticket" : "tickets"} against your username
                with one timestamp. It cannot be undone from here - tickets have to
                be reopened individually.
              </Alert>
              <MuiTextField
                label="Auditor note (applied to every ticket)"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                multiline
                minRows={2}
                fullWidth
                placeholder="e.g. Reviewed with the finance team; no vendor master update was due."
                helperText="Leave blank to record the acknowledgement with no note."
              />
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={pending}>Cancel</Button>
        {!nothingToDo && (
          <>
            <Button onClick={() => run("dismissed")} disabled={pending} color="inherit">
              Dismiss all
            </Button>
            <Button
              onClick={() => run("acknowledged")}
              disabled={pending}
              variant="contained"
              startIcon={pending ? <CircularProgress size={16} color="inherit" /> : <DoneAllIcon />}
            >
              Acknowledge {scope.count}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
};

/* ================================================================== */
/* Toolbar - search, filters, grouping, bulk                           */
/* ================================================================== */
const TicketToolbar = ({
  onBulk, groupBy, setGroupBy,
}: {
  onBulk: (s: BulkScope) => void;
  groupBy: GroupKey;
  setGroupBy: (g: GroupKey) => void;
}) => {
  const { setFilters, filterValues } = useListContext<any>();
  const ruleNames = useRuleNames();
  const [filters, setLocal] = useState({
    q: "",
    status: "",
    severity: "",
    rule_code: "",
  });

  // Read through a ref: setFilters changes identity every time the store does,
  // and depending on it re-runs this effect on every render. An unguarded call
  // never converges - react-admin rebuilds filterValues each time.
  const applyRef = useRef({ setFilters, filterValues });
  applyRef.current = { setFilters, filterValues };

  useEffect(() => {
    const handle = setTimeout(() => {
      const next: Record<string, string> = {};
      (Object.keys(filters) as (keyof typeof filters)[]).forEach((k) => {
        if (filters[k]) next[k] = filters[k];
      });
      const { setFilters: apply, filterValues: live } = applyRef.current;
      const liveValues: Record<string, unknown> = live ?? {};
      const changed =
        Object.keys(next).length !== Object.keys(liveValues).length ||
        Object.keys(next).some((k) => liveValues[k] !== next[k]);
      if (changed) apply(next, "ignored");
    }, 300);
    return () => clearTimeout(handle);
  }, [filters]);

  const set = (patch: Partial<typeof filters>) => setLocal((f) => ({ ...f, ...patch }));
  const active = Object.values(filters).some(Boolean);

  const Picker = ({
    label, options, value, onPick,
  }: {
    label: string;
    options: readonly string[];
    value: string;
    onPick: (v: string) => void;
  }) => (
    <MuiTextField select size="small" label={label} value={value}
      onChange={(e) => onPick(e.target.value)} sx={{ minWidth: 140 }}>
      <MenuItem value="">
        <em>All</em>
      </MenuItem>
      {options.map((o) => (
        <MenuItem key={o} value={o} sx={{ textTransform: "capitalize" }}>{o}</MenuItem>
      ))}
    </MuiTextField>
  );

  return (
    <TopToolbar>
      <MuiTextField size="small" placeholder="Search ticket no, summary…"
        value={filters.q} onChange={(e) => set({ q: e.target.value })} sx={{ minWidth: 240 }} />
      <Picker label="status" options={STATUS_OPTIONS} value={filters.status}
        onPick={(v) => set({ status: v })} />
      <Picker label="severity" options={SEVERITY_OPTIONS} value={filters.severity}
        onPick={(v) => set({ severity: v })} />
      <MuiTextField select size="small" label="rule" value={filters.rule_code}
        onChange={(e) => set({ rule_code: e.target.value })} sx={{ minWidth: 170 }}>
        <MenuItem value=""><em>All</em></MenuItem>
        {Object.keys(ruleNames).sort().map((code) => (
          <MenuItem key={code} value={code}>{code}</MenuItem>
        ))}
      </MuiTextField>

      {active && (
        <Button size="small"
          onClick={() => setLocal({ q: "", status: "", severity: "", rule_code: "" })}>
          Clear
        </Button>
      )}

      <Box sx={{ flex: 1 }} />

      <MuiTextField select size="small" label="Group by" value={groupBy}
        onChange={(e) => setGroupBy(e.target.value as GroupKey)} sx={{ minWidth: 160 }}>
        {GROUPINGS.map((g) => (
          <MenuItem key={g.value || "none"} value={g.value}>{g.label}</MenuItem>
        ))}
      </MuiTextField>

      <Button variant="contained" startIcon={<DoneAllIcon />}
        onClick={() =>
          onBulk({
            describe: filters.rule_code
              ? `raised by ${filters.rule_code}`
              : filters.severity
                ? `at ${filters.severity} severity`
                : filters.q
                  ? `matching “${filters.q}”`
                  : "across every rule, ignoring filters",
            count: 0,
            payload: {
              rule_code: filters.rule_code || undefined,
              severity: filters.severity || undefined,
              q: filters.q || undefined,
            },
          })
        }>
        Acknowledge all
      </Button>
    </TopToolbar>
  );
};

/* ================================================================== */
/* The inbox                                                           */
/* ================================================================== */
interface Group {
  key: string;
  label: string;
  sublabel?: string;
  tickets: any[];
}

const TicketInbox = ({
  onBulk, groupBy,
}: {
  onBulk: (s: BulkScope) => void;
  groupBy: GroupKey;
}) => {
  const { data, isPending } = useListContext<any>();
  const ruleNames = useRuleNames();
  // Tracks what is OPEN, not what is closed. Empty means every group is shut,
  // which is the state an inbox should open in: four rules with counts, rather
  // than twenty-five rows before the eye has decided what to look at.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const navigate = useNavigate();

  const tickets: any[] = useMemo(() => data ?? [], [data]);

  const groups: Group[] = useMemo(() => {
    if (!groupBy) {
      return [{ key: "all", label: "", tickets }];
    }
    const map = new Map<string, any[]>();
    tickets.forEach((t) => {
      const key = String(t[groupBy] ?? "—");
      (map.get(key) ?? map.set(key, []).get(key)!).push(t);
    });
    return Array.from(map.entries())
      .map(([key, rows]) => {
        const open = rows.filter((r) => r.status === "open").length;
        if (groupBy === "rule_code") {
          return {
            key,
            label: key,
            sublabel: ruleNames[key],
            tickets: rows,
          };
        }
        return {
          key,
          label: key.charAt(0).toUpperCase() + key.slice(1),
          sublabel: open === rows.length ? undefined : `${open} open of ${rows.length}`,
          tickets: rows,
        };
      })
      // Most alarming first: critical before high, open before closed.
      .sort((a, b) => {
        if (groupBy === "severity") {
          const order = ["critical", "high", "medium", "low"];
          return order.indexOf(a.key) - order.indexOf(b.key);
        }
        return a.label.localeCompare(b.label);
      });
  }, [tickets, groupBy, ruleNames]);

  const openCount = tickets.filter((t) => t.status === "open").length;

  // Expansion must not survive a change of result set. A group opened before a
  // search would otherwise still be open over an entirely different set of
  // tickets, which reads as stale rather than remembered.
  const signature = useMemo(() => tickets.map((t) => t.id).join(","), [tickets]);
  useEffect(() => {
    setExpanded({});
  }, [signature]);

  const allExpanded = groups.length > 0 && groups.every((g) => expanded[g.key]);
  const toggleAll = () =>
    setExpanded(
      allExpanded ? {} : Object.fromEntries(groups.map((g) => [g.key, true])),
    );

  if (isPending) {
    return (
      <Box sx={{ p: 4, textAlign: "center" }}><CircularProgress size={26} /></Box>
    );
  }

  if (!tickets.length) {
    return (
      <Box sx={{ p: 6, textAlign: "center" }}>
        <Typography variant="body1" color="text.secondary">
          No tickets match these filters.
        </Typography>
      </Box>
    );
  }

  const severityMix = (rows: any[]) => {
    const counts = new Map<string, number>();
    rows.forEach((r) => counts.set(r.severity, (counts.get(r.severity) ?? 0) + 1));
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  };

  return (
    <>
      <Box sx={{ px: 2, py: 1, bgcolor: "#fafbfe", borderBottom: "1px solid #eef1f6" }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2" color="text.secondary">
            <strong>{tickets.length}</strong> ticket{tickets.length === 1 ? "" : "s"} on this page ·{" "}
            <strong>{openCount}</strong> still open
            {groupBy ? ` · grouped by ${groupBy.replace(/_/g, " ")}` : ""}
          </Typography>
          <Box sx={{ flex: 1 }} />
          {groupBy && groups.length > 1 && (
            <Button size="small" onClick={toggleAll}>
              {allExpanded ? "Collapse all" : "Expand all"}
            </Button>
          )}
        </Stack>
      </Box>

      <TableContainer>
        <Table size="small" sx={{ "& td, & th": { borderColor: "#eef1f6" } }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ ...headCellSx, width: 190 }}>Ticket</TableCell>
              <TableCell sx={{ ...headCellSx, width: 120 }}>Severity</TableCell>
              <TableCell sx={{ ...headCellSx, width: 150 }}>Status</TableCell>
              <TableCell sx={headCellSx}>Finding</TableCell>
              <TableCell sx={{ ...headCellSx, width: 80 }}>Rows</TableCell>
              <TableCell sx={{ ...headCellSx, width: 170 }}>Raised</TableCell>
              <TableCell align="right" sx={{ ...headCellSx, width: 210 }}>Actions</TableCell>
            </TableRow>
          </TableHead>

          <TableBody>
            {groups.map((group) => {
              // Ungrouped views have nothing to open, so they stay flat.
              const isCollapsed = Boolean(groupBy && group.label) && !expanded[group.key];
              const groupOpen = group.tickets.filter((t) => t.status === "open").length;
              return (
                <Fragment key={group.key}>
                  {group.label && (
                    <TableRow
                      hover
                      sx={{ bgcolor: "#eef2f8", cursor: "pointer" }}
                      onClick={() =>
                        setExpanded((prev) => ({ ...prev, [group.key]: !prev[group.key] }))
                      }
                    >
                      <TableCell colSpan={7} sx={{ py: 1 }}>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Box
                            role="button"
                            aria-label={
                              isCollapsed ? `Expand ${group.label}` : `Collapse ${group.label}`
                            }
                            onClick={(e) => {
                              // The whole header row is also clickable. Without
                              // this the chevron toggles and then bubbles to the
                              // row, which toggles again - so nothing appears to
                              // happen.
                              e.stopPropagation();
                              setExpanded((prev) => ({ ...prev, [group.key]: !prev[group.key] }));
                            }}
                            sx={{
                              width: 26, height: 26, display: "flex", alignItems: "center",
                              justifyContent: "center", borderRadius: 1, cursor: "pointer",
                              color: "text.secondary", "&:hover": { bgcolor: "action.hover" },
                            }}
                          >
                            {isCollapsed
                              ? <ChevronRightIcon fontSize="small" />
                              : <ExpandMoreIcon fontSize="small" />}
                          </Box>

                          <Typography variant="body2"
                            sx={{ fontFamily: "monospace", fontWeight: 700 }}>
                            {group.label}
                          </Typography>

                          {group.sublabel && (
                            <Typography variant="body2" color="text.secondary" noWrap>
                              {group.sublabel}
                            </Typography>
                          )}

                          <Stack direction="row" spacing={0.75} sx={{ ml: 0.5 }}>
                            {severityMix(group.tickets).map(([sev, n]) => (
                              <Chip key={sev} size="small" variant="outlined"
                                label={`${n} ${sev}`}
                                sx={{ textTransform: "capitalize", fontSize: FONT.label }} />
                            ))}
                          </Stack>

                          <Box sx={{ flex: 1 }} />

                          <Typography variant="caption" color="text.secondary" sx={{ mr: 1 }}>
                            {group.tickets.length} ticket{group.tickets.length === 1 ? "" : "s"}
                            {groupOpen !== group.tickets.length ? ` · ${groupOpen} open` : ""}
                          </Typography>

                          {groupBy === "rule_code" && groupOpen > 0 && (
                            <Tooltip title={`Acknowledge every open ticket from ${group.label}`}>
                              <span>
                                <Button size="small" startIcon={<DoneAllIcon />}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onBulk({
                                      describe: `raised by ${group.label}`,
                                      count: groupOpen,
                                      payload: { rule_code: group.label },
                                    });
                                  }}>
                                  Acknowledge all {groupOpen}
                                </Button>
                              </span>
                            </Tooltip>
                          )}
                        </Stack>
                      </TableCell>
                    </TableRow>
                  )}

                  {!isCollapsed &&
                    group.tickets.map((t) => (
                      <TableRow key={t.id} hover sx={{ cursor: "pointer" }}
                        onClick={() => navigate(`/tickets/${t.id}/show`)}>
                        <TableCell sx={{ pl: 5 }}>
                          <Typography variant="body2"
                            sx={{ fontFamily: "monospace", fontWeight: 700 }}>
                            {t.ticket_no}
                          </Typography>
                        </TableCell>
                        <TableCell><SeverityDot value={t.severity} /></TableCell>
                        <TableCell><StatusDot value={t.status} /></TableCell>
                        <TableCell>
                          <Typography variant="body2" sx={{ lineHeight: 1.45 }}>{t.summary}</Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
                            {t.rows?.length ?? 0}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color="text.secondary">
                            {new Date(t.created_at).toLocaleString()}
                          </Typography>
                        </TableCell>
                        <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                          <Stack direction="row" spacing={0.5} justifyContent="flex-end"
                            alignItems="center">
                            {t.acknowledged_by && (
                              <Tooltip
                                title={`${t.acknowledged_by} · ${new Date(t.acknowledged_at).toLocaleString()}${t.ack_note ? ` · ${t.ack_note}` : ""}`}
                              >
                                <Chip size="small" label={t.acknowledged_by}
                                  sx={{ fontSize: FONT.label }} />
                              </Tooltip>
                            )}
                            <AcknowledgeButton record={t} />
                            <ReopenButton record={t} />
                          </Stack>
                        </TableCell>
                      </TableRow>
                    ))}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </>
  );
};

/* ================================================================== */
/* List                                                                */
/* ================================================================== */
export const TicketList = () => {
  const [scope, setScope] = useState<BulkScope | null>(null);
  const [groupBy, setGroupBy] = useState<GroupKey>("rule_code");

  // A group already knows how many open tickets it holds. The toolbar button
  // does not, so ask the API how many the scope holds before opening the dialog
  // - never guess a number in a confirmation.
  const openScope = (s: BulkScope) => {
    if (s.count) {
      setScope(s);
      return;
    }
    const params = new URLSearchParams({ status: "open", perPage: "1" });
    if (s.payload.rule_code) params.set("rule_code", s.payload.rule_code);
    if (s.payload.severity) params.set("severity", s.payload.severity);
    if (s.payload.q) params.set("q", s.payload.q);
    fetch(`/api/tickets?${params}`, {
      headers: { Authorization: `Bearer ${localStorage.getItem("abm.token")}` },
    })
      .then((r) => r.json())
      .then((d) => setScope({ ...s, count: d.total ?? 0 }))
      .catch(() => setScope({ ...s, count: 0 }));
  };

  return (
    <>
      <List
        actions={<TicketToolbar onBulk={openScope} groupBy={groupBy} setGroupBy={setGroupBy} />}
        sort={{ field: "created_at", order: "DESC" }}
        perPage={25}
        pagination={<Pagination rowsPerPageOptions={[10, 25, 50, 100]} />}
      >
        <TicketInbox onBulk={openScope} groupBy={groupBy} />
      </List>
      <BulkAckDialog scope={scope} onClose={() => setScope(null)} />
    </>
  );
};

/* ================================================================== */
/* Show                                                                */
/* ================================================================== */
const Fact = ({ label, value }: { label: string; value: React.ReactNode }) => (
  <Box>
    <Typography variant="caption" color="text.secondary"
      sx={{ textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 700 }}>
      {label}
    </Typography>
    <Typography variant="body2" sx={{ mt: 0.25, fontWeight: 600 }}>{value}</Typography>
  </Box>
);

const TicketDetails = () => {
  const record = useRecordContext<any>();
  const navigate = useNavigate();

  useEffect(() => {
    if (record?.ticket_no) document.title = `${record.ticket_no} · ABM Audit Portal`;
  }, [record?.ticket_no]);

  if (!record) return null;

  const acknowledged = Boolean(record.acknowledged_by);

  return (
    <Stack spacing={2}>
      {/* Header: the ticket number is the identity of this page */}
      <Card>
        <CardContent>
          <Stack direction="row" justifyContent="space-between" alignItems="flex-start"
            spacing={2} sx={{ mb: 1.5 }}>
            <Box>
              <Typography variant="h5" sx={{ fontFamily: "monospace", fontWeight: 700 }}>
                {record.ticket_no}
              </Typography>
              <Stack direction="row" spacing={1} sx={{ mt: 1 }} alignItems="center">
                <SeverityDot value={record.severity} />
                <StatusDot value={record.status} />
              </Stack>
            </Box>
            <Stack direction="row" spacing={1}>
              <AcknowledgeButton record={record} />
              <ReopenButton record={record} />
            </Stack>
          </Stack>

          <Typography variant="body1" sx={{ fontSize: FONT.bodyAlt, lineHeight: 1.5 }}>
            {record.summary}
          </Typography>

          <Divider sx={{ my: 2 }} />

          <Box sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr 1fr", sm: "repeat(4, 1fr)" },
            gap: 2,
          }}>
            <Fact
              label="Rule"
              value={
                <Box
                  component="span"
                  onClick={() => navigate(`/rules/${encodeURIComponent(record.rule_code)}/show`)}
                  sx={{ cursor: "pointer", color: "primary.main", "&:hover": { textDecoration: "underline" } }}
                >
                  {record.rule_code}
                  <ArrowForwardIcon sx={{ fontSize: 14, ml: 0.5, verticalAlign: "middle" }} />
                </Box>
              }
            />
            <Fact
              label="Run"
              value={
                <Box component="span" onClick={() => navigate(`/runs/${record.run_id}/show`)}
                  sx={{ cursor: "pointer", color: "primary.main", "&:hover": { textDecoration: "underline" } }}>
                  #{record.run_id}
                  <ArrowForwardIcon sx={{ fontSize: 14, ml: 0.5, verticalAlign: "middle" }} />
                </Box>
              }
            />
            <Fact label="Exception rows" value={record.rows?.length ?? 0} />
            <Fact
              label="Raised"
              value={new Date(record.created_at).toLocaleString()}
            />
            <Fact
              label="Trigger"
              value={record.schedule_id ? `schedule #${record.schedule_id}` : "manual run"}
            />
          </Box>
        </CardContent>
      </Card>

      {/* Acknowledgement: the audit trail for this ticket */}
      <Card sx={{ borderLeft: acknowledged ? "3px solid #2e7d32" : "3px solid #ef6c00" }}>
        <CardContent>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="h6">Acknowledgement</Typography>
          </Stack>

          {acknowledged ? (
            <Stack spacing={1}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" gap={1}>
                <Typography variant="body2" sx={{ fontWeight: 700 }}>
                  {record.acknowledged_by}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {new Date(record.acknowledged_at).toLocaleString()}
                </Typography>
                <Chip size="small" label={record.status}
                  color={record.status === "dismissed" ? "info" : "success"}
                  sx={{ textTransform: "capitalize" }} />
              </Stack>
              <Typography variant="body2" color={record.ack_note ? "text.primary" : "text.secondary"}
                sx={{ fontStyle: record.ack_note ? "normal" : "italic" }}>
                {record.ack_note || "No note was recorded with this acknowledgement."}
              </Typography>
            </Stack>
          ) : (
            <Alert severity="warning">
              Not yet reviewed. Acknowledging records your username and the time
              against this ticket. It does not close the underlying control - the
              next scheduled run will raise again if the breach persists.
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* The evidence */}
      <Card>
        <CardContent>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
            <Typography variant="h6">Exception rows</Typography>
            <Typography variant="body2" color="text.secondary">
              {record.rows?.length ?? 0} row{(record.rows?.length ?? 0) === 1 ? "" : "s"}
            </Typography>
          </Stack>
          <RowsTable rows={record.rows ?? []} />
        </CardContent>
      </Card>
    </Stack>
  );
};

export const TicketShow = () => (
  <ShowBase>
    <TicketDetails />
  </ShowBase>
);