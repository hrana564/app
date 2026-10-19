/**
 * The DATASOURCES declaration round trip, checked outside a browser.
 *
 *   cd frontend && npx tsx src/components/ruleSource.check.ts
 *
 * This rewrites a Python file an auditor wrote, so "it looks right in the
 * dropdown" is not evidence. Every case below asserts the real function, and
 * several run against the real rule files in backend/rules/.
 *
 * A plain script rather than a test suite: this project has no frontend test
 * runner, and adding one to cover a single file is not a trade worth making.
 * Exits non-zero on failure, so it can be wired into a pre-commit hook.
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  inspectBinding,
  readDeclaredDatasources,
  renderDeclaration,
  tablesReferencedIn,
  writeDeclaredDatasources,
} from "./ruleSource";

const HERE = dirname(fileURLToPath(import.meta.url));
const RULES = resolve(HERE, "../../../backend/rules");

/**
 * A rule's file is named for its code, and the code carries the client's code
 * - so the file is named after the client too, and its name is not the same
 * in every deployment. Found by the base code, so this script does not name a
 * client either.
 */
const ruleFile = (base: string, version: number) => {
  const stem = `${base}_v${version}.py`;
  const names = readdirSync(RULES);
  const found = names.find((n) => n.endsWith(`-${stem}`)) ?? names.find((n) => n === stem);
  if (!found) throw new Error(`no rule file for ${base} v${version} in ${RULES}`);
  return found;
};

let failures = 0;

const eq = (label: string, got: unknown, want: unknown) => {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  if (g !== w) {
    failures++;
    console.log(`  FAIL  ${label}`);
    console.log(`          got  ${g}`);
    console.log(`          want ${w}`);
  } else {
    console.log(`  PASS  ${label}`);
  }
};

const isTrue = (label: string, value: boolean) => eq(label, value, true);

// --- the real files ---------------------------------------------------------
const template = readFileSync(resolve(RULES, "_template.py"), "utf8");
const rule04 = readFileSync(resolve(RULES, ruleFile("CA-04", 4)), "utf8");

console.log("=== reading the shipped rules ===");
eq("template declares ipos_sales", readDeclaredDatasources(template), ["ipos_sales"]);
eq("CA-04 declares ipos_sales", readDeclaredDatasources(rule04), ["ipos_sales"]);

// The bug this file exists for: the template's docstring shows
// `ctx.count_rows("name")` as an example. Reading raw text offered to bind a
// table called `name`.
eq("template's docstring example is not a table", tablesReferencedIn(template), ["ipos_sales"]);
eq("CA-04 reads what it declares", tablesReferencedIn(rule04), ["ipos_sales"]);

console.log("\n=== a rewrite leaves the rest of the file alone ===");
const rewritten = writeDeclaredDatasources(rule04, ["ipos_sales", "hr_employees"]);
eq("reads back", readDeclaredDatasources(rewritten.source), ["hr_employees", "ipos_sales"]);
isTrue("module docstring survives", rewritten.source.includes("CA-04  -  Single staff member"));
isTrue("import survives", rewritten.source.includes("import polars as pl"));
isTrue("run(ctx) survives", rewritten.source.includes("def run(ctx) -> dict:"));
eq("exactly one declaration", (rewritten.source.match(/^DATASOURCES/gm) ?? []).length, 1);
isTrue(
  "declared before it is used",
  rewritten.source.indexOf("DATASOURCES") < rewritten.source.indexOf("def run"),
);
eq(
  "writing the same set is a no-op",
  writeDeclaredDatasources(rule04, ["ipos_sales"]).changed,
  false,
);

console.log("\n=== rendering ===");
eq("one", renderDeclaration(["a"]), 'DATASOURCES = ["a"]');
eq("two", renderDeclaration(["a", "b"]), 'DATASOURCES = ["a", "b"]');
eq("none", renderDeclaration([]), "DATASOURCES = []");
isTrue("long lists go multi-line", renderDeclaration(["a", "b", "c", "d"]).includes("\n"));
eq("duplicates collapse", renderDeclaration(["a", "a", "b"]), 'DATASOURCES = ["a", "b"]');

