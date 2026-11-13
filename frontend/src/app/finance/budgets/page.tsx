"use client";

/**
 * Tài chính › Ngân sách (đợt 3; B1 pháp nhân x phòng ban x mã chi x tháng, B2
 * phòng con cộng vào phòng cha, B4 file Excel mẫu, B5 đã thực hiện / đang chờ).
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ConfirmButton } from "@/components/confirm";
import { FIELD, MonthSwitch } from "@/components/finance";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, SecondaryButton, Select } from "@/components/pr";
import { Empty, ErrorBox, Loading } from "@/components/states";
import { monthOf } from "@/lib/hr-attendance";
import { money, spendApi, type BudgetLine } from "@/lib/finance";

function CellDialog({ month, line, onClose }: { month: string; line: BudgetLine | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const catalog = useQuery({ queryKey: ["finance", "spend-catalog"], queryFn: spendApi.catalog });
  const codes = useQuery({ queryKey: ["finance", "codes"], queryFn: spendApi.codes });
  const [entity, setEntity] = useState(line?.legal_entity_id ?? "");
  const [department, setDepartment] = useState(line?.department_id ?? "");
  const [code, setCode] = useState(line?.code ?? "");
  const [amount, setAmount] = useState(line ? String(line.budget) : "");
  const save = useMutation({
    mutationFn: () =>
      spendApi.setBudget({ month, legal_entity_id: entity, department_id: department, code, amount: amount.trim() || null }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
      onClose();
    },
  });
  return (
    <Modal open onClose={onClose} title={line ? "Sửa ngân sách" : "Thêm dòng ngân sách"} busy={save.isPending}>
      <ModalBody className="space-y-3">
        <Select aria-label="Pháp nhân" value={entity} onChange={(event) => setEntity(event.target.value)} className="w-full" disabled={Boolean(line)}>
          <option value="">Chọn pháp nhân</option>
          {(catalog.data?.entities ?? []).map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </Select>
        <Select aria-label="Phòng ban" value={department} onChange={(event) => setDepartment(event.target.value)} className="w-full" disabled={Boolean(line)}>
          <option value="">Chọn phòng ban</option>
          {(catalog.data?.departments ?? []).map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </Select>
        <Select aria-label="Mã ngân sách" value={code} onChange={(event) => setCode(event.target.value)} className="w-full" disabled={Boolean(line)}>
          <option value="">Chọn mã ngân sách</option>
          {(codes.data?.codes ?? []).filter((item) => item.leaf).map((item) => (
            <option key={item.code} value={item.code}>{item.code} - {item.name}</option>
          ))}
        </Select>
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Số tiền (để trống = bỏ dòng)
          <input aria-label="Số tiền ngân sách" inputMode="numeric" value={amount} onChange={(event) => setAmount(event.target.value)} className={`mt-1 ${FIELD}`} />
        </label>
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>Huỷ</SecondaryButton>
        <ConfirmButton
          spec={{ title: "Lưu ngân sách?", description: `${code} · tháng ${month.slice(5)}/${month.slice(0, 4)}`, confirmLabel: "Lưu" }}
          disabled={!entity || !department || !code}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          Lưu
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

export default function FinanceBudgetsPage() {
  const queryClient = useQueryClient();
  const [month, setMonth] = useState(monthOf());
  const [entity, setEntity] = useState("");
  const [editing, setEditing] = useState<BudgetLine | null | "new">(null);
  const [message, setMessage] = useState<string | null>(null);
  const [picked, setPicked] = useState<File | null>(null);
  const catalog = useQuery({ queryKey: ["finance", "spend-catalog"], queryFn: spendApi.catalog });
  const budgets = useQuery({
    queryKey: ["finance", "budgets", month, entity],
    queryFn: () => spendApi.budgets(month, entity || undefined),
    retry: false,
  });
  const importFile = useMutation({
    mutationFn: (file: File) => spendApi.importBudgets(file),
    onSuccess: (result) => {
      setPicked(null);
      setMessage(`Đã nhập ${result.lines} dòng.`);
      void queryClient.invalidateQueries({ queryKey: ["finance", "budgets"] });
    },
    onError: (error: Error) => setMessage(error.message),
  });
  const lines = budgets.data?.lines ?? [];
  const total = lines.reduce(
    (sum, line) => ({
      budget: sum.budget + line.budget,
      committed: sum.committed + line.committed,
      realized: sum.realized + line.realized,
      remaining: sum.remaining + line.remaining,
    }),
    { budget: 0, committed: 0, realized: 0, remaining: 0 },
  );
  return (
    <div className="space-y-5">
      <PageHeader
        title="Ngân sách"
        subtitle="Theo pháp nhân × phòng ban × mã chi × tháng. Phòng con tính vào phòng cha. Đã thực hiện: mua sắm khi nghiệm thu, khoản khác khi chi tiền."
      />
      <div className="flex flex-wrap items-end gap-3">
        <MonthSwitch month={month} onChange={setMonth} />
        <label className="block w-64 text-xs font-medium text-[var(--text-muted)]">
          Pháp nhân
          <Select value={entity} onChange={(event) => setEntity(event.target.value)} className="mt-1 w-full">
            <option value="">Tất cả</option>
            {(catalog.data?.entities ?? []).map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </Select>
        </label>
        {budgets.data?.can_edit ? (
          <>
            <SecondaryButton type="button" onClick={() => setEditing("new")}>+ Thêm dòng</SecondaryButton>
            <a href={spendApi.budgetTemplateUrl(month)} className="inline-flex min-h-11 items-center rounded-lg border border-[var(--border)] px-4 text-sm hover:bg-[var(--surface-muted)]">
              Tải file mẫu
            </a>
            <label className="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-[var(--border)] px-4 text-sm hover:bg-[var(--surface-muted)]">
              Chọn file Excel
              <input
                type="file"
                accept=".xlsx"
                className="sr-only"
                aria-label="Nhập file ngân sách"
                onChange={(event) => {
                  setMessage(null);
                  setPicked(event.target.files?.[0] ?? null);
                  event.target.value = "";
                }}
              />
            </label>
            {picked ? (
              <ConfirmButton
                spec={{
                  title: `Nhập "${picked.name}"?`,
                  description: "Dòng nào sai thì không nhập dòng nào. Ô số tiền để trống là bỏ dòng ngân sách đó.",
                  confirmLabel: "Nhập",
                }}
                onConfirm={() => importFile.mutate(picked)}
                pending={importFile.isPending}
                error={importFile.error}
                tone="primary"
              >
                Nhập {picked.name}
              </ConfirmButton>
            ) : null}
          </>
        ) : null}
      </div>
      {message ? <p className="whitespace-pre-line text-sm" role="status">{message}</p> : null}
      {budgets.isPending ? <Loading /> : null}
      {budgets.isError ? <ErrorBox error={budgets.error} onRetry={() => budgets.refetch()} /> : null}
      {budgets.data && !lines.length ? <Empty message="Tháng này chưa có ngân sách hay khoản chi." /> : null}
      {lines.length ? (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[60rem]">
            <thead>
              <tr>
                <th>Pháp nhân</th>
                <th>Phòng ban</th>
                <th>Mã chi</th>
                <th className="text-right">Ngân sách</th>
                <th className="text-right">Đang chờ</th>
                <th className="text-right">Đã thực hiện</th>
                <th className="text-right">Còn lại</th>
                {budgets.data?.can_edit ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={`${line.legal_entity_id}-${line.department_id}-${line.code}`} data-testid="budget-row">
                  <td>{line.legal_entity}</td>
                  <td>{line.department}</td>
                  <td>
                    <span className="font-medium">{line.code}</span>
                    <span className="block text-xs text-[var(--text-muted)]">{line.code_name}</span>
                  </td>
                  <td className="text-right tabular-nums">{money(line.budget)}</td>
                  <td className="text-right tabular-nums">{money(line.committed)}</td>
                  <td className="text-right tabular-nums">{money(line.realized)}</td>
                  <td className={`text-right font-medium tabular-nums ${line.remaining < 0 ? "text-[var(--bad)]" : ""}`}>{money(line.remaining)}</td>
                  {budgets.data?.can_edit ? (
                    <td>
                      <button type="button" className="text-xs text-[var(--accent)] hover:underline" onClick={() => setEditing(line)} aria-label={`Sửa ngân sách ${line.code}`}>
                        Sửa
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="font-semibold">
                <td colSpan={3}>Tổng</td>
                <td className="text-right tabular-nums">{money(total.budget)}</td>
                <td className="text-right tabular-nums">{money(total.committed)}</td>
                <td className="text-right tabular-nums">{money(total.realized)}</td>
                <td className="text-right tabular-nums">{money(total.remaining)}</td>
                {budgets.data?.can_edit ? <td /> : null}
              </tr>
            </tfoot>
          </table>
        </div>
      ) : null}
      {editing ? <CellDialog month={month} line={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}
