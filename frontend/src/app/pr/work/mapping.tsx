"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { CONTENT_TYPE_ORDER, contentTypeLabel } from "@/lib/labels";
import { ErrorBox, Pill } from "@/components/states";
import { Select } from "@/components/pr";

/**
 * Vietnamese for each contribution kind, **including the retired one**.
 *
 * `PUBLICATION` is here so a rule a department configured before M3.1 still
 * renders as words rather than as a raw code. It is deliberately absent from
 * `CONFIGURABLE_KINDS` below: V1 records automatic workload at two content
 * milestones, and offering a third in the picker would promise a projection
 * that no longer happens.
 */
const CONTENT_WORK_KIND_LABELS: Record<string, string> = {
  CONTENT_CREATION: "Viết nội dung",
  PRODUCTION: "Sản xuất",
  PUBLICATION: "Đăng bài (không còn tự động)",
};

/** The two milestones V1 actually projects. See the label map above. */
const CONFIGURABLE_KINDS = ["CONTENT_CREATION", "PRODUCTION"] as const;

/**
 * *Mapping nội dung* - which content milestone counts as which kind of work.
 *
 * **M3.1 moved this panel out of the KPI view and into *Cấu hình*, beside the
 * work types it selects from.** M3 put it under *Kế hoạch KPI* because that was
 * the only `PR_WORK_CONFIGURE` surface at the time. It never belonged there: a
 * mapping has nothing to do with a reporting period, and the two settings a
 * person needs together - "what kinds of work exist" and "which content
 * produces them" - were two tabs apart, so setting a department up meant
 * discovering the second one by accident.
 */
/**
 * *Ánh xạ nội dung → công việc* - M3's one configurable thing.
 *
 * **Only the work type is a setting.** Which content milestone counts as work,
 * whose work it is and when independent validation is required are server rules
 * and are deliberately not on this screen: a dropdown for the milestone would be
 * a dropdown that turns the anti-gaming boundary off.
 *
 * A missing mapping is shown as missing rather than hidden, because *"why is my
 * script not appearing on my board"* is the question this panel exists to
 * answer, and the answer is usually a row nobody has written yet.
 */
export function ContentWorkMapping() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const rules = useQuery({
    queryKey: ["content-work-rules"],
    queryFn: api.contentWorkRules,
    enabled: open,
  });
  const types = useQuery({
    queryKey: ["work-types"],
    queryFn: () => api.workTypes(),
    enabled: open,
  });

  const [kind, setKind] = useState("CONTENT_CREATION");
  const [contentType, setContentType] = useState("");
  const [workTypeId, setWorkTypeId] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.upsertContentWorkRule({
        contribution_kind: kind,
        content_type: contentType || null,
        work_type_id: workTypeId,
      }),
    onSuccess: () => {
      setWorkTypeId("");
      void queryClient.invalidateQueries({ queryKey: ["content-work-rules"] });
    },
  });

  return (
    <details
      className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3"
      onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer text-sm font-semibold">
        Ánh xạ nội dung → công việc
      </summary>
      <p className="mt-2 text-xs text-[var(--text-muted)]">
        Công việc từ quy trình nội dung được ghi nhận tự động. Ở đây chỉ chọn{" "}
        <strong>loại công việc</strong> tương ứng — mốc nào được tính và ai được ghi nhận là
        quy tắc hệ thống, không cấu hình được. Loại nội dung chưa có ánh xạ sẽ được hệ thống
        tự tạo loại công việc và ánh xạ khi nội dung đầu tiên được duyệt; ánh xạ đặt ở đây
        luôn được ưu tiên.
      </p>

      {rules.isError ? <ErrorBox error={rules.error} /> : null}
      {rules.data?.length === 0 ? (
        <p className="mt-2 rounded border border-[var(--border)] p-2 text-xs text-[var(--text-muted)]">
          Chưa có ánh xạ nào. Nội dung đầu tiên được duyệt của mỗi loại sẽ tự tạo ánh xạ ở đây.
        </p>
      ) : null}

      <ul className="mt-2 space-y-1">
        {rules.data?.map((one) => (
          <li key={one.id} className="flex flex-wrap items-center gap-2 text-xs">
            <Pill>{CONTENT_WORK_KIND_LABELS[one.contribution_kind] ?? one.contribution_kind}</Pill>
            <span className="text-[var(--text-muted)]">
              {/*
                `contentTypeLabel(null)` is "Chưa phân loại" - correct for a
                *content item* with no type, and wrong for a *rule* with none,
                where null is the default scope. So the null case is answered
                here rather than by handing null to the helper.
              */}
              {one.content_type
                ? contentTypeLabel(one.content_type)
                : "Mặc định (mọi loại nội dung)"}
            </span>
            <span aria-hidden>→</span>
            <span className="font-medium">{one.work_type_name ?? one.work_type_code}</span>
            {one.auto_provisioned ? <Pill tone="neutral">Hệ thống tự tạo</Pill> : null}
            {one.is_active ? null : <Pill tone="warn">Đã tắt</Pill>}
          </li>
        ))}
      </ul>

      <form
        className="mt-3 grid gap-2 sm:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <Select
          value={kind}
          onChange={(event) => setKind(event.target.value)}
          aria-label="Loại đóng góp"
          className="text-xs"
        >
          {CONFIGURABLE_KINDS.map((value) => (
            <option key={value} value={value}>
              {CONTENT_WORK_KIND_LABELS[value]}
            </option>
          ))}
        </Select>
        <Select
          value={contentType}
          onChange={(event) => setContentType(event.target.value)}
          aria-label="Loại nội dung"
          className="text-xs"
        >
          {/*
            The empty value is the **default scope**, not a content type, and
            certainly not "Chưa phân loại": a rule with no content type is the
            fallback for its whole kind. The two are one keystroke apart in the
            markup and mean entirely different things, which is why the wording
            here says "mọi loại nội dung" rather than anything about
            classification.
          */}
          <option value="">Mặc định (mọi loại nội dung)</option>
          {CONTENT_TYPE_ORDER.map((one) => (
            <option key={one} value={one}>
              {contentTypeLabel(one)}
            </option>
          ))}
        </Select>
        <Select
          value={workTypeId}
          onChange={(event) => setWorkTypeId(event.target.value)}
          // Distinct from the quota form's picker further up the page: two
          // controls both called "Loại công việc" are two controls a person -
          // and a screen reader - cannot tell apart.
          aria-label="Loại công việc tương ứng"
          className="text-xs"
        >
          <option value="">Chọn loại công việc</option>
          {types.data?.map((one) => (
            <option key={one.id} value={one.id}>
              {one.name}
            </option>
          ))}
        </Select>
        <button
          type="submit"
          disabled={!workTypeId || save.isPending}
          className="min-h-11 rounded bg-[var(--text)] px-3 text-xs text-[var(--surface)] disabled:opacity-50 sm:col-span-3 sm:justify-self-start"
        >
          {save.isPending ? "Đang lưu…" : "Lưu ánh xạ"}
        </button>
        {save.isError ? <ErrorBox error={save.error} /> : null}
      </form>
    </details>
  );
}
