// Owner scope selector shared by Transactions, Accounts and Reports, so the
// three pages filter the same way (and the same owner stays selected as the
// user moves between them by convention, not by shared state).
import type { Owner } from "@/api/types";

export default function OwnerFilterChips({
  owners,
  value,
  onChange,
  label = "Owner",
  testid = "owner-filter",
}: {
  owners: Owner[];
  /** null = every owner (no owner_id param). */
  value: string | null;
  onChange: (id: string | null) => void;
  label?: string;
  testid?: string;
}) {
  // `min-h-11` (44px) and `text-sm`, not the `px-3 py-1 text-xs` this was: that
  // computed to 24px, and chips are thumb targets (§4.5). Selection is carried
  // by `aria-pressed` as well as the fill, so it is never colour alone.
  const chip = (on: boolean) =>
    `inline-flex min-h-11 shrink-0 items-center rounded-full border px-4 text-sm whitespace-nowrap ${
      on ? "border-accent bg-accent/20 font-medium text-accent-ink" : "border-border-strong text-fg-muted hover:text-fg"
    }`;

  return (
    <div>
      <p className="mb-2 text-xs font-medium text-fg-muted">{label}</p>
      <div className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0 sm:pb-0" data-testid={testid}>
        <button
          type="button"
          onClick={() => onChange(null)}
          aria-pressed={value === null}
          className={chip(value === null)}
          data-testid="owner-filter-all"
        >
          All
        </button>
        {owners.map((o) => {
          const on = value === o.id;
          return (
            <button
              key={o.id}
              type="button"
              onClick={() => onChange(o.id)}
              aria-pressed={on}
              className={chip(on)}
              data-testid={`owner-filter-${o.id}`}
            >
              {o.name}
            </button>
          );
        })}
        {owners.length === 0 && <span className="text-xs text-fg-muted">No owners</span>}
      </div>
    </div>
  );
}
