"use client";

/**
 * "Nhân sự" (0096): one person's profile - the drawer of the mock
 * `QuanLyHoSoNhanSu.html` as a page. Tabs: Thông tin cá nhân · Thông tin công
 * việc · Hợp đồng lao động · Lương & phụ cấp · Bảo hiểm & thuế · Tài liệu đính
 * kèm. Sensitive tabs show only when the server sent them (oneself, HCNS +
 * the OWNER). HCNS edits each section in a dialog; a person asks HCNS to
 * change their own contact details ("Yêu cầu sửa").
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import {
  documentUrl,
  hrApi,
  money,
  uploadHrDocument,
  type HrAccess,
  type HrContract,
  type HrProfile,
} from "@/lib/hr";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { ConfirmButton } from "@/components/confirm";
import { DateInput } from "@/components/date-input";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, SecondaryButton, Select, TabStrip } from "@/components/pr";
import { DepartmentTag } from "@/components/department-tags";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";

export const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

type FieldKind = "text" | "textarea" | "date" | "select" | "money" | "int";
type FieldSpec = {
  name: string;
  label: string;
  kind?: FieldKind;
  options?: { value: string; label: string }[];
};

/** A label / value grid; blank values read "—". */
export function InfoGrid({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
      {rows.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
          <dd className="mt-0.5 break-words text-sm">
            {value === null || value === undefined || value === "" ? "—" : value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function StatusPill({ status, label }: { status: string; label: string }) {
  const tone =
    status === "WORKING"
      ? "good"
      : status === "LEFT"
        ? "bad"
        : status === "PROBATION"
          ? "neutral"
          : "warn";
  return <Pill tone={tone}>{label}</Pill>;
}

export function ContractPill({ contract }: { contract: HrProfile["contract"] }) {
  if (!contract) return <Pill tone="warn">Chưa có hợp đồng</Pill>;
  const tone =
    contract.state === "EXPIRED" || contract.state === "ENDED"
      ? "bad"
      : contract.days_left !== null && contract.days_left <= 30
        ? "warn"
        : "good";
  return (
    <Pill tone={tone}>
      {contract.state_label}
      {contract.state === "ACTIVE" && contract.days_left !== null
        ? ` · còn ${contract.days_left} ngày`
        : ""}
    </Pill>
  );
}

function text(value: string | number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

/** The value a field sends: blank = null, money / int as numbers. */
function parsed(spec: FieldSpec, raw: string): unknown {
  const value = raw.trim();
  if (!value) return null;
  if (spec.kind === "money") return Number(value.replace(/\D/g, "")) || 0;
  if (spec.kind === "int") return Number(value);
  return value;
}

/** One section of a profile, edited in a dialog; only changed keys are sent. */
function SectionEditor({
  title,
  userId,
  fields,
  initial,
  onClose,
  onSaved,
}: {
  title: string;
  userId: string;
  fields: FieldSpec[];
  initial: Record<string, string>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(initial);
  const changes = useMemo(() => {
    const out: Record<string, unknown> = {};
    for (const spec of fields) {
      if ((values[spec.name] ?? "") !== (initial[spec.name] ?? "")) {
        out[spec.name] = parsed(spec, values[spec.name] ?? "");
      }
    }
    return out;
  }, [values, initial, fields]);
  const save = useMutation({
    mutationFn: () => hrApi.update(userId, changes),
    onSuccess: () => onSaved(),
  });
  const dirty = Object.keys(changes).length > 0;
  return (
    <Modal open onClose={onClose} busy={save.isPending} size="xl" title={title}>
      <ModalBody>
        <div className="grid gap-3 sm:grid-cols-2">
          {fields.map((spec) => {
            const value = values[spec.name] ?? "";
            const set = (next: string) => setValues((old) => ({ ...old, [spec.name]: next }));
            const id = `hr-${spec.name}`;
            return (
              <div
                key={spec.name}
                className={spec.kind === "textarea" ? "sm:col-span-2" : undefined}
              >
                <label htmlFor={id} className="text-xs text-[var(--text-muted)]">
                  {spec.label}
                </label>
                <div className="mt-1">
                  {spec.kind === "date" ? (
                    <DateInput
                      id={id}
                      value={value}
                      onChange={(event) => set(event.target.value)}
                      className={FIELD}
                    />
                  ) : spec.kind === "select" ? (
                    <Select id={id} value={value} onChange={(event) => set(event.target.value)}>
                      <option value="">—</option>
                      {(spec.options ?? []).map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </Select>
                  ) : spec.kind === "textarea" ? (
                    <textarea
                      id={id}
                      value={value}
                      rows={3}
                      onChange={(event) => set(event.target.value)}
                      className={`${FIELD} py-2`}
                    />
                  ) : (
                    <input
                      id={id}
                      value={
                        spec.kind === "money" && value
                          ? new Intl.NumberFormat("vi-VN").format(Number(value.replace(/\D/g, "")))
                          : value
                      }
                      inputMode={spec.kind === "money" || spec.kind === "int" ? "numeric" : undefined}
                      onChange={(event) =>
                        set(
                          spec.kind === "money"
                            ? event.target.value.replace(/\D/g, "")
                            : event.target.value,
                        )
                      }
                      className={FIELD}
                    />
                  )}
                </div>
              </div>
            );
          })}
        </div>
        {save.isError ? (
          <div className="mt-3">
            <NoticeBox error={save.error} />
          </div>
        ) : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose} disabled={save.isPending}>
          Huỷ
        </SecondaryButton>
        <ConfirmButton
          spec={{
            title: `Lưu ${title.toLowerCase()}?`,
            description: `${Object.keys(changes).length} thông tin thay đổi sẽ được ghi vào hồ sơ và lưu vết.`,
            confirmLabel: "Lưu",
          }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={!dirty}
          tone="primary"
        >
          Lưu
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

function ContractDialog({
  userId,
  contract,
  access,
  onClose,
  onSaved,
}: {
  userId: string;
  contract: HrContract | null;
  access: HrAccess;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [typeCode, setTypeCode] = useState(contract?.type_code ?? "");
  const [number, setNumber] = useState(contract?.number ?? "");
  const [start, setStart] = useState(contract?.start_on ?? "");
  const [end, setEnd] = useState(contract?.end_on ?? "");
  const [ended, setEnded] = useState(contract?.ended_on ?? "");
  const [signed, setSigned] = useState(contract?.signed ?? false);
  const [signedBy, setSignedBy] = useState(contract?.signed_by ?? "");
  const [salary, setSalary] = useState(text(contract?.salary));
  const [note, setNote] = useState(contract?.note ?? "");
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        type_code: typeCode,
        number: number || null,
        start_on: start || null,
        signed,
        signed_by: signedBy || null,
        ended_on: ended || null,
        note: note || null,
      };
      // Blank end = the type's length from the start (the server fills it).
      if (end || contract) body.end_on = end || null;
      if (access.sensitive) body.salary = salary ? Number(salary) : null;
      return contract
        ? hrApi.editContract(userId, contract.id, body)
        : hrApi.addContract(userId, body);
    },
    onSuccess: () => onSaved(),
  });
  return (
    <Modal
      open
      onClose={onClose}
      busy={save.isPending}
      size="lg"
      title={contract ? "Sửa hợp đồng" : "Thêm hợp đồng"}
      description="Để trống ngày kết thúc: hệ thống tự tính theo loại hợp đồng."
    >
      <ModalBody className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-[var(--text-muted)]">
          Loại hợp đồng
          <Select value={typeCode} onChange={(event) => setTypeCode(event.target.value)} className="mt-1">
            <option value="">— Chọn —</option>
            {access.choices.contract_types.map((type) => (
              <option key={type.code} value={type.code}>
                {type.name}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-xs text-[var(--text-muted)]">
          Số hợp đồng
          <input value={number} onChange={(event) => setNumber(event.target.value)} className={`${FIELD} mt-1`} />
        </label>
        <label className="text-xs text-[var(--text-muted)]">
          Từ ngày
          <div className="mt-1">
            <DateInput value={start} onChange={(event) => setStart(event.target.value)} className={FIELD} aria-label="Từ ngày" />
          </div>
        </label>
        <label className="text-xs text-[var(--text-muted)]">
          Đến ngày
          <div className="mt-1">
            <DateInput value={end} onChange={(event) => setEnd(event.target.value)} className={FIELD} aria-label="Đến ngày" />
          </div>
        </label>
        <label className="text-xs text-[var(--text-muted)]">
          Người ký
          <input value={signedBy} onChange={(event) => setSignedBy(event.target.value)} className={`${FIELD} mt-1`} />
        </label>
        <label className="text-xs text-[var(--text-muted)]">
          Chấm dứt ngày (nếu có)
          <div className="mt-1">
            <DateInput value={ended} onChange={(event) => setEnded(event.target.value)} className={FIELD} aria-label="Chấm dứt ngày" />
          </div>
        </label>
        {access.sensitive ? (
          <label className="text-xs text-[var(--text-muted)]">
            Lương trên hợp đồng (đồng)
            <input
              value={salary ? new Intl.NumberFormat("vi-VN").format(Number(salary)) : ""}
              inputMode="numeric"
              onChange={(event) => setSalary(event.target.value.replace(/\D/g, ""))}
              className={`${FIELD} mt-1`}
            />
          </label>
        ) : null}
        <label className="inline-flex min-h-11 items-center gap-2 self-end text-sm">
          <input type="checkbox" checked={signed} onChange={(event) => setSigned(event.target.checked)} />
          Đã ký
        </label>
        <label className="text-xs text-[var(--text-muted)] sm:col-span-2">
          Ghi chú
          <textarea value={note} rows={2} onChange={(event) => setNote(event.target.value)} className={`${FIELD} mt-1 py-2`} />
        </label>
        {save.isError ? (
          <div className="sm:col-span-2">
            <NoticeBox error={save.error} />
          </div>
        ) : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose} disabled={save.isPending}>
          Huỷ
        </SecondaryButton>
        <ConfirmButton
          spec={{
            title: contract ? "Lưu hợp đồng?" : "Thêm hợp đồng?",
            description: "Hợp đồng được ghi vào hồ sơ và lưu vết.",
            confirmLabel: "Lưu",
          }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={!typeCode}
          tone="primary"
        >
          Lưu
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

function RequestDialog({
  access,
  profile,
  onClose,
  onSaved,
}: {
  access: HrAccess;
  profile: HrProfile;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [field, setField] = useState(access.choices.request_fields[0]?.code ?? "phone");
  const current = text(
    profile.personal ? (profile.personal as Record<string, unknown>)[field] as string | null : null,
  );
  const [value, setValue] = useState("");
  const submit = useMutation({
    mutationFn: () => hrApi.submitRequest(field, value),
    onSuccess: () => onSaved(),
  });
  return (
    <Modal
      open
      onClose={onClose}
      busy={submit.isPending}
      title="Yêu cầu sửa thông tin"
      description="Phòng Hành chính nhân sự duyệt rồi mới đổi trên hồ sơ."
    >
      <ModalBody className="space-y-3">
        <label className="block text-xs text-[var(--text-muted)]">
          Thông tin
          <Select value={field} onChange={(event) => setField(event.target.value)} className="mt-1">
            {access.choices.request_fields.map((item) => (
              <option key={item.code} value={item.code}>
                {item.label}
              </option>
            ))}
          </Select>
        </label>
        <p className="text-sm">
          Hiện tại: <span className="font-medium">{current || "—"}</span>
        </p>
        <label className="block text-xs text-[var(--text-muted)]">
          Giá trị mới
          <input value={value} onChange={(event) => setValue(event.target.value)} className={`${FIELD} mt-1`} data-autofocus />
        </label>
        {submit.isError ? <NoticeBox error={submit.error} /> : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose} disabled={submit.isPending}>
          Huỷ
        </SecondaryButton>
        <ConfirmButton
          spec={{
            title: "Gửi yêu cầu sửa?",
            description: "Yêu cầu chờ phòng Hành chính nhân sự duyệt.",
            confirmLabel: "Gửi",
          }}
          onConfirm={() => submit.mutate()}
          pending={submit.isPending}
          error={submit.error}
          disabled={!value.trim()}
          tone="primary"
        >
          Gửi yêu cầu
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

function Documents({ profile, access, onChanged }: { profile: HrProfile; access: HrAccess; onChanged: () => void }) {
  const [kind, setKind] = useState(access.choices.document_kinds[0]?.code ?? "OTHER");
  const upload = useMutation({
    mutationFn: (file: File) => uploadHrDocument(profile.user_id, kind, file),
    onSuccess: () => onChanged(),
  });
  const remove = useMutation({
    mutationFn: (id: string) => hrApi.deleteDocument(id),
    onSuccess: () => onChanged(),
  });
  const kinds = new Map(access.choices.document_kinds.map((item) => [item.code, item.label]));
  return (
    <div className="space-y-4">
      {profile.missing_documents.length ? (
        <p className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
          Còn thiếu: {profile.missing_documents.map((code) => kinds.get(code) ?? code).join(", ")}
        </p>
      ) : null}
      {profile.can_edit ? (
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-[var(--text-muted)]">
            Loại tài liệu
            <Select value={kind} onChange={(event) => setKind(event.target.value)} className="mt-1">
              {access.choices.document_kinds.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.label}
                </option>
              ))}
            </Select>
          </label>
          <label className="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm hover:bg-[var(--surface-muted)]">
            {upload.isPending ? "Đang tải lên…" : "+ Tải tệp lên"}
            <input
              type="file"
              accept="application/pdf,image/jpeg,image/png,image/webp"
              className="sr-only"
              disabled={upload.isPending}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) upload.mutate(file);
                event.target.value = "";
              }}
            />
          </label>
          <span className="text-xs text-[var(--text-muted)]">
            PDF hoặc ảnh, tối đa {access.choices.max_document_mb} MB.
          </span>
        </div>
      ) : null}
      {upload.isError ? (
        <p role="alert" className="text-sm text-[var(--bad)]">
          {upload.error instanceof Error ? upload.error.message : "Không tải lên được."}
        </p>
      ) : null}
      {(profile.documents ?? []).length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">Chưa có tài liệu.</p>
      ) : (
        <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
          {(profile.documents ?? []).map((doc) => (
            <li key={doc.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
              <Pill>{doc.kind_label}</Pill>
              <a href={documentUrl(doc.id)} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate text-[var(--accent)] hover:underline">
                {doc.name}
              </a>
              <span className="text-xs text-[var(--text-muted)]">
                {Math.max(1, Math.round(doc.size_bytes / 1024))} KB · {formatDateTime(doc.created_at)}
              </span>
              {profile.can_edit ? (
                <ConfirmButton
                  spec={{
                    title: `Xoá tài liệu ${doc.name}?`,
                    description: "Tệp bị xoá khỏi hồ sơ, không lấy lại được.",
                    confirmLabel: "Xoá",
                    variant: "destructive",
                  }}
                  onConfirm={() => remove.mutate(doc.id)}
                  pending={remove.isPending}
                  error={remove.error}
                  tone="danger"
                  ariaLabel={`Xoá ${doc.name}`}
                >
                  Xoá
                </ConfirmButton>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type Tab = "personal" | "work" | "contracts" | "pay" | "insurance" | "documents";

/** The whole profile page body. `me`: one's own (shows "Yêu cầu sửa"). */
export function HrProfileView({ userId, me = false }: { userId: string; me?: boolean }) {
  const queryClient = useQueryClient();
  const access = useQuery({ queryKey: ["hr", "access"], queryFn: hrApi.access });
  const profile = useQuery({ queryKey: ["hr", "profile", userId], queryFn: () => hrApi.profile(userId) });
  const entities = useQuery({ queryKey: ["legal-entities"], queryFn: () => api.legalEntities() });
  const [tab, setTab] = useState<Tab>("work");
  const [editing, setEditing] = useState<string | null>(null);
  const [contract, setContract] = useState<HrContract | "new" | null>(null);
  const [asking, setAsking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const removeContract = useMutation({
    mutationFn: (id: string) => hrApi.deleteContract(id),
    onSuccess: () => refresh("Đã xoá hợp đồng."),
  });

  function refresh(text: string | null = null) {
    setEditing(null);
    setContract(null);
    setAsking(false);
    if (text) setNotice(text);
    void queryClient.invalidateQueries({ queryKey: ["hr"] });
    void queryClient.invalidateQueries({ queryKey: ["units", "me"] });
  }

  if (profile.isPending || access.isPending) return <Loading label="Đang tải hồ sơ…" />;
  if (profile.isError) return <ErrorBox error={profile.error} onRetry={() => profile.refetch()} />;
  if (access.isError) return <ErrorBox error={access.error} onRetry={() => access.refetch()} />;
  const p = profile.data;
  const a = access.data;
  const options = (items: { code: string; label: string }[]) =>
    items.map((item) => ({ value: item.code, label: item.label }));
  const plain = (items: string[]) => items.map((item) => ({ value: item, label: item }));
  const sensitive = p.personal !== null;

  const sections: Record<string, { title: string; fields: FieldSpec[]; initial: Record<string, string> }> = {
    work: {
      title: "Thông tin công việc",
      fields: [
        { name: "full_name", label: "Họ và tên" },
        { name: "job_title", label: "Chức danh" },
        { name: "branch", label: "Chi nhánh", kind: "select", options: options(a.choices.branches) },
        {
          name: "legal_entity_id",
          label: "Pháp nhân",
          kind: "select",
          options: (entities.data?.entities ?? []).map((item) => ({ value: item.id, label: item.code })),
        },
        { name: "employment_status", label: "Trạng thái", kind: "select", options: options(a.choices.statuses) },
        { name: "grade", label: "Cấp bậc" },
        { name: "work_form", label: "Hình thức làm việc", kind: "select", options: plain(a.choices.work_forms) },
        { name: "work_email", label: "Email công việc" },
        { name: "joined_on", label: "Ngày vào làm", kind: "date" },
        { name: "official_on", label: "Ngày chính thức", kind: "date" },
        { name: "left_on", label: "Ngày nghỉ việc", kind: "date" },
        { name: "leave_reason", label: "Lý do nghỉ", kind: "textarea" },
        { name: "note", label: "Ghi chú", kind: "textarea" },
      ],
      initial: {
        full_name: p.full_name,
        job_title: text(p.job_title),
        branch: text(p.branch),
        legal_entity_id: text(p.legal_entity_id),
        employment_status: p.employment_status,
        grade: text(p.work.grade),
        work_form: text(p.work.work_form),
        work_email: text(p.work.work_email),
        joined_on: text(p.work.joined_on),
        official_on: text(p.work.official_on),
        left_on: text(p.work.left_on),
        leave_reason: text(p.work.leave_reason),
        note: text(p.work.note),
      },
    },
    personal: {
      title: "Thông tin cá nhân",
      fields: [
        { name: "gender", label: "Giới tính", kind: "select", options: options(a.choices.genders) },
        { name: "birth_date", label: "Ngày sinh", kind: "date" },
        { name: "birth_place", label: "Nơi sinh" },
        { name: "nationality", label: "Quốc tịch" },
        { name: "ethnicity", label: "Dân tộc" },
        { name: "religion", label: "Tôn giáo" },
        { name: "marital_status", label: "Tình trạng hôn nhân", kind: "select", options: plain(a.choices.marital) },
        { name: "id_number", label: "Số CCCD" },
        { name: "id_issued_on", label: "Ngày cấp", kind: "date" },
        { name: "id_issued_by", label: "Nơi cấp" },
        { name: "phone", label: "Điện thoại" },
        { name: "personal_email", label: "Email cá nhân" },
        { name: "drive_email", label: "Email drive" },
        { name: "bank_name", label: "Ngân hàng" },
        { name: "bank_account", label: "Số tài khoản" },
        { name: "bank_holder", label: "Chủ tài khoản" },
        { name: "permanent_address", label: "Hộ khẩu thường trú", kind: "textarea" },
        { name: "current_address", label: "Chỗ ở hiện tại", kind: "textarea" },
      ],
      initial: Object.fromEntries(
        Object.entries(p.personal ?? {}).map(([key, value]) => [key, text(value as string | number | null)]),
      ),
    },
    pay: {
      title: "Lương & phụ cấp",
      fields: [
        { name: "base_salary", label: "Lương cơ bản (đồng)", kind: "money" },
        { name: "allowance", label: "Phụ cấp (đồng)", kind: "money" },
        { name: "salary_type", label: "Kiểu lương", kind: "select", options: plain(a.choices.salary_types) },
        { name: "salary_note", label: "Ghi chú lương", kind: "textarea" },
      ],
      initial: {
        base_salary: text(p.pay?.base_salary),
        allowance: text(p.pay?.allowance),
        salary_type: text(p.pay?.salary_type),
        salary_note: text(p.pay?.salary_note),
      },
    },
    insurance: {
      title: "Bảo hiểm & thuế",
      fields: [
        { name: "social_insurance_no", label: "Số sổ BHXH" },
        { name: "social_insurance_from", label: "Tham gia BHXH từ", kind: "date" },
        { name: "insurance_salary", label: "Mức lương đóng BH (đồng)", kind: "money" },
        { name: "health_insurance_no", label: "Số thẻ BHYT" },
        { name: "tax_code", label: "Mã số thuế cá nhân" },
        { name: "dependants", label: "Số người phụ thuộc", kind: "int" },
      ],
      initial: Object.fromEntries(
        Object.entries(p.insurance ?? {}).map(([key, value]) => [key, text(value as string | number | null)]),
      ),
    },
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: "work", label: "Thông tin công việc" },
    ...(sensitive ? [{ key: "personal" as Tab, label: "Thông tin cá nhân" }] : []),
    { key: "contracts", label: "Hợp đồng lao động" },
    ...(sensitive
      ? [
          { key: "pay" as Tab, label: "Lương & phụ cấp" },
          { key: "insurance" as Tab, label: "Bảo hiểm & thuế" },
          { key: "documents" as Tab, label: "Tài liệu đính kèm" },
        ]
      : []),
  ];
  const editButton = (key: string) =>
    p.can_edit && (key === "work" || a.sensitive) ? (
      <SecondaryButton type="button" onClick={() => setEditing(key)}>
        Sửa
      </SecondaryButton>
    ) : null;

  return (
    <div className="space-y-4">
      <section className="panel flex flex-wrap items-start gap-4 p-4 print:shadow-none">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-[var(--accent-soft)] text-lg font-semibold text-[var(--accent-strong)]">
          {p.full_name
            .split(" ")
            .slice(-2)
            .map((word) => word[0])
            .join("")}
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight">{p.full_name}</h1>
          <p className="mt-0.5 text-sm text-[var(--text-muted)]">
            <span className="font-mono">{p.member_code}</span>
            {p.job_title ? ` · ${p.job_title}` : ""}
            {p.branch_label ? ` · ${p.branch_label}` : ""}
            {p.legal_entity_code ? ` · ${p.legal_entity_code}` : ""}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StatusPill status={p.employment_status} label={p.employment_status_label} />
            <ContractPill contract={p.contract} />
            {p.departments.map((seat) => (
              <DepartmentTag key={seat.department_id} seat={seat} />
            ))}
            {p.telegram_linked ? null : <Pill>Chưa liên kết Telegram</Pill>}
          </div>
        </div>
        <div className="flex gap-2 print:hidden">
          {me && sensitive ? (
            <SecondaryButton type="button" onClick={() => setAsking(true)}>
              Yêu cầu sửa
            </SecondaryButton>
          ) : null}
          <SecondaryButton type="button" onClick={() => window.print()}>
            In
          </SecondaryButton>
        </div>
      </section>

      {notice ? (
        <p role="status" className="rounded-xl border border-[var(--good)]/25 bg-[var(--good-soft)] px-4 py-3 text-sm font-medium text-[var(--good)]">
          {notice}
        </p>
      ) : null}
      {p.requests.length ? (
        <p className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
          {p.requests.length} yêu cầu sửa thông tin đang chờ duyệt.
        </p>
      ) : null}

      <div className="print:hidden">
        <TabStrip tabs={tabs} active={tab} onSelect={(key) => setTab(key as Tab)} label="Phần hồ sơ" />
      </div>

      <section className="panel space-y-4 p-4" aria-label={tabs.find((item) => item.key === tab)?.label}>
        {tab === "work" ? (
          <>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-base font-semibold">Thông tin công việc</h2>
              {editButton("work")}
            </div>
            <InfoGrid
              rows={[
                ["Mã nhân viên", p.member_code ? <span key="code" className="font-mono">{p.member_code}</span> : null],
                ["Phòng ban", p.departments.map((seat) => `${seat.name}${seat.position === "HEAD" ? " (Trưởng phòng)" : ""}`).join(", ")],
                ["Quản lý trực tiếp", p.departments.map((seat) => seat.manager).filter(Boolean).join(", ")],
                ["Chức danh", p.job_title],
                ["Cấp bậc", p.work.grade],
                ["Chi nhánh", p.branch_label],
                ["Pháp nhân", p.legal_entity_code],
                ["Hình thức làm việc", p.work.work_form],
                ["Email công việc", p.work.work_email],
                ["Ngày vào làm", formatDate(p.work.joined_on)],
                ["Ngày chính thức", formatDate(p.work.official_on)],
                ["Thâm niên", p.work.seniority],
                ["Trạng thái", p.employment_status_label],
                ["Ngày nghỉ việc", formatDate(p.work.left_on)],
                ["Lý do nghỉ", p.work.leave_reason],
                ["Ghi chú", p.work.note],
              ]}
            />
          </>
        ) : null}

        {tab === "personal" && p.personal ? (
          <>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-base font-semibold">Thông tin cá nhân</h2>
              {editButton("personal")}
            </div>
            <InfoGrid
              rows={[
                ["Giới tính", p.gender_label],
                ["Ngày sinh", p.personal.birth_date ? `${formatDate(p.personal.birth_date)} (${p.personal.age} tuổi)` : null],
                ["Nơi sinh", p.personal.birth_place],
                ["Quốc tịch", p.personal.nationality],
                ["Dân tộc", p.personal.ethnicity],
                ["Tôn giáo", p.personal.religion],
                ["Tình trạng hôn nhân", p.personal.marital_status],
                ["Số CCCD", p.personal.id_number],
                ["Ngày cấp", formatDate(p.personal.id_issued_on)],
                ["Nơi cấp", p.personal.id_issued_by],
                ["Điện thoại", p.personal.phone],
                ["Email cá nhân", p.personal.personal_email],
                ["Email drive", p.personal.drive_email],
                ["Hộ khẩu thường trú", p.personal.permanent_address],
                ["Chỗ ở hiện tại", p.personal.current_address],
                ["Ngân hàng", p.personal.bank_name],
                ["Số tài khoản", p.personal.bank_account],
                ["Chủ tài khoản", p.personal.bank_holder],
              ]}
            />
          </>
        ) : null}

        {tab === "contracts" ? (
          <>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-base font-semibold">Hợp đồng lao động</h2>
              {p.can_edit ? (
                <PrimaryButton type="button" onClick={() => setContract("new")}>
                  + Thêm hợp đồng
                </PrimaryButton>
              ) : null}
            </div>
            {p.contracts.length === 0 ? (
              <p className="text-sm text-[var(--text-muted)]">Chưa có hợp đồng.</p>
            ) : (
              <div className="table-scroll-x">
                <table className="table-dense min-w-[44rem]">
                  <thead>
                    <tr>
                      <th>Số hợp đồng</th>
                      <th>Loại</th>
                      <th>Từ ngày</th>
                      <th>Đến ngày</th>
                      {sensitive ? <th>Lương</th> : null}
                      <th>Đã ký</th>
                      <th>Trạng thái</th>
                      {p.can_edit ? <th><span className="sr-only">Thao tác</span></th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {p.contracts.map((item) => (
                      <tr key={item.id}>
                        <td className="font-mono text-xs">{item.number || "—"}</td>
                        <td>{item.type_name}</td>
                        <td>{formatDate(item.start_on) || "—"}</td>
                        <td>{item.end_on ? formatDate(item.end_on) : "Không thời hạn"}</td>
                        {sensitive ? <td>{money(item.salary)}</td> : null}
                        <td>{item.signed ? "Đã ký" : "Chưa"}</td>
                        <td>
                          <ContractPill contract={item} />
                        </td>
                        {p.can_edit ? (
                          <td className="whitespace-nowrap text-right">
                            <button type="button" className="mr-2 text-sm text-[var(--accent)] hover:underline" onClick={() => setContract(item)}>
                              Sửa
                            </button>
                            <ConfirmButton
                              spec={{
                                title: `Xoá hợp đồng ${item.number ?? item.type_name}?`,
                                description: "Hợp đồng bị xoá khỏi hồ sơ.",
                                confirmLabel: "Xoá",
                                variant: "destructive",
                              }}
                              onConfirm={() => removeContract.mutate(item.id)}
                              pending={removeContract.isPending}
                              error={removeContract.error}
                              tone="danger"
                              ariaLabel={`Xoá hợp đồng ${item.number ?? item.type_name}`}
                            >
                              Xoá
                            </ConfirmButton>
                          </td>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : null}

        {tab === "pay" && p.pay ? (
          <>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-base font-semibold">Lương & phụ cấp</h2>
              {editButton("pay")}
            </div>
            <InfoGrid
              rows={[
                ["Lương cơ bản", money(p.pay.base_salary)],
                ["Phụ cấp", money(p.pay.allowance)],
                ["Tổng thu nhập / tháng", money(p.pay.total)],
                ["Kiểu lương", p.pay.salary_type],
                ["Ghi chú", p.pay.salary_note],
              ]}
            />
            <p className="text-xs text-[var(--text-muted)]">
              Chỉ lưu và xem; chưa tính lương. Số liệu được mã hoá.
            </p>
          </>
        ) : null}

        {tab === "insurance" && p.insurance ? (
          <>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-base font-semibold">Bảo hiểm & thuế</h2>
              {editButton("insurance")}
            </div>
            <InfoGrid
              rows={[
                ["Số sổ BHXH", p.insurance.social_insurance_no],
                ["Tham gia BHXH từ", formatDate(p.insurance.social_insurance_from)],
                ["Mức lương đóng BH", money(p.insurance.insurance_salary)],
                ["Số thẻ BHYT", p.insurance.health_insurance_no],
                ["Mã số thuế", p.insurance.tax_code],
                ["Số người phụ thuộc", p.insurance.dependants],
              ]}
            />
          </>
        ) : null}

        {tab === "documents" && p.documents ? (
          <>
            <h2 className="text-base font-semibold">Tài liệu đính kèm</h2>
            <Documents profile={p} access={a} onChanged={() => refresh(null)} />
          </>
        ) : null}
      </section>

      {editing && sections[editing] ? (
        <SectionEditor
          title={sections[editing].title}
          userId={p.user_id}
          fields={sections[editing].fields}
          initial={sections[editing].initial}
          onClose={() => setEditing(null)}
          onSaved={() => refresh(`Đã lưu ${sections[editing].title.toLowerCase()}.`)}
        />
      ) : null}
      {contract ? (
        <ContractDialog
          userId={p.user_id}
          contract={contract === "new" ? null : contract}
          access={a}
          onClose={() => setContract(null)}
          onSaved={() => refresh("Đã lưu hợp đồng.")}
        />
      ) : null}
      {asking ? (
        <RequestDialog
          access={a}
          profile={p}
          onClose={() => setAsking(false)}
          onSaved={() => refresh("Đã gửi yêu cầu sửa, chờ phòng Hành chính nhân sự duyệt.")}
        />
      ) : null}
    </div>
  );
}
