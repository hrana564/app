import { useEffect, useRef, useState } from "react";
import { TopToolbar, useListContext } from "react-admin";
import { Box, Button, MenuItem, TextField as MuiTextField } from "@mui/material";
import { GenerateRuleDialog } from "./GenerateRuleDialog";
import { SEVERITY_OPTIONS, STATUS_OPTIONS } from "./options";

export const RuleToolbar = () => {
  const { setFilters, filterValues } = useListContext<any>();
  const [filters, setLocal] = useState({
    q: "",
    status: "",
    severity: "",
  });

  const applyRef = useRef({ setFilters, filterValues });
  applyRef.current = { setFilters, filterValues };

  useEffect(() => {
    const handle = setTimeout(() => {
      const next: Record<string, string> = {};
      (Object.keys(filters) as (keyof typeof filters)[]).forEach((k) => {
        if (filters[k]) next[k] = filters[k];
      });

      const { setFilters: apply, filterValues: current } = applyRef.current;
      const live: Record<string, unknown> = current ?? {};
      const changed =
        Object.keys(next).length !== Object.keys(live).length ||
        Object.keys(next).some((k) => live[k] !== next[k]);

      if (changed) apply(next, "ignored");
    }, 300);
    return () => clearTimeout(handle);
  }, [filters]);

  const set = (patch: Partial<typeof filters>) => setLocal((f) => ({ ...f, ...patch }));
  const active = Object.values(filters).some(Boolean);

  const FilterSelect = ({
    label, options, value,
  }: { label: string; options: readonly string[]; value: string }) => (
    <MuiTextField
      select
      size="small"
      label={label}
      value={value}
      onChange={(e) => set({ [label]: e.target.value } as Partial<typeof filters>)}
      sx={{ minWidth: 130 }}
    >
      <MenuItem value="">
        <em>All</em>
      </MenuItem>
      {options.map((o) => (
        <MenuItem key={o} value={o} sx={{ textTransform: "capitalize" }}>
          {o}
        </MenuItem>
      ))}
    </MuiTextField>
  );

  return (
    <TopToolbar>
      <MuiTextField size="small" placeholder="Search rule code or name…" value={filters.q}
        onChange={(e) => set({ q: e.target.value })} sx={{ minWidth: 220 }} />
      <FilterSelect label="status" options={STATUS_OPTIONS} value={filters.status} />
      <FilterSelect label="severity" options={SEVERITY_OPTIONS} value={filters.severity} />
      {active && (
        <Button
          size="small"
          onClick={() => setLocal({ q: "", status: "", severity: "" })}
        >
          Clear
        </Button>
      )}
      <Box sx={{ flex: 1 }} />
      <GenerateRuleDialog />
    </TopToolbar>
  );
};
