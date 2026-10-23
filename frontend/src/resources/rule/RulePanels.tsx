import { useEffect, useState } from "react";
import { Datagrid, List, Pagination, TextField } from "react-admin";
import { useNavigate } from "react-router";
import {
  Alert, Box, Button, ButtonBase, Card, CardContent, CircularProgress,
  Collapse, Divider, Stack, Typography,
} from "@mui/material";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";
import { api, type DatasourceOverview, type DatasourceRow } from "../../dataProvider";
import { IdField, RowCountField, SeverityChipField, StatusChipField } from "../../components/ui";
import { LIST_HEADER_SX } from "../../components/listTable";
import { FONT } from "../../theme";
import { RunOutcomeCell, RunStatusCell } from "../Run";

/* ================================================================== */
/* The facts a maintainer needs, a reader does not                      */
/* ================================================================== */
export const TechnicalDetails = ({ record }: { record: any }) => {
  const [open, setOpen] = useState(false);
  return (
    <Box sx={{ mt: 1.5 }}>
      <ButtonBase
        onClick={() => setOpen((o) => !o)}
        sx={{
          display: "inline-grid", gridTemplateColumns: "auto auto",
          columnGap: 0.5, alignItems: "center", px: 0.5, py: 0.25,
          borderRadius: 1, "&:hover": { bgcolor: "#f2f5fa" },
        }}
      >
        {open ? (
          <KeyboardArrowDownIcon sx={{ fontSize: 18, color: "text.secondary" }} />
        ) : (
          <KeyboardArrowRightIcon sx={{ fontSize: 18, color: "text.secondary" }} />
        )}
        <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 600 }}>
          Technical details
        </Typography>
      </ButtonBase>

      <Collapse in={open} timeout="auto" unmountOnExit>
        <Box
          sx={{
            mt: 0.75, p: 1.5, borderRadius: 1.5,
            bgcolor: "#fafbfe", border: "1px solid #e9edf4",
            display: "grid", rowGap: 0.75,
            gridTemplateColumns: { xs: "1fr", sm: "170px 1fr" },
            columnGap: 2, alignItems: "baseline",
          }}
        >
          {[
            ["Source file", record.source_file],
            ["Version", `v${record.version}${record.is_version_active ? " (current)" : ""}`],
            ["Created by", record.created_by || "—"],
            [
              "Written",
              record.origin === "llm" ? "by the rule assistant, read and saved by a person" : "by hand",
            ],
            ["Lookback", `${record.lookback_days} day(s)`],
            [
              "Last updated",
              record.updated_at ? new Date(record.updated_at).toLocaleString() : "—",
            ],
          ].map(([label, value]) => (
            <Box key={label} sx={{ display: "contents" }}>
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                {label}
              </Typography>
              <Typography variant="body2" sx={{ fontFamily: label === "Source file" ? "monospace" : undefined }}>
                {value}
              </Typography>
            </Box>
          ))}
        </Box>
      </Collapse>
    </Box>
  );
};

/* ================================================================== */
/* What this control reads                                              */
/* ================================================================== */
export const RuleDatasources = ({ entity, names }: { entity: string; names: string[] }) => {
  const [registered, setRegistered] = useState<DatasourceRow[] | null>(null);
  const [failed, setFailed] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (!names.length) return;
    let live = true;
    api
      .datasourceOverview(entity)
      .then((o: DatasourceOverview) => live && setRegistered(o.registered ?? []))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [entity, names.length]);

  if (!names.length) {
    return (
      <Box sx={{ mt: 2 }}>
        <Divider sx={{ my: 1.5 }} />
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          This rule reads no data sources.
        </Typography>
      </Box>
    );
  }

  const missing = registered
    ? names.filter((n) => !registered.some((r) => r.name === n))
    : [];

  return (
    <Box sx={{ mt: 2 }}>
      <Divider sx={{ my: 1.5 }} />
      <Stack
        direction="row"
        sx={{ mb: 1, alignItems: "baseline", justifyContent: "space-between" }}
      >
        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
          Data sources
          <Typography
            component="span"
            variant="caption"
            sx={{ ml: 1, fontWeight: 400, color: "text.secondary" }}
          >
            {names.length} table{names.length === 1 ? "" : "s"}
          </Typography>
        </Typography>
        <Button size="small" onClick={() => navigate("/datasources")}>
          Manage in Data Sources
        </Button>
      </Stack>

      {/* Three fixed columns rather than a flex row, so the systems line up and
          the eye can compare them down the page instead of re-reading each. */}
      <Box sx={{ display: "grid", rowGap: 0.5 }}>
        {names.map((name) => {
          const row = registered?.find((r) => r.name === name);
          return (
            <Box key={name}
              sx={{ display: "grid", alignItems: "baseline", columnGap: 2,
                    gridTemplateColumns: { xs: "1fr", sm: "200px 170px 1fr" } }}>
              <Typography variant="body2"
                sx={{ fontFamily: "monospace", fontWeight: 600, color: "primary.main" }}>
                {name}
              </Typography>
              <Typography variant="body2" sx={{ color: "text.secondary" }}>
                {registered === null ? "—" : row?.system || "—"}
              </Typography>
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                {registered === null
                  ? ""
                  : row?.grain || (row ? "" : "not registered")}
              </Typography>
            </Box>
          );
        })}
      </Box>

      {failed && (
        <Alert severity="warning" sx={{ mt: 1.5 }}>
          Could not load the datasource catalogue, so the systems above are
          blank. The table names are this rule&apos;s own declaration and are
          still correct.
        </Alert>
      )}

      {missing.length > 0 && (
        <Alert severity="error" sx={{ mt: 1.5 }}>
          <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
            {missing.length === 1 ? "1 table is" : `${missing.length} tables are`} no longer
            registered for {entity}
          </Typography>
          <Typography variant="body2" sx={{ mb: 1 }}>
            <strong>{missing.join(", ")}</strong> — this rule fails on its next
            run, not eventually. Remove it from the rule or register the table
            again.
          </Typography>
          <Button size="small" color="error" onClick={() => navigate("/datasources")}>
            Go to Data Sources
          </Button>
        </Alert>
      )}
    </Box>
  );
};

