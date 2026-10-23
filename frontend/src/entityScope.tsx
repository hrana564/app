import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Box, CircularProgress, Typography } from "@mui/material";
import {
  api, ApiError, getToken, goToLogin, SESSION_EVENT, type EntityOut,
} from "./dataProvider";


export type Entity = EntityOut;

const STORAGE_KEY = "audit.entity";

type Scope = {
  entity: string;
  setEntity: (code: string) => void;
  entities: Entity[];
  signedIn: boolean;
};

const EntityContext = createContext<Scope>({
  entity: "",
  setEntity: () => undefined,
  entities: [],
  signedIn: false,
});

export const EntityScope = ({ children }: { children: ReactNode }) => {
  const [entities, setEntities] = useState<Entity[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [entity, set] = useState("");
  // Whether there is a session to scope. Read once here and then kept in
  // step with the session events, because a token can appear or vanish while
  // the app is open - a sign-in, a sign-out, or a 401 anywhere clearing it.
  const [signedIn, setSignedIn] = useState(() => Boolean(getToken()));

  useEffect(() => {
    const onSession = () => setSignedIn(Boolean(getToken()));
    window.addEventListener(SESSION_EVENT, onSession);
    onSession();
    return () => window.removeEventListener(SESSION_EVENT, onSession);
  }, []);

  // No session, so the login form is what should be on screen. This is the
  // same rule the entry point applies before the first render, and it is
  // applied here too because the session can go away while the app is open -
  // a sign-out, or a 401 anywhere clearing it. See `goToLogin`.
  useEffect(() => {
    if (!signedIn) goToLogin();
  }, [signedIn]);

  useEffect(() => {
    if (!signedIn) {
      setEntities(null);
      setError(null);
      return;
    }
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
      .catch((e: Error) => {
        if (!live) return;
        // A 401 is not a broken API. The request was made and refused, which
        // is a different thing, and the message has to say which it was.
        setError(
          e instanceof ApiError && e.status === 401
            ? "Your session has expired. Please sign in again."
            : "Could not reach the API to list the clients.",
        );
      });
    return () => {
      live = false;
    };
  }, [signedIn]);

  const value = useMemo<Scope>(
    () => ({
      entity,
      entities: entities ?? [],
      signedIn,
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
    [entity, entities, signedIn],
  );

  // No session, no scope, and no list to wait for. The shell renders and the
  // login form is put up. This is the whole fix: the list is an authenticated
  // read, so asking for it as a stranger was asking for a 401, and the error
  // that came back replaced the login form with a wall.
  if (!signedIn) {
    return <EntityContext.Provider value={value}>{children}</EntityContext.Provider>;
  }

  if (error) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh" }}>
        <Typography variant="body1">{error}</Typography>
      </Box>
    );
  }

  // There IS a session, so the scope now matters, and it has to be right
  // before anything below renders. An undefined entity is no filter at all.
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