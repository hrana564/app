/**
 * Programme templates.
 *
 * A blank text box asks a user to be an engineer before they can be an
 * auditor. A template asks them the questions an engineer would ask, in the
 * order that matters, and answers the parts the catalogue already knows.
 *
 * THESE ARE CONTROLS, NOT SHAPES
 * -------------------------------
 * Each template is a control from the audit programme, in the programme's own
 * words - "Sales posted / settled after the store closing hours" - because a
 * control an auditor recognises is worth more than a shape they have to
 * imagine. The judgement stays with the user: the limit, the grace period, the
 * case that looks the same and is legitimate.
 *
 * WHAT THE CATALOGUE KNOWS, AND SO THE TEMPLATE KNOWS
 * ---------------------------------------------------
 * For every registered table we have its grain, its description and its
 * columns with their types. That is enough to write a prompt that names the
 * table and says what a row in it is. So the user is left filling in the only
 * part that is actually theirs: the judgement.
 *
 * The blanks are written as <angle brackets>. They are not placeholders the
 * product resolves - they are the questions the user has to answer, and the
 * prompt is not finished until they have.
 *
 * The prompt names the SYSTEMS, never the columns. "Join ipos_sales to
 * hr_employees on staff_id" is the model's job and it is better at it than we
 * are; and a column name written by hand is a guess that is wrong the moment
 * the ETL renames it. The one exception is the value column, and that is
 * because the catalogue has already worked out which one it is.
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

const type = (t: string) => (t ?? "").toUpperCase();

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

const valueColumn = (t: PromptTable): string | undefined => {
  const numeric = Object.entries(t.columns ?? {}).filter(([, ty]) =>
    NUMERIC.has(type(ty)),
  );
  const named = numeric.filter(([name]) => VALUE_WORDS.test(name));
  if (named.length) return named[0][0];
  const float = numeric.find(([, ty]) => type(ty) === "DOUBLE");
  if (float) return float[0];
  return numeric[0]?.[0];
};

/** "one row per invoice" -> "invoice". Used to phrase a finding's grain. */
const grainNoun = (g: string): string => {
  const m = /one row per (.+)$/i.exec((g ?? "").trim());
  return m ? m[1].trim() : (g || "record");
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

export type TemplateId = "after_closing" | "discount_limit";

export interface ProgrammeTemplate {
  id: TemplateId;
  /** The control, in the programme's own words. */
  label: string;
  /** Where the control sits, and what kind of control it is. */
  programme: string;
  /** The tables the control reads. */
  tables: string[];
  build: (tables: PromptTable[]) => string;
}

export const TEMPLATES: ProgrammeTemplate[] = [
  {
    id: "after_closing",
    label: "Sales posted / settled after the store closing hours",
    programme:
      "Stores Management / Store Operations / Sales & discounts — for management monitoring",
    tables: ["ipos_sales", "hr_attendance"],
    build: (t) => {
      const [sales, attendance] = t;
      return [
        "Flag every sale that was posted after the store had closed for the day.",
        "",
        `${holds(sales)}`,
        `${holds(attendance)}`,
        "A sale, and the attendance that closes its store, are the rows for the same store on the same day.",
        "",
        "The store closed at <the moment the store closed: the clock-out of the staff who did the end-of-day closing, or the last clock-out at that store>.",
        "A sale is late when it was posted more than <how many minutes after closing> that moment.",
        "",
        `A finding is one row per ${grainNoun(sales.grain)}, showing <the fields an auditor would act on: the store, the employee who settled it, the invoice, its value, the time it was posted, and how many minutes late it was>.`,
        "Do not flag <the case that looks the same but is legitimate — for example a voided invoice, or a sale posted a minute or two after closing>.",
      ].join("\n");
    },
  },
  {
    id: "discount_limit",
    label: "Discounts beyond a specified limit by store and by employee",
    programme:
      "Stores Management / Store Operations / Sales & discounts — for management monitoring",
    tables: ["ipos_discounts", "ipos_sales"],
    build: (t) => {
      const [discounts, sales] = t;
      return [
        "Flag every discount given beyond the agreed limit, by store and by employee.",
        "",
        `${holds(discounts)}`,
        `${holds(sales)}`,
        "A discount, and the invoice it was given on, are the rows for the same invoice.",
        "",
        `The value to test is ${valueColumn(discounts) ?? "the discount value"}.`,
        "The limit is <the limit for a single discount, in the currency the till settles in — for example 200>.",
        "Flag a discount when its value is above the limit, and also flag a store and an employee whose discounts together on one day come to more than <the limit for one employee at one store in one day>.",
        "",
        `A finding is one row per ${grainNoun(discounts.grain)}, showing <the fields an auditor would act on: the store, the employee who gave it, the invoice, the discount, the invoice value, and the total that employee has given at that store on that day>.`,
        "Do not flag <the case that looks the same but is legitimate — for example a promotion that was agreed for the month>.",
      ].join("\n");
    },
  },
];

const templateById = (id: TemplateId): ProgrammeTemplate =>
  TEMPLATES.find((t) => t.id === id) ?? TEMPLATES[0];

/**
 * Build the prompt for a template.
 *
 * A template that wants two tables and has been given one still produces a
 * usable prompt - the missing half is named as a blank, because a prompt that
 * names the table it has and asks for the other is more use to a user than a
 * refusal. The dialog does not offer a template whose feeds the client does
 * not have, so this is the belt to that braces.
 */
export const templatePrompt = (
  id: TemplateId,
  tables: PromptTable[],
): string => {
  const template = templateById(id);
  const missing: PromptTable = {
    name: "<the other table>",
    system: "",
    description: "",
    grain: "one row per record",
    columns: {},
  };
  const chosen = template.tables.map((name, i) => {
    const found = tables.find((t) => t.name === name);
    if (found) return found;
    return { ...missing, name: i === 0 ? "<the table>" : missing.name };
  });
  return template.build(chosen);
};
