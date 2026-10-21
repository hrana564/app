import {
  Chip, Box, IconButton, Stack, Tooltip, Typography, useTheme,
} from "@mui/material";
import { useMemo } from "react";
import { Link } from "react-router";
import { useRecordContext } from "react-admin";
import { useEntityScope } from "../entityScope";
import { severityColor, statusColor, entityColour, FONT } from "../theme";

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

export const EntityChip = ({ value }: { value?: string }) => {
  const { entities } = useEntityScope();
  if (!value) return null;
  const at = entities.findIndex((e) => e.code === value);
  // An unknown code is styled neutrally rather than as the first client: a
  // chip that borrowed another client's colour would be asserting a link that
  // may not exist.
  return (
    <Chip
      size="small"
      label={value}
      color={at < 0 ? "default" : entityColour(at)}
      variant="outlined"
    />
  );
};

/* ------------------------------------------------------------------ */
/* Dot + text                                                          */
/* ------------------------------------------------------------------ */
/**
 * The rules screen used a filled Chip for severity, status and validation -
 * three coloured blocks per row, so the eye had nothing to land on. Colour
 * survives here as a small accent dot; the fill does not.
 *
 * `tone` is a palette key, so the colour comes from the theme rather than a
 * second hard-coded table that could drift out of step with it.
 */
export const DotLabel = ({
  value,
  tone = "default",
  hint,
}: {
  value?: string | null;
  tone?: string;
  hint?: string;
}) => {
  const theme = useTheme();
  const palette = theme.palette as unknown as Record<string, { main?: string } | undefined>;
  const colour =
    tone === "default" ? "#8d99ae" : (palette[tone]?.main ?? "#8d99ae");

  if (!value) {
    return (
      <Typography variant="body2" sx={{ color: "text.secondary" }}>
        &mdash;
      </Typography>
    );
  }
  return (
    <Stack direction="row" spacing={0.75} sx={{ minWidth: 0, alignItems: "center" }}>
      <Box
        sx={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          bgcolor: colour,
          flexShrink: 0,
        }}
      />
      <Typography
        variant="body2"
        noWrap
        title={hint ?? value}
        sx={{ textTransform: "capitalize" }}
      >
        {value}
      </Typography>
    </Stack>
  );
};

/** Status on the first line, validation state as a quiet caption beneath. */
export const RuleStateCell = ({ record }: { record: any }) => {
  const status = record?.status;
  return (
    <Stack spacing={0.15} sx={{ minWidth: 0 }}>
      <DotLabel value={status} tone={statusColor[status] ?? "default"} />
      <Typography variant="caption" noWrap sx={{ color: "text.secondary" }}>
        {record?.validation_status === "valid"
          ? "validated"
          : record?.validation_status === "invalid"
            ? "validation failed"
            : "not validated"}
      </Typography>
    </Stack>
  );
};

/* ------------------------------------------------------------------ */
/* Datagrid cell fields                                                */
/* react-admin's TextField has no `render` prop - these read the row   */
/* record from context instead.                                        */
/* ------------------------------------------------------------------ */
/**
 * Build a react-admin Datagrid cell that reads its value off the row record.
 *
 * `source` is still required on the element: the Datagrid inspects children's
 * `source` prop to decide which columns are sortable.
 */
export const fieldFromRecord =
  (render: (value: any, record: any) => React.ReactNode) =>
  // eslint-disable-next-line react/display-name
  ({ source, label }: { source: string; label?: string }) => {
    void label; // read by the Datagrid to title the column; nothing to render
    const record = useRecordContext<any>();
    return <>{render(record?.[source], record)}</>;
  };

/**
 * The same chip, two ways: as a `<Datagrid>` column, or handed a `record`
 * directly where there is no row to read from.
 *
 * The second path exists because a rule's detail page showed these chips in a
 * page header and not in a grid, and passing it an `EntityField` there was
 * refused at runtime - the field names its column through react-admin's
 * context, so without one the header threw.
 */
const chipsFor =
  (render: (props: { value?: string }) => React.ReactElement) =>
  // eslint-disable-next-line react/display-name
  ({ source, record }: { source: string; record?: any }) => {
    const contextRecord = useRecordContext<any>();
    return render({ value: (record ?? contextRecord)?.[source] });
  };

