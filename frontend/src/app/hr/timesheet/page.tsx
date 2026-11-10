"use client";

/**
 * "Bảng công" (0100): the month, one row per person, one cell per day (X đi
 * làm, P phép, L lễ, V vắng, ! thiếu giờ…) and the totals. HCNS loads the time
 * clock's workbook ("Nhập file chấm công": preview, then load), locks the
 * month and exports it; a Trưởng phòng sees their departments' people;
 * everybody else goes straight to their own sheet.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  attendanceApi,
  days,
  importAttendance,
  monthOf,
  shiftMonth,
  timesheetExportUrl,
  type ImportReport,
} from "@/lib/hr-attendance";
import { hrApi } from "@/lib/hr";
import { formatDate } from "@/lib/datetime";
import { symbolTone, useMonthLabel } from "@/components/hr-attendance";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";

function ImportDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const preview = useMutation({
    mutationFn: (picked: File) => importAttendance(picked, false),
    onSuccess: setReport,
  });
  const load = useMutation({
    mutationFn: () => importAttendance(file as File, true),
    onSuccess: (done) => {
      setReport(done);
      void queryClient.invalidateQueries({ queryKey: ["hr-attendance"] });
    },
  });
  const fmt = (iso: string | null) => (iso ? formatDate(iso) : "—");
  return (
    <Modal open onClose={onClose} title="Nhập file chấm công" size="xl" busy={preview.isPending || load.isPending}>
      <ModalBody className="space-y-4">
        <p className="text-sm text-[var(--text-muted)]">
          File Excel xuất từ máy chấm công, mỗi cơ sở một tab (như “VP - T10”, “CS HN - T10”): dòng tiêu đề có
          <strong> Mã nhân viên, Ngày, Vào, Ra</strong>. Các tab khác tự bỏ qua. Ngày HCNS đã sửa tay được giữ nguyên.
        </p>
        <input
          ref={input}
          type="file"
          accept=".xlsx"
          aria-label="Chọn file chấm công"
          onChange={(event) => {
            const picked = event.target.files?.[0] ?? null;
            setFile(picked);
            setReport(null);
            if (picked) preview.mutate(picked);
          }}
          className="block w-full text-sm"
        />
        {preview.isPending ? <Loading label="Đang đọc file…" /> : null}
        {preview.isError ? <NoticeBox error={preview.error} /> : null}
        {report ? (
          <div className="space-y-3" data-testid="import-report">
            {report.committed ? (
              <p className="rounded-lg border border-[var(--good)]/30 bg-[var(--good-soft)] p-3 text-sm">
                Đã nhập {report.days} ngày công của {report.people} người.
              </p>
            ) : null}
            {report.problems.map((problem) => (
              <p key={problem} className="rounded-lg bg-[var(--bad-soft)] p-3 text-sm text-[var(--bad)]">
                {problem}
              </p>
            ))}
            <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <div>
                <dt className="text-xs text-[var(--text-muted)]">Khoảng ngày</dt>
                <dd className="font-medium">
                  {fmt(report.from)} - {fmt(report.to)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--text-muted)]">Người khớp mã</dt>
                <dd className="font-medium tabular-nums">{report.people}</dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--text-muted)]">Ngày công</dt>
                <dd className="font-medium tabular-nums">
                  {report.days} ({report.new_days} mới, {report.updated_days} cập nhật)
                </dd>
              </div>
              <div>
                <dt className="text-xs text-[var(--text-muted)]">Giữ ngày đã sửa tay</dt>
                <dd className="font-medium tabular-nums">{report.kept_manual}</dd>
              </div>
            </dl>
            <ul className="text-sm">
              {report.sheets.map((sheet) => (
                <li key={sheet.name}>
                  {sheet.read ? "✓" : "–"} <strong>{sheet.name}</strong>:{" "}
                  {sheet.read ? `${sheet.rows} dòng` : (sheet.reason ?? "bỏ qua")}
                  {sheet.bad_rows ? `, ${sheet.bad_rows} dòng thiếu mã / ngày` : ""}
                </li>
              ))}
            </ul>
            {report.locked.length ? (
              <p className="rounded-lg bg-[var(--bad-soft)] p-3 text-sm text-[var(--bad)]">
                Tháng {report.locked.join(", ")} đã khoá: mở khoá trước khi nhập.
              </p>
            ) : null}
            {report.unmatched.length ? (
              <details className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
                <summary>
                  {report.unmatched.length} mã nhân viên không có trên APM (bỏ qua{" "}
                  {report.unmatched.reduce((sum, item) => sum + item.rows, 0)} dòng)
                </summary>
                <p className="mt-2 font-mono text-xs">
                  {report.unmatched.map((item) => `${item.code} (${item.rows})`).join(", ")}
                </p>
              </details>
            ) : null}
          </div>
        ) : null}
        {load.isError ? <NoticeBox error={load.error} /> : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          {report?.committed ? "Đóng" : "Huỷ"}
        </SecondaryButton>
        {!report?.committed ? (
          <ConfirmButton
            spec={{
              title: `Nhập ${report?.days ?? 0} ngày công?`,
              description: `${report?.people ?? 0} người, ${fmt(report?.from ?? null)} - ${fmt(report?.to ?? null)}. Giờ vào/ra đã có sẽ được thay bằng file (trừ ngày sửa tay).`,
              confirmLabel: "Nhập",
            }}
            disabled={!file || !report || report.days === 0 || report.locked.length > 0 || report.problems.length > 0}
            onConfirm={() => load.mutate()}
            pending={load.isPending}
            error={load.error}
            tone="primary"
          >
            Nhập vào bảng công
          </ConfirmButton>
        ) : null}
      </ModalFooter>
    </Modal>
  );
}

function TimesheetScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const month = params.get("month") ?? monthOf();
  const [department, setDepartment] = useState("");
  const [q, setQ] = useState("");
  const [importing, setImporting] = useState(false);
  const queryClient = useQueryClient();
  const label = useMonthLabel(month);
  const access = useQuery({ queryKey: ["hr-attendance", "access"], queryFn: attendanceApi.access });
  const hr = useQuery({ queryKey: ["hr", "access"], queryFn: hrApi.access });
  const lists = access.data ? access.data.lists || access.data.timesheet : undefined;
  useEffect(() => {
    if (lists === false) router.replace(`/hr/timesheet/me?month=${month}`);
  }, [lists, month, router]);
  const sheet = useQuery({
    queryKey: ["hr-attendance", "month", month, department, q],
    queryFn: () => attendanceApi.month({ month, department_id: department || undefined, q: q || undefined }),
    enabled: Boolean(lists),
  });
  const lock = useMutation({
    mutationFn: (locked: boolean) => attendanceApi.lock(month, locked),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["hr-attendance"] }),
  });
  const go = (value: string) => router.replace(`/hr/timesheet?month=${value}`);
  if (lists === false) return <Loading />;
  const data = sheet.data;
  return (
    <div className="space-y-4">
      <PageHeader
        title="Bảng công"
        subtitle="Giờ vào/ra từ máy chấm công, trừ ngày lễ và đơn đã duyệt. Bấm tên để xem chi tiết từng ngày."
        action={
          data?.can_manage ? (
            <PrimaryButton type="button" onClick={() => setImporting(true)}>
              Nhập file chấm công
            </PrimaryButton>
          ) : undefined
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <SecondaryButton type="button" onClick={() => go(shiftMonth(month, -1))} aria-label="Tháng trước">
          ‹
        </SecondaryButton>
        <span className="min-w-28 text-center font-semibold">{label}</span>
        <SecondaryButton type="button" onClick={() => go(shiftMonth(month, 1))} aria-label="Tháng sau">
          ›
        </SecondaryButton>
        {data?.locked ? <Pill tone="neutral">Đã khoá</Pill> : null}
        <Select value={department} onChange={(event) => setDepartment(event.target.value)} aria-label="Phòng ban">
          <option value="">Mọi phòng ban</option>
          {(hr.data?.choices.departments ?? []).map((item) => (
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
        <div className="ml-auto flex flex-wrap gap-2">
          <a
            href={timesheetExportUrl(month, department || undefined)}
            className="inline-flex min-h-11 items-center rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm hover:bg-[var(--surface-muted)]"
          >
            Xuất Excel
          </a>
          {data?.can_manage ? (
            <ConfirmButton
              spec={{
                title: data.locked ? `Mở khoá bảng công ${label.toLowerCase()}?` : `Khoá bảng công ${label.toLowerCase()}?`,
                description: data.locked
                  ? "Mở khoá thì nhập file, sửa giờ, gửi / huỷ đơn trong tháng được trở lại."
                  : "Khoá rồi thì không nhập file, sửa giờ, gửi hay huỷ đơn trong tháng này nữa.",
                confirmLabel: data.locked ? "Mở khoá" : "Khoá",
              }}
              onConfirm={() => lock.mutate(!data.locked)}
              pending={lock.isPending}
              error={lock.error}
              tone="secondary"
            >
              {data.locked ? "Mở khoá tháng" : "Khoá tháng"}
            </ConfirmButton>
          ) : null}
        </div>
      </div>
      {sheet.isPending ? (
        <Loading label="Đang tính bảng công…" />
      ) : sheet.isError ? (
        <ErrorBox error={sheet.error} onRetry={() => sheet.refetch()} />
      ) : data ? (
        <>
          <div className="panel table-scroll-x" data-testid="timesheet-grid">
            <table className="table-dense text-xs [&_td]:px-1 [&_th]:px-1">
              <thead>
                <tr>
                  <th className="sticky left-0 z-10 min-w-48 bg-[var(--surface)] text-left">Nhân viên</th>
                  {data.days.map((day) => (
                    <th
                      key={day.day}
                      title={day.holiday ?? undefined}
                      className={`min-w-8 text-center ${day.weekday === "CN" ? "bg-[var(--surface-muted)]" : ""}`}
                    >
                      {Number(day.day.slice(8))}
                      <span className="block font-normal">{day.weekday}</span>
                    </th>
                  ))}
                  <th className="text-right">Công chuẩn</th>
                  <th className="text-right">Đi làm</th>
                  <th className="text-right">Nghỉ có lương</th>
                  <th className="text-right">Không lương</th>
                  <th className="text-right">Lễ</th>
                  <th className="text-right">Vắng</th>
                  <th className="text-right">Tính lương</th>
                  <th className="text-right">Muộn</th>
                  <th className="text-right">Sớm</th>
                  <th className="text-right">Thiếu</th>
                </tr>
              </thead>
              <tbody>
                {data.people.map((person) => (
                  <tr key={person.user_id} data-testid="timesheet-row">
                    <td className="sticky left-0 z-10 bg-[var(--surface)]">
                      <Link
                        href={`/hr/timesheet/${person.user_id}?month=${month}`}
                        className="font-medium text-[var(--accent)] hover:underline"
                      >
                        {person.name}
                      </Link>
                      <span className="block font-mono text-[var(--text-muted)]">{person.member_code}</span>
                    </td>
                    {person.days.map((cell) => (
                      <td
                        key={cell.day}
                        className={`text-center ${symbolTone(cell)}`}
                        title={[
                          cell.in || cell.out ? `${cell.in ?? "--"} → ${cell.out ?? "--"}` : "",
                          cell.late ? `muộn ${cell.late}′` : "",
                          cell.early ? `sớm ${cell.early}′` : "",
                          cell.edited ? "đã sửa tay" : "",
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      >
                        {cell.symbol}
                      </td>
                    ))}
                    <td className="text-right tabular-nums">{days(person.totals.standard)}</td>
                    <td className="text-right tabular-nums">{days(person.totals.worked)}</td>
                    <td className="text-right tabular-nums">{days(person.totals.paid_leave)}</td>
                    <td className="text-right tabular-nums">{days(person.totals.unpaid_leave)}</td>
                    <td className="text-right tabular-nums">{days(person.totals.holiday)}</td>
                    <td className="text-right tabular-nums">{days(person.totals.absent)}</td>
                    <td className="text-right font-semibold tabular-nums">{days(person.totals.payable)}</td>
                    <td className="text-right tabular-nums">
                      {person.totals.late_count ? `${person.totals.late_count} (${person.totals.late_minutes}′)` : "—"}
                    </td>
                    <td className="text-right tabular-nums">
                      {person.totals.early_count
                        ? `${person.totals.early_count} (${person.totals.early_minutes}′)`
                        : "—"}
                    </td>
                    <td className={`text-right tabular-nums ${person.totals.missing ? "text-[var(--bad)]" : ""}`}>
                      {person.totals.missing || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-muted)]">
            {data.symbols
              .filter((item) => item.symbol !== "")
              .map((item) => (
                <span key={item.symbol}>
                  <strong className="text-[var(--text)]">{item.symbol}</strong> {item.label}
                </span>
              ))}
          </p>
        </>
      ) : null}
      {importing ? <ImportDialog onClose={() => setImporting(false)} /> : null}
    </div>
  );
}

export default function TimesheetPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải bảng công…" />}>
      <TimesheetScreen />
    </Suspense>
  );
}
