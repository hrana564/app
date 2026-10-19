import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Box, CircularProgress, Typography } from "@mui/material";
import { api, type EntityOut } from "./dataProvider";

/**
 * The client entity every screen is scoped to.
 *
 * The audited businesses share one portal: their data lives in separate
 * folders, their rules are separate rules and their tickets are separate
 * tickets - but the choice of which client you are looking at belongs to the
 * shell, not to each screen. A filter repeated on five toolbars is five chances
 * to disagree with the one above it.
 *
 * WHO THE CLIENTS ARE IS CONFIGURATION, and it arrives from the API.
 *
 * There used to be a hardcoded pair here. That was wrong twice over: it named
 * two specific businesses inside a product meant to be deployed for whoever
 * bought it, and it was a second source of truth - the backend already serves
 * the list from `GET /api/datasources/entities`, driven by CLIENT_ENTITIES in
 * the deployment's .env. Two copies drift the first time somebody adds a
 * client, and the drift is silent: the app bar offers a client the API has
 * never heard of, and every screen scoped to it comes back empty.
 *
 * So nothing below this provider may render before the list has arrived.
 * Rendering early would hand `<List filter={{ entity }}>` an undefined entity,
 * which is no filter at all - the app bar would say one client while the list
 * beneath it showed every client.
 */

export type Entity = EntityOut;

const STORAGE_KEY = "audit.entity";

type Scope = {
  entity: string;
  setEntity: (code: string) => void;
  entities: Entity[];
};

const EntityContext = createContext<Scope>({
  entity: "",
  setEntity: () => undefined,
  entities: [],
});

export const EntityScope = ({ children }: { children: ReactNode }) => {
  const [entities, setEntities] = useState<Entity[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [entity, set] = useState("");

  useEffect(() => {
    let live = true;
    api
      .entities()
      .then((list) => {
        if (!live) return;
        if (list.length === 0) {
          setError("This deployment has no clients configured.");
          return;
        }
        setEntities(list);

        // A stored code that no longer exists - a client removed from
        // CLIENT_ENTITIES - falls back to the first one rather than scoping the
        // whole app to nothing, which would read as an empty portal.
        let chosen = "";
        try {
          chosen = localStorage.getItem(STORAGE_KEY) ?? "";
        } catch {
          chosen = "";
        }
        set(list.some((e) => e.code === chosen) ? chosen : list[0].code);
      })
      .catch(() => live && setError("Could not reach the API to list the clients."));
    return () => {
      live = false;
    };
  }, []);

  const value = useMemo<Scope>(
    () => ({
      entity,
      entities: entities ?? [],
      setEntity: (code: string) => {
        if (!entities?.some((e) => e.code === code)) return;
        try {
          localStorage.setItem(STORAGE_KEY, code);
        } catch {
          /* private browsing: the choice just will not survive a reload */
        }
        set(code);
      },
    }),
    [entity, entities],
  );

  if (error) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh" }}>
        <Typography variant="body1">{error}</Typography>
      </Box>
    );
  }

  if (!entities || entity === "") {
    return (
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh" }}>
        <CircularProgress />
      </Box>
    );
  }

  return <EntityContext.Provider value={value}>{children}</EntityContext.Provider>;
};

export const useEntityScope = () => useContext(EntityContext);