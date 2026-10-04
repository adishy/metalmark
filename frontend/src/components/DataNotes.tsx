/**
 * Report caveats stay discoverable without overwhelming the figures.
 *
 * Collapsed, the summary is the only thing on screen — so a caller whose notes
 * include something that changes how the figure above should be read passes its
 * own `summary` and `tone="warning"`, and the collapsed line says it. The quiet
 * default is for notes that only qualify the figure.
 */
export default function DataNotes({ notes, summary = "Some data is incomplete", tone = "neutral", testid }: {
  notes: readonly string[];
  summary?: string;
  /** `warning` colours the summary line; the words must carry the warning too. */
  tone?: "neutral" | "warning";
  testid: string;
}) {
  if (!notes.length) return null;
  // One line per distinct sentence, but the count is of notes: two trades that
  // each lack a rate on the same day are two things missing, not one.
  const counts = new Map<string, number>();
  for (const note of notes) counts.set(note, (counts.get(note) ?? 0) + 1);
  const total = notes.length;
  return (
    <details className="mt-3 rounded-control border border-border bg-surface-inset/40 text-sm text-fg-muted" data-testid={testid}>
      <summary
        className={`min-h-11 cursor-pointer rounded-control px-3 py-3 font-medium ${tone === "warning" ? "text-warning" : ""}`}
        data-tone={tone}
      >
        {summary} <span className="font-normal">· {total} {total === 1 ? "note" : "notes"}</span>
      </summary>
      <ul className="list-disc space-y-2 px-4 pb-3 pl-8">
        {[...counts].map(([note, times]) => (
          <li key={note} className="break-words">
            {note}
            {times > 1 && (
              <span className="whitespace-nowrap font-medium" aria-label={`, ${times} times`}> ×{times}</span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
