import { useRecordContext } from "react-admin";
import { Typography } from "@mui/material";
import { FONT } from "../theme";


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

export const LIST_ROW_SX = {
  py: 0.65,
  px: 1.25,
  borderColor: "#eef1f6",
  overflow: "hidden",
  lineHeight: 1.5,
};

const columnWidth = (i: number, w: number) => ({
  [`& .RaDatagrid-headerCell:nth-of-type(${i}), & .RaDatagrid-rowCell:nth-of-type(${i})`]:
    { width: w },
});

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
