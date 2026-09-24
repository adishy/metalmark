// Choosing a category, as a list you can search, not a <select> of seventy rows.
//
// Categories are shown with their emoji and grouped the way the household groups
// them. The groups that fit the money's direction come first — money in lists
// income and transfer groups, money out expense and transfer groups — and the
// rest follow under "Other categories", because a refund filed under Clothing is
// a real choice a person can make and the picker should not forbid it.
import { useMemo, useState } from "react";
import type { Category, CategoryGroup } from "@/api/types";
import Dialog from "@/components/Dialog";
import { Input } from "@/components/form";

/** "🛒 Groceries" — the one way a category is written wherever it is shown. */
export function categoryLabel(c: Pick<Category, "name" | "icon"> | undefined | null): string {
  if (!c) return "Uncategorized";
  return c.icon ? `${c.icon} ${c.name}` : c.name;
}

export const UNCATEGORIZED_ICON = "❔";

export default function CategoryPicker({
  open,
  onClose,
  categories,
  groups,
  value,
  amount,
  onPick,
  testid = "category-picker",
}: {
  open: boolean;
  onClose: () => void;
  categories: Category[];
  groups: CategoryGroup[];
  value: string | null;
  /** The transaction's signed amount: its direction orders the groups. */
  amount: string | number;
  onPick: (categoryId: string | null) => void;
  testid?: string;
}) {
  const [query, setQuery] = useState("");
  const direction = Number(amount) > 0 ? "income" : "expense";

  const sections = useMemo(() => {
    const q = query.trim().toLocaleLowerCase();
    const match = (c: Category) => !q || c.name.toLocaleLowerCase().includes(q);
    const ordered = [...groups].sort((a, b) => a.sort - b.sort);
    const fits = (g: CategoryGroup) => g.type === direction || g.type === "transfer";
    const build = (gs: CategoryGroup[]) =>
      gs
        .map((g) => ({
          group: g,
          rows: categories
            .filter((c) => c.group_id === g.id && match(c))
            .sort((a, b) => a.sort - b.sort || a.name.localeCompare(b.name)),
        }))
        .filter((s) => s.rows.length > 0);
    return { likely: build(ordered.filter(fits)), rest: build(ordered.filter((g) => !fits(g))) };
  }, [categories, groups, query, direction]);

  const pick = (id: string | null) => {
    onPick(id);
    setQuery("");
    onClose();
  };

  const option = (c: Category) => (
    <li key={c.id}>
      <button
        type="button"
        aria-pressed={value === c.id}
        onClick={() => pick(c.id)}
        className={`flex min-h-11 w-full items-center gap-3 rounded-control px-3 text-left text-base ${
          value === c.id ? "bg-accent/20 font-medium text-accent-ink" : "hover:bg-surface-inset"
        }`}
        data-testid={`${testid}-option-${c.id}`}
      >
        <span aria-hidden="true" className="w-6 text-center">{c.icon ?? "•"}</span>
        <span className="flex-1">{c.name}</span>
      </button>
    </li>
  );

  const nothing = sections.likely.length === 0 && sections.rest.length === 0;

  return (
    <Dialog open={open} onClose={onClose} title="Choose a category" testid={testid}>
      <div className="space-y-3">
        <Input
          type="search"
          aria-label="Search categories"
          placeholder="Search categories"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoComplete="off"
          data-testid={`${testid}-search`}
        />
        <div className="max-h-96 space-y-4 overflow-y-auto">
          {!query && (
            <button
              type="button"
              aria-pressed={value === null}
              onClick={() => pick(null)}
              className="flex min-h-11 w-full items-center gap-3 rounded-control px-3 text-left text-base text-fg-muted hover:bg-surface-inset"
              data-testid={`${testid}-none`}
            >
              <span aria-hidden="true" className="w-6 text-center">{UNCATEGORIZED_ICON}</span>
              <span className="flex-1">Uncategorized</span>
            </button>
          )}
          {sections.likely.map(({ group, rows }) => (
            <section key={group.id} aria-label={group.name}>
              <h3 className="px-3 pb-1 text-sm font-medium text-fg-muted">{group.name}</h3>
              <ul>{rows.map(option)}</ul>
            </section>
          ))}
          {sections.rest.length > 0 && (
            <details open={!!query}>
              <summary className="cursor-pointer px-3 py-2 text-sm text-fg-muted">Other categories</summary>
              {sections.rest.map(({ group, rows }) => (
                <section key={group.id} aria-label={group.name}>
                  <h3 className="px-3 pb-1 pt-2 text-sm font-medium text-fg-muted">{group.name}</h3>
                  <ul>{rows.map(option)}</ul>
                </section>
              ))}
            </details>
          )}
          {nothing && (
            <p className="px-3 py-6 text-center text-sm text-fg-muted">
              No category called “{query}”. Add one in Settings → Categories.
            </p>
          )}
        </div>
      </div>
    </Dialog>
  );
}
