import { Fragment, useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Collapse,
  Dialog, DialogActions, DialogContent, DialogTitle, IconButton, MenuItem,
  Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from "@mui/material";
import BlockOutlinedIcon from "@mui/icons-material/BlockOutlined";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import StorageIcon from "@mui/icons-material/Storage";
import UndoIcon from "@mui/icons-material/Undo";
import { useNotify } from "react-admin";
import { EntityChip } from "../components/ui";
import {
  api,
  type DatasourceOverview,
  type DatasourceRow,
  type DiscoveredFile,
} from "../dataProvider";
import { FONT } from "../theme";

/* ================= Datasources ================= */

/**
 * What the ETL delivered, and what we consume.
 *
 * Two questions, two views, because conflating them is what made the previous
 * version useless: "did the file arrive" is not the same as "are we auditing it",
 * and an admin deciding what to switch off in the ETL needs to see the first
 * without hunting through the second.
 *
 * Every figure shown here is real. Row counts come from the file on disk,
 * columns from its header, and "used by" from the datasources each active rule
 * actually declared - none of it is decorative.
 */
type View = "registered" | "landed";

export const DatasourceList = () => {
  const notify = useNotify();
  const [entity, setEntity] = useState("LAD");
  const [view, setView] = useState<View>("registered");
  const [dataDate, setDataDate] = useState<string>("");
  const [overview, setOverview] = useState<DatasourceOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [registering, setRegistering] = useState<DiscoveredFile | null>(null);
  const [retiring, setRetiring] = useState<DatasourceRow | null>(null);
  const [deleting, setDeleting] = useState<DatasourceRow | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (nextEntity: string, nextDate: string) => {
    try {
      setOverview(await api.datasourceOverview(nextEntity, nextDate || undefined));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    let live = true;
    Promise.resolve()
      .then(() => load(entity, dataDate))
      .catch((e) => live && setError((e as Error).message));
    return () => { live = false; };
  }, [entity, dataDate, load]);

  const onToggle = (name: string) =>
    setExpanded((prev) => ({ ...prev, [name]: !prev[name] }));

  const afterChange = async (message: string) => {
    setBusy(false);
    notify(message, { type: "info" });
    await load(entity, dataDate);
  };

  if (error && !overview) {
    return <Alert severity="error">Could not load datasources — {error}</Alert>;
  }

  if (!overview) {
    return (
      <Stack direction="row" spacing={1} alignItems="center">
        <CircularProgress size={20} />
        <Typography variant="body2" color="text.secondary">Loading datasources…</Typography>
      </Stack>
    );
  }

  const active = overview.registered.filter((r) => r.is_active);
  const retired = overview.registered.filter((r) => !r.is_active);
  const unconsumed = overview.discovered.filter((d) => d.state === "new");
  const absent = active.filter((r) => r.landing === "absent");

  return (
    <Box>
      <Stack
        direction="row"
        spacing={2}
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 2, flexWrap: "wrap", gap: 1 }}
      >
        <TextField
          select size="small" label="Entity" value={entity}
          onChange={(e) => { setEntity(e.target.value); setExpanded({}); }}
          sx={{ minWidth: 200 }}
        >
          <MenuItem value="LAD">LAD — Laduree</MenuItem>
          <MenuItem value="ABM">ABM — Al Boom Marine</MenuItem>
        </TextField>

        <Stack direction="row" spacing={1.5} alignItems="center" sx={{ flexWrap: "wrap", gap: 1 }}>
          <TextField
            select size="small" label="Data date" value={dataDate}
            onChange={(e) => setDataDate(e.target.value)} sx={{ minWidth: 150 }}
          >
            <MenuItem value="">Latest ({overview.data_date || "none"})</MenuItem>
            {[...overview.dates].reverse().map((d) => (
              <MenuItem key={d} value={d}>{d}</MenuItem>
            ))}
          </TextField>
          <ToggleButtonGroup
            size="small" exclusive value={view} onChange={(_, v) => v && setView(v)}
          >
            <ToggleButton value="registered">Registered</ToggleButton>
            <ToggleButton value="landed">Landed</ToggleButton>
          </ToggleButtonGroup>
        </Stack>
      </Stack>

      {/* The three facts worth knowing before touching anything. */}
      <Stack direction="row" spacing={1.5} sx={{ mb: 2, flexWrap: "wrap", gap: 1.5 }}>
        <Stat label="Registered" value={active.length} />
        <Stat
          label="Landed on this date"
          value={overview.discovered.length}
          tone="success"
        />
        <Stat
          label="Awaiting decision"
          value={unconsumed.length}
          tone={unconsumed.length ? "warning" : "default"}
          hint="Files the ETL sent that no rule is registered to read"
        />
        <Stat
          label="Registered but absent"
          value={absent.length}
          tone={absent.length ? "error" : "default"}
          hint="Registered tables whose file did not land on this date"
        />
        {retired.length > 0 && (
          <Stat label="Retired" value={retired.length} tone="default" />
        )}
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {view === "registered" ? (
        <RegisteredTable
          rows={[...active, ...retired]}
          expanded={expanded}
          onToggle={onToggle}
          onRetire={setRetiring}
          onDelete={setDeleting}
          onRestore={async (row) => {
            setBusy(true);
            try {
              await api.restoreDatasource(row.id);
              await afterChange(`${row.name} restored`);
            } catch (e) {
              setBusy(false);
              notify((e as Error).message, { type: "error" });
            }
          }}
          busy={busy}
        />
      ) : (
        <LandedTable
          files={overview.discovered}
          onRegister={setRegistering}
          busy={busy}
        />
      )}

      <RegisterDialog
        file={registering}
        entity={entity}
        busy={busy}
        onClose={() => setRegistering(null)}
        onDone={async () => { setRegistering(null); await afterChange("Datasource registered"); }}
      />

      <RetireDialog
        row={retiring}
        busy={busy}
        onClose={() => setRetiring(null)}
        onDone={async (name) => { setRetiring(null); await afterChange(`${name} retired`); }}
        onDelete={() => { setDeleting(retiring); setRetiring(null); }}
      />

      <DeleteDialog
        row={deleting}
        busy={busy}
        onClose={() => setDeleting(null)}
        onDone={async (name) => { setDeleting(null); await afterChange(`${name} removed`); }}
      />
    </Box>
  );
};

