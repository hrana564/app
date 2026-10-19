/**
 * Reading and writing a rule's `DATASOURCES` constant.
 *
 * WHY THIS EXISTS
 * ---------------
 * `DATASOURCES = ["ipos_sales", "hr_employees"]` is a module constant in the
 * rule's own Python file. That is where the truth lives, and it stays there.
 *
 * A dropdown that saved to a database column would create a second source of
 * truth: the file says one thing, the row says another, and the runner reads
 * the file. So the picker edits the source. The dropdown is a convenience over
 * the declaration, never a parallel copy of it.
 *
 * Pure functions, no React - so the rewrite can be checked without a browser.
 *
 * WHAT CAN GO WRONG
 * -----------------
 * The editor also lets someone type Python freely, so the constant may be
 * missing, malformed, commented out, or written across several lines. Every
 * function here degrades rather than throwing: a rule with an unparseable
 * declaration is one the admin must fix by hand, and the picker says so instead
 * of silently overwriting their work.
 */

/** Matches a module-level `DATASOURCES = [ ... ]`, single or multi-line. */
const ASSIGNMENT =
  /^[ \t]*DATASOURCES[ \t]*=[ \t]*\[[^\]]*\][ \t]*$/m;

/** The first string literal inside the list. Deliberately conservative. */
const QUOTED = /["']([A-Za-z_]\w*)["']/g;

/**
 * Blank out docstrings and comments, preserving line structure.
 *
 * `_template.py` documents itself in its module docstring with
 * `ctx.table("ipos_sales")` and `ctx.count_rows("name")`. Scanning raw text
 * picked up `count_rows("name")` and offered to bind a table that does not
 * exist - so a documentation example would have become a real selection.
 *
 * Only docstrings and comments go. String literals stay, because the table name
 * IS a string literal - blanking them would hide every real call.
 */
const stripDocsAndComments = (source: string): string => {
  const blank = (m: string) => m.replace(/[^\n]/g, " ");
  return source
    .replace(/"""[\s\S]*?"""/g, blank)
    .replace(/'''[\s\S]*?'''/g, blank)
    // A `#` inside a string is not a comment, so protect literals first.
    .replace(/"(?:[^"\\\n]|\\.)*"/g, (m) => m.replace(/#/g, ""))
    .replace(/#[^\n]*/g, "");
};

/**
 * Tables the rule reads via `ctx.table("x")` / `ctx.count_rows("x")`.
 *
 * Separate from the declaration on purpose: a rule can declare one thing and
 * read another, and that gap is exactly what the static validator exists to
 * catch. Surfacing both lets the admin see it before running anything.
 */
export const tablesReferencedIn = (source: string): string[] => {
  const code = stripDocsAndComments(source);
  const found = new Set<string>();
  const call = /ctx\.(?:table|count_rows)\(\s*["']([A-Za-z_]\w*)["']/g;
  let m: RegExpExecArray | null;
  while ((m = call.exec(code)) !== null) found.add(m[1]);
  return [...found].sort();
};

/** The names in the `DATASOURCES = [...]` declaration, or null if unreadable. */
export const readDeclaredDatasources = (source: string): string[] | null => {
  // Match against the code with docstrings and comments removed, but locate the
  // match in the ORIGINAL text so a rewrite replaces the right characters. A
  // commented-out declaration is not a declaration, and a docstring showing the
  // old value is not the current one.
  const code = stripDocsAndComments(source);
  const match = ASSIGNMENT.exec(code);
  if (!match) return null;

  const inner = match[0].slice(match[0].indexOf("[") + 1, match[0].lastIndexOf("]"));
  const names: string[] = [];
  let m: RegExpExecArray | null;
  QUOTED.lastIndex = 0;
  while ((m = QUOTED.exec(inner)) !== null) names.push(m[1]);
  return [...new Set(names)].sort();
};

/** The literal line we would write for a given set of names. */
export const renderDeclaration = (names: string[]): string => {
  const unique = [...new Set(names)].sort();
  if (unique.length === 0) return "DATASOURCES = []";
  if (unique.length <= 3) {
    return `DATASOURCES = [${unique.map((n) => `"${n}"`).join(", ")}]`;
  }
  // Long lists go multi-line, matching how a person would write them.
  return `DATASOURCES = [\n${unique.map((n) => `    "${n}",`).join("\n")}\n]`;
};

/**
 * Write the declaration into the source.
 *
 * Returns the source unchanged when the constant cannot be found safely. A
 * rule that spells its declaration oddly is flagged for a human rather than
 * having its code rewritten by a text substitution.
 */
export const writeDeclaredDatasources = (
  source: string,
  names: string[],
): { source: string; changed: boolean; inserted: boolean } => {
  const line = renderDeclaration(names);
  const match = ASSIGNMENT.exec(stripDocsAndComments(source));

  if (match) {
    // Only the matched line is replaced; the rest of the file is untouched.
    const updated =
      source.slice(0, match.index) + line + source.slice(match.index + match[0].length);
    return { source: updated, changed: updated !== source, inserted: false };
  }

  // No declaration at all. Put one where Python expects it: after the imports
  // and the module docstring, before anything that uses it.
  const anchor = findInsertionPoint(source);
  const updated = `${source.slice(0, anchor)}${line}\n\n${source.slice(anchor)}`;
  return { source: updated, changed: true, inserted: true };
};

/**
 * Where a missing `DATASOURCES` line should go.
 *
 * After the last top-level `import`, or after the module docstring, whichever
 * is lower. Inserting before the imports would produce a file that reads
 * strangely and that some linters reject outright.
 */
const findInsertionPoint = (source: string): number => {
  const lines = source.split("\n");
  let lastImportEnd = 0;
  let inDocstring = false;

  lines.forEach((line, i) => {
    const trimmed = line.trim();
    if (inDocstring) {
      if (trimmed.endsWith('"""')) inDocstring = false;
      return;
    }
    if (i === 0 && trimmed.startsWith('"""')) {
      // Single-line docstring, or the opening line of a long one.
      if (!(trimmed.length > 3 && trimmed.endsWith('"""'))) inDocstring = true;
      return;
    }
    if (trimmed.startsWith("import ") || trimmed.startsWith("from ")) {
      lastImportEnd = i + 1;
    }
  });

  if (lastImportEnd > 0) return lines.slice(0, lastImportEnd).join("\n").length + 1;

  // No imports: go after the docstring, or to the top of the file.
  const docstringEnd = source.indexOf('"""', 3);
  if (docstringEnd > 0) {
    const closing = source.indexOf('"""', docstringEnd);
    if (closing > 0) return closing + 3;
  }
  return 0;
};

/**
 * What the admin needs to know about a rule's binding, in one shape.
 *
 * `unreadable` is not an error case to hide. A declaration that cannot be parsed
 * means the picker cannot safely write it, and the admin must fix the file.
 */
export const inspectBinding = (source: string) => {
  const declared = readDeclaredDatasources(source);
  const referenced = tablesReferencedIn(source);
  const unreadable = declared === null;

  return {
    declared: declared ?? [],
    referenced,
    unreadable,
    /** Read but never declared - the validator warns about this. */
    undeclared: unreadable ? referenced : referenced.filter((t) => !declared.includes(t)),
    /** Declared but never read - harmless, but usually a leftover. */
    unused: unreadable ? [] : declared.filter((t) => !referenced.includes(t)),
  };
};