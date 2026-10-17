import { Chip, Box } from "@mui/material";
import { useRecordContext } from "react-admin";
import { severityColor, statusColor, entityColor } from "../theme";

export const SeverityChip = ({ value }: { value?: string }) =>
  value ? (
    <Chip size="small" label={value.toUpperCase()} color={severityColor[value] ?? "info"} />
  ) : (
    <Chip size="small" label="-" color="info" />
  );

export const StatusChip = ({ value }: { value?: string }) =>
  value ? (
    <Chip size="small" label={value.toUpperCase()} color={statusColor[value] ?? "info"} />
  ) : (
    <Chip size="small" label="-" color="info" />
  );

export const EntityChip = ({ value }: { value?: string }) =>
  value ? (
    <Chip size="small" label={value} color={entityColor[value] ?? "primary"} variant="outlined" />
  ) : null;

/* ------------------------------------------------------------------ */
/* Datagrid cell fields                                                */
/* react-admin's TextField has no `render` prop - these read the row   */
/* record from context instead.                                        */
/* ------------------------------------------------------------------ */
const fieldFromRecord =
  (render: (value: any, record: any) => React.ReactNode) =>
  // eslint-disable-next-line react/display-name
  ({ source }: { source: string }) => {
    const record = useRecordContext<any>();
    return <>{render(record?.[source], record)}</>;
  };

export const SeverityField = fieldFromRecord((v) => <SeverityChip value={v} />);
export const StatusField = fieldFromRecord((v) => <StatusChip value={v} />);
export const EntityField = fieldFromRecord((v) => <EntityChip value={v} />);
export const RowCountField = fieldFromRecord((v) => <RowCount value={v?.length} />);
export const IdField = fieldFromRecord((v) => <RowCount value={v} />);
export const BooleanDotField = fieldFromRecord((v) => (
  <Chip size="small" label={v ? "ENABLED" : "DISABLED"} color={v ? "success" : "default"} />
));

export const RowCount = ({ value }: { value?: number }) => (
  <Box component="span" sx={{ fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>
    {value ?? 0}
  </Box>
);

/** Renders an arbitrary JSON object as a compact two-column key/value list. */
export const JsonBlock = ({ value }: { value: Record<string, unknown> | undefined }) => {
  if (!value || Object.keys(value).length === 0) {
    return <Box sx={{ color: "text.secondary", fontStyle: "italic" }}>No metrics recorded.</Box>;
  }
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "repeat(auto-fill, minmax(220px, 1fr))" },
        gap: 1,
      }}
    >
      {Object.entries(value).map(([k, v]) => (
        <Box
          key={k}
          sx={{
            bgcolor: "#f7f9fc", border: "1px solid #e9edf4",
            borderRadius: 1.5, px: 1.25, py: 1,
          }}
        >
          <Box sx={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, color: "text.secondary", fontWeight: 700 }}>
            {k.replace(/_/g, " ")}
          </Box>
          <Box sx={{ fontFamily: "monospace", fontSize: 14, fontWeight: 600, wordBreak: "break-word" }}>
            {typeof v === "number" ? v.toLocaleString() : String(v)}
          </Box>
        </Box>
      ))}
    </Box>
  );
};

/** Read-only Python source viewer. Deliberately not a full editor - keep it light. */
export const CodeBlock = ({ code }: { code: string }) => (
  <Box
    component="pre"
    sx={{
      m: 0,
      p: 2,
      bgcolor: "#0d1b2a",
      color: "#e6edf3",
      borderRadius: 2,
      overflow: "auto",
      maxHeight: 560,
      fontSize: 13,
      lineHeight: 1.55,
      fontFamily: '"JetBrains Mono", "Fira Code", Consolas, "Courier New", monospace',
      msoOutline: "none",
    }}
  >
    {code || "# no source available"}
  </Box>
);

/** Tabular viewer for the exception rows attached to a ticket or run. */
export const RowsTable = ({ rows }: { rows: Record<string, unknown>[] }) => {
  if (!rows || rows.length === 0) {
    return <Box sx={{ color: "text.secondary", fontStyle: "italic" }}>No exception rows.</Box>;
  }
  const columns = Array.from(
    rows.reduce((set, row) => {
      Object.keys(row).forEach((k) => set.add(k));
      return set;
    }, new Set<string>()),
  ).slice(0, 14);

  return (
    <Box sx={{ overflow: "auto", maxHeight: 420, border: "1px solid #e9edf4", borderRadius: 2 }}>
      <Box component="table" sx={{ borderCollapse: "collapse", width: "100%", fontSize: 13 }}>
        <Box component="thead" sx={{ position: "sticky", top: 0, zIndex: 1 }}>
          <Box component="tr" sx={{ bgcolor: "#f7f9fc" }}>
            {columns.map((c) => (
              <Box
                key={c}
                component="th"
                sx={{
                  textAlign: "left", px: 1.5, py: 1, fontSize: 11, fontWeight: 700,
                  textTransform: "uppercase", letterSpacing: 0.5, color: "text.secondary",
                  borderBottom: "1px solid #e9edf4", whiteSpace: "nowrap",
                }}
              >
                {c.replace(/_/g, " ")}
              </Box>
            ))}
          </Box>
        </Box>
        <Box component="tbody">
          {rows.map((row, i) => (
            <Box
              key={i}
              component="tr"
              sx={{ "&:nth-of-type(odd)": { bgcolor: "#fcfdff" }, "&:hover": { bgcolor: "#f2f7fd" } }}
            >
              {columns.map((c) => (
                <Box
                  key={c}
                  component="td"
                  sx={{
                    px: 1.5, py: 0.85, borderBottom: "1px solid #f0f3f8",
                    fontFamily: "monospace", fontSize: 12.5, whiteSpace: "nowrap",
                  }}
                >
                  {row[c] === null || row[c] === undefined
                    ? "—"
                    : String(row[c])}
                </Box>
              ))}
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  );
};