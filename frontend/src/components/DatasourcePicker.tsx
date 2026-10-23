import { useEffect, useMemo, useState } from "react";
import {
  Alert, Autocomplete, Box, Chip, CircularProgress, Stack, TextField,
  Typography,
} from "@mui/material";
import { api, type DatasourceRow, type SourceBinding } from "../dataProvider";

export const DatasourcePicker = ({
  entity,
  source,
  onChange,
  disabled,
}: {
  entity: string;
  source: string;
  onChange: (nextSource: string) => void;
  disabled?: boolean;
}) => {
  const [rows, setRows] = useState<DatasourceRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [binding, setBinding] = useState<SourceBinding | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .datasourceOverview(entity)
      .then((ov) => live && setRows(ov.registered))
      .catch((e: Error) => live && setLoadError(e.message));
    return () => { live = false; };
  }, [entity]);

  // The server owns the answer. It has the AST, and it is what decides whether
  // a rule may be activated - so a second answer here could disagree with it.
  useEffect(() => {
    let live = true;
    api
      .sourceBinding(source)
      .then((b) => live && setBinding(b))
      .catch(() => live && setBinding(null));
    return () => { live = false; };
  }, [source]);

  // What the admin has picked: the declaration if readable, otherwise whatever
  // the code actually calls - so a rule that never declared anything still shows
  // its real dependencies rather than appearing unbound.
  const selected = binding
    ? binding.unreadable
      ? binding.referenced
      : binding.declared
    : [];

  const options = useMemo(() => rows ?? [], [rows]);

  const unknown = selected.filter(
    (name) => rows !== null && !rows.some((r) => r.name === name),
  );

  if (loadError) {
    return (
      <Alert severity="error" sx={{ mb: 2 }}>
        Could not load datasources — {loadError}
      </Alert>
    );
  }

  if (rows === null || binding === null) {
    return (
      <Stack direction="row" spacing={1} sx={{ mb: 2, alignItems: "center" }}>
        <CircularProgress size={18} />
        <Typography variant="body2" sx={{ color: "text.secondary" }}>Loading datasources…</Typography>
      </Stack>
    );
  }

  const apply = async (names: string[]) => {
    if (!binding || binding.unreadable) return; // never rewrite a file we cannot read
    setSaving(true);
    try {
      const next = await api.sourceBinding(source, names);
      setBinding(next);
      onChange(next.source_code);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box sx={{ mb: 2 }}>
      <Stack direction="row" spacing={1} sx={{ mb: 0.5, alignItems: "baseline" }}>
        <Typography variant="subtitle2">Datasources</Typography>
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          writes <code>DATASOURCES</code> in the source below
        </Typography>
      </Stack>

      {binding.unreadable ? (
        <Alert severity="warning" sx={{ mb: 1.5 }}>
          This rule has no readable <code>DATASOURCES</code> declaration, so the
          picker cannot bind it. It reads{" "}
          {binding.referenced.length ? (
            <strong>{binding.referenced.join(", ")}</strong>
          ) : (
            "nothing yet"
          )}
          . Ask the assistant to rewrite the rule, and it will declare the tables
          it reads.
        </Alert>
      ) : (
        <Autocomplete
          multiple
          disabled={disabled}
          options={options}
          value={options.filter((o) => selected.includes(o.name))}
          onChange={(_, next) => apply(next.map((o) => o.name))}
          getOptionLabel={(o) => o.name}
          isOptionEqualToValue={(a, b) => a.name === b.name}
          renderValue={(value, getItemProps) =>
            value.map((option, i) => (
              <Chip
                {...getItemProps({ index: i })}
                key={option.name}
                label={option.name}
                size="small"
                variant="outlined"
                sx={{ fontFamily: "monospace" }}
              />
            ))
          }
          renderOption={(props, option) => (
            <li {...props} key={option.name}>
              <Box>
                <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 600 }}>
                  {option.name}
                </Typography>
                <Typography variant="caption" sx={{ color: "text.secondary", display: "block" }}>
                  {option.grain || option.description}
                </Typography>
              </Box>
            </li>
          )}
          renderInput={(params) => (
            <TextField {...params} size="small" placeholder="Select the tables this rule reads" />
          )}
        />
      )}

      {/* Facts the declaration alone would not tell you. */}
      <Stack spacing={0.75} sx={{ mt: 1.25 }}>
        {unknown.length > 0 && (
          <Alert severity="error">
            <strong>{unknown.join(", ")}</strong>{" "}
            {unknown.length === 1 ? "is" : "are"} declared but not registered for{" "}
            {entity}. Validation will refuse this rule, and the rule would fail on
            its next run — register the table on the Data Sources screen, or
            remove it here.
          </Alert>
        )}

        {binding.undeclared.length > 0 && (
          <Alert severity="warning">
            <code>ctx.table("{binding.undeclared[0]}")</code>{" "}
            {binding.undeclared.length === 1 ? "is" : "are"} read but not declared.
            Add {binding.undeclared.length === 1 ? "it" : "them"} above so
            validation covers {binding.undeclared.length === 1 ? "it" : "them"}.
          </Alert>
        )}

        {binding.unused.length > 0 && !binding.unreadable && (
          <Alert severity="info">
            Declared but never read: <strong>{binding.unused.join(", ")}</strong>.
            Harmless, but usually left over from an earlier version.
          </Alert>
        )}
        {!unknown.length &&
          !binding.undeclared.length &&
          !binding.unused.length &&
          !binding.unreadable && (
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              Declaration and code agree — every declared table is read, and every
              table read is declared.
            </Typography>
          )}
      </Stack>

      {/* Show the literal line, so what the file will contain is never a surprise. */}
      {!binding.unreadable && (
        <Typography
          variant="caption"
          sx={{ display: "block", mt: 1.5, fontFamily: "monospace", color: "text.secondary" }}
        >
          {binding.declaration}
        </Typography>
      )}
    </Box>
  );
};