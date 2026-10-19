# Creating a rule

*A guide for the people who write them.*

You can write a rule by hand in a text editor, or you can describe the control
you want and let the portal write it for you. This guide is about the second
route, because it is the one that is new, and because the difference between a
rule that works and a rule that looks like it works is the whole subject.

---

## The five steps

1. **Open it.** Rules → **Create a rule**.
2. **Choose the datasources.** Tick every table the control needs, and no
   others. The selection binds: the model sees only these, and a rule that
   reads anything else is refused before it runs.
3. **Write the prompt.** Use **Start from a template** to get the shape, then
   fill in the blanks. Say what should not happen, what a finding shows, and
   what the legitimate case looks like.
4. **Set the lookback and the test date.** The lookback is how far back the
   rule reads. The test date is the day it is run against while it is being
   written - pick a day with something wrong on it, or the test proves
   nothing.
5. **Read it, then save.** The agent writes the rule, runs it, and repairs it
   until it runs. You get the source, the verdict and what it found. Read the
   code, press **Save as draft**, then activate it when you are ready.

Nothing is created until step 5.

---

## 1. What the button actually does

**Rules → Create a rule** opens a form with four things:

| Field | What it means |
|---|---|
| **Describe the audit control** | Plain language. What should not happen, and what a finding should show. |
| **Datasources the rule may read** | The tables the control is allowed to touch. |
| **Lookback days** | How far back the rule reads: 1 is that day alone, 5 is T-4 to T. |
| **Test on** | The date the rule is run against while it is being written. |

Press **Write and test the rule**.

What happens next is not a single request to a model. It is a loop:

```
   write the rule
         |
         v
   check it is allowed to run  ---->  not allowed? --> tell the model why
         |
         v
   RUN IT against real data     ---->  it failed?  --> tell the model why
         |                                      |
         |                                      +--> write it again
         v
   it ran, and here is what it found
```

The rule is written, checked, executed and - if it did not work - rewritten
with the error in hand, up to three times. Only then is it shown to you.

**Nothing is saved at this point.** The rule does not exist in the portal
until you press **Save as draft**. If the agent never gets it working, no rule
is created and you are told what it tried and why each attempt failed.

---

## 2. Writing the prompt

> **In a hurry? Use the template, or the button in the dialog.**
> **Start from a template** fills in everything the catalogue already knows -
> the table, its grain, its columns - and leaves you the blanks.

The model is shown your datasources, their real columns, and a few real rows
from each. It is never shown anything else. What it has to work out from your
sentence is the *judgement* you want applied.

**Say what should not happen, not what should.**

> Weak: "check the sales data"
>
> Strong: "Flag POS transactions settled by employees who have already left
> the company."

**Say what a finding should show.** The rows the rule returns become the
ticket, so a finding nobody can act on is a finding nobody will action.

> Add: "Group the findings per person and show how much they settled."

**Say the threshold, if you have one in mind.** It ends up as a named
constant in the rule file, which is where a threshold belongs.

> Add: "Flag invoices above 500."

**Do not describe the implementation.** "Join ipos_sales to hr_employees on
staff_id" is the model's job, and it is the part it is better at than you
are. Describe the control and let it work out the data.

**Name the systems, not the columns.** "An employee who exited" is a control.
"`employment_status == 'exited'`" is a guess about the data that will be
wrong the moment the ETL renames a column.

### The master template

Copy this, fill in the blanks, and it is a good prompt. The blanks are in
angle brackets.

```
Flag <the thing that should not happen> in <the table>.

<the table> holds <what it holds> - <what one row is>.
A finding is <one row per what>, showing <the fields an auditor would act on>.
Do not flag <the case that looks the same but is legitimate>.
```

For a control that spans two systems:

```
Flag <the thing that should not happen>, by comparing <table A> with <table B>.

<table A> holds <what it holds> - <what one row is>.
<table B> holds <what it holds> - <what one row is>.
They are linked by <the key that joins them>.
A finding is <one row per what>, showing <the fields an auditor would act on>.
Do not flag <the case that looks the same but is legitimate>.
```

The last line is the one people leave out, and it is the one that matters
most. A control that flags the legitimate case produces a ticket every day,
and a rule nobody trusts is a rule nobody reads.

### A worked example

The cross-system control that CA-06 does, written out in full:

```
Flag POS transactions settled by employees who have already left the company,
by comparing ipos_sales with hr_employees.

ipos_sales holds Point-of-sale invoice header - one row per invoice.
hr_employees holds Employee master - one row per employee.
They are linked by <staff_id in ipos_sales> to <employee_id in hr_employees>.
A finding is one row per employee, showing the employee's name, the number of
transactions they settled and the total value.
Do not flag transactions settled before the employee's exit date.
```

Every angle bracket is a decision only you can make - what the thing is
called, how the two systems link, what a finding shows, and what the
legitimate case is. The rest is the catalogue, which the portal already had.

---

## 3. Choosing the datasources

The selection is binding. The model is shown only the tables you tick, and a
rule that reads anything else is refused before it runs.

That is deliberate. A picker you can wander outside of is a picker that does
not select anything, and you would find out at the worst moment - when a
control you thought read the sales ledger was quietly reading the payroll.

Tick every table the control needs, and no others:

- **The more you tick, the more the model has to choose between.** Three
  tables for a control that reads one is three chances to pick the wrong one.
- **A cross-system control needs both sides.** "An exited employee still
  settling invoices" needs the POS *and* the HR system. Tick both.
