/**
 * Starter prompts.
 *
 * A blank text box asks a user to be an engineer before they can be an
 * auditor. A starter asks them the questions an engineer would ask, in the
 * order that matters, and answers the parts the catalogue already knows.
 *
 * WHAT THE CATALOGUE KNOWS, AND SO THE STARTER KNOWS
 * ----------------------------------------------------
 * For every registered table we have its grain, its description and its
 * columns with their types. That is enough to write a prompt that names the
 * table, says what a row in it is, and points at the column that holds the
 * number. So the user is left filling in the only part that is actually
 * theirs: the judgement.
 *
 *   "ipos_sales is Point-of-sale invoice header - one row per invoice.
 *    The value to test is invoice_value (DOUBLE)."
 *
 * The blanks are written as <angle brackets>. They are not placeholders the
 * product resolves - they are the questions the user has to answer, and the
 * prompt is not finished until they have.
 *
 * Pure functions. No React, so the text can be checked without a browser.
 */

export interface PromptTable {
  name: string;
  system: string;
  description: string;
  grain: string;
  columns: Record<string, string>;
}

const NUMERIC = new Set([
  "DOUBLE", "INTEGER", "INT", "BIGINT", "SMALLINT", "DECIMAL", "NUMERIC",
  "FLOAT", "REAL", "NUMBER", "MONEY",
]);

const TEMPORAL = new Set([
  "TIMESTAMP", "DATE", "DATETIME", "TIME", "TIMESTAMP WITH TIME ZONE",
]);

const type = (t: string) => (t ?? "").toUpperCase();

/** The columns that hold a number, in catalogue order. */
export const numericColumns = (t: PromptTable): string[] =>
  Object.entries(t.columns ?? {})
    .filter(([, ty]) => NUMERIC.has(type(ty)))
    .map(([name]) => name);

/**
 * The column a threshold would apply to.
 *
 * The first numeric column is the wrong answer more often than not: on a
 * sales table that is `guest_count`, and a control that checks a headcount
 * against a money limit is a control nobody wanted. So the column whose NAME
 * says it holds a quantity of money wins, and a float beats an integer
 * because money is not a count.
 */
const VALUE_WORDS =
  /value|amount|total|sum|qty|quantity|price|cost|salary|balance|turnover|revenue/i;

export const valueColumn = (t: PromptTable): string | undefined => {
  const numeric = Object.entries(t.columns ?? {}).filter(([, ty]) =>
    NUMERIC.has(type(ty)),
  );
  const named = numeric.filter(([name]) => VALUE_WORDS.test(name));
  if (named.length) return named[0][0];
  const float = numeric.find(([, ty]) => type(ty) === "DOUBLE");
  if (float) return float[0];
  return numeric[0]?.[0];
};

/** The columns that hold a moment in time, in catalogue order. */
export const temporalColumns = (t: PromptTable): string[] =>
  Object.entries(t.columns ?? {})
    .filter(([, ty]) => TEMPORAL.has(type(ty)))
    .map(([name]) => name);

/** The columns that look like an identifier. */
export const identifierColumns = (t: PromptTable): string[] =>
  Object.keys(t.columns ?? {}).filter((c) => /(^|_)id$|_no$|^id$/i.test(c));

/**
 * The columns two tables have in common.
 *
 * Usually the join key, and usually the reason a cross-system control is
 * possible at all. When there is exactly one, it is worth naming in the
 * prompt - otherwise the model has to find it, and a model that has to find
 * it may find a different one.
 */
export const sharedColumns = (a: PromptTable, b: PromptTable): string[] =>
  Object.keys(a.columns ?? {})
    .filter((c) => Object.prototype.hasOwnProperty.call(b.columns ?? {}, c))
    .sort();

/**
 * How the two tables join, as a hint the user can confirm or correct.
 *
 * A shared column name is the strong signal. Failing that, the two tables
 * often use DIFFERENT names for the same thing - a POS calls an employee
 * `staff_id` and an HR system calls the same person `employee_id` - so a
 * prompt that only offers "the shared column" would say "they are not
 * linked", which is wrong and would send the model hunting.
 */
export const joinHint = (a: PromptTable, b: PromptTable): string => {
  const shared = sharedColumns(a, b);
  if (shared.length === 1) return shared[0];
  if (shared.length > 1) return `<one of ${shared.join(", ")}>`;
  const bIds = identifierColumns(b);
  if (bIds.length === 1) {
    return `<a column in ${a.name}> to ${bIds[0]} in ${b.name}>`;
  }
  return `<the key that joins ${a.name} to ${b.name}>`;
};

/** "one row per invoice" -> "invoice". Used to phrase a finding's grain. */
export const grainNoun = (grain: string): string => {
  const m = /one row per (.+)$/i.exec((grain ?? "").trim());
  return m ? m[1].trim() : (grain || "record");
};

