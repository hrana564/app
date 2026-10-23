export const ruleShowPath = (code: string, version?: number | null): string =>
  `/rules/${encodeURIComponent(code)}/show${version ? `/v${version}` : ""}`;

export const ruleEditPath = (code: string): string =>
  `/rules/${encodeURIComponent(code)}/edit`;

export const versionFromParam = (raw?: string): number | undefined => {
  const match = /^v?(\d+)$/.exec(raw ?? "");
  return match ? Number(match[1]) : undefined;
};
