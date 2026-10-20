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
 *
 * BUT NOT BEFORE THERE IS A SESSION TO SCOPE
 * ------------------------------------------
 * The list comes from an endpoint that needs a token, so asking for it before
 * sign-in gets a 401. This provider used to ask anyway, and the 401 became a
 * message that replaced the whole application - which replaced the login
 * form, so the portal could not be signed in to at all.
 *
 * Before sign-in there is no client to scope to, and the login page does not
 * ask for one. So the shell renders, the login form is put up, and the list is
 * fetched when there is a session to scope.
 *
 * THE SESSION IS THIS PROVIDER'S TO ANSWER FOR
 * -------------------------------------------
 * With no session, the whole of the app is the login form. react-admin's admin
 * is not mounted at all, and the app says so rather than mounting an admin
 * that has no session and letting it bounce.
 *
 * That is not a preference. react-admin's own answer to "no session" is to
 * sign the visitor out of the protected branch, and the sign-out clears the
 * query cache, which re-runs the auth check, which fails, which signs the
 * visitor out again - fifty times over, and that is the "maximum update depth
 * exceeded" the console was full of. Mounting the admin only when there is a
 * session means that machinery never sees an anonymous visitor, so it never
 * has to sign one out.
 */

export type Entity = EntityOut;

const STORAGE_KEY = "audit.entity";

type Scope = {
  entity: string;
  setEntity: (code: string) => void;
  entities: Entity[];
  /**
   * Whether there is a session.
   *
   * The shell needs this and not just the client scope: with no session the
   * login form is the whole of the app, and react-admin's admin is not mounted
   * at all. See the note above on this provider.
   */
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