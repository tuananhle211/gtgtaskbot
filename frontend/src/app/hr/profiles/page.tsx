"use client";

/**
 * "Quản lý hồ sơ" (0096, mock `QuanLyHoSoNhanSu.html`): every profile the
 * reader may see - HCNS everybody, a Trưởng phòng their departments' people.
 * Table or cards, search (mã, tên, email, SĐT), filters, column picker; HCNS
 * ticks rows for "Sửa hàng loạt" and "Gia hạn / thêm HĐ", adds a profile and
 * exports Excel.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { exportUrl, hrApi, type HrFilters, type HrRow } from "@/lib/hr";
import { formatDate } from "@/lib/datetime";
import { ConfirmButton } from "@/components/confirm";
import { DateInput } from "@/components/date-input";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import { ContractPill, FIELD, StatusPill } from "@/components/hr-profile";

type Column = { key: string; label: string; sensitive?: boolean; cell: (row: HrRow) => React.ReactNode };

const COLUMNS: Column[] = [
  { key: "departments", label: "Phòng ban", cell: (row) => row.departments.map((seat) => seat.name).join(", ") || "—" },
  { key: "job_title", label: "Chức danh", cell: (row) => row.job_title || "—" },
  { key: "branch", label: "Chi nhánh", cell: (row) => row.branch_label || "—" },
  { key: "entity", label: "Pháp nhân", cell: (row) => row.legal_entity_code || "—" },
  { key: "status", label: "Trạng thái", cell: (row) => <StatusPill status={row.employment_status} label={row.employment_status_label} /> },
  { key: "gender", label: "Giới tính", cell: (row) => row.gender_label || "—" },
  { key: "grade", label: "Cấp bậc", cell: (row) => row.grade || "—" },
  { key: "joined_on", label: "Ngày vào", cell: (row) => formatDate(row.joined_on) || "—" },
  { key: "seniority", label: "Thâm niên", cell: (row) => row.seniority || "—" },
  { key: "contract", label: "Hợp đồng", cell: (row) => <ContractPill contract={row.contract} /> },
  { key: "work_email", label: "Email công việc", cell: (row) => row.work_email || "—" },
  { key: "phone", label: "Điện thoại", sensitive: true, cell: (row) => row.phone || "—" },
  { key: "birth_date", label: "Ngày sinh", sensitive: true, cell: (row) => formatDate(row.birth_date ?? null) || "—" },
  { key: "id_masked", label: "CCCD", sensitive: true, cell: (row) => row.id_masked || "—" },
];
const DEFAULT_COLUMNS = ["departments", "job_title", "branch", "status", "contract"];
export default function HrProfilesPage() {
  const queryClient = useQueryClient();
  const access = useQuery({ queryKey: ["hr", "access"], queryFn: hrApi.access });
  const departments = useQuery({ queryKey: ["departments"], queryFn: api.departments, retry: false });
  const entities = useQuery({ queryKey: ["legal-entities"], queryFn: () => api.legalEntities() });
  const [filters, setFilters] = useState<HrFilters>({ page: 1, page_size: 50 });
  const [search, setSearch] = useState("");
  const [view, setView] = useState<"table" | "cards">("table");
  const [columns, setColumns] = useState<string[]>(DEFAULT_COLUMNS);
  const [picking, setPicking] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dialog, setDialog] = useState<"bulk" | "renew" | "add" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    const timer = setTimeout(() => setFilters((old) => ({ ...old, q: search || undefined, page: 1 })), 250);
    return () => clearTimeout(timer);
  }, [search]);
  const list = useQuery({
    queryKey: ["hr", "profiles", filters],
    queryFn: () => hrApi.profiles(filters),
    enabled: Boolean(access.data?.lists),
  });
  const set = (patch: HrFilters) => {
    setFilters((old) => ({ ...old, ...patch, page: patch.page ?? 1 }));
    setSelected(new Set());
  };
  const a = access.data;
  const shown = useMemo(
    () => COLUMNS.filter((column) => columns.includes(column.key) && (!column.sensitive || a?.sensitive)),
    [columns, a],
  );

  if (access.isPending) return <Loading label="Đang tải…" />;
  if (access.isError) return <ErrorBox error={access.error} onRetry={() => access.refetch()} />;
  if (!a!.lists) {
    return (
      <div className="space-y-4">
        <PageHeader title="Quản lý hồ sơ" />
        <p className="text-sm text-[var(--text-muted)]">
          Danh sách hồ sơ dành cho phòng Hành chính nhân sự và trưởng phòng. Xem{" "}
          <Link href="/hr/me" className="text-[var(--accent)] hover:underline">hồ sơ của bạn</Link>.
        </p>
      </div>
    );
  }
  const rows = list.data?.items ?? [];
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / list.data.page_size)) : 1;
  const allTicked = rows.length > 0 && rows.every((row) => selected.has(row.user_id));
  const done = (text: string) => {
    setDialog(null);
    setSelected(new Set());
    setNotice(text);
    void queryClient.invalidateQueries({ queryKey: ["hr"] });
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Quản lý hồ sơ"
        subtitle={a!.edit ? "Hồ sơ toàn công ty." : "Hồ sơ người trong các phòng bạn làm trưởng phòng."}
      />
      <section aria-label="Bộ lọc hồ sơ" className="panel flex flex-wrap items-end gap-3 p-3 sm:p-4">
        <label className="flex min-w-[14rem] flex-1 flex-col gap-1 text-xs text-[var(--text-muted)]">
          Tìm mã, tên, email, SĐT
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Ví dụ: D050" className={FIELD} />
        </label>
        <label className="flex min-w-[11rem] flex-col gap-1 text-xs text-[var(--text-muted)]">
          Phòng ban
          <Select aria-label="Lọc theo phòng ban" value={filters.department_id ?? ""} onChange={(event) => set({ department_id: event.target.value || undefined })}>
            <option value="">Tất cả</option>
            {(departments.data?.departments ?? []).filter((item) => item.active).map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </Select>
        </label>
        <label className="flex min-w-[9rem] flex-col gap-1 text-xs text-[var(--text-muted)]">
          Chi nhánh
          <Select aria-label="Lọc theo chi nhánh" value={filters.branch ?? ""} onChange={(event) => set({ branch: event.target.value || undefined })}>
            <option value="">Tất cả</option>
            {a!.choices.branches.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}
          </Select>
        </label>
        <label className="flex min-w-[9rem] flex-col gap-1 text-xs text-[var(--text-muted)]">
          Trạng thái
          <Select aria-label="Lọc theo trạng thái" value={filters.employment_status ?? ""} onChange={(event) => set({ employment_status: event.target.value || undefined })}>
            <option value="">Đang làm việc (mọi loại)</option>
            {a!.choices.statuses.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}
          </Select>
        </label>
        <label className="flex min-w-[8rem] flex-col gap-1 text-xs text-[var(--text-muted)]">
          Pháp nhân
          <Select aria-label="Lọc theo pháp nhân" value={filters.legal_entity_id ?? ""} onChange={(event) => set({ legal_entity_id: event.target.value || undefined })}>
            <option value="">Tất cả</option>
            {(entities.data?.entities ?? []).map((item) => <option key={item.id} value={item.id}>{item.code}</option>)}
          </Select>
        </label>
        <label className="flex min-w-[8rem] flex-col gap-1 text-xs text-[var(--text-muted)]">
          Giới tính
          <Select aria-label="Lọc theo giới tính" value={filters.gender ?? ""} onChange={(event) => set({ gender: event.target.value || undefined })}>
            <option value="">Tất cả</option>
            {a!.choices.genders.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}
          </Select>
        </label>
        <label className="flex min-w-[10rem] flex-col gap-1 text-xs text-[var(--text-muted)]">
          Hợp đồng
          <Select aria-label="Lọc theo hợp đồng" value={filters.contract ?? ""} onChange={(event) => set({ contract: event.target.value || undefined })}>
            <option value="">Tất cả</option>
            <option value="expiring">Sắp hết hạn (60 ngày)</option>
            <option value="expired">Đã hết hạn</option>
            <option value="none">Chưa có hợp đồng</option>
          </Select>
        </label>
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <p className="mr-auto text-sm text-[var(--text-muted)]">{list.data?.total ?? 0} hồ sơ</p>
        <SecondaryButton type="button" onClick={() => setView(view === "table" ? "cards" : "table")}>
          {view === "table" ? "Xem dạng thẻ" : "Xem dạng bảng"}
        </SecondaryButton>
        <SecondaryButton type="button" onClick={() => setPicking(true)}>Cột</SecondaryButton>
        {a!.export ? (
          <a href={exportUrl} className="inline-flex min-h-11 items-center rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm hover:bg-[var(--surface-muted)]">
            Xuất Excel
          </a>
        ) : null}
        {a!.edit ? <PrimaryButton type="button" onClick={() => setDialog("add")}>+ Thêm hồ sơ</PrimaryButton> : null}
      </div>

      {a!.edit && selected.size > 0 ? (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-[var(--accent)]/30 bg-[var(--accent-soft)] px-4 py-2 text-sm">
          <span className="mr-auto font-medium">Đã chọn {selected.size} hồ sơ</span>
          <SecondaryButton type="button" onClick={() => setDialog("bulk")}>Sửa hàng loạt</SecondaryButton>
          <SecondaryButton type="button" onClick={() => setDialog("renew")}>Gia hạn / thêm HĐ</SecondaryButton>
          <SecondaryButton type="button" onClick={() => setSelected(new Set())}>Bỏ chọn</SecondaryButton>
        </div>
      ) : null}

      {notice ? (
        <p role="status" className="rounded-xl border border-[var(--good)]/25 bg-[var(--good-soft)] px-4 py-3 text-sm font-medium text-[var(--good)]">{notice}</p>
      ) : null}

      {list.isPending ? (
        <Loading label="Đang tải hồ sơ…" />
      ) : list.isError ? (
        <ErrorBox error={list.error} onRetry={() => list.refetch()} />
      ) : rows.length === 0 ? (
        <p className="panel p-4 text-sm text-[var(--text-muted)]">Không có hồ sơ nào trong bộ lọc này.</p>
      ) : view === "table" ? (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[56rem] [&_td]:align-middle">
            <thead>
              <tr>
                {a!.edit ? (
                  <th className="w-10">
                    <input
                      type="checkbox"
                      aria-label="Chọn tất cả trên trang"
                      checked={allTicked}
                      onChange={(event) =>
                        setSelected(event.target.checked ? new Set(rows.map((row) => row.user_id)) : new Set())
                      }
                    />
                  </th>
                ) : null}
                <th>Nhân viên</th>
                {shown.map((column) => <th key={column.key}>{column.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.user_id} className={row.active ? undefined : "opacity-60"}>
                  {a!.edit ? (
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Chọn ${row.full_name}`}
                        checked={selected.has(row.user_id)}
                        onChange={(event) => {
                          const next = new Set(selected);
                          if (event.target.checked) next.add(row.user_id);
                          else next.delete(row.user_id);
                          setSelected(next);
                        }}
                      />
                    </td>
                  ) : null}
                  <td>
                    <Link href={`/hr/profiles/${row.user_id}`} className="font-medium hover:underline">{row.full_name}</Link>
                    <span className="block font-mono text-xs text-[var(--text-muted)]">
                      {row.member_code}
                      {row.missing_documents.length ? " · thiếu tài liệu" : ""}
                    </span>
                  </td>
                  {shown.map((column) => <td key={column.key}>{column.cell(row)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {rows.map((row) => (
            <Link key={row.user_id} href={`/hr/profiles/${row.user_id}`} className="panel block p-4 hover:bg-[var(--surface-muted)]">
              <p className="font-medium">{row.full_name}</p>
              <p className="font-mono text-xs text-[var(--text-muted)]">{row.member_code}</p>
              <p className="mt-1 text-sm">{row.job_title || "—"}</p>
              <p className="text-xs text-[var(--text-muted)]">
                {row.departments.map((seat) => seat.name).join(", ")} {row.branch_label ? `· ${row.branch_label}` : ""}
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <StatusPill status={row.employment_status} label={row.employment_status_label} />
                <ContractPill contract={row.contract} />
                {row.missing_documents.length ? <Pill tone="warn">Thiếu tài liệu</Pill> : null}
              </div>
            </Link>
          ))}
        </div>
      )}

      {pages > 1 ? (
        <div className="flex items-center justify-center gap-2">
          <SecondaryButton type="button" disabled={(filters.page ?? 1) <= 1} onClick={() => set({ page: (filters.page ?? 1) - 1 })}>‹ Trước</SecondaryButton>
          <span className="text-sm">Trang {filters.page ?? 1} / {pages}</span>
          <SecondaryButton type="button" disabled={(filters.page ?? 1) >= pages} onClick={() => set({ page: (filters.page ?? 1) + 1 })}>Sau ›</SecondaryButton>
        </div>
      ) : null}

      {picking ? (
        <Modal open onClose={() => setPicking(false)} title="Cột hiển thị">
          <ModalBody className="grid gap-2 sm:grid-cols-2">
            {COLUMNS.filter((column) => !column.sensitive || a!.sensitive).map((column) => (
              <label key={column.key} className="inline-flex min-h-9 items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={columns.includes(column.key)}
                  onChange={(event) => {
                    const next = event.target.checked
                      ? [...columns, column.key]
                      : columns.filter((key) => key !== column.key);
                    setColumns(next);
                  }}
                />
                {column.label}
              </label>
            ))}
          </ModalBody>
          <ModalFooter>
            <PrimaryButton type="button" onClick={() => setPicking(false)}>Xong</PrimaryButton>
          </ModalFooter>
        </Modal>
      ) : null}
      {dialog === "bulk" ? (
        <BulkDialog ids={[...selected]} onClose={() => setDialog(null)} onDone={done} />
      ) : null}
      {dialog === "renew" ? (
        <RenewDialog ids={[...selected]} onClose={() => setDialog(null)} onDone={done} />
      ) : null}
      {dialog === "add" ? <AddDialog onClose={() => setDialog(null)} onDone={done} /> : null}
    </div>
  );
}

function BulkDialog({ ids, onClose, onDone }: { ids: string[]; onClose: () => void; onDone: (text: string) => void }) {
  const access = useQuery({ queryKey: ["hr", "access"], queryFn: hrApi.access });
  const entities = useQuery({ queryKey: ["legal-entities"], queryFn: () => api.legalEntities() });
  const [fields, setFields] = useState<Record<string, string>>({});
  const sent = Object.fromEntries(Object.entries(fields).filter(([, value]) => value !== ""));
  const save = useMutation({
    mutationFn: () => hrApi.bulk(ids, sent),
    onSuccess: (result) => onDone(`Đã sửa ${result.changed} hồ sơ.`),
  });
  const a = access.data;
  const pick = (name: string, label: string, options: { value: string; label: string }[]) => (
    <label className="text-xs text-[var(--text-muted)]">
      {label}
      <Select value={fields[name] ?? ""} onChange={(event) => setFields((old) => ({ ...old, [name]: event.target.value }))} className="mt-1">
        <option value="">Giữ nguyên</option>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </Select>
    </label>
  );
  return (
    <Modal open onClose={onClose} busy={save.isPending} size="lg" title="Sửa hàng loạt" description={`${ids.length} hồ sơ đã chọn. Chỉ trường bạn chọn mới thay đổi.`}>
      <ModalBody className="grid gap-3 sm:grid-cols-2">
        {pick("branch", "Chi nhánh", (a?.choices.branches ?? []).map((item) => ({ value: item.code, label: item.label })))}
        {pick("legal_entity_id", "Pháp nhân", (entities.data?.entities ?? []).map((item) => ({ value: item.id, label: item.code })))}
        {pick("employment_status", "Trạng thái", (a?.choices.statuses ?? []).filter((item) => item.code !== "LEFT").map((item) => ({ value: item.code, label: item.label })))}
        {pick("work_form", "Hình thức làm việc", (a?.choices.work_forms ?? []).map((item) => ({ value: item, label: item })))}
        <label className="text-xs text-[var(--text-muted)]">
          Cấp bậc
          <input value={fields.grade ?? ""} onChange={(event) => setFields((old) => ({ ...old, grade: event.target.value }))} placeholder="Giữ nguyên" className={`${FIELD} mt-1`} />
        </label>
        <label className="text-xs text-[var(--text-muted)]">
          Chức danh
          <input value={fields.job_title ?? ""} onChange={(event) => setFields((old) => ({ ...old, job_title: event.target.value }))} placeholder="Giữ nguyên" className={`${FIELD} mt-1`} />
        </label>
        {save.isError ? <div className="sm:col-span-2"><NoticeBox error={save.error} /></div> : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose} disabled={save.isPending}>Huỷ</SecondaryButton>
        <ConfirmButton
          spec={{
            title: `Sửa ${ids.length} hồ sơ?`,
            description: `${Object.keys(sent).length} trường được đặt giống nhau cho mọi hồ sơ đã chọn.`,
            confirmLabel: "Sửa hàng loạt",
          }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={Object.keys(sent).length === 0}
          tone="primary"
        >
          Áp dụng
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

function RenewDialog({ ids, onClose, onDone }: { ids: string[]; onClose: () => void; onDone: (text: string) => void }) {
  const access = useQuery({ queryKey: ["hr", "access"], queryFn: hrApi.access });
  const [type, setType] = useState("");
  const [start, setStart] = useState("");
  const [signed, setSigned] = useState(false);
  const save = useMutation({
    mutationFn: () => hrApi.renew(ids, type, start || null, signed),
    onSuccess: (result) => onDone(`Đã thêm ${result.created} hợp đồng.`),
  });
  return (
    <Modal open onClose={onClose} busy={save.isPending} title="Gia hạn / thêm hợp đồng" description={`${ids.length} người đã chọn. Để trống ngày bắt đầu: tiếp ngay sau hợp đồng đang có, hoặc từ hôm nay.`}>
      <ModalBody className="space-y-3">
        <label className="block text-xs text-[var(--text-muted)]">
          Loại hợp đồng
          <Select value={type} onChange={(event) => setType(event.target.value)} className="mt-1">
            <option value="">— Chọn —</option>
            {(access.data?.choices.contract_types ?? []).map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}
          </Select>
        </label>
        <label className="block text-xs text-[var(--text-muted)]">
          Từ ngày (tuỳ chọn)
          <div className="mt-1">
            <DateInput value={start} onChange={(event) => setStart(event.target.value)} className={FIELD} aria-label="Từ ngày" />
          </div>
        </label>
        <label className="inline-flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" checked={signed} onChange={(event) => setSigned(event.target.checked)} /> Đã ký
        </label>
        {save.isError ? <NoticeBox error={save.error} /> : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose} disabled={save.isPending}>Huỷ</SecondaryButton>
        <ConfirmButton
          spec={{ title: `Thêm hợp đồng cho ${ids.length} người?`, description: "Mỗi người có thêm một hợp đồng mới; ngày kết thúc tự tính theo loại.", confirmLabel: "Thêm hợp đồng" }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={!type}
          tone="primary"
        >
          Thêm hợp đồng
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

function AddDialog({ onClose, onDone }: { onClose: () => void; onDone: (text: string) => void }) {
  const departments = useQuery({ queryKey: ["departments"], queryFn: api.departments });
  const access = useQuery({ queryKey: ["hr", "access"], queryFn: hrApi.access });
  const [form, setForm] = useState({ member_code: "", full_name: "", department_id: "", position: "MEMBER", job_title: "", branch: "", joined_on: "" });
  const [made, setMade] = useState<{ member_code: string; password: string } | null>(null);
  const save = useMutation({
    mutationFn: () =>
      hrApi.create({
        member_code: form.member_code,
        full_name: form.full_name,
        department_id: form.department_id,
        position: form.position,
        ...(form.job_title ? { job_title: form.job_title } : {}),
        ...(form.branch ? { branch: form.branch } : {}),
        ...(form.joined_on ? { joined_on: form.joined_on } : {}),
      }),
    onSuccess: (result) => setMade({ member_code: result.member_code, password: result.password }),
  });
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm((old) => ({ ...old, [key]: event.target.value }));
  if (made) {
    return (
      <Modal open onClose={() => onDone(`Đã thêm hồ sơ ${made.member_code}.`)} title="Đã tạo tài khoản">
        <ModalBody className="space-y-2 text-sm">
          <p>Gửi cho nhân viên (chỉ hiện một lần):</p>
          <p>Mã đăng nhập: <span className="font-mono font-semibold">{made.member_code}</span></p>
          <p>Mật khẩu tạm: <span className="font-mono font-semibold">{made.password}</span></p>
          <p className="text-xs text-[var(--text-muted)]">Lần đầu đăng nhập hệ thống bắt đổi mật khẩu.</p>
        </ModalBody>
        <ModalFooter>
          <PrimaryButton type="button" onClick={() => onDone(`Đã thêm hồ sơ ${made.member_code}.`)}>Xong</PrimaryButton>
        </ModalFooter>
      </Modal>
    );
  }
  return (
    <Modal open onClose={onClose} busy={save.isPending} size="lg" title="Thêm hồ sơ nhân viên" description="Tạo tài khoản (chưa liên kết Telegram), xếp phòng và mở hồ sơ.">
      <ModalBody className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-[var(--text-muted)]">Mã nhân viên<input value={form.member_code} onChange={set("member_code")} className={`${FIELD} mt-1 font-mono uppercase`} data-autofocus /></label>
        <label className="text-xs text-[var(--text-muted)]">Họ và tên<input value={form.full_name} onChange={set("full_name")} className={`${FIELD} mt-1`} /></label>
        <label className="text-xs text-[var(--text-muted)]">
          Phòng ban
          <Select value={form.department_id} onChange={set("department_id")} className="mt-1">
            <option value="">— Chọn —</option>
            {(departments.data?.departments ?? []).filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </Select>
        </label>
        <label className="text-xs text-[var(--text-muted)]">
          Vị trí
          <Select value={form.position} onChange={set("position")} className="mt-1">
            <option value="MEMBER">Nhân viên</option>
            <option value="HEAD">Trưởng phòng</option>
          </Select>
        </label>
        <label className="text-xs text-[var(--text-muted)]">Chức danh<input value={form.job_title} onChange={set("job_title")} className={`${FIELD} mt-1`} /></label>
        <label className="text-xs text-[var(--text-muted)]">
          Chi nhánh
          <Select value={form.branch} onChange={set("branch")} className="mt-1">
            <option value="">—</option>
            {(access.data?.choices.branches ?? []).map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}
          </Select>
        </label>
        <label className="text-xs text-[var(--text-muted)]">
          Ngày vào làm
          <div className="mt-1"><DateInput value={form.joined_on} onChange={set("joined_on")} className={FIELD} aria-label="Ngày vào làm" /></div>
        </label>
        {save.isError ? <div className="sm:col-span-2"><NoticeBox error={save.error} /></div> : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose} disabled={save.isPending}>Huỷ</SecondaryButton>
        <ConfirmButton
          spec={{ title: `Thêm ${form.full_name || "nhân viên"}?`, description: "Tạo tài khoản với mật khẩu tạm và xếp vào phòng đã chọn.", confirmLabel: "Thêm" }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={!form.member_code.trim() || !form.full_name.trim() || !form.department_id}
          tone="primary"
        >
          Thêm hồ sơ
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}
