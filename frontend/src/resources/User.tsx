import { useCallback, useEffect, useState } from "react";
import {
  Alert, Box, Button, Card, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, IconButton, MenuItem, Stack,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField,
  Tooltip, Typography,
} from "@mui/material";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import EditIcon from "@mui/icons-material/Edit";
import KeyOutlinedIcon from "@mui/icons-material/KeyOutlined";
import PersonAddAltIcon from "@mui/icons-material/PersonAddAlt";
import { useNotify } from "react-admin";
import { api, type RoleOption, type UserRow } from "../dataProvider";
import { can, usePermissions } from "../permissions";
import { FONT } from "../theme";

/**
 * The accounts, and the roles that decide what they may do.
 *
 * There was no screen here, and no endpoint either: the only two accounts a
 * deployment ever had were the two in its `.env`. A portal whose whole promise
 * is that an auditor has a role to play could not add an auditor.
 *
 * The role column is the important one. Everything else on this screen -
 * the name, the password - is bookkeeping; the role is what the person is
 * actually here to do, and it is the thing worth reading down the column.
 *
 * THE LIST OF ROLES IS HANDED IN
 * -------------------------------
 * It comes from `GET /api/roles`, fetched once by the Settings page. The
 * picker used to carry its own list of three, and it was missing one: a role
 * that existed on the server and could not be assigned through the screen
 * because nobody had remembered to add it here. Two copies of a list are two
 * things that have to agree, and only one of them is checked.
 */

const labelFor = (roles: RoleOption[], role: string) =>
  roles.find((r) => r.name === role)?.label ?? role;

const descriptionFor = (roles: RoleOption[], role: string) =>
  roles.find((r) => r.name === role)?.description ?? "";