/**
 * "ipos_sales holds Point-of-sale invoice header - one row per invoice."
 *
 * The catalogue's description is a noun phrase, so it needs a verb, and its
 * full stop needs removing or the sentence reads as two. Only the FIRST
 * sentence is used: descriptions often run to two ("Employee master. Drives
 * access reviews and payroll."), and the second is written for a human
 * reading the Data Sources screen, not for a prompt.
 */
const holds = (t: PromptTable): string => {
  const first = (t.description || "the records").split(/(?<=\.)\s+/)[0];
  return `${t.name} holds ${first.replace(/\.\s*$/, "")} - ${t.grain}.`;
};

const list = (items: string[], fallback = "") =>
  items.length ? items.join(", ") : fallback;

export type StarterId =
  | "should_not_happen"
  | "threshold"
  | "two_systems"
  | "baseline"
  | "missing";

export interface Starter {
  id: StarterId;
  label: string;
  /** What it is for, in one line. */
  hint: string;
  /** How many datasources it wants selected. */
  tables: number;
  build: (tables: PromptTable[], lookback: number) => string;
}

export const STARTERS: Starter[] = [
  {
    id: "should_not_happen",
    label: "Something should not happen",
    hint: "A transaction, a person, a state that must never occur",
    tables: 1,
    build: (t) => {
      const a = t[0];
      const fields = list(
        [...identifierColumns(a), ...temporalColumns(a), ...numericColumns(a)].slice(0, 4),
        "the fields on the row",
      );
      return [
        `Flag <the thing that should not happen> in ${a.name}.`,
        ``,
        `${holds(a)} The fields to test are ${fields}.`,
        `A finding is <one row per what>, showing <the fields an auditor would act on>.`,
        `Do not flag <the case that looks the same but is legitimate>.`,
      ].join("\n");
    },
  },
  {
    id: "threshold",
    label: "A value above a threshold",
    hint: "A limit that is agreed, and a number that is checked against it",
    tables: 1,
    build: (t) => {
      const a = t[0];
      const col = valueColumn(a) ?? "<the value column>";
      return [
        `Flag <the thing that is wrong> above <the limit> in ${a.name}.`,
        ``,
        `${holds(a)} The value to test is ${col}.`,
        `A finding is one row per ${grainNoun(a.grain)}, showing ${col} and the identifying fields.`,
        `Do not flag <the case that looks the same but is legitimate>.`,
      ].join("\n");
    },
  },
  {
    id: "two_systems",
    label: "Two systems that must agree",
    hint: "One system says one thing, another says another",
    tables: 2,
    build: (t) => {
      const [a, b] = t;
      return [
        `Flag <the thing that should not happen>, by comparing ${a.name} with ${b.name}.`,
        ``,
        `${holds(a)}`,
        `${holds(b)}`,
        `They are linked by ${joinHint(a, b)}.`,
        `A finding is <one row per what>, showing <the fields an auditor would act on>.`,
        `Do not flag <the case that looks the same but is legitimate>.`,
      ].join("\n");
    },
  },
  {
    id: "baseline",
    label: "This period against a baseline",
    hint: "One stretch of time measured against another",
    tables: 1,
    build: (t, lookback) => {
      const a = t[0];
      const when = temporalColumns(a)[0] ?? "<the timestamp column>";
      const what = valueColumn(a) ?? "<the value column>";
      const recent = Math.max(1, Math.floor(lookback / 2));
      const older = Math.max(1, lookback - recent);
      return [
        `Flag <the thing> that is out of line with <the baseline>.`,
        ``,
        `${holds(a)} It has ${when} and ${what}.`,
        `The rule reads ${lookback} day(s) at a time, so it can compare the most recent ${recent} against the ${older} before it.`,
        `A finding is one row per <what>, showing ${what}, <the baseline> and <the ratio>.`,
      ].join("\n");
    },
  },
  {
    id: "missing",
    label: "A record that should exist, and does not",
    hint: "An absence, not a bad value",
    tables: 1,
    build: (t) => {
      const a = t[0];
      const key = identifierColumns(a)[0] ?? "<the key column>";
      return [
        `Flag <the thing that should be there and is not> in ${a.name}.`,
        ``,
        `${holds(a)} It is keyed by ${key}.`,
        `A finding is one row per <what is missing>, showing <the identifying fields>.`,
      ].join("\n");
    },
  },
];

export const starterById = (id: StarterId): Starter =>
  STARTERS.find((s) => s.id === id) ?? STARTERS[0];

/**
 * Build the prompt for a starter.
 *
 * A starter that wants two tables and has been given one still produces a
 * usable prompt - the missing half is named as a blank, because a prompt
 * that names the table it has and asks for the other is more use to a user
 * than a refusal.
 */
export const starterPrompt = (
  id: StarterId,
  tables: PromptTable[],
  lookback: number,
): string => {
  const starter = starterById(id);
  const chosen = tables.slice(0, starter.tables);
  while (chosen.length < starter.tables) {
    chosen.push({
      // "the other table" only reads right for the second of two.
      name: chosen.length === 0 ? "<the table>" : "<the other table>",
      system: "",
      description: "",
      grain: "one row per record",
      columns: {},
    });
  }
  return starter.build(chosen, lookback);
};
