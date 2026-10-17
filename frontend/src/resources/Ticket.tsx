import { useEffect, useState } from "react";
import {
  DateField, Datagrid, List, Pagination, ShowBase, TextField, TopToolbar,
  useListContext, useNotify, useRecordContext, useRefresh,
} from "react-admin";
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, MenuItem, Stack,
  TextField as MuiTextField, Typography,
} from "@mui/material";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import ReplayIcon from "@mui/icons-material/Replay";
import { api } from "../dataProvider";
import {
  RowCountField, RowsTable, SeverityField, StatusField,
} from "../components/ui";

/* ------------------------------------------------------------------ */
/* Acknowledgement - the auditor's primary action                      */
/* ------------------------------------------------------------------ */
/**
 * `record` is passed explicitly where the context is not reliable (the show
 * view); in a Datagrid row it falls back to the row context.
 */
const AcknowledgeButton = ({ record: explicit }: { record?: any }) => {
  const contextRecord = useRecordContext<any>();
  const record = explicit ?? contextRecord;
  const notify = useNotify();
  const refresh = useRefresh();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);

  if (!record || record.status !== "open") return null;

  const submit = async (status: "acknowledged" | "dismissed") => {
    setPending(true);
    try {
      await api.acknowledgeTicket(record.id, status, note);
      notify(`${record.ticket_no} ${status}`, { type: "success" });
      setOpen(false);
      setNote("");
      refresh();
    } catch (e: any) {
      notify(`Failed: ${e.message}`, { type: "error" });
    } finally {
      setPending(false);
    }
  };

  return (
    <>
      <Button
        size="small"
        variant="contained"
        startIcon={<CheckCircleOutlineIcon />}
        onClick={(e) => { e.stopPropagation(); setOpen(true); }}
      >
        Acknowledge
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>
          Acknowledge {record.ticket_no}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {record.summary}
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <MuiTextField
              label="Auditor note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              multiline
              minRows={3}
              fullWidth
              placeholder="What was the outcome of your review?"
              helperText="Recorded against your username with a timestamp - this is the audit trail."
            />
            <Alert severity="info">
              Acknowledging marks this ticket reviewed. It does not close the underlying
              control - the next scheduled run will still raise a ticket if the breach persists.
            </Alert>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)} disabled={pending}>Cancel</Button>
          <Button onClick={() => submit("dismissed")} disabled={pending} color="inherit">
            Dismiss
          </Button>
          <Button
            onClick={() => submit("acknowledged")}
            disabled={pending}
            variant="contained"
            startIcon={pending ? <CircularProgress size={16} color="inherit" /> : undefined}
          >
            Acknowledge
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

const ReopenButton = ({ record: explicit }: { record?: any }) => {
  const contextRecord = useRecordContext<any>();
  const record = explicit ?? contextRecord;
  const notify = useNotify();
  const refresh = useRefresh();
  if (!record || record.status === "open") return null;
  return (
    <Button
      size="small"
      startIcon={<ReplayIcon />}
      onClick={async (e) => {
        e.stopPropagation();
        try {
          await api.reopenTicket(record.id);
          notify("Ticket reopened", { type: "info" });
          refresh();
        } catch (err: any) {
          notify(`Failed: ${err.message}`, { type: "error" });
        }
      }}
    >
      Reopen
    </Button>
  );
};

/** Datagrid action column: ack / reopen without leaving the inbox. */
const TicketActionsField = () => (
  <Box sx={{ display: "flex", gap: 0.5, justifyContent: "flex-end" }}>
    <AcknowledgeButton />
    <ReopenButton />
  </Box>
);

/* ------------------------------------------------------------------ */
/* List - the alert inbox                                              */
/* ------------------------------------------------------------------ */
/**
 * Search and status filter are driven explicitly against the list context
 * rather than through react-admin's filter machinery, which needs its
 * SearchInput/SelectInput components registered via <Filter> and does not
 * render them inline reliably in this setup.
 */
