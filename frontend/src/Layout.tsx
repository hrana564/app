import type { ReactNode } from "react";
import { Box, MenuItem, Select, Typography } from "@mui/material";
import {
  AppBar,
  Layout as RaLayout,
  LoadingIndicator,
  type AppBarProps,
} from "react-admin";
import { useEntityScope } from "./entityScope";

/**
 * The shell: what the app is called, and which client it is looking at.
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