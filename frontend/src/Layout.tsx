import { useState, type ReactNode } from "react";
import {
  Avatar, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  Divider, Menu, MenuItem, Select, Stack, TextField,
  Typography,
} from "@mui/material";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import ArrowDropDownIcon from "@mui/icons-material/ArrowDropDown";
import BusinessOutlinedIcon from "@mui/icons-material/BusinessOutlined";
import KeyOutlinedIcon from "@mui/icons-material/KeyOutlined";
import LogoutOutlinedIcon from "@mui/icons-material/LogoutOutlined";
import SettingsIcon from "@mui/icons-material/Settings";
import {
  AppBar,
  Layout as RaLayout,
  LoadingIndicator,
  Menu as RaMenu,
  type AppBarProps,
} from "react-admin";
import { useLogout, useNotify } from "react-admin";
import { useEntityScope } from "./entityScope";
import { api } from "./dataProvider";
import { useCurrentUser } from "./permissions";
import { FONT } from "./theme";

/* ------------------------------------------------------------------ */
/* The sidebar                                                         */
/* ------------------------------------------------------------------ */

const GroupHeading = ({ children }: { children: ReactNode }) => (
  <Typography
    component="li"
    className="RaMenuGroup"
    variant="overline"
    sx={{
      px: 2,
      pt: 2.5,
      pb: 0.5,
      color: "#9aa7ba",
      fontWeight: 700,
      letterSpacing: 1.2,
      listStyle: "none",
      whiteSpace: "nowrap",
      overflow: "hidden",
    }}
  >
    {children}
  </Typography>
);

const GroupRule = () => (
  <Box
    component="li"
    className="RaMenuGroup"
    sx={{
      mx: 2,
      my: 1.5,
      borderTop: "1px solid",
      borderColor: "divider",
      listStyle: "none",
    }}
  />
);

const PortalMenu = () => (
  <RaMenu
    sx={{
      pt: 0.5,
      // Collapsed, only the icons survive - headings and rules are the
      // width of the column.
      "&.RaMenu-closed .RaMenuGroup": { display: "none" },
    }}
  >
    <GroupHeading>The audit</GroupHeading>
    <RaMenu.DashboardItem />
    <RaMenu.ResourceItem name="tickets" />
    <RaMenu.ResourceItem name="rules" />
    <RaMenu.ResourceItem name="runs" />
    <RaMenu.ResourceItem name="datasources" />
    <GroupRule />
    <GroupHeading>Settings</GroupHeading>
    <RaMenu.Item
      to="/settings"
      primaryText="Settings"
      leftIcon={<SettingsIcon />}
      sx={{ color: "#8d99ae", "& .RaMenuItemLink-icon": { color: "#8d99ae" } }}
    />
  </RaMenu>
);

/* ------------------------------------------------------------------ */
/* Which client the portal is looking at                               */
/* ------------------------------------------------------------------ */