- **If the rule needs a table you did not tick, the screen says so**, by
  name. Add it and try again. That is the selection working, not failing.

The picker shows each table's grain and how many columns it has, because
choosing a table you know nothing about is a guess.

---

## 4. Lookback days

How far back the rule reads, staged fresh on every run.

| Lookback | Reads | Use it for |
|---|---|---|
| 1 | That day alone | Point-in-time controls: a single spike, a single bad transaction. |
| 5 | T-4 to T | Controls that need a baseline - "this hour against the average of the week". |
| 30 | T-29 to T | Rolling controls: new starters, ageing, slow-moving stock. |

A rule that reads 30 days needs 30 days of data to have landed. If the ETL
has not delivered them, the run fails and says so - it does not quietly audit
less than it claims.

**A longer lookback is not a more thorough rule.** It is more data to stage
on every run, and a control that only needs one day should not carry thirty.

---

## 5. The test date

The date the rule is run against while it is being written. Pick a day you
expect to be interesting.

**A clean day tells you very little.** If you test on a day with nothing
wrong, the rule may still be broken - it just had nothing to catch. A day
with a known problem is the test worth running, because it answers the
question you actually care about: *would this have caught it?*

The screen says plainly when the rule found nothing:

> The rule ran and found nothing on 2026-10-20. That may be correct - but if
> the control should have caught something, the logic is too loose.

---

## 6. Reading the result

### It worked

You get the source, the verdict, and the findings.

> The rule ran and found **12** exception rows on 2026-10-16. It took **2**
> attempts to get there.

Then:

- **Rule name** - a title for the control, pre-filled from the rule. Change
  it to something an auditor will recognise.
- **Rule code** - the permanent identifier. Generated rules are numbered
  `GEN-01`, `GEN-02`. Your own audit programme's codes (`CA-03`) are yours to
  allocate; a generated rule should not be given one, because it has not been
  through your programme's numbering.
- **Severity** - how loudly it should speak when it fires.

Read the code. It is your control, and the first thing a reviewer asks is
"what is this actually checking?".

### It did not work

You get the last error and everything it tried:

> Attempt 1: `InvalidOperationError: casting from string to date is not
> supported. It was removed in Polars 2.0. Use str.to_date() instead.`
>
> Attempt 2: `ColumnNotFoundError: unable to find column "employee_id"`

That is a genuinely useful answer. A model that guesses a column name and
wins is a model you cannot trust. A model that is **caught** guessing a column
name, by running the rule against your actual data, is one whose work you can
check.

If it still cannot get there, the usual causes are:

| Message | What it means |
|---|---|
| `reads X, which was not selected` | The control needs a table you did not tick. Add it. |
| `The data this rule needs is not there` | The ETL has not landed the file for that date. Not fixable by rewriting the rule - pick a later date. |
| `The rule is not allowed to run` | It tried to import something a rule may not use. |

---

## 7. Saving, and what happens next

**Save as draft** creates the rule. It is a draft: it does not run on a
schedule, and it cannot raise a ticket, until you activate it.

The rule is validated as it is saved, so if it says it is valid, it is.

To put it to work:

1. Open the rule.
2. **Validate** - confirms it is allowed to run and reads only registered
   tables. (Already done for you on save; do it again if you edited the code.)
3. **Activate** - the rule joins the schedule and starts running.
4. Set a **cadence** - daily, weekly, on demand.

Until it is activated it is a document, not a control.

---

## 8. What the trial does and does not touch

The agent runs the rule to check it. That run is real - it stages the same
CSVs, through the same code, that a scheduled run would - but it is kept
apart from the ledger:

| | A trial | A real run |
|---|---|---|
| Reads the estate's data | Yes | Yes |
| Writes a `rule_runs` row | **No** | Yes |
| Raises a ticket | **No** | Yes |
| Counts towards the dashboard | **No** | Yes |

So a rule that finds something during testing has not yet raised a finding.
Nothing has been claimed about your estate that you did not ask for.

---

## 9. When the agent is the wrong tool

The agent is a fast first draft. It is the wrong tool when:

- **The control is subtle.** "Material misstatement" is a judgement the model
  cannot make for you.
- **The threshold is contested.** A number like 500 should be your number,
  agreed with the business, and the agent will not know that.
- **The rule is a control you will be asked to defend.** Write it yourself,
  or generate it and then rewrite it properly.
- **You need to know exactly what it did.** Generated code is code you have
  not read. Read it.

A generated rule is a draft with a working example attached. The trial proves
it *runs*; only reading it proves it is *right*.

---

## 10. If the button says "no LLM endpoint configured"

Rule generation needs a model. The portal talks to any OpenAI-compatible
endpoint, so yours may already be one:

```bash
# backend/.env
LLM_BASE_URL=https://your-endpoint/v1
LLM_API_KEY=...
LLM_MODEL=your-model-name
```

Two things worth knowing:

- **Reasoning models are slow.** A model that thinks for 9,000 tokens before
  answering will take a minute or two to write a rule. Most providers let you
  turn that down:

  ```bash
  LLM_EXTRA_PARAMS={"reasoning_effort": "low"}
  ```

  It is optional, and if your endpoint does not understand it the call is
  retried once without it.

- **Everything else works without it.** Rules can be written by hand, the
  catalogue, the scheduler, the tickets and the dashboard do not need a model
  at all.

---

## Related

- `README.md` - what the portal is, and how to run it
- `AGENTS.md` - how the code is put together, for whoever changes it
- The **Rules** screen - writing a rule by hand
- The **Data Sources** screen - registering a table, and what a table contains
