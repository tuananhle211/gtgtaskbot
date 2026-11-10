"use client";

/**
 * "Ca & ngày lễ" (0100, HCNS - ``TIMESHEET``): the working shifts (per weekday
 * a morning and an afternoon, minutes of grace, for which departments /
 * branches; one is the default), the public holidays of a year, the kinds of
 * leave (paid or not, taken from annual leave or not, their letter).
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { attendanceApi, type LeaveType, type Shift, type ShiftWeek } from "@/lib/hr-attendance";
import { hrApi } from "@/lib/hr";
import { formatDate } from "@/lib/datetime";
import { ConfirmButton } from "@/components/confirm";
import { DateInput } from "@/components/date-input";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, SecondaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";

const WEEKDAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN"];
const SESSIONS = [
  ["morning", "Sáng"],
  ["afternoon", "Chiều"],
] as const;
const CLOCK = /^([01]?\d|2[0-3]):[0-5]\d$/;
const FIELD = "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3";

function weekText(week: ShiftWeek): string {
  const parts: string[] = [];
  for (let day = 0; day < 7; day += 1) {
    const sessions = week[String(day)];
    if (!sessions) continue;
    const times = SESSIONS.flatMap(([key]) => (sessions[key] ? [`${sessions[key]![0]}-${sessions[key]![1]}`] : []));
    parts.push(`${WEEKDAYS[day]}: ${times.join(", ")}`);
  }
  return parts.join(" · ") || "Chưa có buổi làm";
}

function ShiftDialog({ shift, onClose }: { shift: Shift | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const hr = useQuery({ queryKey: ["hr", "access"], queryFn: hrApi.access });
  const access = useQuery({ queryKey: ["hr-attendance", "access"], queryFn: attendanceApi.access });
  const [name, setName] = useState(shift?.name ?? "");
  const [grace, setGrace] = useState(String(shift?.grace_minutes ?? 5));
  const [week, setWeek] = useState<Record<string, Record<string, [string, string] | null>>>(() => {
    const filled: Record<string, Record<string, [string, string] | null>> = {};
    for (let day = 0; day < 7; day += 1) {
      const sessions = shift?.week[String(day)] ?? {};
      filled[String(day)] = {
        morning: sessions.morning ?? null,
        afternoon: sessions.afternoon ?? null,
      };
    }
    return filled;
  });
  const [branches, setBranches] = useState<string[]>(shift?.branches ?? []);
  const [departments, setDepartments] = useState<string[]>(shift?.department_ids ?? []);
  const [isDefault, setIsDefault] = useState(shift?.is_default ?? false);
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        grace_minutes: Number(grace),
        week: Object.fromEntries(
          Object.entries(week).map(([day, sessions]) => [
            day,
            Object.fromEntries(Object.entries(sessions).filter(([, pair]) => pair !== null)),
          ]),
        ) as ShiftWeek,
        branches,
        department_ids: departments,
        ...(isDefault ? { is_default: true } : {}),
      };
      return shift ? attendanceApi.editShift(shift.id, body) : attendanceApi.addShift(body);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["hr-attendance"] });
      onClose();
    },
  });
  const allTimes = Object.values(week).flatMap((sessions) => Object.values(sessions).filter(Boolean) as [string, string][]);
  const valid =
    Boolean(name.trim()) && allTimes.length > 0 && allTimes.every(([a, b]) => CLOCK.test(a) && CLOCK.test(b)) && /^\d+$/.test(grace);
  const setTime = (day: string, key: string, index: 0 | 1, value: string) =>
    setWeek((current) => {
      const pair = current[day][key] ?? ["", ""];
      const next: [string, string] = index === 0 ? [value, pair[1]] : [pair[0], value];
      return { ...current, [day]: { ...current[day], [key]: next } };
    });
  const toggle = (day: string, key: string) =>
    setWeek((current) => ({
      ...current,
      [day]: {
        ...current[day],
        [key]: current[day][key] ? null : key === "morning" ? ["08:00", "12:00"] : ["13:30", "17:30"],
      },
    }));
  return (
    <Modal open onClose={onClose} title={shift ? `Sửa ca ${shift.name}` : "Thêm ca"} size="xl" busy={save.isPending}>
      <ModalBody className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Tên ca
            <input value={name} onChange={(event) => setName(event.target.value)} className={`${FIELD} mt-1`} />
          </label>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Cho phép trễ (phút)
            <input value={grace} onChange={(event) => setGrace(event.target.value)} inputMode="numeric" className={`${FIELD} mt-1`} />
          </label>
        </div>
        <table className="table-dense w-full text-sm">
          <thead>
            <tr>
              <th>Ngày</th>
              <th>Buổi sáng</th>
              <th>Buổi chiều</th>
            </tr>
          </thead>
          <tbody>
            {WEEKDAYS.map((label, day) => (
              <tr key={label}>
                <td className="font-medium">{label}</td>
                {SESSIONS.map(([key]) => {
                  const pair = week[String(day)][key];
                  return (
                    <td key={key}>
                      <div className="flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={Boolean(pair)}
                          onChange={() => toggle(String(day), key)}
                          aria-label={`${label} buổi ${key === "morning" ? "sáng" : "chiều"}`}
                        />
                        {pair ? (
                          <>
                            <input
                              value={pair[0]}
                              onChange={(event) => setTime(String(day), key, 0, event.target.value)}
                              className="min-h-9 w-16 rounded border border-[var(--border)] bg-[var(--surface)] px-1 text-center"
                              aria-label="Bắt đầu"
                            />
                            –
                            <input
                              value={pair[1]}
                              onChange={(event) => setTime(String(day), key, 1, event.target.value)}
                              className="min-h-9 w-16 rounded border border-[var(--border)] bg-[var(--surface)] px-1 text-center"
                              aria-label="Kết thúc"
                            />
                          </>
                        ) : (
                          <span className="text-xs text-[var(--text-muted)]">nghỉ</span>
                        )}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={isDefault} onChange={(event) => setIsDefault(event.target.checked)} />
          Ca mặc định (cho người không thuộc ca nào khác)
        </label>
        <fieldset>
          <legend className="text-xs font-medium text-[var(--text-muted)]">Áp dụng cho chi nhánh</legend>
          <div className="mt-1 flex flex-wrap gap-3 text-sm">
            {(access.data?.choices.branches ?? []).map((item) => (
              <label key={item.code} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={branches.includes(item.code)}
                  onChange={(event) =>
                    setBranches((current) =>
                      event.target.checked ? [...current, item.code] : current.filter((code) => code !== item.code),
                    )
                  }
                />
                {item.label}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend className="text-xs font-medium text-[var(--text-muted)]">Áp dụng cho phòng ban (ưu tiên hơn chi nhánh)</legend>
          <div className="mt-1 grid max-h-48 gap-1 overflow-y-auto text-sm sm:grid-cols-2">
            {(hr.data?.choices.departments ?? []).map((item) => (
              <label key={item.id} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={departments.includes(item.id)}
                  onChange={(event) =>
                    setDepartments((current) =>
                      event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id),
                    )
                  }
                />
                {item.name}
              </label>
            ))}
          </div>
        </fieldset>
        {save.isError ? <NoticeBox error={save.error} /> : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Huỷ
        </SecondaryButton>
        <ConfirmButton
          spec={{
            title: shift ? `Lưu ca ${name}?` : `Thêm ca ${name}?`,
            description: "Bảng công các tháng chưa khoá tính lại theo ca mới.",
            confirmLabel: "Lưu",
          }}
          disabled={!valid}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          Lưu ca
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

function Holidays() {
  const queryClient = useQueryClient();
  const [year, setYear] = useState(new Date().getFullYear());
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [name, setName] = useState("");
  const list = useQuery({ queryKey: ["hr-attendance", "holidays", year], queryFn: () => attendanceApi.holidays(year) });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["hr-attendance"] });
  const add = useMutation({
    mutationFn: () => attendanceApi.addHolidays({ start, end: end || null, name: name.trim() }),
    onSuccess: () => {
      setStart("");
      setEnd("");
      setName("");
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => attendanceApi.deleteHoliday(id), onSuccess: refresh });
  return (
    <section className="panel space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Ngày lễ</h2>
        <Select value={String(year)} onChange={(event) => setYear(Number(event.target.value))} aria-label="Năm">
          {[year - 1, year, year + 1].map((item) => (
            <option key={item} value={item}>
              Năm {item}
            </option>
          ))}
        </Select>
      </div>
      <div className="grid gap-2 sm:grid-cols-[10rem_10rem_1fr_auto] sm:items-end">
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Từ ngày
          <DateInput value={start} onChange={(event) => setStart(event.target.value)} className="mt-1" aria-label="Từ ngày lễ" />
        </label>
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Đến ngày
          <DateInput value={end} onChange={(event) => setEnd(event.target.value)} className="mt-1" aria-label="Đến ngày lễ" />
        </label>
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Tên
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Tết Nguyên đán" className={`${FIELD} mt-1`} />
        </label>
        <ConfirmButton
          spec={{
            title: `Thêm ngày lễ ${name}?`,
            description: `${start ? formatDate(start) : ""}${end ? ` - ${formatDate(end)}` : ""}: tính là ngày nghỉ có lương cho mọi người.`,
            confirmLabel: "Thêm",
          }}
          disabled={!start || !name.trim()}
          onConfirm={() => add.mutate()}
          pending={add.isPending}
          error={add.error}
          tone="primary"
        >
          Thêm
        </ConfirmButton>
      </div>
      {list.isPending ? (
        <Loading />
      ) : list.data?.items.length ? (
        <ul className="divide-y divide-[var(--border)] text-sm">
          {list.data.items.map((item) => (
            <li key={item.id} className="flex items-center justify-between gap-2 py-2">
              <span>
                <strong className="tabular-nums">{formatDate(item.day)}</strong> · {item.name}
              </span>
              <ConfirmButton
                spec={{ title: `Xoá ngày lễ ${formatDate(item.day)}?`, description: item.name, confirmLabel: "Xoá", variant: "destructive" }}
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
        <p className="text-sm text-[var(--text-muted)]">Chưa có ngày lễ nào trong năm {year}.</p>
      )}
    </section>
  );
}

function LeaveTypeRow({ item }: { item: LeaveType }) {
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (body: Partial<LeaveType>) => attendanceApi.editLeaveType(item.id, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["hr-attendance"] }),
  });
  const flag = (key: "paid" | "annual" | "active", label: string) => (
    <ConfirmButton
      spec={{
        title: `${item[key] ? "Bỏ" : "Đặt"} “${label}” cho ${item.name}?`,
        description: "Bảng công các tháng chưa khoá tính lại.",
        confirmLabel: "Lưu",
      }}
      onConfirm={() => save.mutate({ [key]: !item[key] })}
      pending={save.isPending}
      error={save.error}
      tone="secondary"
      className="!min-h-8 !px-2 text-xs"
    >
      {item[key] ? "Có" : "Không"}
    </ConfirmButton>
  );
  return (
    <tr>
      <td className="font-mono text-xs">{item.code}</td>
      <td className="font-medium">{item.name}</td>
      <td className="text-center font-semibold">{item.symbol}</td>
      <td>{flag("paid", "Có lương")}</td>
      <td>{flag("annual", "Trừ phép năm")}</td>
      <td className="tabular-nums">{item.max_days ?? "—"}</td>
      <td>{flag("active", "Đang dùng")}</td>
    </tr>
  );
}

function LeaveTypes() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: ["hr-attendance", "leave-types"], queryFn: attendanceApi.leaveTypes });
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const add = useMutation({
    mutationFn: () => attendanceApi.addLeaveType({ code: code.trim().toUpperCase(), name: name.trim(), paid: true }),
    onSuccess: () => {
      setCode("");
      setName("");
      void queryClient.invalidateQueries({ queryKey: ["hr-attendance"] });
    },
  });
  return (
    <section className="panel space-y-3 p-4">
      <h2 className="text-base font-semibold">Loại nghỉ</h2>
      {list.isPending ? (
        <Loading />
      ) : list.isError ? (
        <ErrorBox error={list.error} />
      ) : (
        <div className="table-scroll-x">
          <table className="table-dense min-w-[40rem] text-sm">
            <thead>
              <tr>
                <th>Mã</th>
                <th>Tên</th>
                <th>Ký hiệu</th>
                <th>Có lương</th>
                <th>Trừ phép năm</th>
                <th>Tối đa (ngày)</th>
                <th>Đang dùng</th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((item) => (
                <LeaveTypeRow key={item.id} item={item} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Mã
          <input value={code} onChange={(event) => setCode(event.target.value)} placeholder="HOC_TAP" className={`${FIELD} mt-1 w-36`} />
        </label>
        <label className="block flex-1 text-xs font-medium text-[var(--text-muted)]">
          Tên
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Nghỉ đi học" className={`${FIELD} mt-1`} />
        </label>
        <ConfirmButton
          spec={{ title: `Thêm loại nghỉ ${name}?`, description: "Mặc định có lương, không trừ phép năm.", confirmLabel: "Thêm" }}
          disabled={!code.trim() || !name.trim()}
          onConfirm={() => add.mutate()}
          pending={add.isPending}
          error={add.error}
          tone="secondary"
        >
          + Thêm loại nghỉ
        </ConfirmButton>
      </div>
    </section>
  );
}

export default function AttendanceSettingsPage() {
  const shifts = useQuery({ queryKey: ["hr-attendance", "shifts"], queryFn: attendanceApi.shifts });
  const [editing, setEditing] = useState<Shift | "new" | null>(null);
  return (
    <div className="space-y-4">
      <PageHeader
        title="Ca & ngày lễ"
        subtitle="Ca làm việc theo phòng ban / chi nhánh, ngày lễ và các loại nghỉ - dùng cho đơn từ và bảng công."
      />
      <section className="panel space-y-3 p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">Ca làm việc</h2>
          {shifts.data?.can_edit ? (
            <SecondaryButton type="button" onClick={() => setEditing("new")}>
              + Thêm ca
            </SecondaryButton>
          ) : null}
        </div>
        {shifts.isPending ? (
          <Loading />
        ) : shifts.isError ? (
          <ErrorBox error={shifts.error} onRetry={() => shifts.refetch()} />
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {shifts.data.items.map((shift) => (
              <li key={shift.id} className="flex flex-wrap items-start justify-between gap-2 py-3" data-testid="shift-row">
                <div className="min-w-0">
                  <p className="font-medium">
                    {shift.name} {shift.is_default ? <Pill tone="good">Mặc định</Pill> : null}{" "}
                    {!shift.active ? <Pill tone="neutral">Đã tắt</Pill> : null}
                  </p>
                  <p className="text-xs text-[var(--text-muted)]">{weekText(shift.week)}</p>
                  <p className="text-xs text-[var(--text-muted)]">
                    Cho phép trễ {shift.grace_minutes} phút
                    {shift.department_ids.length ? ` · ${shift.department_ids.length} phòng ban` : ""}
                    {shift.branches.length ? ` · chi nhánh ${shift.branches.join(", ")}` : ""}
                  </p>
                </div>
                {shifts.data.can_edit ? (
                  <SecondaryButton type="button" onClick={() => setEditing(shift)}>
                    Sửa
                  </SecondaryButton>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
      <Holidays />
      <LeaveTypes />
      {editing ? <ShiftDialog shift={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}
