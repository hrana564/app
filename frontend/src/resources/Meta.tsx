import { useEffect, useState } from "react";
import { Datagrid, List, useNotify, useRefresh } from "react-admin";
import {
  Box, Card, CardContent, Chip, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, Typography,
} from "@mui/material";
import StorageIcon from "@mui/icons-material/Storage";
import TuneIcon from "@mui/icons-material/Tune";
import { api } from "../dataProvider";
import { EntityChip } from "../components/ui";

/* ================= Parameters ================= */
export const ParamList = () => {
  const [rows, setRows] = useState<any[] | null>(null);
  const notify = useNotify();
  const refresh = useRefresh();

  const load = () => {
    fetch("/api/params", {
      headers: { Authorization: `Bearer ${localStorage.getItem("abm.token")}` },
    })
      .then((r) => r.json())
      .then(setRows)
      .catch(() => notify("Could not load parameters", { type: "error" }));
  };

  useEffect(load, []);

  const update = async (key: string, value: number) => {
    try {
      await fetch("/api/params", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${localStorage.getItem("abm.token")}`,
        },
        body: JSON.stringify({ key, value_numeric: value }),
      });
      notify(`${key} saved`, { type: "success" });
      refresh();
      load();
    } catch {
      notify("Could not save parameter", { type: "error" });
    }
  };

  if (!rows) return <Typography>Loading…</Typography>;

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Thresholds are read by rules via <code>ctx.params</code>. Changing one here re-scopes
        every rule that uses it on its next run - no code change required.
      </Typography>
      <Card>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Key</TableCell>
              <TableCell>Label</TableCell>
              <TableCell align="right">Value</TableCell>
              <TableCell>Description</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((p) => (
              <TableRow key={p.id} hover>
                <TableCell>
                  <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 700 }}>
                    {p.key}
                  </Typography>
                </TableCell>
                <TableCell>{p.label}</TableCell>
                <TableCell align="right">
                  <Typography
                    component="span"
                    sx={{ fontFamily: "monospace", fontWeight: 700, color: "primary.main" }}
                  >
                    {p.value_numeric ?? p.value_text ?? "—"}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Typography variant="caption" color="text.secondary">{p.description}</Typography>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </Box>
  );
};

/* ================= Datasources ================= */
export const DatasourceList = () => {
  const [entities, setEntities] = useState<any[] | null>(null);
  const [sources, setSources] = useState<any[] | null>(null);

  useEffect(() => {
    const headers = { Authorization: `Bearer ${localStorage.getItem("abm.token")}` };
    fetch("/api/datasources/entities", { headers }).then((r) => r.json()).then(setEntities);
    fetch("/api/datasources", { headers }).then((r) => r.json()).then(setSources);
  }, []);

  if (!entities || !sources) return <Typography>Loading…</Typography>;

  return (
    <Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Each table below is registered once with its columns, grain and derived fields. Rules
        reference these as <code>ctx.table(&quot;name&quot;)</code> and never touch a file path.
      </Typography>

      <Stack spacing={2.5}>
        {entities.map((e) => {
          const mine = sources.filter((s) => s.entity === e.code);
          return (
            <Card key={e.code}>
              <CardContent>
                <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                  <EntityChip value={e.code} />
                  <Typography variant="h6">{e.name}</Typography>
                </Stack>
                <Stack direction="row" spacing={0.75} sx={{ mb: 2, flexWrap: "wrap", gap: 0.75 }}>
                  {e.systems.map((s: string) => (
                    <Chip key={s} size="small" icon={<StorageIcon />} label={s} variant="outlined" />
                  ))}
                </Stack>

                <Stack spacing={1.5}>
                  {mine.map((s) => (
                    <Box
                      key={s.name}
                      sx={{ p: 1.5, border: "1px solid #e9edf4", borderRadius: 2, bgcolor: "#fcfdff" }}
                    >
                      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 0.5 }}>
                        <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 700, color: "primary.main" }}>
                          {s.name}
                        </Typography>
                        <Chip size="small" label={s.grain} />
                      </Stack>
                      <Typography variant="caption" color="text.secondary" display="block">
                        {s.description}
                      </Typography>
                      <Stack direction="row" spacing={0.5} sx={{ mt: 1, flexWrap: "wrap", gap: 0.5 }}>
                        {Object.entries(s.columns ?? {}).map(([col, type]) => (
                          <Chip key={col} size="small" variant="outlined" label={`${col}: ${type}`} />
                        ))}
                        {Object.keys(s.derived ?? {}).map((col) => (
                          <Chip key={col} size="small" color="secondary" label={`${col} (derived)`} />
                        ))}
                      </Stack>
                      <Typography variant="caption" sx={{ display: "block", mt: 1, color: "#8d99ae" }}>
                        {s.path_template}
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              </CardContent>
            </Card>
          );
        })}
      </Stack>
    </Box>
  );
};