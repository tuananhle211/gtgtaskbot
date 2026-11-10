"use client";

/**
 * One person's month ("me" = oneself): every day with its shift's công, the
 * punches, late / early minutes (and what an approved request excused), the
 * leave taken, and the totals. HCNS fixes a day's punches ("Sửa giờ").
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, use, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { attendanceApi, days, monthOf, shiftMonth, type DayDetail } from "@/lib/hr-attendance";
import { formatDate } from "@/lib/datetime";
import { symbolTone, useMonthLabel } from "@/components/hr-attendance";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, SecondaryButton } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";

const CLOCK = /^([01]?\d|2[0-3]):[0-5]\d$/;

function EditDay({ userId, day, onClose }: { userId: string; day: DayDetail; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [checkIn, setCheckIn] = useState(day.in ?? "");
  const [checkOut, setCheckOut] = useState(day.out ?? "");
  const [note, setNote] = useState(day.note ?? "");
  const save = useMutation({
    mutationFn: () =>
      attendanceApi.editDay(userId, day.day, {
        check_in: checkIn || null,
        check_out: checkOut || null,
        note: note.trim(),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["hr-attendance"] });
      onClose();
    },
  });
  const valid = (!checkIn || CLOCK.test(checkIn)) && (!checkOut || CLOCK.test(checkOut)) && Boolean(note.trim());
  const field = "mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3";
  return (
    <Modal open onClose={onClose} title={`Sửa giờ chấm công ${formatDate(day.day)}`} busy={save.isPending}>
      <ModalBody className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Giờ vào (HH:mm)
            <input value={checkIn} onChange={(event) => setCheckIn(event.target.value)} placeholder="08:00" className={field} />
          </label>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Giờ ra (HH:mm)
            <input value={checkOut} onChange={(event) => setCheckOut(event.target.value)} placeholder="17:30" className={field} />
          </label>
        </div>
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Lý do sửa
          <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Quên chấm ra…" className={field} />
        </label>
        <p className="text-xs text-[var(--text-muted)]">Ngày đã sửa tay được giữ nguyên khi nhập lại file máy chấm công.</p>
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Huỷ
        </SecondaryButton>
        <ConfirmButton
          spec={{
            title: `Lưu giờ ${checkIn || "--"} → ${checkOut || "--"}?`,
            description: `Ngày ${formatDate(day.day)} · ${note}`,
            confirmLabel: "Lưu",
          }}
          disabled={!valid}
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

function PersonScreen({ id }: { id: string }) {
  const router = useRouter();
  const params = useSearchParams();
  const month = params.get("month") ?? monthOf();
  const label = useMonthLabel(month);
  const [editing, setEditing] = useState<DayDetail | null>(null);
  const sheet = useQuery({
    queryKey: ["hr-attendance", "person", id, month],
    queryFn: () => attendanceApi.person(id, month),
  });
  if (sheet.isPending) return <Loading label="Đang tính bảng công…" />;
  if (sheet.isError) return <ErrorBox error={sheet.error} onRetry={() => sheet.refetch()} />;
  const data = sheet.data;
  const t = data.totals;
  const go = (value: string) => router.replace(`/hr/timesheet/${id}?month=${value}`);
  const tiles: [string, string][] = [
    ["Công chuẩn", days(t.standard)],
    ["Đi làm", days(t.worked)],
    ["Nghỉ có lương", days(t.paid_leave)],
    ["Nghỉ không lương", days(t.unpaid_leave)],
    ["Lễ", days(t.holiday)],
    ["Vắng", days(t.absent)],
    ["Công tính lương", days(t.payable)],
    ["Đi muộn", t.late_count ? `${t.late_count} lần · ${t.late_minutes}′` : "0"],
    ["Về sớm", t.early_count ? `${t.early_count} lần · ${t.early_minutes}′` : "0"],
    ["Thiếu giờ vào/ra", String(t.missing)],
  ];
  return (
    <div className="space-y-4">
      {id !== "me" ? (
        <Link href={`/hr/timesheet?month=${month}`} className="text-sm text-[var(--accent)] hover:underline">
          ← Bảng công
        </Link>
      ) : null}
      <PageHeader
        title={id === "me" ? "Bảng công của tôi" : data.name}
        subtitle={`${data.member_code ?? ""} · ${data.departments.join(", ")} · Ca ${data.shift}`}
      />
      <div className="flex items-center gap-2">
        <SecondaryButton type="button" onClick={() => go(shiftMonth(month, -1))} aria-label="Tháng trước">
          ‹
        </SecondaryButton>
        <span className="min-w-28 text-center font-semibold">{label}</span>
        <SecondaryButton type="button" onClick={() => go(shiftMonth(month, 1))} aria-label="Tháng sau">
          ›
        </SecondaryButton>
        {data.locked ? <Pill tone="neutral">Đã khoá</Pill> : null}
      </div>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {tiles.map(([name, value]) => (
          <div key={name} className="panel p-3">
            <dt className="text-xs text-[var(--text-muted)]">{name}</dt>
            <dd className="mt-1 text-xl font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="panel table-scroll-x">
        <table className="table-dense min-w-[48rem]">
          <thead>
            <tr>
              <th>Ngày</th>
              <th>Ký hiệu</th>
              <th>Vào</th>
              <th>Ra</th>
              <th className="text-right">Công</th>
              <th>Muộn / sớm</th>
              <th>Nghỉ</th>
              <th>Ghi chú</th>
              {data.can_manage && !data.locked ? <th /> : null}
            </tr>
          </thead>
          <tbody>
            {data.days.map((day) => (
              <tr key={day.day} data-testid="day-row" className={day.weekday === "CN" ? "bg-[var(--surface-muted)]" : ""}>
                <td className="whitespace-nowrap">
                  {formatDate(day.day).slice(0, 5)} <span className="text-xs text-[var(--text-muted)]">{day.weekday}</span>
                </td>
                <td>
                  <span className={`inline-block min-w-8 rounded px-1 text-center text-xs ${symbolTone(day)}`}>
                    {day.symbol || " "}
                  </span>
                </td>
                <td className="tabular-nums">{day.in ?? "—"}</td>
                <td className="tabular-nums">{day.out ?? "—"}</td>
                <td className="text-right tabular-nums">{day.expected ? days(day.worked) : ""}</td>
                <td className="text-xs">
                  {day.late_raw ? (
                    <span className={day.late ? "text-[var(--bad)]" : "text-[var(--text-muted)]"}>
                      muộn {day.late_raw}′{day.late_excused ? ` (có đơn ${day.late_excused}′)` : ""}
                    </span>
                  ) : null}
                  {day.early_raw ? (
                    <span className={`block ${day.early ? "text-[var(--bad)]" : "text-[var(--text-muted)]"}`}>
                      sớm {day.early_raw}′{day.early_excused ? ` (có đơn ${day.early_excused}′)` : ""}
                    </span>
                  ) : null}
                  {day.missing ? <span className="text-[var(--bad)]">thiếu giờ vào / ra</span> : null}
                </td>
                <td className="text-xs">{day.leave_names.join(", ")}</td>
                <td className="text-xs text-[var(--text-muted)]">
                  {day.edited ? "Sửa tay. " : ""}
                  {day.note}
                </td>
                {data.can_manage && !data.locked ? (
                  <td>
                    <button
                      type="button"
                      onClick={() => setEditing(day)}
                      className="text-xs text-[var(--accent)] hover:underline"
                    >
                      Sửa giờ
                    </button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing ? <EditDay userId={data.user_id} day={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

export default function PersonTimesheetPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<Loading label="Đang tải bảng công…" />}>
      <PersonScreen id={id} />
    </Suspense>
  );
}