export const UserList = ({ roles }: { roles: RoleOption[] }) => {
  const notify = useNotify();
  const perms = usePermissions();
  const mayWrite = can(perms, "users.write");
  const [rows, setRows] = useState<UserRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<UserRow | null>(null);
  const [resetting, setResetting] = useState<UserRow | null>(null);
  const [deleting, setDeleting] = useState<UserRow | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows(await api.listUsers());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const afterChange = async (message: string) => {
    setBusy(false);
    notify(message, { type: "info" });
    await load();
  };

  if (error && !rows) {
    return <Alert severity="error">Could not load the accounts — {error}</Alert>;
  }

  if (!rows) {
    return (
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <CircularProgress size={20} />
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          Loading accounts…
        </Typography>
      </Stack>
    );
  }

  return (
    <Box>
      <Stack
        direction="row"
        spacing={2}
        sx={{ mb: 2, flexWrap: "wrap", gap: 1, alignItems: "center", justifyContent: "space-between" }}
      >
        <Typography variant="h6" sx={{ fontWeight: 700 }}>
          Accounts
          <Typography component="span" variant="body2" sx={{ ml: 1, color: "text.secondary" }}>
            who may read the estate, and who may change it
          </Typography>
        </Typography>
        {mayWrite ? (
          <Button
            variant="contained"
            startIcon={<PersonAddAltIcon />}
            onClick={() => setCreating(true)}
          >
            Add an account
          </Button>
        ) : (
          <Tooltip title="Adding an account needs the users.write capability">
            <span>
              <Button variant="contained" startIcon={<PersonAddAltIcon />} disabled>
                Add an account
              </Button>
            </span>
          </Tooltip>
        )}
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <TableContainer component={Card}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={{ width: 200 }}>Username</TableCell>
              <TableCell>Name</TableCell>
              <TableCell sx={{ width: 160 }}>Role</TableCell>
              <TableCell sx={{ width: 120 }}>Status</TableCell>
              <TableCell sx={{ width: 200 }}>Capabilities</TableCell>
              <TableCell align="right" sx={{ width: 140 }} />
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((u) => (
              <TableRow key={u.id} hover>
                <TableCell>
                  <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 600 }}>
                    {u.username}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Typography variant="body2">{u.full_name || "—"}</Typography>
                </TableCell>
                <TableCell>
                  <Chip size="small" variant="outlined" label={labelFor(roles, u.role)} />
                </TableCell>
                <TableCell>
                  <Chip
                    size="small"
                    label={u.is_active ? "ACTIVE" : "DISABLED"}
                    color={u.is_active ? "success" : "default"}
                  />
                </TableCell>
                <TableCell>
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {u.permissions.length} capabilit
                    {u.permissions.length === 1 ? "y" : "ies"}
                  </Typography>
                </TableCell>
                <TableCell align="right">
                  <Stack direction="row" spacing={0} sx={{ justifyContent: "flex-end" }}>
                    <Tooltip title="Change the role or the name">
                      <span>
                        <IconButton
                          size="small"
                          aria-label={`edit-${u.username}`}
                          disabled={!mayWrite}
                          onClick={() => setEditing(u)}
                        >
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </span>
                    </Tooltip>
                    <Tooltip title="Reset the password">
                      <span>
                        <IconButton
                          size="small"
                          aria-label={`reset-${u.username}`}
                          disabled={!mayWrite}
                          onClick={() => setResetting(u)}
                        >
                          <KeyOutlinedIcon fontSize="small" />
                        </IconButton>
                      </span>
                    </Tooltip>
                    <Tooltip title="Remove the account">
                      <span>
                        <IconButton
                          size="small"
                          aria-label={`delete-${u.username}`}
                          disabled={!mayWrite}
                          onClick={() => setDeleting(u)}
                        >
                          <DeleteOutlinedIcon fontSize="small" />
                        </IconButton>
                      </span>
                    </Tooltip>
                  </Stack>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

      <CreateDialog
        open={creating}
        busy={busy}
        roles={roles}
        onClose={() => setCreating(false)}
        onDone={async () => { setCreating(false); await afterChange("Account created"); }}
      />

      <EditDialog
        user={editing}
        busy={busy}
        roles={roles}
        onClose={() => setEditing(null)}
        onDone={async () => { setEditing(null); await afterChange("Account updated"); }}
      />

      <ResetDialog
        user={resetting}
        busy={busy}
        onClose={() => setResetting(null)}
        onDone={async () => { setResetting(null); await afterChange("Password reset"); }}
      />

      <DeleteDialog
        user={deleting}
        busy={busy}
        onClose={() => setDeleting(null)}
        onDone={async (name) => { setDeleting(null); await afterChange(`${name} removed`); }}
      />
    </Box>
  );
};

/* ------------------------------------------------------------------ */
/* Create                                                             */
/* ------------------------------------------------------------------ */
const CreateDialog = ({
  open, busy, roles, onClose, onDone,
}: {
  open: boolean;
  busy: boolean;
  roles: RoleOption[];
  onClose: () => void;
  onDone: () => void;
}) => {
  const notify = useNotify();
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("auditor");

  useEffect(() => {
    if (!open) return;
    setUsername("");
    setFullName("");
    setPassword("");
    setRole("auditor");
  }, [open]);

  const submit = async () => {
    try {
      await api.createUser({
        username,
        password,
        full_name: fullName,
        role,
      });
      onDone();
    } catch (e) {
      notify((e as Error).message, { type: "error" });
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: FONT.bodyAlt, fontWeight: 700 }}>
        Add an account
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Username" size="small" value={username}
            onChange={(e) => setUsername(e.target.value)}
            helperText="Letters, digits, dot, underscore and hyphen."
          />
          <TextField
            label="Full name" size="small" value={fullName}
            onChange={(e) => setFullName(e.target.value)}
          />
          <TextField
            label="Initial password" type="password" size="small" value={password}
            onChange={(e) => setPassword(e.target.value)}
            helperText="At least 8 characters. The person should change it themselves."
          />
          <TextField
            select label="Role" size="small" value={role}
            onChange={(e) => setRole(e.target.value)}
            helperText={descriptionFor(roles, role)}
          >
            {roles.map((r) => (
              <MenuItem key={r.name} value={r.name}>{r.label}</MenuItem>
            ))}
          </TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={busy || !username || password.length < 8}
        >
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
};

