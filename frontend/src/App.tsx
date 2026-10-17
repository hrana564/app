import { Admin, Resource } from "react-admin";
import { Box, Typography } from "@mui/material";
import DashboardIcon from "@mui/icons-material/Dashboard";
import RuleIcon from "@mui/icons-material/Rule";
import ScheduleIcon from "@mui/icons-material/Schedule";
import PlayCircleIcon from "@mui/icons-material/PlayCircle";
import ReportProblemIcon from "@mui/icons-material/ReportProblem";
import TuneIcon from "@mui/icons-material/Tune";
import StorageIcon from "@mui/icons-material/Storage";

import { theme } from "./theme";
import { dataProvider } from "./dataProvider";
import { authProvider } from "./authProvider";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { RuleList, RuleShow } from "./resources/Rule";
import { RunList, RunShow, ScheduleList, ScheduleShow } from "./resources/Run";
import { TicketList, TicketShow } from "./resources/Ticket";
import { DatasourceList, ParamList } from "./resources/Meta";

/**
 * Hide admin-only screens from auditors. The backend enforces this too (403) -
 * this just keeps the navigation honest.
 */
const AdminOnly = ({ children }: { children: React.ReactNode }) => {
  const role = JSON.parse(localStorage.getItem("abm.user") ?? "{}").role;
  if (role !== "admin") {
    return (
      <Box sx={{ p: 4 }}>
        <Typography variant="h6">Restricted</Typography>
        <Typography variant="body2" color="text.secondary">
          This area requires the admin role. Ask an audit administrator if you need access.
        </Typography>
      </Box>
    );
  }
  return <>{children}</>;
};

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
      icon={RuleIcon}
      options={{ label: "Rules" }}
    />
    <Resource
      name="schedules"
      list={ScheduleList}
      show={ScheduleShow}
      icon={ScheduleIcon}
      options={{ label: "Schedules" }}
    />
    <Resource
      name="runs"
      list={RunList}
      show={RunShow}
      icon={PlayCircleIcon}
      options={{ label: "Runs" }}
    />
    <Resource
      name="params"
      list={() => <AdminOnly><ParamList /></AdminOnly>}
      icon={TuneIcon}
      options={{ label: "Parameters" }}
    />
    <Resource
      name="datasources"
      list={() => <DatasourceList />}
      icon={StorageIcon}
      options={{ label: "Data Sources" }}
    />
  </Admin>
);