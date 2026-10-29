import { useEffect, useState } from "react";
import { useNotify, useRecordContext, useRefresh } from "react-admin";
import { useNavigate } from "react-router";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, Stack, Typography,
} from "@mui/material";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import EditIcon from "@mui/icons-material/Edit";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import HistoryIcon from "@mui/icons-material/History";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import PublishIcon from "@mui/icons-material/Publish";
import { api } from "../../dataProvider";
import { RuleExplainer } from "../../components/RuleExplainer";
import { EntityField, SeverityChipField, StatusChipField } from "../../components/ui";
import { can, usePermissions } from "../../permissions";
import { ruleEditPath, ruleShowPath } from "../../rulePaths";
import { DeleteRuleDialog, DiscardDraftDialog, RunRuleDialog } from "./RuleDialogs";
import { RuleActivity, RuleDatasources, TechnicalDetails } from "./RulePanels";
import { SchedulesSection } from "./Schedules";

/* ================================================================== */
/* Rule detail                                                         */
/* ================================================================== */
export const RuleDetails = ({ asked }: { asked?: number }) => {
  const record = useRecordContext<any>();
  const navigate = useNavigate();
  const [source, setSource] = useState("");
  const [versions, setVersions] = useState<any[]>([]);
  const [report, setReport] = useState<any>(null);
  const [validating, setValidating] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const notify = useNotify();
  const refresh = useRefresh();

  // Four separate questions, because they are four separate permissions.
  // Running a control is a read of the data; validating one writes a verdict
  // to its row; changing its lifecycle is a change to the estate; and only
  // the first of them is the auditor's.
  const perms = usePermissions();
  const mayTest = can(perms, "rules.test");
  const mayWrite = can(perms, "rules.write");
  const mayActivate = can(perms, "rules.activate");
  const mayWriteSchedules = can(perms, "schedules.write");
  const mayDelete = can(perms, "rules.delete");

  // react-admin does not export a title hook, so set the document title directly.
  useEffect(() => {
    if (record?.code) document.title = `${record.code} · SquareOne Holdings`;
  }, [record?.code]);

  const load = () => {
    if (!record?.id) return;
    Promise.all([
      api.ruleSource(record.code, record.version),
      api.ruleVersions(record.code),
      api.validationReport(record.code, record.version),
    ])
      .then(([s, v, r]) => {
        setSource(s.source_code ?? "");
        setVersions(v ?? []);
        setReport(r);
      })
      .catch(() => {});
  };

  useEffect(load, [record?.code, record?.version, record?.validation_status]);

  // `/rules/X/show` is a doorway, not a page: it lands on the head and replaces
  // the URL with the version it landed on, so the address bar always names the
  // row on screen. Links written before versions were in the URL - a ticket, a
  // run, a bookmark - therefore still work and end up canonical.
  useEffect(() => {
    if (asked == null && record?.code && record?.version) {
      navigate(ruleShowPath(record.code, record.version), { replace: true });
    }
  }, [asked, record?.code, record?.version, navigate]);

  const runValidation = async () => {
    setValidating(true);
    try {
      const r = await api.validateRule(record.code, record.version);
      setReport(r);
      notify(r.status === "valid" ? "Validation passed" : `${r.errors.length} error(s)`,
        { type: r.status === "valid" ? "success" : "error" });
      refresh();
    } catch (e: any) {
      notify(`Validation failed: ${e.message}`, { type: "error" });
    } finally {
      setValidating(false);
    }
  };

  const setStatus = async (status: string) => {
    try {
      await api.setRuleStatus(record.code, status);
      notify(`Rule is now ${status}`, { type: "success" });
      refresh();
    } catch (e: any) {
      notify(e.message, { type: "error" });
    }
  };

  const restore = async () => {
    setRestoring(true);
    try {
      const result = await api.rollbackRule(record.code, record.version);
      const next = result.rule;
      notify(
        result.activated
          ? `v${next.version} created from v${record.version} and activated`
          : `v${next.version} created from v${record.version}, but it did not validate - nothing was activated`,
        { type: result.activated ? "success" : "warning" },
      );
      setRestoreOpen(false);
      navigate(ruleShowPath(next.code, next.version));
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setRestoring(false);
    }
  };

  const publishDraft = async () => {
    setPublishing(true);
    try {
      const outcome = await api.publishRule(record.code, record.version);
      if (!outcome.activated) {
        notify(
          `v${record.version} did not pass validation, so nothing went live: ` +
            `${outcome.validation?.errors?.join("; ") ?? "see the errors below"}`,
          { type: "warning" },
        );
        if (outcome.validation) setReport(outcome.validation);
        return;
      }
      notify(`v${record.version} is now live`, { type: "success" });
      refresh();
    } catch (e: any) {
      notify(e.message, { type: "error" });
    } finally {
      setPublishing(false);
    }
  };

  if (!record) return null;

  // The head is the version the scheduler runs. `status === "draft"` identifies
  // the working draft - the version an edit rewrites. There is normally exactly
  // one, whether it is the head (a rule never published, where the draft is all
  // there is) or a candidate beside the live version; taking the NEWEST draft
  // also resolves a rule an older build left with several.
  const headVersion =
    versions.find((v) => v.is_version_active)?.version ?? record.version;
  const liveVersion = versions.find((v) => v.is_version_active)?.version ?? null;
  const draftVersions = versions.filter((v) => v.status === "draft");
  const workingDraftVersion = draftVersions.length
    ? Math.max(...draftVersions.map((v) => v.version))
    : null;
  const isDraft = record.status === "draft" && record.version === workingDraftVersion;
  // A version that has ever been published carries active/paused/retired. Any
  // such row means something is on the air for a draft to sit beside.
  const hasPublished = versions.some((v) => v.status !== "draft");
  const isLive = record.is_version_active && !isDraft;
  // Everything else - a superseded version, or a stale draft head an older build
  // left behind - is a read-only record.
  const isRecord = !isDraft && !isLive;
  const draftHasLive = hasPublished;

  const errors = report?.errors ?? [];
  const warnings = report?.warnings ?? [];

  // One sentence, in the words a person uses. It replaces four chips -
  // `VALIDATED`, `v1`, `manual` and `anchor day` - which said the same things
  // in a vocabulary only the rule's author shared.
  const health = [
    record.status === "active"
      ? "Active"
      : record.status === "paused"
        ? "Paused"
        : record.status === "retired"
          ? "Retired"
          : "In draft",
    record.validation_status === "valid"
      ? "validated"
      : record.validation_status === "invalid"
        ? "failed validation"
        : "not yet validated",
    record.lookback_days === 1
      ? "reading the day's own data"
      : `reading the last ${record.lookback_days} days of data`,
  ].join(", ");

  return (
    // `minWidth: 0` is load-bearing. The tabs below embed react-admin <List>s,
    // whose tables refuse to be narrower than their columns; without this the
    // flex item's automatic minimum width lets that table set the width of the
    // whole page, and at 1366 the rule page scrolled sideways by 225px.
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      {/* Say which of the three this page is, before the title, so it is read
          before somebody presses something. */}
      {isRecord && (
        <Alert
          severity="info"
          action={
            headVersion !== record.version && (
              <Button
                color="inherit"
                size="small"
                onClick={() => navigate(ruleShowPath(record.code, headVersion))}
              >
                Open v{headVersion}
              </Button>
            )
          }
        >
          You are reading <strong>v{record.version}</strong>. v{headVersion} is the
          current version, and the scheduler runs that one. This page is a record
          of what v{record.version} did, and cannot be edited.
        </Alert>
      )}

      {isDraft && (
        <Alert
          severity="warning"
          action={
            draftHasLive && liveVersion !== null && (
              <Button
                color="inherit"
                size="small"
                onClick={() => navigate(ruleShowPath(record.code, liveVersion))}
              >
                Open v{liveVersion}
              </Button>
            )
          }
        >
          <strong>Draft v{record.version}.</strong>{" "}
          {draftHasLive
            ? `This is not the live control - v${liveVersion} keeps running until this is published.`
            : "Nothing is published yet, so this draft is the whole rule."}{" "}
          Editing rewrites this version; the number only moves when it goes live.
        </Alert>
      )}

      {/* The live version, when a replacement is waiting. The draft is the
          reason somebody is on this page, so it is said here rather than only
          on the version history lower down. */}
      {isLive && record.pending_version && (
        <Alert
          severity="info"
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => navigate(ruleShowPath(record.code, record.pending_version))}
            >
              Open the draft
            </Button>
          }
        >
          Draft <strong>v{record.pending_version}</strong> is waiting to be
          published. This version keeps running until it is.
        </Alert>
      )}

      {/* Overview */}
      <Card>
        <CardContent>
          <Stack
            direction="row"
            sx={{ alignItems: "flex-start", justifyContent: "space-between", gap: 2, flexWrap: "wrap" }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", gap: 1, mb: 0.75 }}>
                <Typography sx={{ fontSize: "1.375rem", fontWeight: 700, lineHeight: 1.2 }}>
                  {record.name || record.code}
                </Typography>
                <Chip size="small" variant="outlined" label={record.code} />
              </Stack>
              <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", flexWrap: "wrap", gap: 0.75 }}>
                <EntityField source="entity" />
                <SeverityChipField source="severity" record={record} />
                <StatusChipField source="status" record={record} />
                <Typography variant="body2" sx={{ color: "text.secondary" }}>
                  {health}
                </Typography>
              </Stack>
            </Box>

            {isRecord ? (
              mayActivate && (
                <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", gap: 1 }}>
                  <Button
                    size="small"
                    variant="contained"
                    startIcon={<HistoryIcon />}
                    onClick={() => setRestoreOpen(true)}
                  >
                    Restore v{record.version} as the current version
                  </Button>
                </Stack>
              )
            ) : isDraft ? (
              (mayTest || mayWrite || mayActivate) && (
                <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", gap: 1 }}>
                  {mayWrite && (
                    <Button size="small" startIcon={<EditIcon />}
                      onClick={() => navigate(ruleEditPath(record.code))}>
                      Edit
                    </Button>
                  )}
                  {mayActivate && (
                    <Button
                      size="small"
                      variant="contained"
                      startIcon={publishing ? <CircularProgress size={14} color="inherit" /> : <PublishIcon />}
                      onClick={publishDraft}
                      disabled={publishing}
                    >
                      {publishing ? "Publishing…" : `Publish v${record.version}`}
                    </Button>
                  )}
                  {mayWrite && (
                    <Button size="small" startIcon={<FactCheckIcon />} onClick={runValidation}
                      disabled={validating}>
                      {validating ? "Validating…" : "Validate"}
                    </Button>
                  )}
                  {mayTest && (
                    <Button
                      size="small"
                      startIcon={<PlayArrowIcon />}
                      onClick={() => setRunOpen(true)}
                    >
                      Run now
                    </Button>
                  )}
                  {mayWrite && (
                    <Button size="small" color="error" startIcon={<DeleteOutlinedIcon />}
                      onClick={() => setDiscardOpen(true)}>
                      Discard draft
                    </Button>
                  )}
                  {mayDelete && (
                    <Button size="small" color="error" startIcon={<DeleteOutlinedIcon />}
                      onClick={() => setDeleteOpen(true)}>
                      Delete rule
                    </Button>
                  )}
                </Stack>
              )
            ) : (
              (mayTest || mayWrite || mayActivate) && (
                <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", gap: 1 }}>
                  {mayWrite && (
                    <Button size="small" startIcon={<FactCheckIcon />} onClick={runValidation}
                      disabled={validating}>
                      {validating ? "Validating…" : "Validate"}
                    </Button>
                  )}
                  {mayTest && (
                    <Button
                      size="small"
                      startIcon={<PlayArrowIcon />}
                      onClick={() => setRunOpen(true)}
                    >
                      Run now
                    </Button>
                  )}
                  {mayActivate &&
                    (record.status === "active" ? (
                      <Button size="small" color="warning" onClick={() => setStatus("paused")}>
                        Pause rule
                      </Button>
                    ) : (
                      <Button size="small" variant="contained" disabled={record.validation_status !== "valid"}
                        onClick={() => setStatus("active")}>
                        Activate
                      </Button>
                    ))}
                  {mayActivate && record.status !== "retired" && (
                    <Button size="small" startIcon={<HistoryIcon />}
                      onClick={() => setStatus("retired")}>
                      Retire
                    </Button>
                  )}
                  {mayDelete && (
                    <Button size="small" color="error" startIcon={<DeleteOutlinedIcon />}
                      onClick={() => setDeleteOpen(true)}>
                      Delete rule
                    </Button>
                  )}
                </Stack>
              )
            )}
          </Stack>

          {!isRecord && mayActivate && record.validation_status !== "valid" && record.status !== "active" && (
            <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 1 }}>
              Activation requires a passing validation.
            </Typography>
          )}

          {record.description && (
            <Typography sx={{ mt: 1.5, color: "text.secondary", fontSize: "0.9375rem", lineHeight: 1.5 }}>
              {record.description}
            </Typography>
          )}

          <RuleDatasources entity={record.entity} names={record.declared_datasources ?? []} />

          {/* Everything a maintainer needs and nobody else does. Off the page
              until asked for: a file name, a version and an author are not what
              a head of audit opened this screen to read. */}
          <TechnicalDetails record={record} />
        </CardContent>
      </Card>

      {/* Validation report */}
      {(errors.length > 0 || warnings.length > 0) && (
        <Alert severity={errors.length ? "error" : "warning"}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
            {errors.length ? "Validation errors" : "Validation warnings"}
          </Typography>
          {errors.map((e: string, i: number) => (
            <Typography key={`${i}-${e}`} variant="body2">• {e}</Typography>
          ))}
          {warnings.map((w: string, i: number) => (
            <Typography key={`${i}-${w}`} variant="body2">• {w}</Typography>
          ))}
        </Alert>
      )}

      {/* What it does, and the code it does it in */}
      <RuleExplainer
        ruleCode={record.code}
        version={record.version}
        source={source}
        mayWrite={mayWrite}
        mayReview={can(perms, "rules.explain_review")}
        readOnly={isRecord}
        onChanged={refresh}
      />

      {/* Schedules belong to the rule, not to one version, but a page about an
          old version is not the place to change when the control runs. */}
      <SchedulesSection ruleCode={record.code} canWrite={mayWriteSchedules && !isRecord} />

      {/* Version history */}
      {versions.length > 0 && (
        <Card>
          <CardContent>
            <Typography variant="h6" sx={{ mb: 1 }}>Version history</Typography>
            <Stack spacing={1}>
              {versions.map((v) => (
                // The version, not the row id: RuleVersionOut carries no id,
                // so `v.id` was undefined - which React reads as *no key at
                // all*, and warns about once per version. The warning is
                // harmless and the list still renders, which is why it went
                // unnoticed: a console error that changes nothing is easy to
                // mistake for noise from someone else's library.
                <Box
                  key={v.version}
                  role="link"
                  tabIndex={0}
                  onClick={() => navigate(ruleShowPath(record.code, v.version))}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      navigate(ruleShowPath(record.code, v.version));
                    }
                  }}
                  sx={{
                    p: 1.25, borderRadius: 1.5, border: "1px solid #e9edf4",
                    cursor: "pointer",
                    bgcolor: v.version === record.version ? "#eef6ff" : "#fafbfe",
                    "&:hover": { borderColor: "#c3d4ee" },
                  }}
                >
                  <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap" }}>
                    <Chip size="small" label={`v${v.version}`}
                      color={v.version === record.version ? "primary" : "default"} />
                    {v.is_version_active && (
                      <Chip size="small" variant="outlined" color="success" label="current" />
                    )}
                    {v.status === "draft" && (
                      <Chip size="small" variant="outlined" color="warning" label="draft" />
                    )}
                    <Typography variant="body2">{v.change_note || "(no note)"}</Typography>
                  </Stack>
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {v.created_by} · {new Date(v.created_at).toLocaleString()}
                  </Typography>
                </Box>
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* Tabs: runs and alerts raised by this rule */}
      <RuleActivity ruleCode={record.code} />

      <RunRuleDialog rule={runOpen ? record : null} onClose={() => setRunOpen(false)} onDone={refresh} />

      <DiscardDraftDialog
        rule={discardOpen ? record : null}
        onClose={() => setDiscardOpen(false)}
        onDone={refresh}
      />

      {/* A rule that has been deleted has no page to refresh into, so the
          delete lands the reader back on the list rather than on a record
          that no longer exists. */}
      <DeleteRuleDialog
        rule={deleteOpen ? record : null}
        onClose={() => setDeleteOpen(false)}
        onDone={() => navigate("/rules")}
      />

      <Dialog open={restoreOpen} onClose={() => setRestoreOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Restore v{record.version} as the current version?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 1.5 }}>
            This copies v{record.version}'s code forward into a new version and
            puts it live. History is not rewritten: the version number only
            moves forward, and v{record.version} keeps its place in it.
          </Typography>
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            The copy is validated first. If the estate has moved since
            v{record.version} ran - a table it read may have been removed - it
            stays a draft and nothing is activated.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRestoreOpen(false)} disabled={restoring}>Cancel</Button>
          <Button variant="contained" onClick={restore} disabled={restoring}>
            {restoring ? "Restoring…" : `Create v${headVersion + 1} and activate it`}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};
