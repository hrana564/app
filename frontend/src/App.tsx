import { Admin, Resource } from "react-admin";
import RuleIcon from "@mui/icons-material/Rule";
import ScheduleIcon from "@mui/icons-material/Schedule";
import PlayCircleIcon from "@mui/icons-material/PlayCircle";
import ReportProblemIcon from "@mui/icons-material/ReportProblem";
import StorageIcon from "@mui/icons-material/Storage";

import { theme } from "./theme";
import { dataProvider } from "./dataProvider";
import { authProvider } from "./authProvider";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { Layout } from "./Layout";
import { EntityScope, useEntityScope } from "./entityScope";
import { RuleList, RuleShow, RuleEdit } from "./resources/Rule";
import { RunList, RunShow } from "./resources/Run";
import { TicketList, TicketShow } from "./resources/Ticket";
import { DatasourceList } from "./resources/Meta";

/**
 * EntityScope wraps the portal rather than sitting among the resources: the
 * app bar reads it and so does every screen, and the app bar is built by
 * react-admin rather than declared here. Context reaches both because it flows
 * down the React tree, not the resource tree.
 *
 * The resources, as elements, built ONCE.
 *
 * react-admin registers each `<Resource>` in an effect keyed on the children it
 * was handed, and its cleanup calls `unregister`, which is a `setState`. Children
 * built inside a render are new objects every time, so the registration tears
 * down and rebuilds on every render of this component.
 *
 * Hoisting them keeps one identity for the life of the module, which is what
 * that effect is keyed on.
 */
const RESOURCES = (
  <>
    <Resource
      name="tickets"
      list={TicketList}
      show={TicketShow}
      icon={ReportProblemIcon}
      options={{ label: "Ticket Inbox" }}
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

/**
 * What the portal is, right now.
 *
 * With a session it is the audit portal. Without one it is the login form, and
 * that is ALL of it - react-admin's admin is not mounted, so its sign-out
 * machinery never has an anonymous visitor to sign out. See `EntityScope` for
 * why that matters.
 *
 * The login form stands on its own rather than being react-admin's login page,
 * because react-admin's login page needs the admin around it, and the admin is
 * exactly what is not being mounted.
 */
const Portal = () => {
  const { signedIn } = useEntityScope();
  if (!signedIn) return <Login />;
  return (
    <Admin
      dataProvider={dataProvider}
      authProvider={authProvider}
      theme={theme}
      title="Scope Investments â€” Audit Portal"
      loginPage={Login}
      dashboard={Dashboard}
      layout={Layout}
      requireAuth
      disableTelemetry
    >
      {RESOURCES}
    </Admin>
  );
};

export const App = () => (
  <EntityScope>
    <Portal />
  </EntityScope>
);