console.log("\n=== a declaration already written across lines ===");
const multi = `import polars as pl\n\nDATASOURCES = [\n    "ipos_sales",\n    "hr_employees",\n]\n\ndef run(ctx):\n    return {}\n`;
eq("is read", readDeclaredDatasources(multi), ["hr_employees", "ipos_sales"]);
const collapsed = writeDeclaredDatasources(multi, ["ipos_sales"]);
eq("collapses to one line", readDeclaredDatasources(collapsed.source), ["ipos_sales"]);
isTrue("the function below survives", collapsed.source.includes("def run(ctx):"));

console.log("\n=== a rule with no declaration gets one, in a legal place ===");
const bare = `"""Docstring."""\n\nimport polars as pl\n\n\ndef run(ctx):\n    return {}\n`;
const inserted = writeDeclaredDatasources(bare, ["ipos_sales"]);
isTrue("inserted rather than replaced", inserted.inserted);
eq("with the right value", readDeclaredDatasources(inserted.source), ["ipos_sales"]);
isTrue(
  "after the import",
  inserted.source.indexOf("import polars") < inserted.source.indexOf("DATASOURCES"),
);
isTrue(
  "before anything that uses it",
  inserted.source.indexOf("DATASOURCES") < inserted.source.indexOf("def run"),
);

const noImport = `"""Doc."""\n\n\ndef run(ctx):\n    return {}\n`;
isTrue(
  "a file with no imports still gets it after the docstring",
  writeDeclaredDatasources(noImport, ["x"]).source.indexOf('"""') <
    writeDeclaredDatasources(noImport, ["x"]).source.indexOf("DATASOURCES"),
);

console.log("\n=== documentation is not a declaration ===");
eq("a commented-out declaration is ignored",
  readDeclaredDatasources(`import polars as pl\n# DATASOURCES = ["old"]\n\ndef run(ctx):\n    return {}\n`),
  null);

const docExample = `"""Rule.\n\n    DATASOURCES = ["stale_example"]\n"""\nimport polars as pl\n\ndef run(ctx):\n    return ctx.table("real_one")\n`;
eq("a docstring example is ignored", readDeclaredDatasources(docExample), null);
eq("but the real call is found", tablesReferencedIn(docExample), ["real_one"]);
const docFixed = writeDeclaredDatasources(docExample, ["real_one"]);
eq("a real declaration is added", readDeclaredDatasources(docFixed.source), ["real_one"]);
eq(
  "and only one survives as code",
  (docFixed.source.replace(/"""[\s\S]*?"""/g, "").match(/^DATASOURCES/gm) ?? []).length,
  1,
);

console.log("\n=== what the picker reports ===");
const mixed = `import polars as pl\nDATASOURCES = ["ipos_sales"]\n\ndef run(ctx):\n    a = ctx.table("ipos_sales")\n    b = ctx.table("hr_employees")\n    return {}\n`;
const binding = inspectBinding(mixed);
eq("declared", binding.declared, ["ipos_sales"]);
eq("referenced", binding.referenced, ["hr_employees", "ipos_sales"]);
eq("read but undeclared is flagged", binding.undeclared, ["hr_employees"]);
eq("declared but unread is flagged", binding.unused, []);

const undeclared = inspectBinding(`import polars as pl\n\ndef run(ctx):\n    return ctx.table("x")\n`);
eq("no declaration is reported unreadable", undeclared.unreadable, true);
eq("and what it reads is still reported", undeclared.referenced, ["x"]);

console.log(`\n${failures === 0 ? "PASS" : "FAIL"} - ${failures} failure(s)`);
process.exit(failures ? 1 : 0);