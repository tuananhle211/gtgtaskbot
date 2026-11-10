"use client";

/**
 * "Phép năm" (0100): each person's annual leave of a year - entitled (12 + 1
 * per 5 years), earned so far, taken, waiting, left. HCNS adds / takes days
 * ("Điều chỉnh": leave taken before APM, corrections); a Trưởng phòng sees
 * their departments' people.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { attendanceApi, days, type BalanceRow } from "@/lib/hr-attendance";
import { hrApi } from "@/lib/hr";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, SecondaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";

function AdjustDialog({ row, year, onClose }: { row: BalanceRow; year: number; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const history = useQuery({
    queryKey: ["hr-attendance", "adjustments", row.user_id, year],
    queryFn: () => attendanceApi.adjustments(row.user_id, year),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["hr-attendance"] });
  const add = useMutation({
    mutationFn: () =>
      attendanceApi.adjust({ user_id: row.user_id, year, days: Number(value.replace(",", ".")), note: note.trim() }),
    onSuccess: () => {
      setValue("");
      setNote("");
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => attendanceApi.deleteAdjustment(id), onSuccess: refresh });
  const number = Number(value.replace(",", "."));
  const valid = value.trim() !== "" && Number.isFinite(number) && number !== 0 && Number.isInteger(number * 2);
  return (
    <Modal open onClose={onClose} title={`Điều chỉnh phép năm ${year}`} description={row.name} size="lg">
      <ModalBody className="space-y-4">
        <p className="text-sm">
          Còn lại hiện tại: <strong>{days(row.remaining)}</strong> ngày (tích luỹ {days(row.earned)}, đã nghỉ{" "}
          {days(row.used)}, điều chỉnh {days(row.adjustment)}).
        </p>
        <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Số ngày (+/-)
            <input
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="-2 hoặc 1,5"
              inputMode="decimal"
              className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3"
            />
          </label>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Lý do
            <input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Đã nghỉ trước khi dùng APM…"
              className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3"
            />
          </label>
        </div>
        <p className="text-xs text-[var(--text-muted)]">Số âm = trừ phép (đã nghỉ trước đó); số dương = cộng thêm. Theo nửa ngày.</p>
        {add.isError ? <NoticeBox error={add.error} /> : null}
        <div>
          <h3 className="text-sm font-semibold">Lịch sử điều chỉnh</h3>
          {history.isPending ? (
            <Loading />
          ) : history.data?.items.length ? (
            <ul className="mt-2 divide-y divide-[var(--border)] text-sm">
              {history.data.items.map((item) => (
                <li key={item.id} className="flex items-start justify-between gap-2 py-2">
                  <span>
                    <strong className="tabular-nums">
                      {item.days > 0 ? "+" : ""}
                      {days(item.days)}
                    </strong>{" "}
                    · {item.note}
                    <span className="block text-xs text-[var(--text-muted)]">
                      {item.created_by ?? ""} · {formatDateTime(item.created_at)}
                    </span>
                  </span>
                  <ConfirmButton
                    spec={{
                      title: "Xoá điều chỉnh này?",
                      description: `${item.days > 0 ? "+" : ""}${days(item.days)} ngày · ${item.note}`,
                      confirmLabel: "Xoá",
                      variant: "destructive",
                    }}
                    onConfirm={() => remove.mutate(item.id)}
                    pending={remove.isPending}
                    error={remove.error}
                    tone="secondary"
                  >
                    Xoá
                  </ConfirmButton>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-sm text-[var(--text-muted)]">Chưa có.</p>
          )}
        </div>
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Đóng
        </SecondaryButton>
        <ConfirmButton
          spec={{
            title: `${number > 0 ? "Cộng" : "Trừ"} ${days(Math.abs(number))} ngày phép năm ${year}?`,
            description: `${row.name} · ${note}`,
            confirmLabel: "Lưu",
          }}
          disabled={!valid || !note.trim()}
          onConfirm={() => add.mutate()}
          pending={add.isPending}
          error={add.error}
          tone="primary"
        >
          Lưu điều chỉnh
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

export default function BalancesPage() {
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear);
  const [department, setDepartment] = useState("");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<BalanceRow | null>(null);
  const access = useQuery({ queryKey: ["hr", "access"], queryFn: hrApi.access });
  const list = useQuery({
    queryKey: ["hr-attendance", "balances", year, department, q],
    queryFn: () => attendanceApi.balances({ year, department_id: department || undefined, q: q || undefined }),
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Phép năm"
        subtitle="12 ngày/năm theo luật, thêm 1 ngày mỗi 5 năm làm việc; cộng dần theo tháng từ ngày chính thức, không cộng dồn sang năm sau."
      />
      <div className="flex flex-wrap gap-2">
        <Select value={String(year)} onChange={(event) => setYear(Number(event.target.value))} aria-label="Năm">
          {[thisYear + 1, thisYear, thisYear - 1].map((item) => (
            <option key={item} value={item}>
              Năm {item}
            </option>
          ))}
        </Select>
        <Select value={department} onChange={(event) => setDepartment(event.target.value)} aria-label="Phòng ban">
          <option value="">Mọi phòng ban</option>
          {(access.data?.choices.departments ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </Select>
        <input
          value={q}
          onChange={(event) => setQ(event.target.value)}
          placeholder="Tìm tên, mã nhân viên"
          aria-label="Tìm"
          className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3"
        />
      </div>
      {list.isPending ? (
        <Loading label="Đang tính phép năm…" />
      ) : list.isError ? (
        <ErrorBox error={list.error} onRetry={() => list.refetch()} />
      ) : (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[60rem]">
            <thead>
              <tr>
                <th>Mã NV</th>
                <th>Họ tên</th>
                <th>Phòng ban</th>
                <th>Ngày chính thức</th>
                <th className="text-right">Cả năm</th>
                <th className="text-right">Tích luỹ</th>
                <th className="text-right">Điều chỉnh</th>
                <th className="text-right">Đã nghỉ</th>
                <th className="text-right">Chờ duyệt</th>
                <th className="text-right">Còn lại</th>
                {list.data.can_adjust ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((row) => (
                <tr key={row.user_id} data-testid="balance-row">
                  <td className="font-mono text-xs">{row.member_code}</td>
                  <td className="font-medium">{row.name}</td>
                  <td className="text-xs">{row.departments.join(", ")}</td>
                  <td className="text-xs">{row.official_on ? formatDate(row.official_on) : "—"}</td>
                  <td className="text-right tabular-nums">{days(row.entitlement)}</td>
                  <td className="text-right tabular-nums">{days(row.earned)}</td>
                  <td className="text-right tabular-nums">{row.adjustment ? days(row.adjustment) : "—"}</td>
                  <td className="text-right tabular-nums">{days(row.used)}</td>
                  <td className="text-right tabular-nums">{row.pending ? days(row.pending) : "—"}</td>
                  <td
                    className={`text-right font-semibold tabular-nums ${row.remaining < 0 ? "text-[var(--bad)]" : ""}`}
                  >
                    {days(row.remaining)}
                  </td>
                  {list.data.can_adjust ? (
                    <td>
                      <button
                        type="button"
                        onClick={() => setEditing(row)}
                        className="text-sm text-[var(--accent)] hover:underline"
                      >
                        Điều chỉnh
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing ? <AdjustDialog row={editing} year={year} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}
