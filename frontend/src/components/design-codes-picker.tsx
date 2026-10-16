"use client";

import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type DesignCode, type DesignCodeItem, type DesignCodePick } from "@/lib/api";
import { Select } from "@/components/pr";

/**
 * The "mã task design" picker: choose codes from the Design catalogue, each with a
 * quantity.
 *
 * The value is the order's own list - snapshots (`code_id`, `code`, `name`,
 * `quantity`), not catalogue rows - so a code that was retired after it was
 * picked still reads and may be kept or removed; only the "add" list is limited
 * to what the catalogue offers now. A quantity being retyped is held as 0 and
 * drawn empty: the list is then not valid (`designCodesValid`) and the dialog
 * that holds the picker keeps its confirm button off.
 */

/** The server's own limits (`domain/orders/design_codes.py`). */
export const MAX_DESIGN_QUANTITY = 999;
export const MAX_DESIGN_ITEMS = 30;

const FIELD =
  "min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-sm text-[var(--text)]";

const validQuantity = (quantity: number) =>
  Number.isInteger(quantity) && quantity >= 1 && quantity <= MAX_DESIGN_QUANTITY;

/**
 * Whether the list may be sent: every quantity is a whole number from 1, and
 * there is at least one code when one is required.
 */
export function designCodesValid(items: DesignCodeItem[], required: boolean): boolean {
  if (items.some((item) => !validQuantity(item.quantity))) return false;
  return items.length > 0 || !required;
}

/** The list as the action request wants it: the row and how many. */
export function toDesignCodePicks(items: DesignCodeItem[]): DesignCodePick[] {
  return items.map((item) => ({ code_id: item.code_id, quantity: item.quantity }));
}

export function DesignCodesPicker({
  value,
  onChange,
  options,
  required = false,
  hint,
  label = "Mã task design",
  loading = false,
}: {
  value: DesignCodeItem[];
  onChange: (next: DesignCodeItem[]) => void;
  /** The catalogue's active rows. */
  options: DesignCode[];
  /** At least one code is needed before the dialog may be confirmed. */
  required?: boolean;
  /** One line under the picker (the action's `hint`). */
  hint?: string | null;
  label?: string;
  /** The catalogue is still on its way: the "add" list says so. */
  loading?: boolean;
}) {
  const addId = useId();
  const chosen = new Set(value.map((item) => item.code_id));
  const addable = [...options]
    .filter((option) => option.active && !chosen.has(option.id))
    .sort((a, b) => a.sort_order - b.sort_order || a.code.localeCompare(b.code));
  const full = value.length >= MAX_DESIGN_ITEMS;

  const add = (id: string) => {
    const option = options.find((candidate) => candidate.id === id);
    if (!option || chosen.has(id)) return;
    onChange([
      ...value,
      { code_id: option.id, code: option.code, name: option.name, quantity: 1 },
    ]);
  };
  const setQuantity = (id: string, raw: string) => {
    const parsed = raw.trim() === "" ? 0 : Number(raw);
    onChange(
      value.map((item) =>
        item.code_id === id
          ? { ...item, quantity: Number.isFinite(parsed) ? parsed : 0 }
          : item,
      ),
    );
  };
  const remove = (id: string) => onChange(value.filter((item) => item.code_id !== id));

  return (
    <div role="group" aria-label={label} className="space-y-2 text-xs text-[var(--text)]">
      <label htmlFor={addId} className="block">
        {required ? `${label} (bắt buộc)` : label}
      </label>
      {value.length > 0 ? (
        <ul className="space-y-1.5">
          {value.map((item) => (
            <li
              key={item.code_id}
              data-testid="design-code-item"
              className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2"
            >
              <span className="min-w-0 flex-1 text-sm leading-snug">
                <span className="rounded bg-[var(--surface-muted)] px-1 py-0.5 font-mono text-[11px] font-semibold">
                  {item.code}
                </span>{" "}
                {item.name}
              </span>
              <input
                type="number"
                inputMode="numeric"
                min={1}
                max={MAX_DESIGN_QUANTITY}
                step={1}
                aria-label={`Số lượng · ${item.code}`}
                aria-invalid={!validQuantity(item.quantity)}
                value={item.quantity === 0 ? "" : item.quantity}
                onChange={(event) => setQuantity(item.code_id, event.target.value)}
                className={`${FIELD} w-20 ${
                  validQuantity(item.quantity) ? "" : "border-[var(--bad)]"
                }`}
              />
              <button
                type="button"
                onClick={() => remove(item.code_id)}
                aria-label={`Bỏ mã ${item.code}`}
                className="min-h-10 rounded-lg border border-[var(--border)] px-2.5 text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
              >
                Bỏ
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[var(--text-muted)]">Chưa chọn mã task design nào.</p>
      )}
      {value.some((item) => !validQuantity(item.quantity)) ? (
        <p role="status" className="font-medium text-[var(--bad)]">
          Số lượng mỗi mã là số nguyên từ 1 đến {MAX_DESIGN_QUANTITY}.
        </p>
      ) : null}
      <Select
        id={addId}
        value=""
        onChange={(event) => add(event.target.value)}
        disabled={loading || full || addable.length === 0}
        className="w-full text-sm"
      >
        <option value="">
          {loading
            ? "Đang tải bảng mã task design…"
            : full
              ? `Đã đủ ${MAX_DESIGN_ITEMS} mã`
              : addable.length === 0
                ? "Không còn mã nào để thêm"
                : "— thêm mã task design —"}
        </option>
        {addable.map((option) => (
          <option key={option.id} value={option.id}>
            {option.code} · {option.name}
          </option>
        ))}
      </Select>
      {hint ? <p className="text-[var(--text-muted)]">{hint}</p> : null}
    </div>
  );
}

/**
 * The picker with the catalogue it picks from: `GET /api/units/ADS/design-codes`
 * (active rows, readable by any ORD member). A catalogue that cannot be read
 * leaves the current list editable (quantities, removals) and says why nothing
 * can be added.
 */
export function DesignCodesInput(
  props: Omit<React.ComponentProps<typeof DesignCodesPicker>, "options" | "loading">,
) {
  const catalogue = useQuery({
    queryKey: ["units", "ADS", "design-codes"],
    queryFn: () => api.designCodes(),
  });
  return (
    <div className="space-y-1">
      <DesignCodesPicker
        {...props}
        options={catalogue.data?.codes ?? []}
        loading={catalogue.isPending}
      />
      {catalogue.isError ? (
        <p role="status" className="text-xs font-medium text-[var(--warn)]">
          Không tải được bảng mã task design nên chưa thêm được mã mới.
        </p>
      ) : null}
    </div>
  );
}
