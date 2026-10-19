import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ShowBase, useNotify, useRecordContext, useRefresh } from "react-admin";
import { useNavigate } from "react-router";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, Divider, MenuItem, Stack,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TablePagination, TextField as MuiTextField, Toolbar, Tooltip, Typography,
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
import { useEntityScope } from "../entityScope";

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
    api.listRules()
      .then((r) => {
        const map: Record<string, string> = {};
        (r.data ?? []).forEach((rule: any) => {
          map[rule.code] = rule.name;
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
              control - the next scheduled run will still raise a ticket if the observation recurs.
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
interface BulkScope {
  describe: string;
  count: number;
  payload: {
    rule_code?: string;
    severity?: string;
    q?: string;
    status?: string;
    /** The client the inbox is scoped to. Part of the scope, not a detail. */
    entity?: string;
  };
}

const BulkAckDialog = ({
  scope, onClose, onDone,
}: {
  scope: BulkScope | null;
  onClose: () => void;
  onDone: () => void;
}) => {
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const notify = useNotify();

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
      onDone();
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
                with one timestamp. It does not stop at the end of this page - it
                covers every open ticket matching the scope.
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
/* The inbox                                                           */
/* ================================================================== */
interface Group {
  key: string;
  label?: string;
  total: number;
  open: number;
  severity_mix: Record<string, number>;
  status_mix: Record<string, number>;
  tickets: any[];
}

const severityOrder = ["critical", "high", "medium", "low"];

/** Wrapped in a TableBody because a bare <tr> may not sit directly under <table>. */
const TableFooterRow = ({ children }: { children: React.ReactNode }) => (
  <TableBody>
    <TableRow>
      <TableCell colSpan={5} sx={{ py: 1, bgcolor: "#fafbfe", borderTop: "1px solid #eef1f6" }}>
        <Stack direction="row" spacing={1} alignItems="center">{children}</Stack>
      </TableCell>
    </TableRow>
  </TableBody>
);

const GroupedInbox = ({
  groups, totalGroups, totalTickets, expanded, onToggle, onToggleAll, onBulk,
}: {
  groups: Group[];
  totalGroups: number;
  totalTickets: number;
  expanded: Record<string, boolean>;
  onToggle: (key: string) => void;
  onToggleAll: () => void;
  onBulk: (s: BulkScope) => void;
}) => {
  const navigate = useNavigate();
  const allExpanded = groups.length > 0 && groups.every((g) => expanded[g.key]);

  return (
    <>
      {/* The header describes the GROUP, because the group is what a row here is. */}
      <TableHead>
        <TableRow>
          <TableCell sx={{ ...headCellSx, width: "26%" }}>Rule</TableCell>
          <TableCell sx={{ ...headCellSx, width: 150 }}>Severity</TableCell>
          <TableCell sx={{ ...headCellSx, width: 120 }}>Open</TableCell>
          <TableCell sx={headCellSx}>Tickets</TableCell>
          <TableCell align="right" sx={{ ...headCellSx, width: 190 }}>Actions</TableCell>
        </TableRow>
      </TableHead>

      <TableBody>
        {groups.map((group) => {
          const isOpen = Boolean(expanded[group.key]);
          const newest = group.tickets[0]?.created_at;
          return (
            <Fragment key={group.key}>
              <TableRow
                hover
                sx={{ bgcolor: "#eef2f8", cursor: "pointer" }}
                onClick={() => onToggle(group.key)}
              >
                <TableCell sx={{ py: 1.25 }}>
                  <Stack direction="row" spacing={0.5} alignItems="center">
                    <Box
                      role="button"
                      aria-label={isOpen ? `Collapse ${group.key}` : `Expand ${group.key}`}
                      onClick={(e) => {
                        // The row is clickable too; without this the chevron
                        // toggles and then bubbles and toggles back again.
                        e.stopPropagation();
                        onToggle(group.key);
                      }}
                      sx={{
                        width: 26, height: 26, display: "flex", alignItems: "center",
                        justifyContent: "center", borderRadius: 1, cursor: "pointer",
                        color: "text.secondary", "&:hover": { bgcolor: "action.hover" },
                      }}
                    >
                      {isOpen
                        ? <ExpandMoreIcon fontSize="small" />
                        : <ChevronRightIcon fontSize="small" />}
                    </Box>
                    <Box sx={{ minWidth: 0 }}>
                      <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 700 }}>
                        {group.key}
                      </Typography>
                      {group.label && (
                        <Typography variant="body2" color="text.secondary" noWrap>
                          {group.label}
                        </Typography>
                      )}
                    </Box>
                  </Stack>
                </TableCell>

                <TableCell>
                  <Stack direction="row" spacing={0.5} flexWrap="wrap" gap={0.5}>
                    {severityOrder
                      .filter((s) => group.severity_mix[s])
                      .map((s) => (
                        <Chip key={s} size="small" variant="outlined"
                          label={`${group.severity_mix[s]} ${s}`}
                          sx={{ fontSize: FONT.label, textTransform: "capitalize" }} />
                      ))}
                  </Stack>
                </TableCell>

                <TableCell>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {group.open}
                    <Typography component="span" variant="body2" color="text.secondary">
                      {" "}of {group.total}
                    </Typography>
                  </Typography>
                </TableCell>

                <TableCell>
                  <Typography variant="body2" color="text.secondary">
                    most recent {newest ? new Date(newest).toLocaleString() : "-"}
                  </Typography>
                </TableCell>

                <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                  {group.open > 0 ? (
                    <Button size="small" startIcon={<DoneAllIcon />}
                      onClick={() =>
                        onBulk({
                          describe: `raised by ${group.key}`,
                          count: group.open,
                          payload: { rule_code: group.key },
                        })
                      }>
                      Acknowledge all {group.open}
                    </Button>
                  ) : (
                    <Typography variant="body2" color="text.secondary">all reviewed</Typography>
                  )}
                </TableCell>
              </TableRow>

              {isOpen &&
                group.tickets.map((t) => (
                  <TableRow key={t.id} hover sx={{ cursor: "pointer" }}
                    onClick={() => navigate(`/tickets/${t.id}/show`)}>
                    <TableCell sx={{ pl: 5 }}>
                      <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 700 }}>
                        {t.ticket_no}
                      </Typography>
                    </TableCell>
                    <TableCell><SeverityDot value={t.severity} /></TableCell>
                    <TableCell><StatusDot value={t.status} /></TableCell>
                    <TableCell>
                      <Typography variant="body2" sx={{ lineHeight: 1.45 }}>{t.summary}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {t.rows?.length ?? 0} row{(t.rows?.length ?? 0) === 1 ? "" : "s"} Â·{" "}
                        {new Date(t.created_at).toLocaleString()}
                        {t.acknowledged_by ? ` Â· reviewed by ${t.acknowledged_by}` : ""}
                      </Typography>
                    </TableCell>
                    <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                      <Stack direction="row" spacing={0.5} justifyContent="flex-end">
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

      {groups.length > 1 && (
        <TableFooterRow>
          <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
            <strong>{groups.length}</strong> of <strong>{totalGroups}</strong>{" "}
            {totalGroups === 1 ? "group" : "groups"} Â·{" "}
            <strong>{totalTickets}</strong> ticket{totalTickets === 1 ? "" : "s"} in total
          </Typography>
          <Button size="small" onClick={onToggleAll}>
            {allExpanded ? "Collapse all" : "Expand all"}
          </Button>
        </TableFooterRow>
      )}
    </>
  );
};

/* ------------------------------------------------------------------ */
const FlatInbox = ({ tickets, total }: { tickets: any[]; total: number }) => {
  const navigate = useNavigate();
  return (
    <>
      <TableHead>
        <TableRow>
          <TableCell sx={{ ...headCellSx, width: 180 }}>Ticket</TableCell>
          <TableCell sx={{ ...headCellSx, width: 120 }}>Severity</TableCell>
          <TableCell sx={{ ...headCellSx, width: 140 }}>Status</TableCell>
          <TableCell sx={headCellSx}>Observation</TableCell>
          <TableCell sx={{ ...headCellSx, width: 80 }}>Rows</TableCell>
          <TableCell sx={{ ...headCellSx, width: 170 }}>Raised</TableCell>
          <TableCell align="right" sx={{ ...headCellSx, width: 200 }}>Actions</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {tickets.map((t) => (
          <TableRow key={t.id} hover sx={{ cursor: "pointer" }}
            onClick={() => navigate(`/tickets/${t.id}/show`)}>
            <TableCell>
              <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 700 }}>
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
              <Stack direction="row" spacing={0.5} justifyContent="flex-end" alignItems="center">
                {t.acknowledged_by && (
                  <Tooltip
                    title={`${t.acknowledged_by} Â· ${new Date(t.acknowledged_at).toLocaleString()}${t.ack_note ? ` Â· ${t.ack_note}` : ""}`}
                  >
                    <Chip size="small" label={t.acknowledged_by} sx={{ fontSize: FONT.label }} />
                  </Tooltip>
                )}
                <AcknowledgeButton record={t} />
                <ReopenButton record={t} />
              </Stack>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </>
  );
};

/* ================================================================== */
/* List - owns its own fetching and paging                             */
/* ================================================================== */
export const TicketList = () => {
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState({ status: "", severity: "", rule_code: "" });
  const [groupBy, setGroupBy] = useState<GroupKey>("rule_code");
  const [page, setPage] = useState(0);
  const [perPage, setPerPage] = useState(25);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [scope, setScope] = useState<BulkScope | null>(null);
  const ruleNames = useRuleNames();
  const { entity } = useEntityScope();

  // Debounced search, so typing does not fire a request per keystroke.
  const [q, setQ] = useState("");
  useEffect(() => {
    const handle = setTimeout(() => {
      setQ(search);
      setPage(0);
    }, 300);
    return () => clearTimeout(handle);
  }, [search]);

  const nonceRef = useRef(0);

  const load = useCallback(() => {
    const nonce = ++nonceRef.current;
    setLoading(true);
    const params: Record<string, string | undefined> = {
      q: q || undefined,
      status: filters.status || undefined,
      severity: filters.severity || undefined,
      rule_code: filters.rule_code || undefined,
      // Part of what is on screen, so it belongs in the request that draws it.
      entity,
      page: String(page + 1),
      perPage: String(perPage),
    };
    const request = groupBy
      ? api.groupedTickets({ ...params, group_by: groupBy })
      : api.listTickets(params);

    request
      .then((d) => {
        if (nonce === nonceRef.current) setData(d);
      })
      .catch(() => {
        if (nonce === nonceRef.current) setData(null);
      })
      .finally(() => {
        if (nonce === nonceRef.current) setLoading(false);
      });
  }, [q, filters, groupBy, page, perPage, entity]);

  useEffect(() => {
    load();
  }, [load]);

  // Any change of result set re-shuts the groups, so an open group never sits
  // over a completely different set of tickets.
  const signature = useMemo(
    () =>
      groupBy
        ? (data?.groups ?? []).map((g: Group) => g.key).join(",")
        : (data?.data ?? []).map((t: any) => t.id).join(","),
    [data, groupBy],
  );
  useEffect(() => {
    setExpanded({});
  }, [signature]);

  const groups: Group[] = data?.groups ?? [];
  const tickets: any[] = data?.data ?? [];
  const rowCount = groupBy ? groups.length : tickets.length;
  const totalCount = groupBy ? (data?.total_groups ?? 0) : (data?.total ?? 0);
  const ticketTotal = groupBy ? (data?.total_tickets ?? 0) : (data?.total ?? 0);
  const openOnPage = groupBy
    ? groups.reduce((n, g) => n + g.open, 0)
    : tickets.filter((t) => t.status === "open").length;

  const onToggle = (key: string) =>
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));
  const onToggleAll = () =>
    setExpanded(
      groups.every((g) => expanded[g.key])
        ? {}
        : Object.fromEntries(groups.map((g) => [g.key, true])),
    );

  /** Count the scope from the server - never from the rows on screen. */
  const onBulk = (s: BulkScope) => {
    if (s.count) {
      setScope(s);
      return;
    }
    const params: Record<string, string | undefined> = {
      status: "open",
      rule_code: s.payload.rule_code,
      severity: s.payload.severity,
      q: s.payload.q,
      // Same client scope the page is drawn with. Without it the confirmation
      // dialog would count one client's tickets and the button behind it would
      // acknowledge both clients'.
      entity,
    };
    api.listTickets({ ...params, perPage: "1" })
      .then((d) => setScope({ ...s, count: d.total }))
      .catch(() => setScope({ ...s, count: 0 }));
  };

  const describe = filters.rule_code
    ? `raised by ${filters.rule_code}`
    : filters.severity
      ? `at ${filters.severity} severity`
      : q
        ? `matching "${q}"`
        : "across every rule, ignoring the other filters";

  const activeFilter = Boolean(q || filters.status || filters.severity || filters.rule_code);

  const Picker = ({
    label, options, value, onPick, width = 140,
  }: {
    label: string; options: readonly string[]; value: string;
    onPick: (v: string) => void; width?: number;
  }) => (
    <MuiTextField select size="small" label={label} value={value}
      onChange={(e) => onPick(e.target.value)} sx={{ minWidth: width }}>
      <MenuItem value=""><em>All</em></MenuItem>
      {options.map((o) => (
        <MenuItem key={o} value={o} sx={{ textTransform: "capitalize" }}>{o}</MenuItem>
      ))}
    </MuiTextField>
  );

  return (
    <>
      <Card>
        <Toolbar sx={{ gap: 1, flexWrap: "wrap", py: 1.5 }}>
          <MuiTextField size="small" placeholder="Search ticket no, summaryâ€¦"
            value={search} onChange={(e) => setSearch(e.target.value)} sx={{ minWidth: 240 }} />
          <Picker label="status" options={STATUS_OPTIONS} value={filters.status}
            onPick={(v) => { setFilters((f) => ({ ...f, status: v })); setPage(0); }} />
          <Picker label="severity" options={SEVERITY_OPTIONS} value={filters.severity}
            onPick={(v) => { setFilters((f) => ({ ...f, severity: v })); setPage(0); }} />
          <MuiTextField select size="small" label="rule" value={filters.rule_code}
            onChange={(e) => { setFilters((f) => ({ ...f, rule_code: e.target.value })); setPage(0); }}
            sx={{ minWidth: 170 }}>
            <MenuItem value=""><em>All</em></MenuItem>
            {Object.keys(ruleNames).sort().map((code) => (
              <MenuItem key={code} value={code}>{code}</MenuItem>
            ))}
          </MuiTextField>

          {activeFilter && (
            <Button size="small" onClick={() => {
              setSearch("");
              setFilters({ status: "", severity: "", rule_code: "" });
              setPage(0);
            }}>
              Clear
            </Button>
          )}

          <Box sx={{ flex: 1 }} />

          <MuiTextField select size="small" label="Group by" value={groupBy}
            onChange={(e) => { setGroupBy(e.target.value as GroupKey); setPage(0); }}
            sx={{ minWidth: 160 }}>
            {GROUPINGS.map((g) => (
              <MenuItem key={g.value || "none"} value={g.value}>{g.label}</MenuItem>
            ))}
          </MuiTextField>

          <Button variant="contained" startIcon={<DoneAllIcon />}
            onClick={() =>
              onBulk({
                describe,
                count: 0,
                payload: {
                  rule_code: filters.rule_code || undefined,
                  severity: filters.severity || undefined,
                  q: q || undefined,
                  entity,
                },
              })
            }>
            Acknowledge all
          </Button>
        </Toolbar>

        <Divider />

        <Box sx={{ px: 2, py: 1, bgcolor: "#fafbfe" }}>
          <Typography variant="body2" color="text.secondary">
            <strong>{rowCount}</strong> {groupBy ? (totalCount === 1 ? "group" : "groups") : "tickets"}{" "}
            on this page Â· <strong>{openOnPage}</strong> open in view Â·{" "}
            <strong>{ticketTotal}</strong> ticket{ticketTotal === 1 ? "" : "s"} match
            {groupBy ? ", counted before paging so no rule is split across pages" : ""}
          </Typography>
        </Box>

        {loading && !data ? (
          <Box sx={{ p: 6, textAlign: "center" }}><CircularProgress size={28} /></Box>
        ) : rowCount === 0 ? (
          <Box sx={{ p: 6, textAlign: "center" }}>
            <Typography variant="body1" color="text.secondary">
              No tickets match these filters.
            </Typography>
          </Box>
        ) : (
          <TableContainer>
            <Table size="small" sx={{ "& td, & th": { borderColor: "#eef1f6" } }}>
              {groupBy ? (
                <GroupedInbox groups={groups} totalGroups={data?.total_groups ?? 0}
                  totalTickets={data?.total_tickets ?? 0} expanded={expanded}
                  onToggle={onToggle} onToggleAll={onToggleAll} onBulk={onBulk} />
              ) : (
                <FlatInbox tickets={tickets} total={data?.total ?? 0} />
              )}
            </Table>
          </TableContainer>
        )}

        <TablePagination
          component="div"
          count={totalCount}
          page={page}
          onPageChange={(_, p) => setPage(p)}
          rowsPerPage={perPage}
          onRowsPerPageChange={(e) => { setPerPage(Number(e.target.value)); setPage(0); }}
          rowsPerPageOptions={[10, 25, 50, 100]}
          labelRowsPerPage={groupBy ? "Groups per page" : "Rows per page"}
          labelDisplayedRows={({ from, to, count }) =>
            groupBy
              ? `${from}â€“${to} of ${count} ${count === 1 ? "group" : "groups"}`
              : `${from}â€“${to} of ${count} tickets`
          }
        />
      </Card>

      <BulkAckDialog scope={scope} onClose={() => setScope(null)} onDone={load} />
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
    if (record?.ticket_no) document.title = `${record.ticket_no} · Audit Portal`;
  }, [record?.ticket_no]);

  if (!record) return null;

  const acknowledged = Boolean(record.acknowledged_by);

  return (
    <Stack spacing={2}>
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
            <Fact label="Exception lines" value={record.rows?.length ?? 0} />
            <Fact label="Raised" value={new Date(record.created_at).toLocaleString()} />
            <Fact
              label="Trigger"
              value={record.schedule_id ? `schedule #${record.schedule_id}` : "manual run"}
            />
          </Box>
        </CardContent>
      </Card>

      <Card sx={{ borderLeft: acknowledged ? "3px solid #2e7d32" : "3px solid #ef6c00" }}>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1 }}>Acknowledgement</Typography>
          {acknowledged ? (
            <Stack spacing={1}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" gap={1}>
                <Typography variant="body2" sx={{ fontWeight: 700 }}>{record.acknowledged_by}</Typography>
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
              next scheduled run will raise again if the observation recurs.
            </Alert>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
            <Typography variant="h6">Exception lines</Typography>
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