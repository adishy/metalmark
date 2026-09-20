// Owner picker shared by the account form, the transaction sheet and its split
// rows. Owners are household data rows, so the list is short and the "+ New
// owner" affordance creates one inline rather than sending the user to Settings.
import { useState } from "react";
import { useCreateOwner, useOwners } from "@/api/hooks";
import { Button, Field, Input, Select, requiredText, useFieldId } from "@/components/form";

export default function OwnerSelect({
  label = "Owner",
  value,
  onChange,
  nullable = false,
  inheritFrom,
  allowCreate = true,
  testid,
  className,
}: {
  label?: string;
  /** null (or "") means "no owner set on this row". */
  value: string | null;
  onChange: (id: string | null) => void;
  /** Render the "Inherit (…)" option, which resolves to null. */
  nullable?: boolean;
  /** Name of the owner a null row falls back to; defaults to Shared. */
  inheritFrom?: string;
  /** Hide the inline create control (split rows, where it would repeat). */
  allowCreate?: boolean;
  testid: string;
  className?: string;
}) {
  const owners = useOwners();
  const create = useCreateOwner();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const id = useFieldId(`${testid}-select`);
  const nameId = useFieldId(`${testid}-new-name`);

  const list = owners.data ?? [];
  const shared = list.find((o) => o.kind === "shared");
  // A non-nullable picker has no empty option, so an unset account would render
  // as whatever option happens to be first — show Shared, which is what the
  // server assigns when owner_id is omitted.
  const shown = value || (nullable ? "" : (shared?.id ?? ""));

  const submitNew = () => {
    const v = requiredText(name);
    setErr(v);
    if (v) return;
    create.mutate(
      { name },
      {
        onSuccess: (owner) => {
          setName("");
          setAdding(false);
          // Pick what was just created: the user asked for it as this row's owner.
          onChange(owner.id);
        },
      },
    );
  };

  return (
    <Field label={label} htmlFor={id} className={className}>
      <Select
        id={id}
        value={shown}
        onChange={(e) => onChange(e.target.value || null)}
        data-testid={testid}
      >
        {nullable && (
          <option value="">Inherit ({inheritFrom ?? shared?.name ?? "Shared"})</option>
        )}
        {list.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </Select>

      {!allowCreate ? null : !adding ? (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="text-xs text-fg-muted underline hover:text-fg"
          data-testid={`${testid}-new`}
        >
          + New owner
        </button>
      ) : (
        <div className="flex items-start gap-2">
          <Input
            id={nameId}
            value={name}
            placeholder="New owner name"
            aria-label="New owner name"
            onChange={(e) => setName(e.target.value)}
            data-testid={`${testid}-new-name`}
          />
          <Button
            type="button"
            variant="secondary"
            className="px-2 py-2 text-xs"
            disabled={create.isPending}
            onClick={submitNew}
            data-testid={`${testid}-new-save`}
          >
            Add
          </Button>
        </div>
      )}
      {err && <p className="text-xs text-negative">{err}</p>}
      {create.isError && (
        <p className="text-xs text-negative" data-testid={`${testid}-new-error`}>
          {(create.error as Error).message}
        </p>
      )}
    </Field>
  );
}
