import { useMemo } from "react";
import { Box, Typography } from "@mui/material";
import { FONT } from "../theme";


export type DiffLine = {
  kind: "same" | "add" | "del";
  text: string;
  left?: number;
  right?: number;
};

// The LCS table is n by m, so a runaway file would cost a browser tab its
// memory rather than show a diff. A rule is a hundred lines; anything past
// this is not a rule anybody is going to read a diff of.
const MAX_DIFF_LINES = 2000;

export const tooBigToDiff = (before: string, after: string): boolean =>
  (before ?? "").split("\n").length > MAX_DIFF_LINES ||
  (after ?? "").split("\n").length > MAX_DIFF_LINES;

export const diffLines = (before: string, after: string): DiffLine[] => {
  const a = (before ?? "").split("\n");
  const b = (after ?? "").split("\n");
  const n = a.length;
  const m = b.length;

  if (n > MAX_DIFF_LINES || m > MAX_DIFF_LINES) return [];

  // dp[i][j] = the length of the LCS of a[i:] and b[j:].
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] =
        a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: "same", text: a[i], left: i + 1, right: j + 1 });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: "del", text: a[i], left: i + 1 });
      i++;
    } else {
      out.push({ kind: "add", text: b[j], right: j + 1 });
      j++;
    }
  }
  while (i < n) out.push({ kind: "del", text: a[i], left: ++i });
  while (j < m) out.push({ kind: "add", text: b[j], right: ++j });
  return out;
};

export const countChanges = (lines: DiffLine[]) => ({
  added: lines.filter((l) => l.kind === "add").length,
  removed: lines.filter((l) => l.kind === "del").length,
});

const MONO = '"JetBrains Mono", "Fira Code", Consolas, "Courier New", monospace';

export const RuleDiff = ({
  before,
  after,
  maxHeight = 620,
}: {
  before: string;
  after: string;
  maxHeight?: number;
}) => {
  const lines = useMemo(() => diffLines(before, after), [before, after]);
  const { added, removed } = countChanges(lines);

  if (tooBigToDiff(before, after)) {
    return (
      <Typography variant="body2" sx={{ color: "text.secondary" }}>
        This file is too large to show a difference. The source is still saved
        in full.
      </Typography>
    );
  }

  if (added === 0 && removed === 0) {
    return (
      <Typography variant="body2" sx={{ color: "text.secondary" }}>
        No change yet.
      </Typography>
    );
  }

  return (
    <Box
      sx={{
        border: "1px solid #d9e0ea",
        borderRadius: 2,
        overflow: "auto",
        maxHeight,
        fontFamily: MONO,
        fontSize: FONT.bodyAlt,
        lineHeight: 1.6,
        bgcolor: "#fff",
      }}
    >
      <Box
        sx={{
          display: "flex",
          gap: 1.5,
          px: 1.5,
          py: 0.5,
          borderBottom: "1px solid #e9edf4",
          bgcolor: "#f7f9fc",
          position: "sticky",
          top: 0,
        }}
      >
        <Typography variant="caption" sx={{ color: "#2e7d32", fontWeight: 700 }}>
          +{added}
        </Typography>
        <Typography variant="caption" sx={{ color: "#c62828", fontWeight: 700 }}>
          −{removed}
        </Typography>
      </Box>

      {lines.map((line, k) => {
        const bg =
          line.kind === "add" ? "#e9f7ee" : line.kind === "del" ? "#fdecec" : "transparent";
        const mark = line.kind === "add" ? "+" : line.kind === "del" ? "−" : " ";
        const tone = line.kind === "add" ? "#2e7d32" : line.kind === "del" ? "#c62828" : "#9aa7b8";
        return (
          <Box
            key={k}
            sx={{
              display: "grid",
              gridTemplateColumns: "3rem 1.25rem 1fr",
              columnGap: 1,
              bgcolor: bg,
              px: 1,
            }}
          >
            <Box component="span" sx={{ textAlign: "right", color: "#9aa7b8", userSelect: "none" }}>
              {line.left ?? line.right ?? ""}
            </Box>
            <Box component="span" sx={{ color: tone, userSelect: "none" }}>
              {mark}
            </Box>
            <Box component="span" sx={{ whiteSpace: "pre", pr: 2 }}>
              {line.text === "" ? " " : line.text}
            </Box>
          </Box>
        );
      })}
    </Box>
  );
};
