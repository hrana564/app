import { useRecordContext } from "react-admin";
import { Typography } from "@mui/material";
import { FONT } from "../theme";

/**
 * The chrome a list table wears, in one place.
 *
 * The runs list and the rules list are the same object - a header row, one
 * line per row, a page picker - and they were built separately, so they
 * disagreed about row height, header weight and how wide a column is. Two
 * tables that are nearly the same are worse than two that are plainly
 * different: the reader cannot tell which differences carry meaning.
 *
 * What is shared is the chrome, not the columns. A caller names the widths it
 * wants pinned and leaves one column out; that column absorbs whatever is
 * left.
 */

/** The header cell: small, upper case, quiet, and never wrapped. */
export const LIST_HEADER_SX = {
  py: 0.75,
  px: 1.25,
  fontSize: FONT.label,
  fontWeight: 700,
  textTransform: "uppercase" as const,
  letterSpacing: 0.5,
  color: "#5b6b82",
  backgroundColor: "#fafbfe",
  whiteSpace: "nowrap" as const,
  overflow: "hidden",
};

/** The row cell: one line, and no taller than one line. */
export const LIST_ROW_SX = {
  py: 0.65,
  px: 1.25,
  borderColor: "#eef1f6",
  overflow: "hidden",
  lineHeight: 1.5,
};

/**
 * Column `i`'s width, addressed the way react-admin names its cells. Columns
 * are numbered from 1 and are the same in the header and the body.
 */
const columnWidth = (i: number, w: number) => ({
  [`& .RaDatagrid-headerCell:nth-of-type(${i}), & .RaDatagrid-rowCell:nth-of-type(${i})`]:
    { width: w },
});

/**
 * The sx for a list `<Datagrid>`, given the widths to pin.
 *
 * Fixed layout is what makes "one row, one line" enforceable: the pinned
 * columns take the width they were given and the unpinned one absorbs the
 * remainder, instead of the browser redistributing the whole table against the
 * longest cell in it - which is how a single long summary wraps every row and
 * the rows stop being scannable.
 */
export const listTableSx = (widths: Array<[number, number]> = []) => ({
  "& .RaDatagrid-table": { tableLayout: "fixed" as const },
  "& .RaDatagrid-headerCell": LIST_HEADER_SX,
  "& .RaDatagrid-rowCell": LIST_ROW_SX,
  "& .RaDatagrid-headerRow:hover": { backgroundColor: "#f3f6fb" },
  ...widths.reduce<Record<string, { width: number }>>(
    (acc, [i, w]) => Object.assign(acc, columnWidth(i, w)),
    {},
  ),
});

/**
 * A one-line text cell. `mono` for identifiers, `secondary` for the values
 * that should recede beside the one thing the row is about, `render` to format
 * on the way out.
 *
 * A cell that is empty says so with a dash rather than nothing: a blank cell
 * reads as a value that failed to load.
 */
export const TextLike = ({
  source, mono, secondary, render,
}: {
  source: string;
  label?: string;
  mono?: boolean;
  secondary?: boolean;
  render?: (value: any) => string;
}) => {
  const record = useRecordContext<any>();
  const raw = record?.[source];
  return (
    <Typography
      variant="body2"
      noWrap
      sx={{
        fontFamily: mono ? "monospace" : undefined,
        fontWeight: mono ? 700 : undefined,
        color: secondary ? "text.secondary" : "text.primary",
      }}
    >
      {render ? render(raw) : (raw ?? "—")}
    </Typography>
  );
};
