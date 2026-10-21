import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import {
  Alert, Box, CircularProgress, Stack, Tab, Tabs, Typography,
} from "@mui/material";
import { api, type RoleMatrix as RoleMatrixData } from "../dataProvider";
import { can, usePermissions } from "../permissions";
import { UserList } from "../resources/User";
import { RoleMatrix } from "../components/RoleMatrix";

/**
 * Where the settings live.
 *
 * The sidebar used to carry the accounts as a destination of its own, which
 * left no room for anything else that configures the portal - and no place to
 * answer the question the accounts screen only implies, which is what each
 * role may actually do. Both now sit behind one entry, as tabs, so the next
 * setting lands beside them rather than beside the ticket inbox.
 *
 * The tab is in the URL, so a screen can be linked to: "the roles matrix" is
 * worth sending to somebody, and a panel that exists only in the browser
 * cannot be pointed at.
 *
 * The matrix is fetched here and handed down, once. Both tabs want it - the
 * accounts editor needs the list of roles to offer, the roles tab is a picture
 * of it - and two callers asking for the same answer means two requests that
 * can arrive in either order.
 */
export const SettingsPage = () => {
  const perms = usePermissions();
  const mayReadAccounts = can(perms, "users.read");
  const [params, setParams] = useSearchParams();
  const [matrix, setMatrix] = useState<RoleMatrixData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .roleMatrix()
      .then((m) => {
        if (live) {
          setMatrix(m);
          setError(null);
        }
      })
      .catch((e) => {
        if (live) setError((e as Error).message);
      });
    return () => {
      live = false;
    };
  }, []);

  /**
   * Which tabs this person is offered.
   *
   * The accounts tab is not shown to somebody who could not read them, rather
   * than shown and then refused: a tab that opens onto nothing is a broken
   * screen, not a permission.
   */
  const tabs = useMemo(
    () => [
      ...(mayReadAccounts ? [{ key: "accounts", label: "Accounts" }] : []),
      { key: "roles", label: "Roles" },
    ],
    [mayReadAccounts],
  );

  // A tab in the URL that this person may not see falls back to the first one
  // they can, rather than to an empty panel.
  const wanted = params.get("tab");
  const found = tabs.findIndex((t) => t.key === wanted);
  const active = found < 0 ? 0 : found;

  // And the URL is corrected to match what is actually on screen. A link that
  // names the accounts tab while showing the roles matrix is a link that lies
  // about where it took you - worth tidying, because such a link is exactly
  // what somebody bookmarks or pastes to a colleague.
  useEffect(() => {
    if (wanted && found < 0 && tabs[0]) {
      setParams({ tab: tabs[0].key }, { replace: true });
    }
  }, [wanted, found, tabs, setParams]);

  return (
    <Box>
      <Box sx={{ mb: 2 }}>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>
          Settings
        </Typography>
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          {mayReadAccounts
            ? "The accounts, and what each role may do."
            : "What each role may do."}
        </Typography>
      </Box>

      <Tabs
        value={active}
        onChange={(_e, value: number) => setParams({ tab: tabs[value].key })}
        sx={{
          minHeight: 44,
          mb: 2.5,
          borderBottom: "1px solid #e3e8f0",
        }}
      >
        {tabs.map((t) => (
          <Tab
            key={t.key}
            label={t.label}
            sx={{ minHeight: 44, px: 2.5, fontWeight: 600 }}
          />
        ))}
      </Tabs>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Could not load the roles — {error}
        </Alert>
      )}

      {!matrix && !error ? (
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <CircularProgress size={20} />
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            Loading the role matrix…
          </Typography>
        </Stack>
      ) : tabs[active]?.key === "accounts" ? (
        <UserList roles={matrix?.roles ?? []} />
      ) : matrix ? (
        <RoleMatrix matrix={matrix} />
      ) : null}
    </Box>
  );
};
