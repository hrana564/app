/**
 * The only place a rule's address is built.
 *
 * A rule is a chain of version rows, so a rule page is a page about ONE of
 * them, and the URL says which: `/rules/LAD-CA-03/show/v2`. Building it here is
 * also what keeps the address bar clean - react-admin's own link helpers append
 * whatever query string the list was showing (`?filter=%7B%7D&page=2&sort=...`),
 * and a rule page that carries the runs list's sort order is a rule page that
 * has to be explained.
 */
export const ruleShowPath = (code: string, version?: number | null): string =>
  `/rules/${encodeURIComponent(code)}/show${version ? `/v${version}` : ""}`;

export const ruleEditPath = (code: string): string =>
  `/rules/${encodeURIComponent(code)}/edit`;

/** `v12` or `12` -> 12. Anything else is no version at all, not version 0. */
export const versionFromParam = (raw?: string): number | undefined => {
  const match = /^v?(\d+)$/.exec(raw ?? "");
  return match ? Number(match[1]) : undefined;
};
