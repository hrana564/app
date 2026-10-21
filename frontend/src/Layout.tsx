import { useState, type ReactNode } from "react";
import {
  Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  IconButton, Menu, MenuItem, Select, Stack, TextField, Tooltip, Typography,
} from "@mui/material";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import {
  AppBar,
  Layout as RaLayout,
  LoadingIndicator,
  type AppBarProps,
} from "react-admin";
import { useLogout, useNotify } from "react-admin";
import { useEntityScope } from "./entityScope";
import { api } from "./dataProvider";
import { useCurrentUser } from "./permissions";
import { FONT } from "./theme";

/**
 * The shell: what the app is called, which client it is looking at, and who is
 * signed in.
 *
 * The entity switch sits immediately before the refresh indicator because that
 * is where a person already looks for the state of the page. It is the only
 * place the client is chosen - no screen carries its own copy, so the ticket
 * inbox can never be showing one client while the rule list above it shows another.
 */
const EntitySwitch = () => {
  const { entity, setEntity, entities } = useEntityScope();
  const current = entities.find((e) => e.code === entity);

  return (
    <Box sx={{ display: "flex", alignItems: "center", pr: 1.5, mr: 1, borderRight: "1px solid rgba(255,255,255,0.25)" }}>
      <Select
        value={entity}
        onChange={(e) => setEntity(e.target.value)}
        variant="standard"
        disableUnderline
        inputProps={{ "aria-label": "Client entity" }}
        renderValue={() => (
          <Typography variant="body2" sx={{ fontWeight: 700, color: "#fff", whiteSpace: "nowrap" }}>
            {current ? `${current.code} — ${current.name}` : entity}
          </Typography>
        )}
        sx={{
          color: "#fff",
          minWidth: 190,
          // The closed control sits on the teal bar; the open menu renders in a
          // portal on paper, so only the closed state needs to be lightened.
          "& .MuiSelect-select": { py: 0.5, pr: "24px !important" },
          "& .MuiSelect-icon": { color: "#fff", right: 0 },
          "&:hover": { bgcolor: "rgba(255,255,255,0.08)" },
        }}
      >
        {entities.map((e) => (
          <MenuItem key={e.code} value={e.code}>
            {e.code} — {e.name}
          </MenuItem>
        ))}
      </Select>
    </Box>
  );
};

/* ------------------------------------------------------------------ */
/* Who is signed in                                                    */
/* ------------------------------------------------------------------ */

/**
 * The account, and the way out.
 *
 * There was no sign-out anywhere before this: the auth provider had a logout
 * method and nothing called it. With roles in play that matters twice over -
 * a shared machine kept one person's session, and an administrator checking
 * what a viewer can see had no way to become a viewer without a private
 * window.
 *
 * The role is shown because it is what the buttons on the screen are about.
 * A person who cannot tell why a button is disabled cannot ask the right
 * question about it.
 */
const AccountMenu = () => {
  const user = useCurrentUser();
  const logout = useLogout();
  const notify = useNotify();
  const [anchor, setAnchor] = useState<null | HTMLElement>(null);
  const [pwOpen, setPwOpen] = useState(false);

  if (!user) return null;

  return (
    <>
      <Tooltip title={`Signed in as ${user.username} (${user.role})`}>
        <IconButton
          aria-label="account-menu"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ color: "#fff" }}
        >
          <AccountCircleIcon />
        </IconButton>
      </Tooltip>

      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <Box sx={{ px: 2, py: 1, minWidth: 200 }}>
          <Typography variant="body2" sx={{ fontWeight: 700 }}>
            {user.full_name || user.username}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 0.5, alignItems: "center" }}>
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              {user.username}
            </Typography>
            <Chip size="small" label={user.role} color="primary" variant="outlined" />
          </Stack>
        </Box>
        <MenuItem
          onClick={() => { setAnchor(null); setPwOpen(true); }}
        >
          Change my password
        </MenuItem>
        <MenuItem
          onClick={() => { setAnchor(null); void logout(); }}
        >
          Sign out
        </MenuItem>
      </Menu>

      <PasswordDialog
        open={pwOpen}
        onClose={() => setPwOpen(false)}
        onDone={() => { setPwOpen(false); notify("Your password has been changed", { type: "info" }); }}
      />
    </>
  );
};

/**
 * Change your own password.
 *
 * The current password is required, so a session left open on a shared
 * machine cannot quietly take the account over. Nobody else can set it - the
 * administrator's route is a reset, and that is a different thing.
 */
const PasswordDialog = ({
  open, onClose, onDone,
}: { open: boolean; onClose: () => void; onDone: () => void }) => {
  const notify = useNotify();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await api.changeOwnPassword(current, next);
      setCurrent("");
      setNext("");
      onDone();
    } catch (e) {
      notify((e as Error).message, { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: FONT.bodyAlt, fontWeight: 700 }}>
        Change my password
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="Current password" type="password" size="small" value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
          <TextField
            label="New password" type="password" size="small" value={next}
            onChange={(e) => setNext(e.target.value)}
            helperText="At least 8 characters"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={busy || !current || next.length < 8}>
          Change password
        </Button>
      </DialogActions>
    </Dialog>
  );
};

/**
 * A fixed title, deliberately not the current page name.
 *
 * The per-page title appeared on the rules list and nowhere else, so the bar
 * read as an accident on four screens out of five. The sidebar already says
 * which screen you are on and highlights it.
 *
 * The name is a child, not the AppBar's `title` prop: that prop is ignored the
 * moment you pass children of your own, which is how the bar ended up nameless.
 */
const AuditAppBar = ({ alwaysOn, title: _title, ...props }: AppBarProps) => (
  // `toolbar={null}` because the default one ends in its own LoadingIndicator -
  // the refresh icon - and supplying one adds a second. The refresh belongs
  // immediately after the switch, so it is placed here rather than left to the
  // slot's position, and the spacer is explicit instead of leaning on the
  // toolbar's own justify-content, which does not spread these children.
  <AppBar {...props} alwaysOn={alwaysOn} toolbar={null}>
    <Typography
      variant="h6"
      component="h1"
      sx={{ color: "#fff", fontWeight: 700, whiteSpace: "nowrap" }}
    >
      Scope Investments — Audit Portal
    </Typography>
    <Box sx={{ flex: 1 }} />
    <EntitySwitch />
    <AccountMenu />
    <LoadingIndicator />
  </AppBar>
);

/**
 * react-admin leaves the content area flush against the bottom edge of the app
 * bar, which is fine for a list and wrong for anything that starts with a
 * floating label: the Data Sources entity picker had its label drawn underneath
 * the bar. One gap, applied once, for every screen.
 */
export const Layout = ({ children }: { children: ReactNode }) => (
  <RaLayout appBar={AuditAppBar} sx={{ "& .RaLayout-content": { pt: 2, pb: 3 } }}>
    {children}
  </RaLayout>
);
