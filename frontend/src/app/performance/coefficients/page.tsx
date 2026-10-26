"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type PointCoefficientRow } from "@/lib/api";
import { pointCoefficientConfirmation } from "@/lib/confirmations";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, SecondaryButton } from "@/components/pr";
import { MonthField, monthOf } from "@/components/performance";
import { Empty, ErrorBox, Loading } from "@/components/states";
import { formatDateTime } from "@/lib/datetime";
import { foldText } from "@/components/searchable-select";
import { monthLabel } from "@/lib/labels";

/**
 * "Hiệu suất › Hệ số điểm" (0076, the OWNER): each member's points
 * coefficient for the month on screen - 1 unless set. A change applies from
 * the month chosen on; the months before keep theirs. The reports show only
 * the points after the coefficient.
 */
export default function CoefficientsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Coefficients />
    </Suspense>
  );
}

/** "1.50" → "1,5"; "1" → "1". */
function showCoefficient(value: string): string {
  const number = Number(value);
  return Number.isFinite(number)
    ? new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(number)
    : value;
}

const isDefault = (value: string) => Number(value) === 1;

function Coefficients() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const month = params.get("month") || monthOf(new Date());
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<PointCoefficientRow | null>(null);
  const list = useQuery({
    queryKey: ["performance", "coefficients", month],
    queryFn: () => api.pointCoefficients(month),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const rows = useMemo(() => {
    const needle = foldText(search.trim());
    const all = list.data?.rows ?? [];
    if (!needle) return all;
    return all.filter((row) =>
      foldText([row.name, row.member_code ?? "", ...row.departments].join(" ")).includes(needle),
    );
  }, [list.data, search]);

  if (list.isPending) return <Loading label="Đang tải hệ số…" />;
  if (list.isError) {
    if (list.error instanceof ApiError && list.error.status === 403) {
      return (
        <section role="alert" className="panel p-6">
          <h1 className="text-lg font-semibold tracking-tight">Không xem được trang này</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">{list.error.message}</p>
        </section>
      );
    }
    return <ErrorBox error={list.error} onRetry={() => list.refetch()} />;
  }
  const changed = (list.data.rows ?? []).filter((row) => !isDefault(row.coefficient)).length;
  return (
    <div className="space-y-4">
      <PageHeader
        title="Hệ số điểm"
        subtitle={`${monthLabel(month)} · điểm của mỗi người được nhân hệ số của tháng; mặc định 1. ${changed} người đang khác 1.`}
      />
      <div className="panel flex flex-wrap items-center gap-4 p-3">
        <MonthField
          value={month}
          onChange={(value) => router.replace(`${pathname}?month=${value}`, { scroll: false })}
        />
        <label className="inline-flex min-w-0 flex-1 items-center gap-2 text-sm text-[var(--text-muted)]">
          Tìm
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Tên, mã nhân viên, phòng ban…"
            aria-label="Tìm nhân sự"
            className="min-h-10 w-full max-w-sm rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
          />
        </label>
      </div>
      <section className="panel overflow-hidden" aria-label="Hệ số từng người">
        {rows.length === 0 ? (
          <div className="p-4">
            <Empty message="Không có nhân sự nào khớp." />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[46rem] text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                  <th className="px-4 py-2 font-medium">Nhân sự</th>
                  <th className="px-4 py-2 text-right font-medium">Hệ số</th>
                  <th className="px-4 py-2 font-medium">Áp dụng từ</th>
                  <th className="px-4 py-2 font-medium">Ghi chú</th>
                  <th className="px-4 py-2 font-medium">
                    <span className="sr-only">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {rows.map((row) => (
                  <tr key={row.user_id}>
                    <td className="px-4 py-2.5">
                      <span className="font-medium">{row.name}</span>
                      {row.member_code ? (
                        <span className="ml-2 font-mono text-xs text-[var(--text-muted)]">{row.member_code}</span>
                      ) : null}
                      <p className="text-xs text-[var(--text-muted)]">{row.departments.join(", ") || "Chưa có phòng"}</p>
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <span
                        className={`inline-flex min-w-12 justify-center rounded-full border px-2.5 py-0.5 text-sm font-semibold tabular-nums ${
                          isDefault(row.coefficient)
                            ? "border-[var(--border)] text-[var(--text-muted)]"
                            : "border-[var(--accent)] text-[var(--accent-strong)]"
                        }`}
                      >
                        × {showCoefficient(row.coefficient)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-[var(--text-muted)]">
                      {row.effective_month ? monthLabel(row.effective_month) : "Mặc định"}
                      {row.next_month && row.next_coefficient ? (
                        <p className="text-xs text-[var(--warn)]">
                          Từ {monthLabel(row.next_month).toLowerCase()}: × {showCoefficient(row.next_coefficient)}
                        </p>
                      ) : null}
                    </td>
                    <td className="max-w-[16rem] truncate px-4 py-2.5 text-xs text-[var(--text-muted)]" title={row.note ?? ""}>
                      {row.note || "–"}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <SecondaryButton
                        type="button"
                        onClick={() => setEditing(row)}
                        aria-label={`Sửa hệ số của ${row.name}`}
                        className="min-h-9 px-3 text-xs"
                      >
                        Sửa hệ số
                      </SecondaryButton>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {editing ? <CoefficientDialog row={editing} month={month} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function CoefficientDialog({
  row,
  month,
  onClose,
}: {
  row: PointCoefficientRow;
  month: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(showCoefficient(row.coefficient));
  const [from, setFrom] = useState(month);
  const [note, setNote] = useState("");
  const history = useQuery({
    queryKey: ["performance", "coefficients", "history", row.user_id],
    queryFn: () => api.pointCoefficientHistory(row.user_id),
  });
  const save = useMutation({
    mutationFn: () =>
      api.setPointCoefficient(row.user_id, { coefficient: value.trim(), month: from, note: note.trim() || null }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["performance"] });
      onClose();
    },
  });
  const number = Number(value.trim().replace(",", "."));
  const valid = value.trim() !== "" && Number.isFinite(number) && number > 0 && number <= 10;
  return (
    <Modal
      open
      onClose={onClose}
      title={`Hệ số điểm · ${row.name}`}
      description="Điểm của người này từ tháng chọn trở đi được nhân hệ số; các tháng trước giữ hệ số cũ."
      busy={save.isPending}
    >
      <ModalBody>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm font-medium">
            Hệ số
            <input
              data-autofocus
              inputMode="decimal"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              aria-describedby="coefficient-hint"
              className="mt-1 min-h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm tabular-nums text-[var(--text)]"
            />
            <span id="coefficient-hint" className="mt-1 block text-xs font-normal text-[var(--text-muted)]">
              Từ 0,01 đến 10, tối đa 2 chữ số thập phân.
            </span>
          </label>
          <label className="block text-sm font-medium">
            Áp dụng từ tháng
            <input
              type="month"
              value={from}
              onChange={(event) => {
                if (event.target.value) setFrom(event.target.value);
              }}
              className="mt-1 min-h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
            />
          </label>
        </div>
        <label className="mt-3 block text-sm font-medium">
          Ghi chú
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={500}
            rows={2}
            placeholder="Lý do (không bắt buộc)"
            className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)]"
          />
        </label>
        <section aria-label="Lịch sử hệ số" className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">Lịch sử</h3>
          {history.data && history.data.length > 0 ? (
            <ul className="mt-2 divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
              {history.data.map((change) => (
                <li key={change.id} className="flex flex-wrap items-baseline justify-between gap-2 px-3 py-2 text-xs">
                  <span>
                    <span className="font-semibold tabular-nums">× {showCoefficient(change.coefficient)}</span>
                    <span className="text-[var(--text-muted)]"> từ {monthLabel(change.effective_month).toLowerCase()}</span>
                    {change.note ? <span className="text-[var(--text-muted)]"> · {change.note}</span> : null}
                  </span>
                  <span className="text-[var(--text-muted)]">
                    {change.created_by ?? "—"} · {formatDateTime(change.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-[var(--text-muted)]">
              {history.isPending ? "Đang tải…" : "Chưa đổi lần nào: hệ số mặc định 1."}
            </p>
          )}
        </section>
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Huỷ
        </SecondaryButton>
        <ConfirmButton
          spec={pointCoefficientConfirmation({
            name: row.name,
            coefficient: value.trim(),
            month: monthLabel(from),
          })}
          pending={save.isPending}
          error={save.error}
          disabled={!valid}
          onConfirm={() => save.mutate()}
        >
          Lưu hệ số
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}