/* ------------------------------------------------------------------ */
/* Stat                                                                */
/* ------------------------------------------------------------------ */
const Stat = ({
  label, value, tone = "default", hint,
}: {
  label: string;
  value: number;
  tone?: "default" | "success" | "warning" | "error";
  hint?: string;
}) => {
  const colour =
    tone === "default" ? "text.primary" : `${tone}.main`;
  const body = (
    <Card sx={{ minWidth: 150, flex: "1 1 150px" }}>
      <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Typography variant="caption" color="text.secondary" display="block">
          {label}
        </Typography>
        <Typography variant="h5" sx={{ color: tone === "default" ? "text.primary" : colour }}>
          {value}
        </Typography>
      </CardContent>
    </Card>
  );
  return hint ? <Tooltip title={hint}>{body}</Tooltip> : body;
};

/* ------------------------------------------------------------------ */
/* Registered                                                          */
/* ------------------------------------------------------------------ */
/**
 * One line per table, expand for the columns.
 *
 * The earlier version rendered every column as a chip, which put 138 chips on
 * one screen and gave the eye nothing to land on. A count plus an expansion is
 * the same information without the wall.
 */
const RegisteredTable = ({
  rows, expanded, onToggle, onRetire, onRestore, onDelete, busy,
}: {
  rows: DatasourceRow[];
  expanded: Record<string, boolean>;
  onToggle: (name: string) => void;
  onRetire: (row: DatasourceRow) => void;
  onRestore: (row: DatasourceRow) => void;
  onDelete: (row: DatasourceRow) => void;
  busy: boolean;
}) => {
  if (!rows.length) {
    return <Alert severity="info">No datasources registered for this entity yet.</Alert>;
  }

  return (
    <TableContainer component={Card}>
      <Table size="small" sx={{ tableLayout: "fixed" }}>
        <TableHead>
          <TableRow>
            <TableCell sx={{ width: 210 }}>Table</TableCell>
            <TableCell sx={{ width: 130 }}>System</TableCell>
            <TableCell>Grain</TableCell>
            <TableCell align="right" sx={{ width: 80 }}>Cols</TableCell>
            <TableCell align="right" sx={{ width: 90 }}>Rows</TableCell>
            <TableCell sx={{ width: 150 }}>Read by</TableCell>
            <TableCell align="center" sx={{ width: 96 }}>Landing</TableCell>
            <TableCell align="right" sx={{ width: 88 }} />
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => {
            const open = Boolean(expanded[row.name]);
            return (
              // Fragment carries the key, not the rows inside it - a bare
              // fragment cannot be keyed, which is what the warning was about.
              <Fragment key={row.name}>
                <TableRow
                  hover
                  onClick={() => onToggle(row.name)}
                  sx={{ cursor: "pointer", opacity: row.is_active ? 1 : 0.55 }}
                >
                  <TableCell>
                    <Stack direction="row" spacing={0.75} alignItems="center" sx={{ minWidth: 0 }}>
                      <Typography
                        variant="body2" noWrap
                        sx={{ fontFamily: "monospace", fontWeight: 600, color: "primary.main" }}
                      >
                        {row.name}
                      </Typography>
                      {!row.is_active && (
                        <Chip size="small" label="retired" color="info" />
                      )}
                    </Stack>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" noWrap>{row.system || "—"}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" noWrap color="text.secondary">
                      {row.grain || "—"}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Typography variant="body2">
                      {Object.keys(row.columns).length}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Typography variant="body2" color="text.secondary">
                      {row.row_count ?? "—"}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <RuleUsers codes={row.used_by} />
                  </TableCell>
                  <TableCell align="center">
                    <LandingChip landing={row.landing} lastSeen={row.last_seen} />
                  </TableCell>
                  <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                    <Stack direction="row" spacing={0.75} justifyContent="flex-end">
                      {row.is_active ? (
                        <>
                          <Tooltip title={`Retire ${row.name} — stops new rules using it, reversible`}>
                            <span>
                              <IconButton
                                size="small" aria-label={`retire-${row.name}`}
                                onClick={() => onRetire(row)} disabled={busy}
                              >
                                <BlockOutlinedIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                          {/* Removal is offered only where it is safe. A table a
                              live rule reads cannot be deleted at all, so the
                              button is absent rather than present and failing. */}
                          {row.used_by.length === 0 && (
                            <Tooltip title={`Remove ${row.name} permanently`}>
                              <span>
                                <IconButton
                                  size="small" aria-label={`delete-${row.name}`}
                                  onClick={() => onDelete(row)} disabled={busy}
                                >
                                  <DeleteOutlineIcon fontSize="small" />
                                </IconButton>
                              </span>
                            </Tooltip>
                          )}
                        </>
                      ) : (
                        <>
                          <Tooltip title={`Restore ${row.name}`}>
                            <span>
                              <IconButton
                                size="small" aria-label={`restore-${row.name}`}
                                onClick={() => onRestore(row)} disabled={busy}
                              >
                                <UndoIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Tooltip title={`Remove ${row.name} permanently`}>
                            <span>
                              <IconButton
                                size="small" aria-label={`delete-${row.name}`}
                                onClick={() => onDelete(row)} disabled={busy}
                              >
                                <DeleteOutlineIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                        </>
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell colSpan={8} sx={{ py: 0, border: 0 }}>
                    <Collapse in={open} timeout="auto" unmountOnExit>
                      <Box sx={{ py: 2 }}>
                        {row.description && (
                          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                            {row.description}
                          </Typography>
                        )}
                        <ColumnGrid columns={row.columns} />
                        <Typography
                          variant="caption"
                          sx={{ display: "block", mt: 1.5, fontFamily: "monospace" }}
                        >
                          {row.path_template}
                        </Typography>
                      </Box>
                    </Collapse>
                  </TableCell>
                </TableRow>
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
};

/* ------------------------------------------------------------------ */
/* Landed                                                              */
/* ------------------------------------------------------------------ */
/**
 * What the ETL actually delivered, read from the files.
 *
 * A file here that is not registered is not an error - it is a decision waiting
 * to be made. "New" means nothing reads it, which is exactly the signal the ETL
 * team needs to know a feed can be switched off.
 */
const LandedTable = ({
  files, onRegister, busy,
}: {
  files: DiscoveredFile[];
  onRegister: (file: DiscoveredFile) => void;
  busy: boolean;
}) => {
  if (!files.length) {
    return (
      <Alert severity="info">
        No files landed on this date. The ETL writes to
        {" "}<code>mount/SourceData/&lt;ENTITY&gt;/&lt;date&gt;/</code>.
      </Alert>
    );
  }

  return (
    <TableContainer component={Card}>
      <Table size="small" sx={{ tableLayout: "fixed" }}>
        <TableHead>
          <TableRow>
            <TableCell sx={{ width: 240 }}>File</TableCell>
            <TableCell align="right" sx={{ width: 80 }}>Cols</TableCell>
            <TableCell align="right" sx={{ width: 90 }}>Rows</TableCell>
            <TableCell align="right" sx={{ width: 100 }}>Size</TableCell>
            <TableCell sx={{ width: 150 }}>State</TableCell>
            <TableCell align="right" sx={{ width: 120 }} />
          </TableRow>
        </TableHead>
        <TableBody>
          {files.map((f) => (
            <TableRow key={f.name} hover>
              <TableCell>
                <Typography
                  variant="body2" noWrap
                  sx={{ fontFamily: "monospace", fontWeight: 600, color: "primary.main" }}
                >
                  {f.name}
                </Typography>
              </TableCell>
              <TableCell align="right">
                <Typography variant="body2">{f.column_count}</Typography>
              </TableCell>
              <TableCell align="right">
                <Typography variant="body2" color="text.secondary">{f.row_count}</Typography>
              </TableCell>
              <TableCell align="right">
                <Typography variant="body2" color="text.secondary">
                  {(f.size_bytes / 1024).toFixed(1)} KB
                </Typography>
              </TableCell>
              <TableCell>
                <StateChip state={f.state} />
              </TableCell>
              <TableCell align="right">
                {f.state === "new" && (
                  <Button
                    size="small" variant="outlined" disabled={busy}
                    onClick={() => onRegister(f)}
                  >
                    Register
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
};

/* ------------------------------------------------------------------ */
/* Small pieces                                                        */
/* ------------------------------------------------------------------ */
/**
 * Dependent rules, capped so the row stays one line.
 *
 * ipos_sales is read by three rules and rendered them all, which made that one
 * row twice the height of every other. Two chips then "+1" with the full list on
 * hover - the count is the thing you scan for, the names are what you check.
 */
const RuleUsers = ({ codes }: { codes: string[] }) => {
  if (!codes.length) {
    return (
      <Typography variant="body2" color="text.disabled">no rule</Typography>
    );
  }
  const shown = codes.slice(0, 2);
  const hidden = codes.slice(2);
  return (
    <Stack direction="row" spacing={0.5} alignItems="center" sx={{ minWidth: 0 }}>
      {shown.map((code) => (
        <Chip key={code} size="small" variant="outlined" label={code} />
      ))}
      {hidden.length > 0 && (
        <Tooltip title={hidden.join(", ")}>
          <Chip size="small" variant="outlined" label={`+${hidden.length}`} />
        </Tooltip>
      )}
    </Stack>
  );
};

const LandingChip = ({
  landing, lastSeen,
}: { landing: "landed" | "absent"; lastSeen: string | null }) => {
  if (landing === "landed") {
    return (
      <Tooltip title={lastSeen ? `Landed ${lastSeen}` : "Landed"}>
        <CheckCircleOutlineIcon fontSize="small" color="success" />
      </Tooltip>
    );
  }
  return (
    <Tooltip title="Registered, but the file did not land on this date">
      <ErrorOutlineIcon fontSize="small" color="error" />
    </Tooltip>
  );
};

const StateChip = ({ state }: { state: DiscoveredFile["state"] }) => {
  const map = {
    registered: { label: "Registered", color: "success" as const, icon: <CheckCircleOutlineIcon /> },
    retired: { label: "Retired", color: "info" as const, icon: <UndoIcon /> },
    new: { label: "Not registered", color: "warning" as const, icon: <StorageIcon /> },
  };
  const c = map[state];
  return (
    <Chip size="small" label={c.label} color={c.color} icon={c.icon} variant="outlined" />
  );
};

/**
 * Columns as a compact grid rather than one chip each. Chip-per-column turned a
 * nine-column table into a paragraph and made a seventy-chip card unreadable.
 */
const ColumnGrid = ({ columns }: { columns: Record<string, string> }) => {
  const entries = Object.entries(columns);
  if (!entries.length) return null;
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))",
        gap: 0.75,
      }}
    >
      {entries.map(([name, type]) => (
        <Stack
          key={name} direction="row" spacing={1} alignItems="baseline"
          sx={{ minWidth: 0 }}
        >
          <Typography
            variant="caption" noWrap
            sx={{ fontFamily: "monospace", fontWeight: 600, color: "text.primary" }}
          >
            {name}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap>
            {type}
          </Typography>
        </Stack>
      ))}
    </Box>
  );
};

/* ------------------------------------------------------------------ */
/* Register                                                            */
/* ------------------------------------------------------------------ */
/**
 * Columns arrive pre-filled from the CSV header. The admin confirms rather than
 * retypes, because a hand-typed column list is a second place for the truth to
 * go wrong.
 */
const RegisterDialog = ({
  file, entity, busy, onClose, onDone,
}: {
  file: DiscoveredFile | null;
  entity: string;
  busy: boolean;
  onClose: () => void;
  onDone: () => void;
}) => {
  const [system, setSystem] = useState("");
  const [description, setDescription] = useState("");
  const [grain, setGrain] = useState("");

  useEffect(() => {
    setSystem("");
    setDescription("");
    setGrain("");
  }, [file?.name]);

  const submit = async () => {
    if (!file) return;
    try {
      await api.registerDatasource({
        name: file.name,
        entity,
        system,
        description,
        grain,
        columns: file.columns,
      });
      onDone();
    } catch {
      /* the notify in the parent reports it */
    }
  };

  return (
    <Dialog open={Boolean(file)} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: FONT.bodyAlt, fontWeight: 700 }}>
        Register {file?.name}
      </DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Columns were read from the file header on {file?.data_date} —{" "}
          {file?.column_count} {file?.column_count === 1 ? "column" : "columns"},{" "}
          {file?.row_count} {file?.row_count === 1 ? "row" : "rows"}. A rule can
          reference this table by name as soon as you save.
        </Typography>
        <Stack spacing={2}>
          <TextField
            label="System" size="small" value={system} onChange={(e) => setSystem(e.target.value)}
            helperText="Which upstream system this file comes from. Cannot be read from the CSV."
          />
          <TextField
            label="Description" size="small" value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <TextField
            label="Grain" size="small" value={grain} onChange={(e) => setGrain(e.target.value)}
            placeholder="one row per invoice"
            helperText="What one row represents. This is what stops two people counting differently."
          />
          <Box>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
              COLUMNS READ FROM THE FILE
            </Typography>
            <ColumnGrid columns={file?.columns ?? {}} />
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={busy}>
          Register
        </Button>
      </DialogActions>
    </Dialog>
  );
};

/* ------------------------------------------------------------------ */
/* Retire                                                              */
/* ------------------------------------------------------------------ */
/**
 * Retiring is reversible and never silently breaks a running control.
 *
 * A table that active rules still read is allowed through, but only after the
 * admin has seen which rules and been told what happens next - the rules keep
 * running and will fail once the ETL stops sending the file. That is a decision
 * to make deliberately, not a side effect of clicking a delete button.
 */
const RetireDialog = ({
  row, busy, onClose, onDone, onDelete,
}: {
  row: DatasourceRow | null;
  busy: boolean;
  onClose: () => void;
  onDone: (name: string) => void;
  onDelete: () => void;
}) => {
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => setConfirmed(false), [row?.id]);

  const submit = async (confirm: boolean) => {
    if (!row) return;
    try {
      await api.retireDatasource(row.id, confirm);
      onDone(row.name);
    } catch {
      /* notified upstream */
    }
  };

  const dependents = row?.used_by ?? [];
  const used = dependents.length > 0;

  return (
    <Dialog open={Boolean(row)} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: FONT.bodyAlt, fontWeight: 700 }}>
        Retire {row?.name}
      </DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 2 }}>
          No new rule will be able to reference this table, and it stops being
          offered as something we consume. Existing rules are not changed, and
          the table keeps its history so this can be undone.
        </Typography>

        {used && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
              {dependents.length} active rule{dependents.length > 1 ? "s" : ""} still read this table
            </Typography>
            <Typography variant="body2" sx={{ mb: 1 }}>
              {dependents.join(", ")}
            </Typography>
            <Typography variant="body2">
              They will keep running. If the ETL then stops sending this file,
              they will fail rather than report a clean day.
            </Typography>
          </Alert>
        )}

        {used && !confirmed && (
          <Button variant="contained" color="warning" onClick={() => setConfirmed(true)}>
            I understand — retire anyway
          </Button>
        )}
        {used && confirmed && (
          <Typography variant="body2" sx={{ color: "warning.main", fontWeight: 600 }}>
            Press Retire to proceed.
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        {!used && (
          <Button onClick={onDelete} color="error">
            Remove permanently instead
          </Button>
        )}
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained" color="warning" disabled={busy || (used && !confirmed)}
          onClick={() => submit(true)}
        >
          <BlockOutlinedIcon fontSize="small" sx={{ mr: 1 }} />
          Retire
        </Button>
      </DialogActions>
    </Dialog>
  );
};

/* ------------------------------------------------------------------ */
/* Delete                                                              */
/* ------------------------------------------------------------------ */
/**
 * Removal is permanent, so it is a separate dialog from retiring and states
 * plainly what is lost. Reachable only for a table no rule reads.
 */
const DeleteDialog = ({
  row, busy, onClose, onDone,
}: {
  row: DatasourceRow | null;
  busy: boolean;
  onClose: () => void;
  onDone: (name: string) => void;
}) => {
  const notify = useNotify();

  const submit = async () => {
    if (!row) return;
    try {
      await api.deleteDatasource(row.id);
      onDone(row.name);
    } catch (e) {
      notify((e as Error).message, { type: "error" });
    }
  };

  return (
    <Dialog open={Boolean(row)} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: FONT.bodyAlt, fontWeight: 700 }}>
        Remove {row?.name}?
      </DialogTitle>
      <DialogContent>
        <Typography variant="body2">
          This deletes the registration and its column list. No rule reads this
          table, so nothing will break — but the record of it is gone, and the
          file landing again would show as unregistered.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="error" onClick={submit} disabled={busy}>
          Remove
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default DatasourceList;