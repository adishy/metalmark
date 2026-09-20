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
  const chip = (on: boolean) =>
    `rounded-full border px-3 py-1 text-xs ${
      on ? "border-accent bg-accent/20 text-accent" : "border-border-strong text-fg-muted hover:text-fg"
    }`;

  return (
    <div>
      <p className="mb-1 text-xs font-medium text-fg-muted">{label}</p>
      <div className="flex flex-wrap gap-2" data-testid={testid}>
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
