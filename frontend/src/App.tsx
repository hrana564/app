import { Admin, CustomRoutes, Resource } from "react-admin";
import { Navigate, Route } from "react-router";
import RuleIcon from "@mui/icons-material/Rule";
import PlayCircleIcon from "@mui/icons-material/PlayCircle";
import ReportProblemIcon from "@mui/icons-material/ReportProblem";
import StorageIcon from "@mui/icons-material/Storage";

import { theme } from "./theme";
import { dataProvider, api, setStoredUser } from "./dataProvider";
import { authProvider } from "./authProvider";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { SettingsPage } from "./pages/Settings";
import { Layout } from "./Layout";
import { EntityScope, useEntityScope } from "./entityScope";
import { RuleList, RuleShow, RuleEdit } from "./resources/Rule";
import { RunList, RunShow } from "./resources/Run";
import { TicketList, TicketShow } from "./resources/Ticket";
import { DatasourceList } from "./resources/Meta";
import { useEffect } from "react";

const RESOURCES = (
  <>
    <Resource
      name="tickets"
      list={TicketList}
      show={TicketShow}
      icon={ReportProblemIcon}
      options={{ label: "Findings" }}
    />
    <Resource
      name="rules"
      list={RuleList}
      show={RuleShow}
      edit={RuleEdit}
      icon={RuleIcon}
      options={{ label: "Rules" }}
    />
    <Resource
      name="runs"
      list={RunList}
      show={RunShow}
      icon={PlayCircleIcon}
      options={{ label: "Runs" }}
    />
    <Resource
      name="datasources"
      list={DatasourceList}
      icon={StorageIcon}
      options={{ label: "Data Sources", recordRepresentation: false }}
    />
  </>
);

const ROUTES = (
  <CustomRoutes>
    <Route path="/settings" element={<SettingsPage />} />
    <Route path="/users" element={<Navigate to="/settings" replace />} />
    {/* A rule page is a page about one VERSION row, and the address says
        which. `:id` rather than `:code` because react-admin's show controller
        reads the id from that segment; the version is ours. */}
    <Route path="/rules/:id/show/:version" element={<RuleShow />} />
  </CustomRoutes>
);

const Portal = () => {
  const { signedIn } = useEntityScope();

  useEffect(() => {
    if (!signedIn) return;
    let live = true;
    api
      .me()
      .then((user) => { if (live) setStoredUser(user); })
      .catch(() => {
        /* the 401 handler has already dealt with an expired session */
      });
    return () => { live = false; };
  }, [signedIn]);

  if (!signedIn) return <Login />;
  return (
    <Admin
      dataProvider={dataProvider}
      authProvider={authProvider}
      theme={theme}
      title="SquareOne Holdings — Agentic Data Analytics"
      loginPage={Login}
      dashboard={Dashboard}
      layout={Layout}
      requireAuth
      disableTelemetry
    >
      {RESOURCES}
      {ROUTES}
    </Admin>
  );
};

export const App = () => (
  <EntityScope>
    <Portal />
  </EntityScope>
);
