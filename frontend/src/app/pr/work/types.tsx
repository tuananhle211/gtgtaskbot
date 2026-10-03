"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type WorkType, type WorkTypeInput } from "@/lib/api";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import {
  activateWorkTypeConfirmation,
  deactivateWorkTypeConfirmation,
  deleteWorkTypeConfirmation,
} from "@/lib/confirmations";
import { ContentWorkMapping } from "./mapping";
import { PerformancePolicyPanel } from "./policy";
import { WorkScoringRules } from "./scoring-rules";
import { WorkMaintenancePanel } from "./maintenance";
import { ApiError, type WorkTypeReferences } from "@/lib/api";

/**
 * *Loại công việc* - the department's own taxonomy. **M2.5.**
 *
 * ## Why this screen exists at all
 *
 * `pr_work_types` shipped empty. M1 built the table and one API route into it;
 * nobody in production ever called that route, so "Giao công việc" offered an
 * empty dropdown and a KPI plan had nothing to set a quota on. The taxonomy was
 * a developer's `INSERT` away, which is another way of saying the business did
 * not own it. This screen is where it becomes theirs.
 *
 * ## The one idea the layout is built around
 *
 * **A used type is not an editable type.** `Mã`, `Cách tính` and `Đơn vị` are
 * what historical rows *mean* - a row filed under `SEEDING_COMMENT` measured in
 * `COMMENT` says `120` is a hundred and twenty comments, and the same type
 * flipped to `ITEM_COUNT` would make it one job. So once anything has been filed
 * under a type, those three are drawn disabled with the reason beside them, and
 * the answer to a genuine change of measurement is a new type beside the old
 * one.
 *
 * The disabled fields are a **courtesy, not the control**: the server refuses
 * the same three regardless of what this screen drew, which is why the lock
 * state is asked of the server per type rather than guessed here.
 *
 * ## Tắt, never Xóa
 *
 * There is no delete, and the dialog says why in the sentence people actually
 * worry about: *"Dữ liệu lịch sử vẫn được giữ nguyên."* A retired type keeps
 * every row filed under it and keeps rendering on old work and approved plans;
 * what stops is being offered for anything new.
 */
