"use client";

/**
 * "Sửa phí" of one BCLT row (Tài chính đợt 2), shared by Bảng kê and the order
 * page. With `marks` (the "BCLT new" pick-lists of sheet "GHI CHÚ") it also
 * picks Tình trạng 2 / Mới / Tình trạng / Up / Phân loại; "Theo gợi ý" = APM's.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { SecondaryButton, Select } from "@/components/pr";
import {
  FEE_LABELS,
  financeApi,
  money,
  type FeeKey,
  type LedgerRow,
  type MarkChoice,
  type MarkKey,
} from "@/lib/finance";

const FEE_KEYS: FeeKey[] = ["card_fee", "referral_fee", "car_fee", "cost"];
const field =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)]";

function dayLabel(day: string): string {
  return `${day.slice(8, 10)}/${day.slice(5, 7)}`;
}

export function FeeDialog({
  row,
  onClose,
  marks = [],
}: {
  row: LedgerRow;
  onClose: () => void;
  marks?: MarkChoice[];
}) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Record<FeeKey, string>>(() =>
    Object.fromEntries(
      FEE_KEYS.map((key) => [key, row.typed.includes(key) ? String(row.fees[key]) : ""]),
    ) as Record<FeeKey, string>,
  );
  const [note, setNote] = useState(row.note ?? "");
  const [picks, setPicks] = useState<Partial<Record<MarkKey, string>>>(() =>
    Object.fromEntries(
      marks.map((mark) => [mark.key, row.picked?.includes(mark.key) ? (row.marks?.[mark.key] ?? "") : ""]),
    ),
  );
  const save = useMutation({
    mutationFn: () =>
      financeApi.setFees({
        order_id: row.order_id!,
        day: row.day,
        ...Object.fromEntries(FEE_KEYS.map((key) => [key, values[key].trim() || null])),
        note: note.trim() || null,
        ...Object.fromEntries(marks.map((mark) => [mark.key, picks[mark.key] || null])),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
      onClose();
    },
  });
  return (
    <Modal
      open
      onClose={onClose}
      title={`${marks.length ? "Sửa dòng" : "Sửa phí"} ${row.order_code ?? ""} · ${dayLabel(row.day)}`}
      busy={save.isPending}
    >
      <ModalBody className="space-y-3">
        {marks.length ? (
          <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <legend className="mb-2 text-sm font-semibold">Phân loại dòng (như BCLT new)</legend>
            {marks.map((mark) => {
              const suggested = row.picked?.includes(mark.key) ? null : (row.marks?.[mark.key] ?? null);
              return (
                <label key={mark.key} className="block text-xs font-medium text-[var(--text-muted)]">
                  {mark.label}
                  <Select
                    aria-label={mark.label}
                    value={picks[mark.key] ?? ""}
                    onChange={(event) => setPicks({ ...picks, [mark.key]: event.target.value })}
                    className="mt-1 w-full"
                  >
                    <option value="">{suggested ? `Theo gợi ý (${suggested})` : "Để trống"}</option>
                    {mark.choices.map((choice) => (
                      <option key={choice} value={choice}>
                        {choice}
                      </option>
                    ))}
                  </Select>
                </label>
              );
            })}
          </fieldset>
        ) : null}
        <p className="text-sm text-[var(--text-muted)]">
          Để trống = dùng số tự tính (phí quẹt theo % phương thức, giá cost theo bảng dịch vụ).
        </p>
        {FEE_KEYS.map((key) => (
          <label key={key} className="block text-xs font-medium text-[var(--text-muted)]">
            {FEE_LABELS[key]} (tự tính: {money(row.auto[key])})
            <input
              inputMode="numeric"
              value={values[key]}
              onChange={(event) => setValues({ ...values, [key]: event.target.value })}
              placeholder={String(row.auto[key])}
              className={`mt-1 ${field}`}
            />
          </label>
        ))}
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Ghi chú
          <input value={note} onChange={(event) => setNote(event.target.value)} className={`mt-1 ${field}`} />
        </label>
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Huỷ
        </SecondaryButton>
        <ConfirmButton
          spec={{
            title: `Lưu ${marks.length ? "dòng" : "phí"} của ${row.order_code ?? "đơn"} ngày ${dayLabel(row.day)}?`,
            description: "Thực thu sau phí của dòng, ngày và tháng tính lại theo số mới.",
            confirmLabel: "Lưu",
          }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          {marks.length ? "Lưu dòng" : "Lưu phí"}
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