export const SeverityField = fieldFromRecord((v) => <SeverityChip value={v} />);
export const SeverityChipField = chipsFor((p) => <SeverityChip {...p} />);
export const StatusField = fieldFromRecord((v) => <StatusChip value={v} />);
export const StatusChipField = chipsFor((p) => <StatusChip {...p} />);
export const EntityField = fieldFromRecord((v) => <EntityChip value={v} />);
export const RowCountField = fieldFromRecord((v) => <RowCount value={v?.length} />);
export const IdField = fieldFromRecord((v) => <RowCount value={v} />);
export const BooleanDotField = fieldFromRecord((v) => (
  <Chip size="small" label={v ? "ENABLED" : "DISABLED"} color={v ? "success" : "default"} />
));

/**
 * An icon action with a tooltip. Disabled actions stay in place and say why,
 * rather than disappearing - a missing button is a question a user has to ask.
 *
 * Give it `to` to navigate or `onClick` to act. It never accepts both.
 */
export const ActionIcon = ({
  title,
  icon,
  onClick,
  disabled,
  to,
  color = "default",
}: {
  title: string;
  icon: React.ReactNode;
  onClick?: (e: React.MouseEvent) => void;
  disabled?: boolean;
  to?: string;
  color?: "default" | "primary" | "error";
}) => {
  const sx = {
    color: disabled ? undefined : color === "error" ? "error.main" : "text.secondary",
    "&:hover": {
      bgcolor: color === "error" ? "rgba(198, 40, 40, 0.08)" : "action.hover",
    },
  };
  return (
    <Tooltip title={title}>
      <span>
        {to ? (
          <IconButton
            size="small"
            disabled={disabled}
            component={Link}
            to={to}
            aria-label={title}
            sx={sx}
          >
            {icon}
          </IconButton>
        ) : (
          <IconButton
            size="small"
            disabled={disabled}
            onClick={onClick}
            aria-label={title}
            sx={sx}
          >
            {icon}
          </IconButton>
        )}
      </span>
    </Tooltip>
  );
};

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
          <Box sx={{ fontSize: FONT.label, textTransform: "uppercase", letterSpacing: 0.5, color: "text.secondary", fontWeight: 700 }}>
            {k.replace(/_/g, " ")}
          </Box>
          <Box sx={{ fontFamily: "monospace", fontSize: FONT.bodyAlt, fontWeight: 600, wordBreak: "break-word" }}>
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
      fontSize: "0.9375rem",
      lineHeight: 1.6,
      fontFamily: '"JetBrains Mono", "Fira Code", Consolas, "Courier New", monospace',
      msoOutline: "none",
    }}
  >
    {code || "# no source available"}
  </Box>
);

/* ---------------------------------------------------------------------------
 * Python, with line numbers
 * ------------------------------------------------------------------------ */

// One pass over the whole file, so a triple-quoted docstring is recognised as
// one string rather than three broken lines. Order matters: comment, string,
// number, word, whitespace, anything else.
const PY_TOKEN =
  /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(\s+)|([\s\S])/g;

const PY_KEYWORDS = new Set(
  ("def class return if elif else for while in not and or import from as with try " +
    "except finally raise lambda pass continue break global None True False is " +
    "yield assert del async await").split(" "),
);

type PyKind = "comment" | "string" | "number" | "keyword" | "builtin" | "text";

const PY_COLOURS: Record<PyKind, string> = {
  comment: "#7d8fa9",
  string: "#a5d6a7",
  number: "#ffcc80",
  keyword: "#82aaff",
  builtin: "#c792ea",
  text: "#e6edf3",
};

/**
 * Split source into coloured tokens, then into lines.
 *
 * A syntax highlighter would be a dependency, and this file needs six colours
 * and no error recovery: the worst it can do to a wrong guess is colour one
 * word strangely. Splitting on newlines *after* tokenising is what keeps a
 * multi-line docstring - which is where a rule explains itself - in one piece.
 */
const pyLines = (code: string): { text: string; kind: PyKind }[][] => {
  const lines: { text: string; kind: PyKind }[][] = [[]];
  PY_TOKEN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = PY_TOKEN.exec(code)) !== null) {
    const [raw, comment, str, num, word] = match;
    let kind: PyKind = "text";
    if (comment) kind = "comment";
    else if (str) kind = "string";
    else if (num) kind = "number";
    else if (word) {
      kind = PY_KEYWORDS.has(word) ? "keyword" : "text";
    }
    raw.split("\n").forEach((part, i) => {
      if (i > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ text: part, kind });
    });
  }
  return lines;
};

/**
 * A rule's source, numbered, with one range lit up.
 *
 * The line numbers are not decoration: every step of the plain-English
 * description points at a range, and a reader checking the description against
 * the code needs to find line 73 quickly. `highlight` is that range, set by
 * whichever step the reader is looking at.
 */
