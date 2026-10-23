import { createTheme } from "@mui/material/styles";

export const FONT = {
  body: "1rem", // 16px
  bodyAlt: "0.9375rem", // 15px
  label: "0.8125rem", // 13px - the floor
} as const;

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
    htmlFontSize: 16,
    h4: { fontSize: "1.75rem", fontWeight: 700, letterSpacing: -0.3 },
    h5: { fontSize: "1.5rem", fontWeight: 700, letterSpacing: -0.2 },
    h6: { fontSize: "1.25rem", fontWeight: 650 },
    subtitle1: { fontSize: "1rem", fontWeight: 600 },
    subtitle2: { fontSize: FONT.bodyAlt, fontWeight: 600 },
    body1: { fontSize: FONT.body, lineHeight: 1.5 },
    body2: { fontSize: FONT.bodyAlt, lineHeight: 1.5 },
    button: { fontSize: FONT.bodyAlt, fontWeight: 600, textTransform: "none" },
    caption: { fontSize: FONT.label, lineHeight: 1.45 },
    overline: { fontSize: FONT.label, fontWeight: 700, letterSpacing: 0.6 },
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
        root: {
          borderColor: "#eef1f6",
          // size="small" halves the default padding, which reads as cramped once
          // the type is larger. Give dense tables room to breathe.
          padding: "10px 16px",
        },
        head: {
          fontWeight: 700,
          fontSize: FONT.label,
          textTransform: "uppercase",
          letterSpacing: 0.5,
          color: "#5b6b82",
          backgroundColor: "#fafbfe",
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          fontWeight: 600,
          // MUI v9 removed the `sizeSmall` styleOverride key - a class name is
          // no longer a slot you can style. A variant is, so the short chip's
          // height is asked for the way v9 asks.
          variants: [{ props: { size: "small" }, style: { height: 24 } }],
        },
        label: { fontSize: FONT.label, paddingLeft: 10, paddingRight: 10 },
      },
    },
    MuiTooltip: {
      styleOverrides: { tooltip: { fontSize: FONT.label } },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        notchedOutline: {
          // The floating label sits at 0.8125em of the input type, which put it
          // at 11.25px - the only text anywhere on screen below the 13px floor.
          "& legend": { fontSize: "0.875em" },
        },
      },
    },
    MuiInputLabel: {
      styleOverrides: { root: { fontSize: FONT.bodyAlt } },
    },
    MuiInputBase: {
      styleOverrides: {
        root: {
          fontSize: FONT.bodyAlt,
          // size="small" inputs shrink their type as well as their box. Small
          // inputs keep the same type as normal ones here.
          "& input": { fontSize: "inherit" },
          "& .MuiSelect-select": { fontSize: "inherit" },
        },
      },
    },
    MuiMenuItem: {
      styleOverrides: { root: { fontSize: FONT.bodyAlt, minHeight: 40 } },
    },
  },
});

export const SEVERITIES = ["critical", "high", "medium", "low"] as const;
export type Severity = (typeof SEVERITIES)[number];

export const SEVERITIES_ASC = [...SEVERITIES].reverse();

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

export const runStatusColor: Record<string, "error" | "warning" | "info" | "success"> = {
  success: "success",
  failed: "error",
  running: "info",
};

export const runOutcomeColor: Record<string, "error" | "warning" | "info" | "success"> = {
  fail: "error",
  pass: "success",
};

export const ENTITY_COLOURS = ["secondary", "primary"] as const;

export const entityColour = (index: number) =>
  ENTITY_COLOURS[index % ENTITY_COLOURS.length];