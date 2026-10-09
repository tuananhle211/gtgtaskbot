"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type OrderAction, type OrderDetail, type OrderNode } from "@/lib/api";
import { formatWhen } from "@/lib/labels";
import { ConfirmButton } from "@/components/confirm";
import { Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";

/** The route each action posts to, keyed by the server's action kind. */
function actionPath(action: OrderAction): string {
  const node = action.node_id ? `/nodes/${action.node_id}` : "";
  switch (action.kind) {
    case "RESUBMIT":
      return "/resubmit";
    case "APPROVE_ORDER":
      return "/approve";
    case "RETURN_ORDER":
      return "/return";
    case "ASSIGN":
      return `${node}/assign`;
    case "ACCEPT":
      return `${node}/accept`;
    case "SUBMIT_WORK":
      return `${node}/submit`;
    case "APPROVE_NODE":
      return `${node}/approve`;
    case "RETURN_NODE":
      return `${node}/return`;
    case "APPROVE_VIDEO":
      return "/video/approve";
    case "RETURN_VIDEO":
      return "/video/return";
    case "APPROVE_FINAL":
      return "/final/approve";
    case "RETURN_FINAL":
      return "/final/return";
    case "SET_PRIORITY":
      return "/priority";
    case "CANCEL":
      return "/cancel";
    default:
      return `/${action.kind.toLowerCase()}`;
  }
}

function statusTone(status: string): "neutral" | "warn" | "good" | "bad" | "critical" {
  if (status === "HOAN_THANH") return "good";
  if (status === "CHO_DUYET" || status === "CHUA_GIAO") return "warn";
  if (status === "DANG_SUA") return "bad";
  return "neutral";
}

export default function OrderDetailPage() {
  const params = useParams<{ ref: string }>();
  const ref = String(params?.ref ?? "");
  const queryClient = useQueryClient();
  const detail = useQuery({ queryKey: ["order", ref], queryFn: () => api.order(ref), enabled: Boolean(ref) });

  if (detail.isPending) return <Loading label="Đang tải order…" />;
  if (detail.isError) return <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />;

  const data = detail.data;
  const order = data.order;
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["order", ref] });
    void queryClient.invalidateQueries({ queryKey: ["board"] });
  };
  const planned = data.nodes.filter((node) => node.status !== "BO_QUA");
  const doneCount = planned.filter((node) => node.status === "HOAN_THANH").length;

  return (
    <div className="space-y-5">
      <nav aria-label="Vị trí" className="text-xs text-[var(--text-muted)]">
        <Link href="/tasks?unit=ADS" className="hover:underline">
          Quản lý task
        </Link>
        <span className="mx-1.5">/</span>
        <span className="font-mono">{order.code}</span>
      </nav>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="unit-tag unit-tag-ads">ORD</span>
            <span className="font-mono text-sm font-semibold">{order.code}</span>
            <Pill tone={order.stage === "COMPLETED" ? "good" : order.stage === "CANCELLED" ? "neutral" : "warn"}>
              {order.stage_label}
            </Pill>
            {order.is_priority ? <Pill tone="warn">Ưu tiên</Pill> : null}
            {order.urgent ? <Pill tone="critical">Gấp</Pill> : null}
          </div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight sm:text-2xl">{order.title}</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            {order.video_type_label} · Marketing: {order.owner_name ?? ""} · Lên order {formatWhen(order.submitted_at)}
            {order.completed_at ? ` · Hoàn thành ${formatWhen(order.completed_at)}` : ""}
          </p>
        </div>
        <dl className="grid grid-cols-3 gap-x-6 text-center text-xs text-[var(--text-muted)]">
          <div>
            <dt>Công đoạn</dt>
            <dd className="text-lg font-semibold tabular-nums text-[var(--text)]">
              {doneCount}/{planned.length}
            </dd>
          </div>
          <div>
            <dt>Trả sửa</dt>
            <dd className="text-lg font-semibold tabular-nums text-[var(--text)]">
              {data.nodes.reduce((sum, node) => sum + node.revision_count, 0)}
            </dd>
          </div>
          <div>
            <dt>Bản nộp</dt>
            <dd className="text-lg font-semibold tabular-nums text-[var(--text)]">{data.submissions.length}</dd>
          </div>
        </dl>
      </header>

      <section aria-label="Tiến trình" className="panel overflow-x-auto p-3">
        <ol className="flex min-w-[56rem] items-stretch gap-2">
          <Step
            label="Lên order"
            person={order.owner_name ?? ""}
            status={order.stage === "ORDER_RETURNED" ? "Bị trả, chờ gửi lại" : "Đã gửi"}
            tone={order.stage === "ORDER_RETURNED" ? "bad" : "good"}
            when={formatWhen(order.submitted_at)}
            current={order.stage === "ORDER_RETURNED"}
          />
          <Step
            label="Duyệt order"
            person="Trưởng phòng"
            status={order.order_approved_at ? "Đã duyệt" : order.stage === "ORDER_PENDING" ? "Chờ duyệt" : "Chưa tới"}
            tone={order.order_approved_at ? "good" : order.stage === "ORDER_PENDING" ? "warn" : "neutral"}
            when={order.order_approved_at ? formatWhen(order.order_approved_at) : ""}
            current={order.stage === "ORDER_PENDING"}
          />
          {data.nodes.map((node) => (
            <Step
              key={node.id}
              label={node.node_type_label}
              person={node.assignee_name ?? (node.preassigned_name ? `Chọn sẵn: ${node.preassigned_name}` : node.status === "BO_QUA" ? "—" : "Để trống")}
              status={node.status_label}
              tone={statusTone(node.status)}
              when={
                node.approved_at
                  ? `Duyệt ${formatWhen(node.approved_at)}`
                  : node.submitted_at
                    ? `Nộp ${formatWhen(node.submitted_at)}`
                    : node.activated_at
                      ? `Tới lượt ${formatWhen(node.activated_at)}`
                      : ""
              }
              extra={node.revision_count > 0 ? `trả sửa ${node.revision_count} lần` : undefined}
              current={node.is_current}
              skipped={node.status === "BO_QUA"}
            />
          ))}
          <Step
            label="Duyệt Final"
            person="Trưởng phòng"
            status={order.stage === "COMPLETED" ? "Hoàn thành" : order.stage === "FINAL_REVIEW" ? "Chờ duyệt" : "Chưa tới"}
            tone={order.stage === "COMPLETED" ? "good" : order.stage === "FINAL_REVIEW" ? "warn" : "neutral"}
            when={order.completed_at ? formatWhen(order.completed_at) : ""}
            current={order.stage === "FINAL_REVIEW"}
          />
        </ol>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,4fr)_minmax(0,3fr)]">
        <div className="space-y-4">
          <section className="panel p-4 text-sm">
            <h2 className="font-semibold">Order từ ORD</h2>
            <dl className="mt-3 grid gap-2 sm:grid-cols-[9rem_minmax(0,1fr)]">
              <dt className="text-[var(--text-muted)]">Source</dt>
              <dd>{order.script_source === "AI" ? "AI" : order.script_source === "REAL" ? "Quay thực tế" : "–"}</dd>
              <dt className="text-[var(--text-muted)]">Nội dung order</dt>
              <dd className="whitespace-pre-wrap leading-relaxed">{order.order_content}</dd>
              {[
                ["Link thiết kế", order.design_link],
                ["Link tham khảo", order.reference_link],
                ["Link source", order.source_link],
                ["Link sản phẩm", order.product_link],
              ].map(([label, link]) => (
                <LinkRow key={label as string} label={label as string} link={link as string | null} />
              ))}
              {order.returned_reason ? (
                <>
                  <dt className="text-[var(--text-muted)]">Lý do trả</dt>
                  <dd className="text-[var(--bad)]">{order.returned_reason}</dd>
                </>
              ) : null}
              {order.cancelled_reason ? (
                <>
                  <dt className="text-[var(--text-muted)]">Lý do huỷ</dt>
                  <dd>{order.cancelled_reason}</dd>
                </>
              ) : null}
            </dl>
          </section>
          <section className="panel p-4 text-sm">
            <h2 className="font-semibold">Bản nộp</h2>
            {data.submissions.length === 0 ? (
              <p className="mt-2 text-[var(--text-muted)]">Chưa có bản nộp nào.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {[...data.submissions].reverse().map((item) => (
                  <li key={item.id} className="rounded-lg border border-[var(--border)] p-3">
                    <p className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
                      <span className="font-mono font-semibold text-[var(--text)]">{item.label}</span>
                      <span>{item.node_type}</span>
                      <span>· {item.submitted_by_name}</span>
                      <span>· {formatWhen(item.created_at)}</span>
                    </p>
                    {item.link ? (
                      <a href={item.link} target="_blank" rel="noreferrer" className="mt-1 block break-all text-[var(--accent)]">
                        {item.link}
                      </a>
                    ) : null}
                    {item.script_text ? <p className="mt-2 whitespace-pre-wrap leading-relaxed">{item.script_text}</p> : null}
                    {item.note ? <p className="mt-1 text-xs text-[var(--text-muted)]">Ghi chú: {item.note}</p> : null}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <div className="space-y-4 xl:order-first xl:col-start-3 xl:row-start-1">
          <div className="xl:sticky xl:top-20">
            <ActionPanel detail={data} onDone={refresh} />
          </div>
        </div>

        <aside className="space-y-4">
          <section className="panel p-4 text-sm">
            <h2 className="font-semibold">Người tham gia</h2>
            <dl className="mt-3 grid grid-cols-[8rem_minmax(0,1fr)] gap-y-1.5 text-sm">
              <dt className="text-[var(--text-muted)]">Marketing</dt>
              <dd>{order.owner_name ?? "–"}</dd>
              {data.nodes
                .filter((node) => node.status !== "BO_QUA")
                .map((node) => (
                  <React.Fragment key={node.id}>
                    <dt className="text-[var(--text-muted)]">{node.node_type_label}</dt>
                    <dd className={node.assignee_name ? "" : "text-[var(--text-muted)]"}>
                      {node.assignee_name ?? (node.preassigned_name ? `Chọn sẵn: ${node.preassigned_name}` : "Chưa giao")}
                    </dd>
                  </React.Fragment>
                ))}
            </dl>
          </section>
          <section className="panel p-4 text-sm">
            <h2 className="font-semibold">Quyết định ở các cổng</h2>
            {data.approvals.length === 0 ? (
              <p className="mt-2 text-[var(--text-muted)]">Chưa có.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {data.approvals.map((item) => (
                  <li key={item.id} className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs">
                      <span className="font-medium">{item.gate}</span> · lần {item.round_no} · {item.actor_name}
                    </span>
                    <Pill tone={item.decision === "APPROVED" ? "good" : "bad"}>
                      {item.decision === "APPROVED" ? "Duyệt" : "Trả"}
                    </Pill>
                    {item.comment ? <span className="w-full text-xs text-[var(--text-muted)]">{item.comment}</span> : null}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="panel p-4 text-sm">
            <h2 className="font-semibold">Hoạt động</h2>
            <ol className="mt-3 space-y-2 border-l border-[var(--border)] pl-3">
              {[...data.events].reverse().map((item) => (
                <li key={item.id} className="relative text-xs">
                  <span className="absolute -left-[0.95rem] top-1.5 h-2 w-2 rounded-full bg-[var(--border)]" />
                  <span className="block text-[var(--text-muted)]">{formatWhen(item.created_at)}</span>
                  <span>
                    {item.actor_name}: {item.kind_label}
                    {item.node_type ? ` (${item.node_type})` : ""}
                    {item.assignee_name ? ` → ${item.assignee_name}` : ""}
                    {item.note ? ` – ${item.note}` : ""}
                  </span>
                </li>
              ))}
            </ol>
          </section>
        </aside>
      </div>
    </div>
  );
}

/** One step of the horizontal progress strip. */
function Step({
  label,
  person,
  status,
  tone,
  when,
  extra,
  current,
  skipped,
}: {
  label: string;
  person: string;
  status: string;
  tone: "neutral" | "warn" | "good" | "bad" | "critical";
  when: string;
  extra?: string;
  current: boolean;
  skipped?: boolean;
}) {
  return (
    <li
      className={`flex min-w-[9rem] flex-1 flex-col gap-1 rounded-lg border p-2.5 ${
        current ? "border-2 border-[var(--text)]" : "border-[var(--border)]"
      } ${tone === "good" && !current ? "bg-[var(--good-soft)]/40" : ""} ${skipped ? "opacity-50" : ""}`}
    >
      <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">{label}</span>
      <span className="truncate text-sm font-medium">{person}</span>
      <Pill tone={tone}>{status}</Pill>
      {when ? <span className="text-[11px] text-[var(--text-muted)]">{when}</span> : null}
      {extra ? <span className="text-[11px] text-[var(--bad)]">{extra}</span> : null}
    </li>
  );
}

function LinkRow({ label, link }: { label: string; link: string | null }) {
  return (
    <>
      <dt className="text-[var(--text-muted)]">{label}</dt>
      <dd>
        {link ? (
          <a href={link} target="_blank" rel="noreferrer" className="break-all text-[var(--accent)]">
            {link}
          </a>
        ) : (
          "–"
        )}
      </dd>
    </>
  );
}

/**
 * The buttons this person may press right now, from `available_actions`.
 *
 * Every action carries the order's version; a stale press is a 409 the
 * dialog shows in place. Fields an action needs (a note, a link, a person)
 * are collected in the same dialog.
 */
function ActionPanel({ detail, onDone }: { detail: OrderDetail; onDone: () => void }) {
  const actions = detail.available_actions;
  const [fields, setFields] = useState<Record<string, string>>({});
  const members = useQuery({
    queryKey: ["units", "ADS", "members"],
    queryFn: () => api.unitMembers("ADS"),
    enabled: actions.some((action) => action.kind === "ASSIGN"),
  });
  const run = useMutation({
    mutationFn: ({ action, body }: { action: OrderAction; body: Record<string, unknown> }) =>
      api.orderAction(detail.order.id, actionPath(action), { version: detail.order.version, ...body }),
    onSuccess: onDone,
  });
  if (actions.length === 0) {
    return (
      <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 text-sm text-[var(--text-muted)]">
        Bạn không có thao tác nào trên order này lúc này.
      </section>
    );
  }
  const current = detail.nodes.find((node) => node.is_current) ?? null;
  const value = (key: string) => fields[key] ?? "";
  const setValue = (key: string, next: string) => setFields((all) => ({ ...all, [key]: next }));
  const field =
    "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

  const bodyFor = (action: OrderAction): Record<string, unknown> => {
    switch (action.kind) {
      case "ASSIGN":
        return { assignee_user_id: value("assignee") };
      case "SUBMIT_WORK":
        return { link: value("link") || null, script_text: value("script_text") || null, note: value("note") || null };
      case "APPROVE_FINAL":
        return { product_link: value("link") || null };
      case "SET_PRIORITY":
        return { is_priority: !detail.order.is_priority };
      case "RESUBMIT":
        return {};
      default:
        return action.requires_note ? { note: value("note") } : {};
    }
  };
  const detailsFor = (action: OrderAction, node: OrderNode | null) => {
    const parts: React.ReactNode[] = [];
    if (action.kind === "ASSIGN") {
      const role = node?.node_type;
      parts.push(
        <label key="assignee" className="block text-xs">
          Giao cho
          <Select value={value("assignee")} onChange={(event) => setValue("assignee", event.target.value)} className="mt-1 w-full">
            <option value="">— chọn —</option>
            {(members.data?.members ?? [])
              .filter((member) => member.active && member.role === role)
              .map((member) => (
                <option key={member.user_id} value={member.user_id}>
                  {member.full_name}
                </option>
              ))}
          </Select>
        </label>,
      );
    }
    if (action.kind === "SUBMIT_WORK" || action.kind === "APPROVE_FINAL") {
      parts.push(
        <label key="link" className="block text-xs">
          {action.kind === "APPROVE_FINAL" ? "Link sản phẩm (để trống = dùng link đã nộp)" : "Link"}
          <input type="url" value={value("link")} onChange={(event) => setValue("link", event.target.value)} className={`mt-1 ${field}`} />
        </label>,
      );
    }
    if (action.kind === "SUBMIT_WORK" && node?.node_type === "BIEN_TAP") {
      parts.push(
        <label key="script" className="block text-xs">
          Nội dung kịch bản
          <textarea value={value("script_text")} onChange={(event) => setValue("script_text", event.target.value)} className={`mt-1 ${field} min-h-24`} />
        </label>,
      );
    }
    if (action.requires_note || action.kind === "SUBMIT_WORK") {
      parts.push(
        <label key="note" className="block text-xs">
          {action.requires_note ? "Lý do (bắt buộc)" : "Ghi chú"}
          <textarea value={value("note")} onChange={(event) => setValue("note", event.target.value)} className={`mt-1 ${field} min-h-20`} />
        </label>,
      );
    }
    return parts.length ? <div className="space-y-2">{parts}</div> : undefined;
  };
  const describe = (action: OrderAction): string => {
    switch (action.kind) {
      case "APPROVE_ORDER":
        return "Order chuyển sang công đoạn đầu tiên; trưởng phòng ban đó và người được chọn sẵn nhận thông báo.";
      case "RETURN_ORDER":
        return "Order quay về người order để sửa và gửi lại.";
      case "APPROVE_NODE":
        return "Công đoạn hoàn thành, ghi KPI cho người làm và kích hoạt bước tiếp theo.";
      case "RETURN_NODE":
      case "RETURN_VIDEO":
      case "RETURN_FINAL":
        return "Bài quay lại người làm kèm ghi chú.";
      case "APPROVE_FINAL":
        return "Order hoàn thành; link sản phẩm hiện cho người order.";
      case "CANCEL":
        return "Order dừng tại đây. KPI đã ghi được giữ nguyên.";
      case "SET_PRIORITY":
        return detail.order.is_priority ? "Bỏ cờ Ưu tiên." : "Order báo đỏ và lên đầu danh sách của mọi bộ phận liên quan.";
      default:
        return "";
    }
  };
  const destructive = new Set(["RETURN_ORDER", "RETURN_NODE", "RETURN_VIDEO", "RETURN_FINAL", "CANCEL"]);

  return (
    <section className="panel border-[var(--accent)] p-4">
      <h2 className="text-sm font-semibold">
        Thao tác của bạn{current ? ` · ${current.node_type_label}` : ""}
      </h2>
      <div className="mt-3 flex flex-wrap gap-2">
        {actions.map((action) => (
          <ConfirmButton
            key={`${action.kind}:${action.node_id ?? ""}`}
            spec={{
              title: `${action.kind === "SET_PRIORITY" && detail.order.is_priority ? "Bỏ ưu tiên" : action.label} ${detail.order.code}?`,
              description: describe(action),
              confirmLabel: action.kind === "SET_PRIORITY" && detail.order.is_priority ? "Bỏ ưu tiên" : action.label,
              variant: destructive.has(action.kind) ? "destructive" : "primary",
              details: detailsFor(action, current),
            }}
            onConfirm={() => run.mutate({ action, body: bodyFor(action) })}
            pending={run.isPending}
            error={run.error}
            tone={destructive.has(action.kind) ? "danger" : action.kind.startsWith("APPROVE") || action.kind === "SUBMIT_WORK" ? "primary" : "secondary"}
          >
            {action.kind === "SET_PRIORITY" && detail.order.is_priority ? "Bỏ ưu tiên" : action.label}
          </ConfirmButton>
        ))}
      </div>
      {run.isError ? <NoticeBox error={run.error} /> : null}
    </section>
  );
}
