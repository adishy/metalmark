// Set, change or clear one category's plan for one period (ADR-0058).
//
// One dialog, reached from two places, because a plan is written from two
// different impulses: **Settings → Categories** is the complete list of
// categories — including ones with no spending yet — so it is the only place a
// plan can be made for a category that has nothing to show; the **Insights →
// Budgets** tab is where a reader is already looking at the number that makes
// them want to change one. Both write through this component and read the same
// `GET /budgets`, so the two screens cannot disagree about what is planned.
//
// The amount is entered as a **magnitude and stored as one**: a budget is not a
// signed quantity. `spending_by_category` counts money out as a positive total
// (its own docstring says so), so the figure this is compared against is never
// negative, and a plan written as `-600` would compare against it backwards.
// The server refuses a negative amount; this refuses it one step earlier, where
// the message can point at the field.
//
// **An empty field is not a delete.** Clearing is the explicit button, which
// only exists when there is a plan to clear: "I emptied the box" is a much more
// common accident than "I meant to remove this month's plan", and the two should
// not be one keystroke apart.
import { useState } from "react";
import type { BudgetRow } from "@/api/types";
import { useClearBudget, useSetBudget } from "@/api/hooks";
import { formatMoney } from "@/lib/format";
import Dialog from "@/components/Dialog";
import { Button, Field, Input, Spinner, useFieldId, validAmount } from "@/components/form";

export default function BudgetDialog({
  category,
  period,
  monthLabel,
  row,
  currency,
  onClose,
}: {
  /** The category this plan is for. Only its name is read — the row is the plan. */
  category: { id: string; name: string };
  /** Any day in the period; the server normalizes it to the month's first day. */
  period: string;
  /** The period as a person reads it ("September 2026"), for the label. */
  monthLabel: string;
  /** The current row, when the category has one — the plan to edit, and the
   *  spend to state beside it. Absent for a category with neither. */
  row: BudgetRow | null;
  currency: string;
  onClose: () => void;
}) {
  const setBudget = useSetBudget();
  const clearBudget = useClearBudget();
  const id = useFieldId(`budget-amount-${category.id}`);

  // Seeded from the stored plan, so opening this on a budgeted category is an
  // edit rather than a retype. `Money` is a string, and the input is a string:
  // nothing here goes through a float.
  const [amount, setAmount] = useState<string>(row?.budget ?? "");
  // **Nothing is marked wrong until Save has been pressed.** The dialog opens
  // empty on a category with no plan, and `validAmount("")` is `"Required"` —
  // shown live, that is a red message under a field nobody has typed in yet,
  // which is a reprimand for opening the dialog. This is the same "validate on
  // submit" the add forms on Settings use.
  const [err, setErr] = useState<string | null>(null);
  const pending = setBudget.isPending || clearBudget.isPending;
  const failure = setBudget.error ?? clearBudget.error;

  function save() {
    const problem =
      validAmount(amount) ??
      (Number(amount) < 0 ? "A budget is a positive amount." : null);
    setErr(problem);
    if (problem) return;
    setBudget.mutate(
      { categoryId: category.id, period, amount: amount.trim() },
      { onSuccess: onClose },
    );
  }

  const spent = row?.spent ?? null;

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Budget — ${category.name}`}
      testid={`budget-dialog-${category.id}`}
      footer={
        <div className="flex flex-wrap items-center gap-2">
          {row?.budget != null && (
            <Button
              variant="ghost"
              className="px-2 py-1 text-xs"
              disabled={pending}
              onClick={() =>
                clearBudget.mutate(
                  { categoryId: category.id, period },
                  { onSuccess: onClose },
                )
              }
              data-testid={`budget-clear-${category.id}`}
            >
              Clear it
            </Button>
          )}
          <div className="flex-1" />
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} disabled={pending} data-testid={`budget-save-${category.id}`}>
            {pending && <Spinner />}
            {row?.budget != null ? "Save" : "Set it"}
          </Button>
        </div>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
        data-testid={`budget-form-${category.id}`}
      >
        <Field
          label={`Budget for ${monthLabel}`}
          htmlFor={id}
          error={err}
          // The window is in the label, and the spend so far is in the hint
          // beside it: a plan is only readable next to what has been spent
          // against it, and this is the one place a person writes one.
          hint={
            spent === null
              ? `Nothing spent on this category in ${monthLabel}.`
              : `Spent against it so far: ${formatMoney(spent, currency)}.`
          }
        >
          <Input
            id={id}
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="600"
            data-testid={`budget-amount-${category.id}`}
          />
        </Field>
        <p className="text-xs text-fg-muted">
          {currency}. The same figure the Spending report gives for this category in{" "}
          {monthLabel} is what it is measured against.
        </p>
        {failure && (
          <p className="text-sm text-negative" role="alert">
            {(failure as Error).message}
          </p>
        )}
      </form>
    </Dialog>
  );
}
