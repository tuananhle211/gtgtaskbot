"use client";

/** "Sửa phí" of one BCLT row (Tài chính đợt 2), shared by Bảng kê and the order page. */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { SecondaryButton } from "@/components/pr";
import { FEE_LABELS, financeApi, money, type FeeKey, type LedgerRow } from "@/lib/finance";

const FEE_KEYS: FeeKey[] = ["card_fee", "referral_fee", "car_fee", "cost"];
const field =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)]";

function dayLabel(day: string): string {
  return `${day.slice(8, 10)}/${day.slice(5, 7)}`;
}

export function FeeDialog({ row, onClose }: { row: LedgerRow; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Record<FeeKey, string>>(() =>
    Object.fromEntries(
      FEE_KEYS.map((key) => [key, row.typed.includes(key) ? String(row.fees[key]) : ""]),
    ) as Record<FeeKey, string>,
  );
  const [note, setNote] = useState(row.note ?? "");
  const save = useMutation({
    mutationFn: () =>
      financeApi.setFees({
        order_id: row.order_id!,
        day: row.day,
        ...Object.fromEntries(FEE_KEYS.map((key) => [key, values[key].trim() || null])),
        note: note.trim() || null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
      onClose();
    },
  });
  return (
    <Modal open onClose={onClose} title={`Sửa phí ${row.order_code ?? ""} · ${dayLabel(row.day)}`} busy={save.isPending}>
      <ModalBody className="space-y-3">
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
            title: `Lưu phí của ${row.order_code ?? "đơn"} ngày ${dayLabel(row.day)}?`,
            description: "Thực thu sau phí của dòng, ngày và tháng tính lại theo số mới.",
            confirmLabel: "Lưu",
          }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          Lưu phí
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

