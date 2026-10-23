
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

const grainNoun = (g: string): string => {
  const m = /one row per (.+)$/i.exec((g ?? "").trim());
  return m ? m[1].trim() : (g || "record");
};

const holds = (t: PromptTable): string => {
  const first = (t.description || "the records").split(/(?<=\.)\s+/)[0];
  return `${t.name} holds ${first.replace(/\.\s*$/, "")} - ${t.grain}.`;
};

export type TemplateId = "after_closing" | "discount_limit";

export interface ProgrammeTemplate {
  id: TemplateId;
  label: string;
  programme: string;
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
