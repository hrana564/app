import { createTheme } from "@mui/material/styles";

/**
 * A deliberately restrained navy/teal palette. High contrast for the severity
 * chips, and generous spacing because the tables carry long audit summaries.
 */
export const theme = createTheme({
  palette: {
    mode: "light",
    primary: { main: "#123a63", light: "#2b6ba8", dark: "#0a2540" },
    secondary: { main: "#0f8a80" },
    background: { default: "#f4f6fa", paper: "#ffffff" },
    error: { main: "#c62828" },
    warning: { main: "#ef6c00" },
    success: { main: "#2e7d32" },
    info: { main: "#0277bd" },
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    h5: { fontWeight: 700, letterSpacing: -0.2 },
    h6: { fontWeight: 650 },
    subtitle2: { fontWeight: 600 },
    button: { textTransform: "none", fontWeight: 600 },
  },
  components: {
    MuiCard: {
      styleOverrides: {
        root: {
          border: "1px solid #e3e8f0",
          boxShadow: "0 1px 3px rgba(18, 58, 99, 0.06)",
        },
      },
    },
    MuiPaper: {
      styleOverrides: { root: { backgroundImage: "none" } },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { borderColor: "#eef1f6" },
        head: {
          fontWeight: 700,
          fontSize: 12,
          textTransform: "uppercase",
          letterSpacing: 0.5,
          color: "#5b6b82",
          backgroundColor: "#fafbfe",
        },
      },
    },
    MuiChip: { styleOverrides: { root: { fontWeight: 600 } } },
  },
});

export const severityColor: Record<string, "error" | "warning" | "info" | "success"> = {
  critical: "error",
  high: "error",
  medium: "warning",
  low: "info",
};

export const statusColor: Record<string, "error" | "warning" | "info" | "success"> = {
  fail: "error",
  error: "error",
  open: "error",
  paused: "warning",
  draft: "warning",
  running: "info",
  pass: "success",
  active: "success",
  acknowledged: "success",
  dismissed: "info",
  retired: "info",
};

export const entityColor: Record<string, "primary" | "secondary"> = {
  LAD: "secondary",
  ABM: "primary",
};