const EntitySwitch = () => {
  const { entity, setEntity, entities } = useEntityScope();
  const current = entities.find((e) => e.code === entity);

  return (
    <Box sx={{ display: "flex", alignItems: "center", mr: 1.5 }}>
      <Select
        value={entity}
        onChange={(e) => setEntity(e.target.value)}
        variant="standard"
        disableUnderline
        inputProps={{ "aria-label": "Client entity" }}
        renderValue={() => (
          <Stack
            direction="row"
            spacing={0.75}
            sx={{ alignItems: "center", pr: 0.25 }}
          >
            <BusinessOutlinedIcon
              sx={{ fontSize: 17, color: "rgba(255,255,255,0.72)" }}
            />
            <Typography
              variant="body2"
              sx={{ fontWeight: 600, color: "#fff", whiteSpace: "nowrap" }}
            >
              {current ? current.name : entity}
            </Typography>
          </Stack>
        )}
        sx={{
          color: "#fff",
          minWidth: 200,
          // The closed control sits on the app bar; the open menu renders in
          // a portal on paper, so only the closed state is drawn as a box.
          "& .MuiSelect-select": {
            py: 0.65,
            pl: 1.25,
            pr: "28px !important",
            borderRadius: 2,
            bgcolor: "rgba(255,255,255,0.10)",
            border: "1px solid rgba(255,255,255,0.20)",
            transition: "background-color 120ms ease, border-color 120ms ease",
          },
          "& .MuiSelect-icon": {
            color: "rgba(255,255,255,0.85)",
            right: 6,
            fontSize: 20,
          },
          "&:hover .MuiSelect-select": {
            bgcolor: "rgba(255,255,255,0.18)",
            borderColor: "rgba(255,255,255,0.40)",
          },
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

const AccountMenu = () => {
  const user = useCurrentUser();
  const logout = useLogout();
  const notify = useNotify();
  const [anchor, setAnchor] = useState<null | HTMLElement>(null);
  const [pwOpen, setPwOpen] = useState(false);

  if (!user) return null;

  const name = user.full_name || user.username;
  const role = user.role_label || user.role;

  return (
    <>
      <Button
        aria-label="account-menu"
        onClick={(e) => setAnchor(e.currentTarget)}
        endIcon={
          <ArrowDropDownIcon sx={{ fontSize: 20, color: "rgba(255,255,255,0.75)" }} />
        }
        sx={{
          color: "#fff",
          px: 1,
          py: 0.4,
          borderRadius: 2,
          minWidth: 0,
          "&:hover": { bgcolor: "rgba(255,255,255,0.12)" },
          "& .MuiButton-endIcon": { ml: 0.25 },
        }}
      >
        <AccountCircleIcon sx={{ fontSize: 32, mr: 1.25 }} />
        {/* The name is dropped on a narrow bar rather than wrapping: a bar
            that grows to two rows pushes the refresh indicator off the edge. */}
        <Box
          sx={{
            display: { xs: "none", md: "block" },
            textAlign: "left",
            lineHeight: 1.15,
            maxWidth: 200,
          }}
        >
          <Typography
            variant="body2"
            sx={{
              color: "#fff",
              fontWeight: 600,
              lineHeight: 1.25,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {name}
          </Typography>
          <Typography
            variant="caption"
            sx={{
              display: "block",
              color: "rgba(255,255,255,0.72)",
              lineHeight: 1.25,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {role}
          </Typography>
        </Box>
      </Button>

      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: { sx: { mt: 0.75, minWidth: 280, borderRadius: 2, border: "1px solid #e3e8f0" } },
        }}
      >
        <Box sx={{ px: 2, py: 1.75 }}>
          <Stack direction="row" spacing={1.25} sx={{ alignItems: "center" }}>
            <Avatar
              sx={{
                bgcolor: "primary.main",
                width: 40,
                height: 40,
                fontWeight: 700,
              }}
            >
              <AccountCircleIcon sx={{ fontSize: 40 }} />
            </Avatar>
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="subtitle2" sx={{ fontWeight: 700 }} noWrap>
                {name}
              </Typography>
              <Typography variant="caption" sx={{ color: "text.secondary" }} noWrap>
                {user.username}
              </Typography>
            </Box>
          </Stack>
          <Stack direction="row" spacing={1} sx={{ mt: 1.5, alignItems: "center" }}>
            <Chip size="small" label={role} color="primary" variant="outlined" />
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              {user.permissions.length} capabilities
            </Typography>
          </Stack>
        </Box>
        <Divider />
        <MenuItem
          onClick={() => {
            setAnchor(null);
            setPwOpen(true);
          }}
        >
          <KeyOutlinedIcon sx={{ mr: 1.5, fontSize: 19, color: "text.secondary" }} />
          Change my password
        </MenuItem>
        <MenuItem
          onClick={() => {
            setAnchor(null);
            void logout();
          }}
        >
          <LogoutOutlinedIcon sx={{ mr: 1.5, fontSize: 19, color: "text.secondary" }} />
          Sign out
        </MenuItem>
      </Menu>

      <PasswordDialog
        open={pwOpen}
        onClose={() => setPwOpen(false)}
        onDone={() => {
          setPwOpen(false);
          notify("Your password has been changed", { type: "info" });
        }}
      />
    </>
  );
};

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

const AuditAppBar = ({ alwaysOn, title: _title, ...props }: AppBarProps) => (
  <AppBar
    {...props}
    alwaysOn={alwaysOn}
    color="primary"
    toolbar={null}
    userMenu={false}
  >
    <Typography
      variant="h6"
      component="h1"
      sx={{ color: "#fff", fontWeight: 700, whiteSpace: "nowrap", display: "flex", alignItems: "baseline", gap: 1 }}
    >
      SquareOne Holdings
      <Typography
        component="span"
        variant="body2"
        sx={{ color: "rgba(255,255,255,0.78)", fontWeight: 400, display: { xs: "none", sm: "inline" } }}
      >
        Agentic Data Analytics
      </Typography>
    </Typography>
    <Box sx={{ flex: 1 }} />
    <EntitySwitch />
    <AccountMenu />
    <LoadingIndicator />
  </AppBar>
);

export const Layout = ({ children }: { children: ReactNode }) => (
  <RaLayout
    appBar={AuditAppBar}
    menu={PortalMenu}
    sx={{
      "& .RaLayout-content": { pt: 2, pb: 3 },
      "& .RaMenu-open": { pt: 0 },
      "& .RaMenu-root": { pt: 0 },
    }}
  >
    {children}
  </RaLayout>
);
