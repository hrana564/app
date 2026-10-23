import { Fragment, useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress,
  Dialog, DialogActions, DialogContent, DialogTitle, IconButton, MenuItem,
  Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from "@mui/material";
import CheckCircleOutlinedIcon from "@mui/icons-material/CheckCircleOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import ErrorOutlinedIcon from "@mui/icons-material/ErrorOutlined";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import StorageIcon from "@mui/icons-material/Storage";
import { useNotify } from "react-admin";
import { RowsTable } from "../components/ui";
import {
  api,
  type DatasourceOverview,
  type DatasourcePreview,
  type DatasourceRow,
  type DiscoveredFile,
} from "../dataProvider";
import { FONT } from "../theme";
import { useEntityScope } from "../entityScope";
import { can, usePermissions } from "../permissions";

/* ================= Datasources ================= */

type View = "registered" | "landed";

export const DatasourceList = () => {
  const notify = useNotify();
  // The client comes from the app bar, not from a control on this screen. Two
  // pickers for the same question is how the list below ends up describing one
  // client while the header says another.
  const { entity, entities } = useEntityScope();
  const [view, setView] = useState<View>("registered");
  const [dataDate, setDataDate] = useState<string>("");
  const [overview, setOverview] = useState<DatasourceOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [registering, setRegistering] = useState<DiscoveredFile | null>(null);
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

  // The date list belongs to the client, so a client change invalidates it.
  useEffect(() => { setExpanded({}); }, [entity]);

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
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <CircularProgress size={20} />
        <Typography variant="body2" sx={{ color: "text.secondary" }}>Loading datasources…</Typography>
      </Stack>
    );
  }

  const active = overview.registered;
  const unconsumed = overview.discovered.filter((d) => d.state === "new");
  const absent = active.filter((r) => r.landing === "absent");
  const current = entities.find((e) => e.code === entity);

  return (
    <Box>
      <Stack
        direction="row"
        spacing={2}
        sx={{ mb: 2, flexWrap: "wrap", gap: 1, alignItems: "center", justifyContent: "space-between" }}
      >
        <Typography variant="h6" sx={{ fontWeight: 700 }}>
          {current?.name ?? entity}
          <Typography component="span" variant="body2" sx={{ ml: 1, color: "text.secondary" }}>
            what the ETL delivered for this client, and what we consume
          </Typography>
        </Typography>

        <Stack
          direction="row"
          spacing={1.5}
          sx={{ flexWrap: "wrap", gap: 1, alignItems: "center" }}
        >
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
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {view === "registered" ? (
        <RegisteredTable
          rows={active}
          expanded={expanded}
          onToggle={onToggle}
          onDelete={setDeleting}
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
        <Typography variant="caption" sx={{ color: "text.secondary", display: "block" }}>
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
const RegisteredTable = ({
  rows, expanded, onToggle, onDelete, busy,
}: {
  rows: DatasourceRow[];
  expanded: Record<string, boolean>;
  onToggle: (name: string) => void;
  onDelete: (row: DatasourceRow) => void;
  busy: boolean;
}) => {
  const perms = usePermissions();
  const mayRemove = can(perms, "datasources.write");

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
            {/* Sized to its content, not to the leftover space. The longest
                grain in the catalogue is "one row per store per SKU per
                snapshot" - 38 characters - so 330px is what that actually costs.
                Unpinned, this column took half the table while showing a short
                phrase in the middle of a very wide empty cell.

                Narrower below 1536px, because the columns to its right are not
                optional: at 1366 a fixed 330 left "Read by" 76px, which clipped
                the rule chips. Grain degrades first - it is the only value in
                this row that is purely descriptive. */}
            <TableCell sx={{ width: { xs: 210, xl: 330 } }}>Grain</TableCell>
            <TableCell align="right" sx={{ width: 80 }}>Cols</TableCell>
            <TableCell align="right" sx={{ width: 90 }}>Rows</TableCell>
            {/* Unpinned, so it absorbs the slack. It is the one column whose
                content genuinely varies - a table is read by no rule, or by
                four - so widening it is the least misleading place to spend it. */}
            <TableCell>Read by</TableCell>
            <TableCell align="center" sx={{ width: 120 }}>Landing</TableCell>
            <TableCell align="right" sx={{ width: 72 }} />
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => {
            const open = Boolean(expanded[row.name]);
            return (
              // Fragment carries the key, not the rows inside it - a bare
              // fragment cannot be keyed, which is what the warning was about.
              <Fragment key={row.name}>
                <TableRow hover onClick={() => onToggle(row.name)} sx={{ cursor: "pointer" }}>
                  <TableCell>
                    <Typography
                      variant="body2" noWrap
                      sx={{ fontFamily: "monospace", fontWeight: 600, color: "primary.main" }}
                    >
                      {row.name}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" noWrap>{row.system || "—"}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" noWrap sx={{ color: "text.secondary" }}>
                      {row.grain || "—"}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Typography variant="body2">
                      {Object.keys(row.columns).length}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Typography variant="body2" sx={{ color: "text.secondary" }}>
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
                      {/* Removal is offered only where it is safe. A table a live
                          rule reads cannot be removed at all, so the button is
                          absent rather than present and failing. And a removal
                          needs the capability that owns the catalogue, so the
                          button stays put and says why. */}
                      {row.used_by.length === 0 ? (
                        mayRemove ? (
                          <Tooltip title={`Remove ${row.name}`}>
                            <span>
                              <IconButton
                                size="small" aria-label={`delete-${row.name}`}
                                onClick={() => onDelete(row)} disabled={busy}
                              >
                                <DeleteOutlinedIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                        ) : (
                          <Tooltip title="Removing a table needs the datasources.write capability">
                            <span>
                              <IconButton size="small" aria-label={`delete-${row.name}`} disabled>
                                <DeleteOutlinedIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                        )
                      ) : (
                        <Tooltip title={`Read by ${row.used_by.join(", ")} — remove those rules first`}>
                          <span>
                            <IconButton size="small" aria-label={`locked-${row.name}`} disabled>
                              <LockOutlinedIcon fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                      )}
                    </TableCell>
                </TableRow>
                {/* Rendered only while open. Collapsing an always-present row left
                    a 1px placeholder per table in the DOM, so the table held nine
                    rows twice over - wrong to anything counting them, including a
                    screen reader. */}
                {open && (
                <TableRow>
                  <TableCell colSpan={8} sx={{ py: 0, border: 0 }}>
                    <Box sx={{ py: 2 }}>
                        {row.description && (
                          <Typography variant="body2" sx={{ mb: 1.5, color: "text.secondary" }}>
                            {row.description}
                          </Typography>
                        )}
                        <DataPreview entity={row.entity} name={row.name} day={row.last_seen} />
                        <Typography
                          variant="caption"
                          sx={{ display: "block", mt: 1.5, fontFamily: "monospace" }}
                        >
                          {row.path_template}
                        </Typography>
                    </Box>
                  </TableCell>
                </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
};

/* ------------------------------------------------------------------ */
/* Data preview - the first rows of the file itself                     */
/* ------------------------------------------------------------------ */
const PREVIEW_ROWS = 10;

const DataPreview = ({ entity, name, day }: {
  entity: string; name: string; day?: string | null;
}) => {
  const [data, setData] = useState<DatasourcePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    api
      .datasourcePreview(entity, name, day ?? undefined, PREVIEW_ROWS)
      .then((p) => live && setData(p))
      .catch((e) => live && setError((e as Error).message));
    return () => {
      live = false;
    };
  }, [entity, name, day]);

  if (error) {
    return (
      <Typography variant="body2" sx={{ mb: 2, color: "text.secondary" }}>
        No preview — {error}
      </Typography>
    );
  }
  if (!data) {
    return (
      <Stack direction="row" spacing={1} sx={{ mb: 2, minHeight: 34, alignItems: "center" }}>
        <CircularProgress size={16} />
        <Typography variant="body2" sx={{ color: "text.secondary" }}>Reading rows…</Typography>
      </Stack>
    );
  }
  if (!data.rows.length) {
    return (
      <Typography variant="body2" sx={{ mb: 2, color: "text.secondary" }}>
        {data.name}.csv on {data.data_date} has no readable rows.
      </Typography>
    );
  }

  return (
    <Box sx={{ mb: 2 }}>
      <Stack direction="row" spacing={1} sx={{ mb: 0.75, alignItems: "baseline" }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Sample data</Typography>
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          {`first ${data.rows.length} of ${data.total_rows ?? "?"} rows · ${data.data_date}`}
        </Typography>
      </Stack>
      {/* Types ride under the column headers rather than in a second list.
          A column grid below this said name-and-type again, one screen lower. */}
      <RowsTable
        rows={data.rows}
        emptyLabel="No rows."
        maxColumns={40}
        types={data.columns}
        caption={null}
      />
    </Box>
  );
};

/* ------------------------------------------------------------------ */
/* Landed                                                              */
/* ------------------------------------------------------------------ */
const LandedTable = ({
  files, onRegister, busy,
}: {
  files: DiscoveredFile[];
  onRegister: (file: DiscoveredFile) => void;
  busy: boolean;
}) => {
  const perms = usePermissions();
  const mayRegister = can(perms, "datasources.write");

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
                <Typography variant="body2" sx={{ color: "text.secondary" }}>{f.row_count}</Typography>
              </TableCell>
              <TableCell align="right">
                <Typography variant="body2" sx={{ color: "text.secondary" }}>
                  {(f.size_bytes / 1024).toFixed(1)} KB
                </Typography>
              </TableCell>
              <TableCell>
                <StateChip state={f.state} />
              </TableCell>
              <TableCell align="right">
                {f.state === "new" && (
                  mayRegister ? (
                    <Button
                      size="small" variant="outlined" disabled={busy}
                      onClick={() => onRegister(f)}
                    >
                      Register
                    </Button>
                  ) : (
                    // Left in place and disabled, with the reason attached.
                    // A button that is simply missing is a question the user
                    // has to ask; a disabled one that says why is an answer.
                    <Tooltip title="Registering a table needs the datasources.write capability">
                      <span>
                        <Button size="small" variant="outlined" disabled>
                          Register
                        </Button>
                      </span>
                    </Tooltip>
                  )
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
const RuleUsers = ({ codes }: { codes: string[] }) => {
  if (!codes.length) {
    return (
      <Typography variant="body2" sx={{ color: "text.disabled" }}>no rule</Typography>
    );
  }
  const shown = codes.slice(0, 2);
  const hidden = codes.slice(2);
  return (
    <Stack direction="row" spacing={0.5} sx={{ minWidth: 0, alignItems: "center" }}>
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
        <CheckCircleOutlinedIcon fontSize="small" color="success" />
      </Tooltip>
    );
  }
  return (
    <Tooltip title="Registered, but the file did not land on this date">
      <ErrorOutlinedIcon fontSize="small" color="error" />
    </Tooltip>
  );
};

const StateChip = ({ state }: { state: DiscoveredFile["state"] }) => {
  const map = {
    registered: { label: "Registered", color: "success" as const, icon: <CheckCircleOutlinedIcon /> },
    new: { label: "Not registered", color: "warning" as const, icon: <StorageIcon /> },
  };
  const c = map[state];
  return (
    <Chip size="small" label={c.label} color={c.color} icon={c.icon} variant="outlined" />
  );
};

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
          key={name}
          direction="row"
          spacing={1}
          sx={{ minWidth: 0, alignItems: "baseline" }}
        >
          <Typography
            variant="caption" noWrap
            sx={{ fontFamily: "monospace", fontWeight: 600, color: "text.primary" }}
          >
            {name}
          </Typography>
          <Typography variant="caption" noWrap sx={{ color: "text.secondary" }}>
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
        <Typography variant="body2" sx={{ mb: 2, color: "text.secondary" }}>
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
            <Typography variant="caption" sx={{ mb: 1, color: "text.secondary", display: "block" }}>
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
/* Remove                                                              */
/* ------------------------------------------------------------------ */
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