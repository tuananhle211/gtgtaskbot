"use client";

/**
 * "✓ Xác nhận số điện thoại" / "Xác nhận QR" (0102): the customer's pending POS
 * Cake order moved to "Đã xác nhận" (else a new confirmed order) - Pancake then
 * reports the Purchase to Facebook. The duplicate check runs first: a number /
 * QR seen elsewhere, or a customer already confirmed, needs "Đã kiểm tra".
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import {
  cskhApi,
  safeUrl,
  type ConfirmBody,
  type ConversationDetail,
  type InboxState,
  type PhoneCheck,
} from "@/lib/cskh";
import { formatDateTime } from "@/lib/datetime";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { Select } from "@/components/pr";
import { Loading } from "@/components/states";
import { ErrorLine, fieldClass } from "@/components/cskh-common";

type Person = { user_id: string; name: string };

export function OrderConfirmDialog({
  mode,
  detail,
  frame,
  onClose,
  onDone,
}: {
  mode: "phone" | "qr";
  detail: ConversationDetail;
  frame: InboxState;
  onClose: () => void;
  onDone: () => void;
}) {
  const conv = detail.conversation;
  const [phone, setPhone] = useState(conv.phones[0] ?? "");
  const [checkedPhone, setCheckedPhone] = useState(phone);
  const [form, setForm] = useState({
    customer_name: conv.customer_name,
    address: "",
    content_name: "",
    order_status: frame.order_statuses[0] ?? "",
    platform: frame.platforms[0] ?? "Facebook",
    marketer_user_id: "",
    creator_user_id: "",
    telesale_name: "",
    customer_code: "",
    note: "",
  });
  const [ack, setAck] = useState(false);
  const [refused, setRefused] = useState<PhoneCheck | null>(null);
  const check = useQuery({
    queryKey: ["cskh", "phone-check", conv.id, checkedPhone],
    queryFn: () => cskhApi.phoneCheck(conv.id, checkedPhone),
  });
  const result = refused ?? check.data ?? null;

  const people = useMemo(() => {
    // Marketing: Phòng Ads first, then whoever the data already names, then everybody.
    const everybody = frame.people ?? [];
    const named = new Map<string, string>();
    for (const page of frame.pages)
      if (page.marketer_user_id && page.marketer_name)
        named.set(page.marketer_user_id, page.marketer_name);
    if (result?.page_marketer?.user_id)
      named.set(result.page_marketer.user_id, result.page_marketer.name ?? "");
    if (result?.old_marketer?.user_id)
      named.set(result.old_marketer.user_id, result.old_marketer.name);
    const ads = everybody.filter((p) => p.departments.includes("ADS"));
    const ordered: Person[] = [
      ...ads.map(({ user_id, name }) => ({ user_id, name })),
      ...[...named.entries()]
        .filter(([id]) => !ads.some((p) => p.user_id === id))
        .map(([user_id, name]) => ({ user_id, name })),
    ];
    if (ordered.length === 0) {
      for (const member of detail.page.members)
        ordered.push({ user_id: member.user_id, name: member.name });
    }
    return ordered;
  }, [detail.page.members, frame.pages, frame.people, result]);

  const telesales = useMemo(
    () => (frame.people ?? []).filter((p) => p.departments.includes("TELE")),
    [frame.people],
  );
  const creators = useMemo(() => {
    const members = detail.page.members.map((m) => ({
      user_id: m.user_id,
      name: `${m.name} (${m.role_label})`,
    }));
    const rest = (frame.people ?? [])
      .filter((p) => !detail.page.members.some((m) => m.user_id === p.user_id))
      .map(({ user_id, name }) => ({ user_id, name }));
    return [...members, ...rest];
  }, [detail.page.members, frame.people]);

  useEffect(() => {
    // Prefill the marketer from the oldest data, else the page's.
    const preset =
      result?.old_marketer?.user_id ?? result?.page_marketer?.user_id ?? "";
    setForm((current) =>
      current.marketer_user_id
        ? current
        : { ...current, marketer_user_id: preset },
    );
  }, [result?.old_marketer?.user_id, result?.page_marketer?.user_id]);

  const submit = useMutation({
    mutationFn: () => {
      const body: ConfirmBody = {
        confirm_type: mode,
        phone: phone.trim(),
        customer_name: form.customer_name.trim(),
        address: form.address.trim(),
        content_name: form.content_name.trim(),
        order_status: form.order_status,
        platform: form.platform,
        marketer_user_id: form.marketer_user_id || null,
        creator_user_id: form.creator_user_id || null,
        telesale_name: form.telesale_name.trim(),
        telesale_user_id:
          telesales.find((p) => p.name === form.telesale_name.trim())
            ?.user_id ?? null,
        customer_code: form.customer_code.trim(),
        note: form.note.trim(),
        dup_ack: ack,
      };
      return cskhApi.confirm(conv.id, body);
    },
    onSuccess: () => {
      onDone();
      onClose();
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        error.details?.reason === "cskh_duplicate" &&
        error.details.dup
      ) {
        setRefused(error.details.dup as PhoneCheck);
        setAck(false);
      }
    },
  });

  const needAck = Boolean(result?.need_ack);
  const old = result?.old_marketer;
  const set =
    (key: keyof typeof form) => (event: { target: { value: string } }) =>
      setForm((current) => ({ ...current, [key]: event.target.value }));

  return (
    <Modal
      open
      onClose={onClose}
      busy={submit.isPending}
      size="xl"
      title={mode === "qr" ? "Xác nhận QR" : "Xác nhận số điện thoại"}
      description={`${conv.customer_name} · ${conv.page_name}`}
    >
      <ModalBody className="space-y-4">
        {mode === "qr" ? (
          <div className="space-y-2">
            {conv.qrs.map((qr, index) => (
              <div
                key={`${qr.mid}-${index}`}
                className="flex gap-2 rounded-lg border border-[var(--border)] p-2 text-xs"
              >
                {safeUrl(qr.url) ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={safeUrl(qr.url)!}
                    alt="Ảnh mã QR"
                    className="h-20 w-20 rounded object-cover"
                  />
                ) : null}
                <div className="min-w-0">
                  <p className="font-semibold">QR {qr.kind}</p>
                  <p className="text-[var(--text-muted)]">
                    Gửi lúc {formatDateTime(qr.at)}
                  </p>
                  <p className="break-all">{qr.text}</p>
                </div>
              </div>
            ))}
          </div>
        ) : null}
        <DuplicateBox check={result} loading={check.isLoading && !refused} />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Họ tên KH">
            <input
              className={fieldClass}
              value={form.customer_name}
              onChange={set("customer_name")}
            />
          </Field>
          <Field label="Số điện thoại">
            <div className="flex gap-2">
              <input
                className={fieldClass}
                inputMode="tel"
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                onBlur={() => {
                  if (phone.trim() !== checkedPhone) {
                    setRefused(null);
                    setCheckedPhone(phone.trim());
                  }
                }}
              />
            </div>
          </Field>
          <Field label="Địa chỉ">
            <input
              className={fieldClass}
              value={form.address}
              onChange={set("address")}
            />
          </Field>
          <Field label="Dịch vụ">
            <input
              className={fieldClass}
              value={form.content_name}
              onChange={set("content_name")}
              placeholder={mode === "qr" ? "Để trống = Xác nhận QR" : ""}
            />
          </Field>
          <Field label="Trạng thái">
            <Select
              value={form.order_status}
              onChange={set("order_status")}
              className="w-full"
            >
              <option value="">—</option>
              {frame.order_statuses.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Nền tảng">
            <Select
              value={form.platform}
              onChange={set("platform")}
              className="w-full"
            >
              {frame.platforms.map((platform) => (
                <option key={platform} value={platform}>
                  {platform}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="Marketing"
            hint={
              old
                ? `Theo dữ liệu cũ: ${old.name} · ${old.page_name}${old.at ? ` · ${formatDateTime(old.at)}` : ""}${
                    old.locked
                      ? ` (giữ ${old.lock_days} ngày, chỉ quản trị đổi được)`
                      : ""
                  }`
                : undefined
            }
          >
            <Select
              value={form.marketer_user_id}
              onChange={set("marketer_user_id")}
              className="w-full"
            >
              <option value="">Tự động (theo dữ liệu cũ / page)</option>
              {people.map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Người tạo">
            <Select
              value={form.creator_user_id}
              onChange={set("creator_user_id")}
              className="w-full"
            >
              <option value="">Tôi</option>
              {creators.map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Telesale">
            <input
              className={fieldClass}
              value={form.telesale_name}
              onChange={set("telesale_name")}
              list="cskh-telesale-names"
            />
            <datalist id="cskh-telesale-names">
              {[
                ...telesales.map((p) => p.name),
                ...frame.pages
                  .flatMap((page) => page.pancake_users)
                  .map((u) => u.name),
              ]
                .filter(
                  (name, index, all) => name && all.indexOf(name) === index,
                )
                .map((name) => (
                  <option key={name} value={name} />
                ))}
            </datalist>
          </Field>
          <Field label="Mã KH">
            <input
              className={fieldClass}
              value={form.customer_code}
              onChange={set("customer_code")}
            />
          </Field>
          <Field label="Ghi chú" wide>
            <textarea
              className={`${fieldClass} py-2`}
              rows={2}
              value={form.note}
              onChange={set("note")}
            />
          </Field>
        </div>
        {needAck ? (
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={ack}
              onChange={(event) => setAck(event.target.checked)}
            />
            Đã kiểm tra, vẫn xác nhận đơn này
          </label>
        ) : null}
        <ErrorLine error={submit.error} />
      </ModalBody>
      <ModalFooter>
        <button
          type="button"
          onClick={onClose}
          className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm"
        >
          Đóng
        </button>
        <ConfirmButton
          spec={{
            title:
              mode === "qr"
                ? "Xác nhận QR cho khách này?"
                : "Xác nhận số điện thoại cho khách này?",
            description:
              "Đơn trên Pancake POS chuyển sang Đã xác nhận (hoặc tạo đơn mới đã xác nhận); Pancake tính 1 lượt mua cho quảng cáo đã đưa khách vào.",
            confirmLabel: "Xác nhận đơn",
          }}
          onConfirm={() => submit.mutate()}
          pending={submit.isPending}
          error={submit.error}
          disabled={(needAck && !ack) || check.isLoading}
          tone="primary"
        >
          Xác nhận đơn
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

function Field({
  label,
  hint,
  wide,
  children,
}: {
  label: string;
  hint?: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className={`block space-y-1 text-sm ${wide ? "sm:col-span-2" : ""}`}>
      <span className="font-medium">{label}</span>
      {children}
      {hint ? (
        <span className="block text-xs text-[var(--text-muted)]">{hint}</span>
      ) : null}
    </label>
  );
}

function DuplicateBox({
  check,
  loading,
}: {
  check: PhoneCheck | null;
  loading: boolean;
}) {
  if (loading) return <Loading label="Đang kiểm tra trùng số…" />;
  if (!check) return null;
  if (!check.need_ack) {
    return (
      <p
        className="rounded-lg border border-[var(--good)]/30 bg-[var(--good-soft)] p-3 text-sm"
        data-testid="cskh-new-customer"
      >
        Khách mới: số / mã QR chưa có ở page hay đơn nào khác.
        {check.pos_error ? ` (Không kiểm được POS: ${check.pos_error})` : ""}
      </p>
    );
  }
  return (
    <div
      className="space-y-2 rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm"
      data-testid="cskh-duplicate"
    >
      {check.own_confirmed.length ? (
        <p className="font-semibold">
          Khách này đã được xác nhận đơn rồi ({check.own_confirmed.length} đơn)
          — xác nhận thêm sẽ tính thêm 1 lượt mua.
        </p>
      ) : null}
      {check.seen.length || check.orders.length ? (
        <p className="font-semibold">Số / mã QR đã có trên hệ thống:</p>
      ) : null}
      <ul className="space-y-1 text-xs">
        {check.seen.map((item) => (
          <li key={`${item.conversation_id}-${item.phone}`}>
            {item.page_name} · {item.customer_name || "khách"} · {item.phone} ·
            lần đầu {formatDateTime(item.first_at)}
            {item.times > 1 ? ` · ${item.times} lần` : ""}{" "}
            <a href={`/cskh?c=${item.conversation_id}`} className="underline">
              Mở hội thoại
            </a>
          </li>
        ))}
        {check.orders.map((order, index) => (
          <li key={`o-${index}`}>
            Đơn {order.source} #{order.order_no || "—"} · {order.page_name} ·{" "}
            {order.status_name ?? ""} · {formatDateTime(order.at)}
            {order.marketer ? ` · MKT ${order.marketer}` : ""}
          </li>
        ))}
      </ul>
      {check.pos_error ? (
        <p className="text-xs">Không kiểm được POS: {check.pos_error}</p>
      ) : null}
    </div>
  );
}
