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
import { EntityScope } from "./entityScope";
import { RuleList, RuleShow, RuleEdit } from "./resources/Rule";
import { RunList, RunShow } from "./resources/Run";
import { TicketList, TicketShow } from "./resources/Ticket";
import { DatasourceList } from "./resources/Meta";

/**
 * EntityScope wraps <Admin> rather than sitting among the resources: the app bar
 * reads it and so does every screen, and the app bar is built by react-admin
 * rather than declared here. Context reaches both because it flows down the
 * React tree, not the resource tree.
 */
export const App = () => (
  <EntityScope>
    <Admin
      dataProvider={dataProvider}
      authProvider={authProvider}
      theme={theme}
      title="Scope Investments — Audit Portal"
      loginPage={Login}
      dashboard={Dashboard}
      layout={Layout}
      requireAuth
      disableTelemetry
    >
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
    </Admin>
  </EntityScope>
);