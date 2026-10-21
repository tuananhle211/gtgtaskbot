"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { PageHeader, PrimaryButton, Select } from "@/components/pr";
import { DateInput } from "@/components/date-input";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";

/**
 * "Tạo đề xuất tài nguyên" (2026-10-21). Anybody may ask. The route it will
 * take is shown before sending: the requester's head(s) (or straight to the
 * spend approval when there is none above them), then the Phòng Tài nguyên.
 */
type Draft = {
  type_id: string;
  quantity: string;
  site: string;
  purpose: string;
  requirements: string;
  reason: string;
  replaces: string;
  budget: string;
  needed_by: string;
};

const EMPTY: Draft = {
  type_id: "",
  quantity: "1",
  site: "",
  purpose: "",
  requirements: "",
  reason: "",
  replaces: "",
  budget: "",
  needed_by: "",
};

const digits = (value: string) => value.replace(/[^\d]/g, "");

export default function NewResourceRequestPage() {
  const router = useRouter();
  const form = useQuery({ queryKey: ["resource-request-form"], queryFn: api.resourceRequestForm });
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const holding = useQuery({
    queryKey: ["resource-request-holding", draft.type_id],
    queryFn: () => api.resourceRequestHolding(draft.type_id),
    enabled: Boolean(draft.type_id),
  });
  const send = useMutation({
    mutationFn: () =>
      api.createResourceRequest({
        type_id: draft.type_id,
        quantity: Number(draft.quantity),
        site: draft.site,
        purpose: draft.purpose.trim(),
        requirements: draft.requirements.trim() || null,
        reason: draft.reason.trim(),
        replaces: draft.replaces.trim() || null,
        budget: Number(digits(draft.budget) || "0"),
        needed_by: draft.needed_by,
      }),
    onSuccess: (created) => router.push(created.detail_path),
  });
  const ids = {
    type: useId(),
    quantity: useId(),
    site: useId(),
    purpose: useId(),
    requirements: useId(),
    reason: useId(),
    replaces: useId(),
    budget: useId(),
    needed: useId(),
  };

  if (form.isPending) return <Loading />;
  if (form.isError) return <ErrorBox error={form.error} onRetry={() => form.refetch()} />;
  const options = form.data;
  const quantity = Number(draft.quantity);
  const ready =
    draft.type_id &&
    quantity >= 1 &&
    quantity <= options.max_quantity &&
    draft.site &&
    draft.purpose.trim() &&
    draft.reason.trim() &&
    digits(draft.budget) &&
    draft.needed_by;
  const field = send.error instanceof ApiError ? (send.error.details?.field as string | undefined) : undefined;
  const typeName = options.types.find((item) => item.value === draft.type_id)?.label ?? "";
  const route =
    options.approvers.length > 0
      ? `Trưởng phòng (${options.approvers.map((person) => person.full_name).join(", ")}) → Duyệt chi (Phòng Tài nguyên) → Mua & nhập kho → Bạn xác nhận nhận`
      : "Duyệt chi (Phòng Tài nguyên) → Mua & nhập kho → Bạn xác nhận nhận (không có trưởng phòng phía trên bạn)";
  const label = "block text-xs text-[var(--text-muted)]";
  const input =
    "mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";
  const area = `${input} py-2`;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Tạo đề xuất tài nguyên"
        subtitle="Điền đủ các mục có dấu *. Sau khi gửi, đề xuất đi theo tuyến duyệt bên dưới."
      />
      <form
        className="panel grid gap-4 p-4 sm:grid-cols-2 sm:p-6"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready && !send.isPending) send.mutate();
        }}
      >
        <label htmlFor={ids.type} className={label}>
          Loại tài nguyên *
          <Select
            id={ids.type}
            value={draft.type_id}
            onChange={(event) => set("type_id", event.target.value)}
            className="mt-1 w-full text-sm"
            aria-invalid={field === "type_id" || undefined}
          >
            <option value="">— chọn —</option>
            {options.types.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </label>
        <label htmlFor={ids.quantity} className={label}>
          Số lượng *
          <input
            id={ids.quantity}
            type="number"
            min={1}
            max={options.max_quantity}
            value={draft.quantity}
            onChange={(event) => set("quantity", event.target.value)}
            className={input}
          />
        </label>
        <label htmlFor={ids.site} className={label}>
          Cơ sở sử dụng *
          <Select
            id={ids.site}
            value={draft.site}
            onChange={(event) => set("site", event.target.value)}
            className="mt-1 w-full text-sm"
          >
            <option value="">— chọn —</option>
            {options.sites.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </label>
        <label htmlFor={ids.purpose} className={label}>
          Mục đích sử dụng *
          <input
            id={ids.purpose}
            value={draft.purpose}
            onChange={(event) => set("purpose", event.target.value)}
            className={input}
            placeholder="VD: chạy chiến dịch tháng 11"
          />
        </label>
        <label htmlFor={ids.requirements} className={`${label} sm:col-span-2`}>
          Yêu cầu với tài nguyên
          <textarea
            id={ids.requirements}
            rows={3}
            value={draft.requirements}
            onChange={(event) => set("requirements", event.target.value)}
            className={area}
          />
        </label>
        <label htmlFor={ids.reason} className={`${label} sm:col-span-2`}>
          Lý do đề xuất *
          <textarea
            id={ids.reason}
            rows={3}
            value={draft.reason}
            onChange={(event) => set("reason", event.target.value)}
            className={area}
          />
        </label>
        <label htmlFor={ids.replaces} className={label}>
          Thay thế cho tài nguyên (nếu có)
          <input
            id={ids.replaces}
            value={draft.replaces}
            onChange={(event) => set("replaces", event.target.value)}
            className={input}
            placeholder="VD: ADS-005"
          />
        </label>
        <label htmlFor={ids.budget} className={label}>
          Dự toán tối đa (đ) *
          <input
            id={ids.budget}
            inputMode="numeric"
            value={digits(draft.budget) ? Number(digits(draft.budget)).toLocaleString("vi-VN") : ""}
            onChange={(event) => set("budget", digits(event.target.value))}
            className={`${input} text-right tabular-nums`}
          />
        </label>
        <label htmlFor={ids.needed} className={label}>
          Cần có trước ngày *
          <DateInput
            id={ids.needed}
            value={draft.needed_by}
            onChange={(event) => set("needed_by", event.target.value)}
            className={input}
            required
            aria-invalid={field === "needed_by" || undefined}
          />
        </label>
        <div className={label}>
          Tuyến duyệt dự kiến
          <p className="mt-1 min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-2 text-sm text-[var(--text)]">
            {route}
          </p>
        </div>
        {draft.type_id && holding.data ? (
          <p className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm sm:col-span-2">
            Hệ thống kiểm tra: bạn đang giữ <b>{holding.data.count}</b> {typeName} ở trạng thái hoạt động.
            Người duyệt sẽ thấy con số này.
          </p>
        ) : null}
        {send.isError ? (
          <div className="sm:col-span-2">
            <NoticeBox error={send.error} onDismiss={() => send.reset()} />
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <PrimaryButton type="submit" disabled={!ready || send.isPending}>
            {send.isPending ? "Đang gửi…" : "Gửi đề xuất"}
          </PrimaryButton>
          <Link
            href="/resources/requests"
            className="inline-flex min-h-11 items-center rounded-lg border border-[var(--border)] px-4 text-sm hover:bg-[var(--surface-muted)]"
          >
            Huỷ
          </Link>
        </div>
      </form>
    </div>
  );
}