export function WorkTypeWorkspace() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // `include_inactive` needs PR_WORK_CONFIGURE, which is the only capability
  // that reaches this screen - see the caller.
  const types = useQuery({
    queryKey: ["work-types", "all"],
    queryFn: () => api.workTypes({ include_inactive: true }),
  });
  // The same query the workload panel further down makes, so a type whose rate
  // is missing can say so on its own row. An auto-provisioned type always
  // starts here: the projector creates it with no rule and no minutes, and the
  // honest thing to show beside "1 sản phẩm" is that it is not yet priced.
  const scoringRules = useQuery({
    queryKey: ["work-scoring-rules"],
    queryFn: () => api.workScoringRules(),
  });
  const pricedTypeIds = new Set(
    (scoringRules.data ?? [])
      .filter((rule) => rule.status === "APPROVED")
      .map((rule) => rule.work_type_id),
  );

  const refresh = () => {
    // Both keys: the pickers read the active-only list, and they must not keep
    // offering a type somebody just retired on this screen.
    void queryClient.invalidateQueries({ queryKey: ["work-types"] });
  };

  const bootstrap = useMutation({
    mutationFn: () => api.bootstrapWorkTypes(),
    onSuccess: refresh,
  });

  if (types.isPending) return <Loading label="Đang tải loại công việc…" />;
  if (types.isError) return <ErrorBox error={types.error} onRetry={() => types.refetch()} />;

  const rows = types.data ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Loại công việc</h2>
          <p className="text-xs text-[var(--text-muted)]">
            Danh mục dùng chung cho giao việc và cho hạn mức KPI. Nhân viên chọn
            từ danh sách này và không tự tạo được loại mới.
          </p>
        </div>
        <SecondaryButton type="button" onClick={() => setCreating((open) => !open)}>
          ➕ Thêm loại công việc
        </SecondaryButton>
      </div>

      {creating ? (
        <WorkTypeForm
          onDone={() => {
            setCreating(false);
            refresh();
          }}
          onCancel={() => setCreating(false)}
        />
      ) : null}

      {rows.length === 0 ? (
        <div className="space-y-2">
          <Empty message="Chưa có loại công việc nào. Hãy tạo loại công việc, hoặc khởi tạo bộ mặc định để bắt đầu nhanh." />
          {/*
            The one-click way out of an empty department. Idempotent on the
            server, so a second press creates nothing rather than duplicating
            the taxonomy.
          */}
          <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
            <p className="text-sm font-medium">Khởi tạo bộ loại công việc mặc định</p>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Tạo sẵn 13 loại công việc thường dùng (kịch bản, dựng video, quay,
              seeding, đăng bài…). Bạn có thể đổi tên, đổi nhóm hoặc tắt bớt sau.
              Chạy lại nhiều lần cũng không tạo trùng.
            </p>
            <PrimaryButton
              type="button"
              className="mt-2"
              disabled={bootstrap.isPending}
              onClick={() => bootstrap.mutate()}
            >
              {bootstrap.isPending ? "Đang khởi tạo…" : "Khởi tạo bộ mặc định"}
            </PrimaryButton>
            {bootstrap.isError ? <ErrorBox error={bootstrap.error} /> : null}
          </div>
        </div>
      ) : null}

      {rows.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[46rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-xs text-[var(--text-muted)]">
                <th className="py-2 pr-3 font-medium">Tên</th>
                <th className="py-2 pr-3 font-medium">Nhóm</th>
                <th className="py-2 pr-3 font-medium">Cách tính</th>
                <th className="py-2 pr-3 font-medium">Đơn vị</th>
                <th className="py-2 pr-3 font-medium">Trạng thái</th>
                <th className="py-2 font-medium">
                  <span className="sr-only">Thao tác</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <WorkTypeRow
                  key={row.id}
                  row={row}
                  unpriced={scoringRules.isSuccess && !pricedTypeIds.has(row.id)}
                  editing={editing === row.id}
                  onEdit={() => setEditing(editing === row.id ? null : row.id)}
                  onDone={() => {
                    setEditing(null);
                    refresh();
                  }}
                  onChanged={refresh}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {/*
        M3.1. The second half of the same setup: what kinds of work exist, and
        which content milestone produces which. Two settings a person needs
        together, so they are one screen rather than two tabs apart.
      */}
      <ContentWorkMapping />

      {/*
        Work maintenance. After the taxonomy and the mapping are corrected, the
        ledger is made to agree with them here - preview first, then sync or
        rebuild. PR_WORK_CONFIGURE reaches this screen, and the server refuses
        every maintenance route for anybody else.
      */}
      <WorkMaintenancePanel />

      {/*
        M6. The other two halves of the same setup, in the order somebody does
        them: what kinds of work exist, which content produces them, what each
        is worth in standard minutes, and how those minutes are weighed.
      */}
      <WorkScoringRules />
      <PerformancePolicyPanel />
    </div>
  );
}

/** What a blocking reference count is called, keyed as the server keys it. */
const REFERENCE_LABELS: Record<string, string> = {
  content_rules_active: "ánh xạ nội dung",
  work_items: "công việc / luồng công việc",
  results: "kết quả công việc",
  contributions: "phần đóng góp",
  recurring_templates: "việc định kỳ",
  quotas: "chỉ tiêu KPI",
  quota_allocations: "phân bổ hạn mức",
  scoring_rules: "quy tắc workload",
  score_allocations: "phân bổ điểm",
};

const EMPTY_REFERENCES = (id: string): WorkTypeReferences => ({
  work_type_id: id,
  content_rules_active: 0,
  content_rules_inactive: 0,
  work_items: 0,
  period_containers: 0,
  empty_containers_removable: 0,
  results: 0,
  contributions: 0,
  recurring_templates: 0,
  quotas: 0,
  quota_allocations: 0,
  scoring_rules: 0,
  score_allocations: 0,
  blocking: {},
  deletable: false,
});