export const RuleActivity = ({ ruleCode }: { ruleCode: string }) => {
  const [tab, setTab] = useState<"runs" | "tickets">("runs");
  return (
    <Card>
      <CardContent>
        <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
          {(["runs", "tickets"] as const).map((t) => (
            <Button key={t} size="small" variant={tab === t ? "contained" : "outlined"}
              onClick={() => setTab(t)} sx={{ textTransform: "capitalize" }}>
              {t === "runs" ? "Runs" : "Findings raised"}
            </Button>
          ))}
        </Stack>
        {tab === "runs" ? (
          <Box sx={{ minWidth: 0, overflowX: "auto", contain: "inline-size", "& .RaDatagrid-root": { boxShadow: "none" } }}>
            {/* Pagination goes in as the `pagination` prop, not as a child.
                A <Pagination> child renders a second footer underneath the one
                the Datagrid already draws - two identical page pickers, stacked,
                which is what this tab showed. */}
            <List
              resource="runs"
              perPage={10}
              filterDefaultValues={{ rule_code: ruleCode }}
              pagination={<Pagination rowsPerPageOptions={[10, 25]} />}
              // The address is about this rule, and it names the version. A
              // list that syncs its filter, page and sort into the same address
              // overwrites that with another screen's state, so a tab inside a
              // page keeps its own.
              disableSyncWithLocation
            >
              <Datagrid bulkActionButtons={false} rowClick="show" size="small"
                sx={{
                  // The header is the list chrome, shared with every other
                  // list. The rows are not: this table deliberately does NOT
                  // use tableLayout:fixed, because pinning the columns leaves
                  // OBSERVATION a fixed box, which truncates the longest
                  // findings - and the finding is the reason anyone reads this
                  // table. Short rows leave a gap instead, which costs nothing.
                  "& .RaDatagrid-headerCell": LIST_HEADER_SX,
                  "& .RaDatagrid-rowCell": {
                    py: 0.75, px: 1.25, fontSize: FONT.body,
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                  },
                }}
              >
                <IdField source="id" label="#" />
                <TextField source="data_date" label="Data date" />
                {/* Two columns, because they answer different questions. One
                    column reading `status` showed a blue SUCCESS for a run that
                    found three breaches - a working control dressed as a clean
                    day, which is the one misreading the ledger split exists to
                    prevent. */}
                <RunStatusCell source="status" label="Rule Execution" />
                <RunOutcomeCell source="outcome" label="Outcome" />
                <TextField source="summary" label="Observation" />
                <RowCountField source="rows" />
                <TextField source="triggered_by" label="By" />
              </Datagrid>
            </List>
          </Box>
        ) : (
          <Box>
            <TicketsForRule ruleCode={ruleCode} />
          </Box>
        )}
      </CardContent>
    </Card>
  );
};

const TicketsForRule = ({ ruleCode }: { ruleCode: string }) => {
  const [tickets, setTickets] = useState<any[] | null>(null);
  useEffect(() => {
    let live = true;
    api.listTickets({ rule_code: ruleCode, perPage: "50" })
      .then((d) => live && setTickets(d.data ?? []))
      .catch(() => live && setTickets([]));
    return () => { live = false; };
  }, [ruleCode]);

  if (!tickets) return <CircularProgress size={22} />;
  if (!tickets.length) {
    return <Typography variant="body2" sx={{ color: "text.secondary" }}>This rule has not raised any findings.</Typography>;
  }
  return (
    <Stack spacing={1}>
      {tickets.map((t) => (
        <Box key={t.id} sx={{ p: 1.25, border: "1px solid #e9edf4", borderRadius: 1.5 }}>
          <Stack
            direction="row"
            spacing={1}
            sx={{ alignItems: "center", flexWrap: "wrap", gap: 1 }}
          >
            <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 700 }}>
              {t.ticket_no}
            </Typography>
            <SeverityChipField source="severity" record={t} />
            <StatusChipField source="status" record={t} />
          </Stack>
          <Typography variant="body2" sx={{ color: "text.secondary" }}>{t.summary}</Typography>
        </Box>
      ))}
    </Stack>
  );
};
