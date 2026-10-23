import { createContext, useContext, useState } from "react";
import { Datagrid, List, Pagination, useRecordContext, useRefresh } from "react-admin";
import { useNavigate } from "react-router";
import { Box, Chip, Stack, Tooltip, Typography } from "@mui/material";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import EditIcon from "@mui/icons-material/Edit";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import { ActionIcon, DotLabel, fieldFromRecord } from "../../components/ui";
import { TextLike, listTableSx } from "../../components/listTable";
import { FONT, severityColor, statusColor } from "../../theme";
import { can, usePermissions } from "../../permissions";
import { useEntityScope } from "../../entityScope";
import { ruleEditPath, ruleShowPath } from "../../rulePaths";
import { DeleteRuleDialog, RunRuleDialog } from "./RuleDialogs";
import { RuleToolbar } from "./RuleToolbar";
import { ScheduleSummary, useScheduleIndex } from "./Schedules";

/* ================================================================== */
/* The rules table - one row per rule; its versions are on its page     */
/* ================================================================== */
const RULE_WIDTHS: Array<[number, number]> = [
  [1, 150], // the rule's code
  [3, 110], // severity
  [4, 110], // state
  [5, 130], // validation
  [6, 170], // schedule
  [7, 110], // actions
];

const RuleRowContext = createContext<{
  schedules: Record<string, any[]>;
  run: (rule: any) => void;
  remove: (rule: any) => void;
}>({ schedules: {}, run: () => {}, remove: () => {} });

const SeverityCell = fieldFromRecord(
  (value) => <DotLabel value={value} tone={severityColor[value] ?? "default"} />,
);

const StateCell = ({ source, label }: { source?: string; label?: string }) => {
  void source;
  void label;
  const record = useRecordContext<any>();
  return (
    <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
      <DotLabel value={record?.status} tone={statusColor[record?.status] ?? "default"} />
      {record?.pending_version ? (
        <Chip
          size="small"
          variant="outlined"
          color="warning"
          label={`draft v${record.pending_version}`}
          sx={{ height: 20, fontSize: FONT.label }}
        />
      ) : null}
    </Stack>
  );
};

const validationLabel = (v?: string) =>
  v === "valid" ? "validated" : v === "invalid" ? "validation failed" : "not validated";

const validationTone = (v?: string) =>
  v === "valid" ? "success" : v === "invalid" ? "error" : "default";

const ValidationCell = fieldFromRecord((value) => (
  <DotLabel value={validationLabel(value)} tone={validationTone(value)} />
));

const ScheduleCell = ({ label }: { label?: string }) => {
  void label;
  const record = useRecordContext<any>();
  const { schedules } = useContext(RuleRowContext);
  return <ScheduleSummary schedules={schedules[record?.code] ?? []} />;
};

const RuleActionsCell = ({ label }: { label?: string }) => {
  void label;
  const record = useRecordContext<any>();
  const perms = usePermissions();
  const { run, remove } = useContext(RuleRowContext);
  const navigate = useNavigate();
  const mayWrite = can(perms, "rules.write");
  const mayDelete = can(perms, "rules.delete");

  return (
    <Stack
      direction="row"
      spacing={0}
      sx={{ justifyContent: "flex-end" }}
      onClick={(e) => e.stopPropagation()}
    >
      <ActionIcon
        title="Run this rule now"
        color="primary"
        icon={<PlayArrowIcon fontSize="small" />}
        onClick={() => run(record)}
      />
      <ActionIcon
        title={mayWrite ? "Edit rule" : "Editing needs the rules.write capability"}
        icon={<EditIcon fontSize="small" />}
        disabled={!mayWrite}
        onClick={() => navigate(ruleEditPath(record.code))}
      />
      <ActionIcon
        title={mayDelete ? "Delete rule" : "Deleting needs the rules.delete capability"}
        icon={<DeleteOutlinedIcon fontSize="small" />}
        color="error"
        disabled={!mayDelete}
        onClick={() => remove(record)}
      />
    </Stack>
  );
};

const RuleTable = () => {
  const { index: schedules } = useScheduleIndex();
  const [runFor, setRunFor] = useState<any>(null);
  const [deleteFor, setDeleteFor] = useState<any>(null);
  const refresh = useRefresh();

  return (
    <>
      <RuleRowContext.Provider
        value={{ schedules, run: setRunFor, remove: setDeleteFor }}
      >
        <Datagrid
          // A rule page is addressed by code and version. Building the path
          // here is what keeps the list's own filter, page and sort out of it.
          rowClick={(_id, _resource, record) =>
            ruleShowPath(record.code, record.version)
          }
          bulkActionButtons={false}
          size="small"
          sx={listTableSx(RULE_WIDTHS)}
          empty={
            <Box sx={{ p: 6, textAlign: "center" }}>
              <Typography variant="body1" sx={{ color: "text.secondary" }}>
                No rules match these filters.
              </Typography>
            </Box>
          }
        >
          <TextLike source="code" label="Rule" mono />
          <TextLike source="name" label="Name" secondary />
          <SeverityCell source="severity" label="Severity" />
          <StateCell source="status" label="State" />
          <ValidationCell source="validation_status" label="Validation" />
          <ScheduleCell label="Schedule" />
          <RuleActionsCell label="Actions" />
        </Datagrid>
      </RuleRowContext.Provider>

      <RunRuleDialog rule={runFor} onClose={() => setRunFor(null)} onDone={refresh} />
      <DeleteRuleDialog
        rule={deleteFor}
        onClose={() => setDeleteFor(null)}
        onDone={refresh}
      />
    </>
  );
};

export const RuleList = () => {
  const { entity } = useEntityScope();
  return (
    <List
      actions={<RuleToolbar />}
      // The client scope is a permanent filter, so the toolbar's Clear button
      // cannot quietly drop it and leave the list showing both clients.
      filter={{ entity }}
      sort={{ field: "code", order: "ASC" }}
      pagination={<Pagination rowsPerPageOptions={[10, 25, 50, 100]} />}
    >
      <RuleTable />
    </List>
  );
};
