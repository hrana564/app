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
import { RuleList, RuleShow, RuleEdit } from "./resources/Rule";
import { RunList, RunShow } from "./resources/Run";
import { TicketList, TicketShow } from "./resources/Ticket";
import { DatasourceList } from "./resources/Meta";

export const App = () => (
  <Admin
    dataProvider={dataProvider}
    authProvider={authProvider}
    theme={theme}
    title="ABM Audit Portal"
    loginPage={Login}
    dashboard={Dashboard}
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
      list={() => <DatasourceList />}
      icon={StorageIcon}
      options={{ label: "Data Sources" }}
    />
  </Admin>
);