export const PythonBlock = ({
  code,
  highlight,
  maxHeight = 620,
}: {
  code: string;
  highlight?: [number, number] | null;
  maxHeight?: number;
}) => {
  const lines = useMemo(() => pyLines(code || ""), [code]);
  const [from, to] = highlight ?? [0, 0];

  return (
    <Box
      sx={{
        bgcolor: "#0d1b2a",
        borderRadius: 2,
        overflow: "auto",
        maxHeight,
        py: 1,
        fontFamily: '"JetBrains Mono", "Fira Code", Consolas, "Courier New", monospace',
        fontSize: FONT.bodyAlt,
        lineHeight: 1.65,
      }}
    >
      {lines.map((tokens, i) => {
        const n = i + 1;
        const lit = n >= from && n <= to;
        return (
          <Box
            key={n}
            data-line={n}
            sx={{
              display: "grid",
              gridTemplateColumns: "3.25rem 1fr",
              columnGap: 1.5,
              bgcolor: lit ? "rgba(130,170,255,0.16)" : "transparent",
              boxShadow: lit ? "inset 2px 0 0 #82aaff" : "none",
            }}
          >
            <Box
              component="span"
              sx={{
                textAlign: "right",
                color: lit ? "#82aaff" : "#4a5b73",
                userSelect: "none",
                pr: 0.5,
              }}
            >
              {n}
            </Box>
            <Box component="span" sx={{ whiteSpace: "pre", pr: 2 }}>
              {tokens.length === 0 ? " " : tokens.map((t, j) => (
                <span key={j} style={{ color: PY_COLOURS[t.kind] }}>{t.text}</span>
              ))}
            </Box>
          </Box>
        );
      })}
    </Box>
  );
};

/** Tabular viewer for the Exception lines attached to a ticket or run. */
/**
 * A grid of records, scrollable in both directions with the header pinned.
 *
 * Shared by a run's Exception lines and a datasource preview, which want the same
 * thing from different data. The optional props exist because the defaults are
 * tuned for the first: a run's rows are the reason the user is on the page, so
 * they are capped and say so when empty, while a preview is for skimming and
 * should show every column the file has.
 */
export const RowsTable = ({
  rows,
  emptyLabel = "No Exception lines.",
  maxColumns = 14,
  types,
  caption,
}: {
  rows: Record<string, unknown>[];
  emptyLabel?: string;
  maxColumns?: number;
  /** Column name -> type, shown under the header rather than beside it. */
  types?: Record<string, string>;
  caption?: React.ReactNode;
}) => {
  if (!rows || rows.length === 0) {
    return (
      <Box sx={{ color: "text.secondary", fontStyle: "italic" }}>{emptyLabel}</Box>
    );
  }
  const all = Array.from(
    rows.reduce((set, row) => {
      Object.keys(row).forEach((k) => set.add(k));
      return set;
    }, new Set<string>()),
  );
  const columns = all.slice(0, maxColumns);
  const hidden = all.length - columns.length;

  return (
    <Box>
      {caption}
      <Box sx={{ overflow: "auto", maxHeight: 420, border: "1px solid #e9edf4", borderRadius: 2 }}>
      <Box component="table" sx={{ borderCollapse: "collapse", width: "100%" }}>
        <Box component="thead" sx={{ position: "sticky", top: 0, zIndex: 1 }}>
          <Box component="tr" sx={{ bgcolor: "#f7f9fc" }}>
            {columns.map((c) => (
              <Box
                key={c}
                component="th"
                sx={{
                  textAlign: "left", px: 1.5, py: 1, fontSize: FONT.label, fontWeight: 700,
                  textTransform: "uppercase", letterSpacing: 0.5, color: "text.secondary",
                  borderBottom: "1px solid #e9edf4", whiteSpace: "nowrap",
                }}
              >
                {c.replace(/_/g, " ")}
                {/* The type belongs under the name it describes. On the same
                    line it reads as "EMPLOYEE ID VARCHAR", and a column list
                    drawn elsewhere on the screen then says it a second time. */}
                {types?.[c] && (
                  <Box
                    sx={{
                      display: "block", fontFamily: "monospace", fontWeight: 400,
                      textTransform: "none", letterSpacing: 0,
                      color: "#8d99ae", mt: 0.25,
                    }}
                  >
                    {types[c]}
                  </Box>
                )}
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
                    fontFamily: "monospace", fontSize: FONT.bodyAlt, whiteSpace: "nowrap",
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
      {hidden > 0 && (
        <Typography variant="caption" sx={{ display: "block", mt: 0.75, color: "text.secondary" }}>
          {`${hidden} more column${hidden === 1 ? "" : "s"} not shown`}
        </Typography>
      )}
      </Box>
    </Box>
  );
};