/** One row, and its editor when open. */
function WorkTypeRow({
  row,
  unpriced,
  editing,
  onEdit,
  onDone,
  onChanged,
}: {
  row: WorkType;
  /** No approved workload rule prices this type. `false` while rules are loading. */
  unpriced: boolean;
  editing: boolean;
  onEdit: () => void;
  onDone: () => void;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const lifecycle = useMutation({
    mutationFn: () =>
      row.is_active ? api.deactivateWorkType(row.id) : api.activateWorkType(row.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["work-types"] });
      onChanged();
    },
  });
  // Work maintenance. **Xóa** asks the server what still refers to the type
  // first; a type anything points at is refused with the counts, and only an
  // unreferenced one gets the confirmation. The server refuses either way.
  const [impact, setImpact] = useState<WorkTypeReferences | null>(null);
  const references = useMutation({
    mutationFn: () => api.workTypeReferences(row.id),
    onSuccess: setImpact,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteWorkType(row.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["work-types"] });
      onChanged();
    },
    onError: (error) => {
      const refs = error instanceof ApiError ? error.details?.references : null;
      if (refs && typeof refs === "object") {
        setImpact((was) => ({ ...(was ?? EMPTY_REFERENCES(row.id)), blocking: refs as Record<string, number>, deletable: false }));
      }
    },
  });
  const sweep = useMutation({
    mutationFn: () => api.cleanupEmptyContainers(row.id),
    onSuccess: (next) => {
      setImpact(next.remaining_references);
      onChanged();
    },
  });

  return (
    <>
      <tr className="border-b border-[var(--border)] align-top">
        <td className="py-2 pr-3">
          <p className="font-medium">{row.name}</p>
          {/*
            The code is shown small and secondary. It is the stable identifier
            and people do need to see it - a mapping conversation is held in
            codes - but it is not what the row *is*, so it does not lead.
          */}
          <p className="font-mono text-xs text-[var(--text-muted)]">{row.code}</p>
          {row.auto_provisioned ? (
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Hệ thống tự tạo từ nội dung
            </p>
          ) : null}
        </td>
        <td className="py-2 pr-3">{row.category_label}</td>
        <td className="py-2 pr-3">{row.default_quota_basis_label}</td>
        <td className="py-2 pr-3">
          {/*
            An ITEM_COUNT type's unit is never read as a quantity, so showing it
            would be showing a field that decides nothing.
          */}
          {row.default_quota_basis === "QUANTITY" ? row.default_unit_label : "—"}
        </td>
        <td className="py-2 pr-3">
          <div className="flex flex-wrap gap-1">
            {row.is_active ? (
              <Pill>Đang dùng</Pill>
            ) : (
              <Pill tone="neutral">Đã tắt</Pill>
            )}
            {/*
              Missing, not zero. A type with no approved rate contributes real
              counted work and no standard minutes - the KPI workload stays
              incomplete until somebody sets one below.
            */}
            {row.is_active && unpriced ? <Pill tone="warn">Chưa có quy tắc workload</Pill> : null}
          </div>
        </td>
        <td className="py-2">
          <div className="flex flex-wrap justify-end gap-1.5">
            <SecondaryButton type="button" onClick={onEdit} aria-label={`Chỉnh sửa ${row.name}`}>
              {editing ? "Đóng" : "Chỉnh sửa"}
            </SecondaryButton>
            <ConfirmButton
              spec={
                row.is_active
                  ? deactivateWorkTypeConfirmation(row.name)
                  : activateWorkTypeConfirmation(row.name)
              }
              onConfirm={() => lifecycle.mutate()}
              pending={lifecycle.isPending}
              error={lifecycle.error}
              tone={row.is_active ? "danger" : "secondary"}
              ariaLabel={`${row.is_active ? "Tắt" : "Bật lại"} ${row.name}`}
            >
              {row.is_active ? "Tắt" : "Bật lại"}
            </ConfirmButton>
            {impact?.deletable ? (
              <ConfirmButton
                spec={deleteWorkTypeConfirmation(row.name)}
                onConfirm={() => remove.mutate()}
                pending={remove.isPending}
                error={remove.error}
                tone="danger"
                ariaLabel={`Xóa ${row.name}`}
              >
                Xóa
              </ConfirmButton>
            ) : (
              <SecondaryButton
                type="button"
                onClick={() => references.mutate()}
                disabled={references.isPending}
                aria-label={`Xóa ${row.name}`}
              >
                {references.isPending ? "Đang kiểm tra…" : "Xóa"}
              </SecondaryButton>
            )}
          </div>
        </td>
      </tr>
      {impact && !impact.deletable ? (
        <tr className="border-b border-[var(--border)]">
          <td colSpan={6} className="py-2">
            <div className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
              <p className="font-medium">Không thể xóa loại công việc này.</p>
              <p className="mt-1">Loại công việc đang được sử dụng bởi:</p>
              <ul className="mt-1 list-disc pl-4">
                {Object.entries(impact.blocking).map(([key, count]) => (
                  <li key={key}>
                    {count} {REFERENCE_LABELS[key] ?? key}
                  </li>
                ))}
              </ul>
              <p className="mt-1">Thay đổi ánh xạ hoặc dọn dữ liệu trước.</p>
              {impact.empty_containers_removable > 0 ? (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span>
                    {impact.empty_containers_removable} luồng công việc trống có thể dọn.
                  </span>
                  <SecondaryButton
                    type="button"
                    onClick={() => sweep.mutate()}
                    disabled={sweep.isPending}
                  >
                    {sweep.isPending ? "Đang dọn…" : "Dọn luồng trống"}
                  </SecondaryButton>
                </div>
              ) : null}
              {sweep.isError ? <ErrorBox error={sweep.error} /> : null}
              {references.isError ? <ErrorBox error={references.error} /> : null}
            </div>
          </td>
        </tr>
      ) : null}
      {editing ? (
        <tr className="border-b border-[var(--border)]">
          <td colSpan={6} className="py-2">
            <WorkTypeForm row={row} onDone={onDone} onCancel={onEdit} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

const CATEGORIES: ReadonlyArray<[string, string]> = [
  ["CONTENT", "Nội dung"],
  ["PRODUCTION", "Sản xuất"],
  ["DISTRIBUTION", "Phân phối"],
  ["COMMUNITY", "Cộng đồng"],
  ["PR_EVENT", "PR / Sự kiện"],
  ["OPERATIONS", "Vận hành"],
  ["RESEARCH", "Nghiên cứu"],
  ["OTHER", "Khác"],
];

const UNITS: ReadonlyArray<[string, string]> = [
  ["ITEM", "sản phẩm"],
  ["CUSTOMER", "khách hàng"],
  ["SCRIPT", "kịch bản"],
  ["ORDER", "đơn hàng"],
  ["VIDEO", "video"],
  ["POST", "bài đăng"],
  ["ARTICLE", "bài viết"],
  ["COMMENT", "bình luận"],
  ["MESSAGE", "tin nhắn"],
  ["ACCOUNT", "tài khoản"],
  ["SESSION", "buổi"],
  ["HOUR", "giờ"],
  ["DAY", "ngày công"],
];

/**
 * Create, or edit one type.
 *
 * The same form both ways, because the difference between them is not which
 * fields exist but which are **locked** - and a separate create form would be a
 * second place for the field list to drift.
 */
function WorkTypeForm({
  row,
  onDone,
  onCancel,
}: {
  row?: WorkType;
  onDone: () => void;
  onCancel: () => void;
}) {
  // Asked per type rather than read off the list: the lock is a server fact,
  // and a list response that omitted it would leave this screen guessing.
  const detail = useQuery({
    queryKey: ["work-type", row?.id],
    queryFn: () => api.workType(row!.id),
    enabled: Boolean(row),
  });
  // Locked until the server says otherwise, and only for an existing row: the
  // structural fields must never be drawn editable while it is still unknown
  // whether they are. Erring the other way would show them enabled for a moment
  // on a used type, which is the one direction that misleads.
  const locked = Boolean(row) && (detail.isPending || Boolean(detail.data?.structure_locked));

  const [code, setCode] = useState(row?.code ?? "");
  const [name, setName] = useState(row?.name ?? "");
  const [category, setCategory] = useState(row?.category ?? "CONTENT");
  const [description, setDescription] = useState(row?.description ?? "");
  const [basis, setBasis] = useState(row?.default_quota_basis ?? "ITEM_COUNT");
  const [unit, setUnit] = useState(row?.default_unit ?? "ITEM");

  const save = useMutation({
    mutationFn: () => {
      const body: WorkTypeInput = {
        name: name.trim(),
        category,
        description: description.trim() || null,
      };
      // Structural fields are sent only when they are actually editable. A
      // locked type would be refused anyway - the server is the control - but
      // sending fields the screen drew disabled would turn a rename into a 409.
      if (!locked) {
        body.code = code.trim();
        body.default_quota_basis = basis;
      }
      // Period-container patch. **The unit is editable on any type, used or
      // not.** It is what a result is called - "khách hàng", "kịch bản" - and
      // every stored amount keeps its own copy, so renaming it rewrites no
      // history. Sent for both bases: an `ITEM_COUNT` stream still reports
      // "23 kịch bản", and the old form's null here is how "Tìm khách hàng"
      // came to read "sản phẩm".
      body.default_unit = unit;
      return row ? api.updateWorkType(row.id, body) : api.createWorkType(body);
    },
    onSuccess: onDone,
  });

  const ready = name.trim() && (row || code.trim());

  return (
    <form
      className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <h3 className="text-sm font-semibold">
        {row ? `Chỉnh sửa “${row.name}”` : "Thêm loại công việc"}
      </h3>

      {locked ? (
        <p className="rounded border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs text-[var(--text-muted)]">
          Loại công việc này đã phát sinh dữ liệu nên không đổi được mã và cách
          tính. Đơn vị kết quả vẫn đổi được: số liệu đã ghi giữ nguyên đơn vị cũ,
          công việc theo kỳ đang mở và bản nháp KPI dùng đơn vị mới.
        </p>
      ) : null}

      <label className="block text-xs">
        Mã
        <input
          value={code}
          onChange={(event) => setCode(event.target.value)}
          disabled={locked}
          placeholder="SEEDING_COMMENT"
          aria-label="Mã"
          className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 font-mono text-sm disabled:opacity-60"
        />
      </label>

      <label className="block text-xs">
        Tên
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Comment seeding"
          aria-label="Tên"
          className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
        />
      </label>

      <label className="block text-xs">
        Nhóm
        <Select
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          aria-label="Nhóm"
          className="mt-1 w-full text-sm"
        >
          {CATEGORIES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
      </label>

      <label className="block text-xs">
        Cách tính
        <Select
          value={basis}
          onChange={(event) => setBasis(event.target.value)}
          disabled={locked}
          aria-label="Cách tính"
          className="mt-1 w-full text-sm disabled:opacity-60"
        >
          <option value="ITEM_COUNT">Theo số công việc</option>
          <option value="QUANTITY">Theo số lượng</option>
        </Select>
      </label>

      <label className="block text-xs">
        Đơn vị kết quả
        <Select
          value={unit}
          onChange={(event) => setUnit(event.target.value)}
          aria-label="Đơn vị"
          className="mt-1 w-full text-sm"
        >
          {UNITS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
        <span className="block text-[var(--text-muted)]">
          Hiện ở chỉ tiêu, kết quả thực tế và form báo cáo: “27 / 20 khách hàng”.
        </span>
      </label>

      <label className="block text-xs">
        Mô tả
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={2}
          aria-label="Mô tả"
          className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
        />
      </label>

      {save.isError ? <ErrorBox error={save.error} /> : null}

      <div className="flex gap-1.5">
        <PrimaryButton type="submit" disabled={!ready || save.isPending}>
          {save.isPending ? "Đang lưu…" : row ? "Lưu" : "Tạo loại công việc"}
        </PrimaryButton>
        <SecondaryButton type="button" onClick={onCancel}>
          Thôi
        </SecondaryButton>
      </div>
    </form>
  );
}