const TicketToolbar = () => {
  const { setFilters, filterValues } = useListContext<any>();
  const [q, setQ] = useState<string>(filterValues?.q ?? "");
  const [status, setStatus] = useState<string>(filterValues?.status ?? "");

  useEffect(() => {
    const handle = setTimeout(() => {
      setFilters({ q: q || undefined, status: status || undefined }, "ignored");
    }, 300);
    return () => clearTimeout(handle);
  }, [q, status]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <TopToolbar>
      <MuiTextField
        size="small"
        placeholder="Search ticket no, summary…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        sx={{ minWidth: 300 }}
      />
      <MuiTextField
        select
        size="small"
        value={status}
        onChange={(e) => setStatus(e.target.value as string)}
        sx={{ minWidth: 170 }}
      >
        <MenuItem value="">All statuses</MenuItem>
        <MenuItem value="open">Open</MenuItem>
        <MenuItem value="acknowledged">Acknowledged</MenuItem>
        <MenuItem value="dismissed">Dismissed</MenuItem>
      </MuiTextField>
    </TopToolbar>
  );
};

export const TicketList = () => (
  <List
    actions={<TicketToolbar />}
    pagination={<Pagination rowsPerPageOptions={[10, 25, 50, 100]} />}
    sort={{ field: "created_at", order: "DESC" }}
    perPage={25}
  >
    <Datagrid rowClick="show" bulkActionButtons={false}>
      <TextField source="ticket_no" label="Ticket" sortable={false} />
      <SeverityField source="severity" />
      <StatusField source="status" />
      <TextField source="summary" label="Finding" />
      <RowCountField source="rows" />
      <TextField source="acknowledged_by" label="Acknowledged by" sortable={false} />
      <DateField source="created_at" label="Raised" showTime />
      <TicketActionsField />
    </Datagrid>
  </List>
);

/* ------------------------------------------------------------------ */
/* Show                                                                */
/* ------------------------------------------------------------------ */
const TicketDetails = () => {
  const record = useRecordContext<any>();

  useEffect(() => {
    if (record?.ticket_no) document.title = `${record.ticket_no} · ABM Audit Portal`;
  }, [record?.ticket_no]);

  if (!record) return null;

  return (
    <Box>
      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <AcknowledgeButton record={record} />
        <ReopenButton record={record} />
      </Stack>
      <Card>
        <CardContent>
          <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: "wrap", gap: 1 }}>
            <SeverityField source="severity" />
            <StatusField source="status" />
            <Chip size="small" variant="outlined" label={`rule #${record.rule_id}`} />
            <Chip size="small" variant="outlined" label={`run #${record.run_id}`} />
          </Stack>

          <Typography variant="body1" sx={{ fontWeight: 600, mb: 1 }}>
            {record.summary}
          </Typography>

          <Typography variant="caption" color="text.secondary">
            Raised {new Date(record.created_at).toLocaleString()}
            {record.schedule_id ? ` by schedule #${record.schedule_id}` : " manually"}
          </Typography>

          {record.acknowledged_by && (
            <Box
              sx={{
                mt: 2, p: 1.5, bgcolor: "#f7f9fc",
                borderLeft: "3px solid #123a63", borderRadius: 1,
              }}
            >
              <Typography variant="caption" sx={{ fontWeight: 700 }}>
                ACKNOWLEDGED BY {String(record.acknowledged_by).toUpperCase()}{" "}
                {new Date(record.acknowledged_at).toLocaleString()}
              </Typography>
              {record.ack_note && (
                <Typography variant="body2" sx={{ mt: 0.5 }}>{record.ack_note}</Typography>
              )}
            </Box>
          )}
        </CardContent>
      </Card>

      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1.5 }}>
            Exception rows ({record.rows?.length ?? 0})
          </Typography>
          <RowsTable rows={record.rows ?? []} />
        </CardContent>
      </Card>
    </Box>
  );
};

export const TicketShow = () => (
  <ShowBase>
    <TicketDetails />
  </ShowBase>
);