/* ------------------------------------------------------------------ */
/* Edit                                                               */
/* ------------------------------------------------------------------ */
const EditDialog = ({
  user, busy, roles, onClose, onDone,
}: {
  user: UserRow | null;
  busy: boolean;
  roles: RoleOption[];
  onClose: () => void;
  onDone: () => void;
}) => {
  const notify = useNotify();
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState("auditor");
  const [active, setActive] = useState(true);

  useEffect(() => {
    if (!user) return;
    setFullName(user.full_name ?? "");
    setRole(user.role);
    setActive(user.is_active);
  }, [user]);

  if (!user) return null;

  const submit = async () => {
    try {
      await api.updateUser(user.id, {
        full_name: fullName,
        role,
        is_active: active,
      });
      onDone();
    } catch (e) {
      notify((e as Error).message, { type: "error" });
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: FONT.bodyAlt, fontWeight: 700 }}>
        {user.username}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Full name" size="small" value={fullName}
            onChange={(e) => setFullName(e.target.value)}
          />
          <TextField
            select label="Role" size="small" value={role}
            onChange={(e) => setRole(e.target.value)}
            helperText={descriptionFor(roles, role)}
          >
            {roles.map((r) => (
              <MenuItem key={r.name} value={r.name}>{r.label}</MenuItem>
            ))}
          </TextField>
          <TextField
            select label="Status" size="small" value={active ? "active" : "disabled"}
            onChange={(e) => setActive(e.target.value === "active")}
            helperText="A disabled account cannot sign in, and its existing session stops working on the next request."
          >
            <MenuItem value="active">Active</MenuItem>
            <MenuItem value="disabled">Disabled</MenuItem>
          </TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={busy}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
};

/* ------------------------------------------------------------------ */
/* Reset the password                                                 */
/* ------------------------------------------------------------------ */
const ResetDialog = ({
  user, busy, onClose, onDone,
}: {
  user: UserRow | null;
  busy: boolean;
  onClose: () => void;
  onDone: () => void;
}) => {
  const notify = useNotify();
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (!user) return;
    setPassword("");
  }, [user]);

  if (!user) return null;

  const submit = async () => {
    try {
      await api.updateUser(user.id, { password });
      onDone();
    } catch (e) {
      notify((e as Error).message, { type: "error" });
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: FONT.bodyAlt, fontWeight: 700 }}>
        Reset the password for {user.username}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="New password" type="password" size="small" value={password}
            onChange={(e) => setPassword(e.target.value)}
            helperText="At least 8 characters. The old password stops working immediately."
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={busy || password.length < 8}>
          Reset
        </Button>
      </DialogActions>
    </Dialog>
  );
};

/* ------------------------------------------------------------------ */
/* Remove                                                             */
/* ------------------------------------------------------------------ */
/**
 * Removal is permanent, so the dialog says plainly what is lost.
 *
 * The ledger keeps the username as text, so the records of what this person
 * did survive - but the account does not, and the name can be reused, which
 * makes those older entries ambiguous. Deactivating is the softer path, and
 * it is on the edit dialog.
 */
const DeleteDialog = ({
  user, busy, onClose, onDone,
}: {
  user: UserRow | null;
  busy: boolean;
  onClose: () => void;
  onDone: (name: string) => void;
}) => {
  const notify = useNotify();

  if (!user) return null;

  const submit = async () => {
    try {
      await api.deleteUser(user.id);
      onDone(user.username);
    } catch (e) {
      notify((e as Error).message, { type: "error" });
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: FONT.bodyAlt, fontWeight: 700 }}>
        Remove {user.username}?
      </DialogTitle>
      <DialogContent>
        <Typography variant="body2">
          The account is deleted and the name can be given to somebody else.
          Records of what {user.username} did keep the name, so they will
          become ambiguous once it is reused. Disabling the account instead
          locks them out without that.
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
