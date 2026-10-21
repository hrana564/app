import { Fragment, useMemo } from "react";
import {
  Box, Card, Stack, Table, TableBody, TableCell, TableHead, TableRow,
  Tooltip, Typography,
} from "@mui/material";
import CheckIcon from "@mui/icons-material/Check";
import CloseIcon from "@mui/icons-material/Close";
import { useCurrentUser } from "../permissions";
import type { RoleMatrix as RoleMatrixData } from "../dataProvider";
import { FONT } from "../theme";

/**
 * Which role holds what, read down and across.
 *
 * This is READ-ONLY and it is meant to be: the matrix is decided in
 * `backend/app/permissions.py`, and an editor here would be a second place to
 * change it - one that the server never reads. The screen's whole job is to
 * answer the question an auditor asks when a button is greyed out: "what can
 * the other role do that I cannot?".
 *
 * The data comes from `GET /api/roles`, so the table is the server's matrix
 * rather than a copy of one. A copy written into the frontend would drift the
 * first time a capability was added, and it would drift silently - the table
 * would still draw, every tick still plausible, while describing a permission
 * model nobody had.
 *
 * Grouped by subject rather than listed as one run of seventeen rows: the
 * rows are the columns' meaning, and seven short blocks read faster than one
 * long one.
 */

/** One cell. A tick, or a cross - and it says which, to a screen reader. */
const HoldMark = ({ holds }: { holds: boolean }) => {
  const text = holds ? "holds this capability" : "does not hold this capability";
  return (
    <Tooltip title={text}>
      <Box
        component="span"
        role="img"
        aria-label={text}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 24,
          height: 24,
        }}
      >
        {holds ? (
          <CheckIcon sx={{ fontSize: 18, color: "success.main" }} />
        ) : (
          <CloseIcon sx={{ fontSize: 16, color: "#c6cedb" }} />
        )}
      </Box>
    </Tooltip>
  );
};

export const RoleMatrix = ({ matrix }: { matrix: RoleMatrixData }) => {
  const user = useCurrentUser();
  const mine = user?.role ?? "";

  /** Capabilities, blocked by the subject they belong to. The response is
   *  already in the server's reading order, so the blocks come out as they
   *  arrive rather than being re-sorted here. */
  const blocks = useMemo(() => {
    const out: { label: string; rows: RoleMatrixData["capabilities"] }[] = [];
    for (const capability of matrix.capabilities) {
      const last = out[out.length - 1];
      if (last && last.label === capability.group_label) last.rows.push(capability);
      else out.push({ label: capability.group_label, rows: [capability] });
    }
    return out;
  }, [matrix]);

  return (
    <Box>
      <Stack
        direction="row"
        spacing={2}
        sx={{
          mb: 1.5, flexWrap: "wrap", gap: 1,
          alignItems: "baseline", justifyContent: "space-between",
        }}
      >
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          Read-only. Decided in{" "}
          <Box component="span" sx={{ fontFamily: "monospace" }}>
            backend/app/permissions.py
          </Box>
          , served here so this table cannot disagree with what the server does.
        </Typography>
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          {matrix.capabilities.length} capabilities &middot; {matrix.roles.length} roles
        </Typography>
      </Stack>

      {/* The scroll shell: a five-column table fits a laptop, but the
          capability column carries two lines of label and the role columns
          need room for their counts, so it scrolls sideways rather than
          wrapping its cells into ragged heights. */}
      <Card sx={{ overflow: "hidden" }}>
        <Box sx={{ overflow: "auto" }}>
          <Table size="small" sx={{ tableLayout: "fixed" }}>
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: "46%", whiteSpace: "normal" }}>
                  What may be done
                </TableCell>
                {matrix.roles.map((role) => (
                  <TableCell
                    key={role.name}
                    align="center"
                    sx={{
                      width: `${54 / matrix.roles.length}%`,
                      whiteSpace: "normal",
                      verticalAlign: "bottom",
                      // The column you are standing in, so your own row of ticks
                      // can be found without counting across from the left.
                      bgcolor: role.name === mine ? "#eef4fd" : undefined,
                    }}
                  >
                    <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                      {role.label}
                      {role.name === mine && (
                        <Typography
                          component="span"
                          variant="caption"
                          sx={{ display: "block", color: "primary.main", fontWeight: 700 }}
                        >
                          you
                        </Typography>
                      )}
                    </Typography>
                    <Typography
                      variant="caption"
                      sx={{ display: "block", color: "text.secondary", fontWeight: 400 }}
                    >
                      {role.capabilities.length} of {matrix.capabilities.length}
                    </Typography>
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {blocks.map((block) => (
                <Fragment key={block.label}>
                  <TableRow sx={{ bgcolor: "#f7f9fc" }}>
                    <TableCell colSpan={matrix.roles.length + 1} sx={{ py: 0.75 }}>
                      <Typography
                        variant="overline"
                        sx={{ color: "#5b6b82", fontWeight: 700, letterSpacing: 1 }}
                      >
                        {block.label}
                      </Typography>
                    </TableCell>
                  </TableRow>
                  {block.rows.map((capability) => (
                    <TableRow key={capability.name} hover>
                      <TableCell sx={{ verticalAlign: "middle" }}>
                        <Typography variant="body2" sx={{ lineHeight: 1.35 }}>
                          {capability.label}
                        </Typography>
                        <Typography
                          variant="caption"
                          sx={{
                            display: "block",
                            color: "text.secondary",
                            fontFamily: "monospace",
                            fontSize: FONT.label,
                            lineHeight: 1.4,
                          }}
                        >
                          {capability.name}
                        </Typography>
                      </TableCell>
                      {matrix.roles.map((role) => (
                        <TableCell
                          key={role.name}
                          align="center"
                          sx={{
                            bgcolor: role.name === mine ? "#eef4fd" : undefined,
                            "&:hover": { bgcolor: role.name === mine ? "#e6effc" : "#f7faff" },
                          }}
                        >
                          <HoldMark holds={role.capabilities.includes(capability.name)} />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </Box>
      </Card>
    </Box>
  );
};
