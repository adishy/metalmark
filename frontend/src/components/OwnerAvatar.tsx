// An owner, as a small avatar rather than a word — so a list of accounts reads as
// *whose* at a glance without a line of text under every row.
//
// Shared is 👥 (it is everyone); a person is their initials on one of the chart
// fills, hashed from the name exactly as account marks are, so the same person
// is the same colour everywhere. The name is always the accessible label and
// the tooltip — the picture never carries the meaning alone (§7 item 8).
import type { Owner } from "@/api/types";
import { FILLS, fillFor, initialsFor } from "@/components/AccountMark";

export default function OwnerAvatar({
  owner,
  className,
  testid,
}: {
  owner: Pick<Owner, "name" | "kind"> | undefined;
  className?: string;
  testid?: string;
}) {
  const name = owner?.name ?? "Owner";
  const shared = owner?.kind === "shared";
  const fill = FILLS[fillFor(name) - 1] ?? FILLS[0];
  return (
    <span
      role="img"
      aria-label={`Owner: ${name}`}
      title={name}
      className={`inline-flex size-6 shrink-0 items-center justify-center rounded-full text-xs leading-none font-semibold ${
        shared ? "border border-border bg-surface-inset" : `text-accent-fg ${fill}`
      } ${className ?? ""}`}
      data-testid={testid}
    >
      <span aria-hidden="true">{shared ? "👥" : initialsFor(name)}</span>
    </span>
  );
}
