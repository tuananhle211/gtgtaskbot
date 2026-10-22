"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ResourceRequest, type ResourceRequestAction } from "@/lib/api";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { formatVnd, isWebLink, requestTone } from "@/lib/resources";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader } from "@/components/pr";
import { PersonLink } from "@/components/person-link";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";

/**
 * One "Đề xuất tài nguyên" (2026-10-21): the five steps, what was asked, the
 * panel of what this viewer may do now (the server's `actions`), the
 * resources bought for it and its history.
 */
export default function ResourceRequestPage() {
  const params = useParams<{ id: string }>();
  const id = String(params.id);
  const request = useQuery({ queryKey: ["resource-request", id], queryFn: () => api.resourceRequest(id) });
  if (request.isPending) return <Loading />;
  if (request.isError) return <ErrorBox error={request.error} onRetry={() => request.refetch()} />;
  const row = request.data;
  return (
    <div className="space-y-4">
      <PageHeader
        title={`${row.code} · Đề xuất ${row.quantity} ${row.type_name}`}
        subtitle={`Tạo ${formatDateTime(row.created_at)} bởi ${row.requester.full_name} · ${row.stage_label}`}
        action={
          <Link
            href="/resources/requests"
            className="inline-flex min-h-10 items-center rounded-lg border border-[var(--border)] px-3 text-sm hover:bg-[var(--surface-muted)]"
          >
            ← Danh sách đề xuất
          </Link>
        }
      />
      <Stepper row={row} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="space-y-4">
          <Content row={row} />
          <Delivered row={row} />
        </div>
        <div className="space-y-4">
          {row.actions.length > 0 ? <ActionPanel row={row} /> : null}
          <History row={row} />
        </div>
      </div>
    </div>
  );
}

/**
 * The process strip, drawn like the task page's (`StepStrip` in
 * app/tasks/[ref]/page.tsx): one card per step - its name, who did it (or who
 * it waits on), a status badge, when, and the note (the "phiếu chi", the
 * purchase summary, a refusal's reason).
 */
const STEP_COLOR: Record<string, string> = {
  done: "green",
  current: "amber",
  stopped: "red",
  skipped: "slate",
  todo: "slate",
};

function Stepper({ row }: { row: ResourceRequest }) {
  return (
    <section aria-label="Tiến trình" className="panel overflow-x-auto p-3">
      <ol className="flex items-stretch gap-2" style={{ minWidth: `${row.steps.length * 9.5}rem` }}>
        {row.steps.map((step) => {
          const current = step.state === "current";
          const done = step.state === "done";
          const muted = step.state === "skipped" || step.state === "todo";
          return (
            <li
              key={step.key}
              data-state={step.state}
              aria-current={current ? "step" : undefined}
              className={`flex min-w-[9rem] flex-1 flex-col gap-1 rounded-lg border p-2.5 ${
                current ? "border-2 border-[var(--text)]" : "border-[var(--border)]"
              } ${done ? "bg-[var(--good-soft)]/40" : ""} ${step.state === "skipped" ? "opacity-50" : ""}`}
            >
              <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                {step.label}
              </span>
              <span
                className={
                  step.person_name && !muted
                    ? current
                      ? "truncate text-sm font-semibold"
                      : "truncate text-sm font-medium"
                    : "text-sm text-[var(--text-muted)]"
                }
              >
                {step.person_name && step.person_user_id && !current ? (
                  <PersonLink userId={step.person_user_id} name={step.person_name} />
                ) : (
                  (step.person_name ?? "–")
                )}
              </span>
              <span className={`st st-${STEP_COLOR[step.state] ?? "slate"} self-start`}>
                <span>{step.status_label || "–"}</span>
              </span>
              {step.at ? (
                <span className="text-[11px] text-[var(--text-muted)]">{formatDateTime(step.at)}</span>
              ) : null}
              {step.note ? (
                <span className="line-clamp-2 text-[11px] text-[var(--text-muted)]" title={step.note}>
                  {step.note}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Content({ row }: { row: ResourceRequest }) {
  const lines: Array<[string, React.ReactNode]> = [
    ["Loại / số lượng", `${row.type_name} × ${row.quantity}`],
    ["Cơ sở sử dụng", row.site_label],
    ["Mục đích sử dụng", row.purpose],
    ["Yêu cầu", row.requirements ? <span className="whitespace-pre-line">{row.requirements}</span> : "–"],
    ["Lý do", <span key="reason" className="whitespace-pre-line">{row.reason}</span>],
    ["Thay thế cho", row.replaces ?? "–"],
    ["Dự toán tối đa", formatVnd(row.budget)],
    ["Cần trước", row.needed_by ? formatDate(row.needed_by) : "–"],
    [
      "Người đề xuất",
      <span key="requester">
        <PersonLink userId={row.requester.user_id} name={row.requester.full_name} />
        <span className="ml-1 text-xs text-[var(--text-muted)]">
          đang giữ {row.holding} {row.type_name} hoạt động
        </span>
      </span>,
    ],
    [
      "Trưởng phòng duyệt",
      row.approvers.length > 0 ? row.approvers.map((person) => person.full_name).join(", ") : "Không có (bỏ qua bước này)",
    ],
  ];
  if (row.actual_cost !== null) {
    lines.push(["Tổng tiền mua thực tế", formatVnd(row.actual_cost)]);
    lines.push(["Nguồn mua", row.supplier ?? "–"]);
  }
  return (
    <section className="panel p-4 sm:p-6" aria-label="Nội dung đề xuất">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Nội dung đề xuất</h2>
        <Pill tone={requestTone(row.stage)}>{row.stage_label}</Pill>
      </div>
      <dl className="mt-3 grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[11rem_minmax(0,1fr)]">
        {lines.map(([term, value]) => (
          <div key={term} className="contents">
            <dt className="text-[var(--text-muted)]">{term}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Delivered({ row }: { row: ResourceRequest }) {
  if (row.resources.length === 0) return null;
  return (
    <section className="panel p-4 sm:p-6" aria-label="Tài nguyên đã nhập kho">
      <h2 className="text-base font-semibold">
        Tài nguyên đã nhập kho ({row.resources.length}/{row.quantity})
      </h2>
      <div className="table-scroll-x mt-3">
        <table className="table-dense w-full">
          <thead>
            <tr>
              <th scope="col">Mã</th>
              <th scope="col">ID</th>
              <th scope="col">Link</th>
            </tr>
          </thead>
          <tbody>
            {row.resources.map((item) => (
              <tr key={item.id}>
                <td className="whitespace-nowrap font-mono">
                  <Link href={item.detail_path} className="text-[var(--accent)] underline">
                    {item.code}
                  </Link>
                </td>
                <td className="font-mono">{item.external_id}</td>
                <td className="max-w-[18rem] truncate">
                  {item.link && isWebLink(item.link) ? (
                    <a href={item.link} target="_blank" rel="noopener noreferrer" className="text-[var(--accent)] underline">
                      {item.link}
                    </a>
                  ) : (
                    (item.link ?? "–")
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function History({ row }: { row: ResourceRequest }) {
  return (
    <section className="panel p-4 sm:p-6" aria-label="Lịch sử">
      <h2 className="text-base font-semibold">Lịch sử</h2>
      <ol className="mt-3 space-y-3 text-sm">
        {row.events.map((event) => (
          <li key={event.id} className="border-l-2 border-[var(--border)] pl-3">
            <p className="font-medium">
              {event.label}
              {event.actor ? <span className="font-normal text-[var(--text-muted)]"> · {event.actor.full_name}</span> : null}
            </p>
            <p className="text-xs text-[var(--text-muted)]">{formatDateTime(event.at)}</p>
            {event.note ? <p className="mt-0.5 whitespace-pre-line">{event.note}</p> : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function ActionPanel({ row }: { row: ResourceRequest }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [lines, setLines] = useState("");
  const [cost, setCost] = useState("");
  const [supplier, setSupplier] = useState("");
  const ids = { note: useId(), lines: useId(), cost: useId(), supplier: useId() };
  const act = useMutation({
    mutationFn: (body: ResourceRequestAction) => api.actOnResourceRequest(row.id, body),
    onSuccess: (next) => {
      queryClient.setQueryData(["resource-request", row.id], next);
      void queryClient.invalidateQueries({ queryKey: ["resource-requests"] });
      void queryClient.invalidateQueries({ queryKey: ["resources"] });
      setNote("");
    },
  });
  const can = (action: string) => row.actions.includes(action);
  const counted = lines.split("\n").filter((line) => line.trim()).length;
  const costDigits = cost.replace(/[^\d]/g, "");
  const trimmedNote = note.trim() || null;
  const title =
    row.stage === "HEAD_REVIEW"
      ? "Xử lý · Trưởng phòng duyệt"
      : row.stage === "FUNDING"
        ? "Xử lý · Duyệt chi"
        : row.stage === "PURCHASING"
          ? "Xử lý · Mua & nhập kho"
          : row.stage === "DELIVERED"
            ? "Xử lý · Xác nhận đã nhận"
            : "Xử lý";
  const input =
    "mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

  return (
    <section className="panel space-y-3 p-4 sm:p-6" aria-label="Xử lý">
      <h2 className="text-base font-semibold">{title}</h2>
      {can("deliver") ? (
        <>
          <p className="text-sm text-[var(--text-muted)]">
            Dán danh sách {row.quantity} {row.type_name} đã mua, mỗi dòng một tài nguyên: <b>ID | Link</b>. Mỗi dòng
            thành một tài nguyên giao cho {row.requester.full_name}, gắn với mã {row.code}.
          </p>
          <label htmlFor={ids.lines} className="block text-xs text-[var(--text-muted)]">
            Danh sách {row.type_name} * ({counted}/{row.quantity} dòng)
            <textarea
              id={ids.lines}
              rows={8}
              value={lines}
              onChange={(event) => setLines(event.target.value)}
              placeholder={"61579008642880 | https://www.facebook.com/profile.php?id=61579008642880\n61579005693181 | https://…"}
              className={`${input} py-2 font-mono text-xs`}
            />
          </label>
          <label htmlFor={ids.cost} className="block text-xs text-[var(--text-muted)]">
            Tổng tiền mua thực tế (đ) *
            <input
              id={ids.cost}
              inputMode="numeric"
              value={costDigits ? Number(costDigits).toLocaleString("vi-VN") : ""}
              onChange={(event) => setCost(event.target.value)}
              className={`${input} text-right tabular-nums`}
            />
          </label>
          <label htmlFor={ids.supplier} className="block text-xs text-[var(--text-muted)]">
            Nguồn mua
            <input id={ids.supplier} value={supplier} onChange={(event) => setSupplier(event.target.value)} className={input} />
          </label>
        </>
      ) : null}
      <label htmlFor={ids.note} className="block text-xs text-[var(--text-muted)]">
        {can("reject") ? "Ghi chú (bắt buộc khi từ chối)" : "Ghi chú"}
        <textarea
          id={ids.note}
          rows={2}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder={can("fund") ? "VD: số phiếu chi bên ngoài" : undefined}
          className={`${input} py-2`}
        />
      </label>
      {act.isError ? <NoticeBox error={act.error} onDismiss={() => act.reset()} /> : null}
      <div className="flex flex-wrap gap-2">
        {can("approve") ? (
          <ConfirmButton
            tone="primary"
            pending={act.isPending}
            spec={{
              title: `Duyệt đề xuất ${row.code}?`,
              description: "Đề xuất chuyển sang Phòng Tài nguyên để duyệt chi.",
              confirmLabel: "Duyệt",
              variant: "primary",
            }}
            onConfirm={() => act.mutate({ action: "approve", note: trimmedNote })}
          >
            Duyệt
          </ConfirmButton>
        ) : null}
        {can("fund") ? (
          <ConfirmButton
            tone="primary"
            pending={act.isPending}
            spec={{
              title: `Duyệt chi cho ${row.code}?`,
              description: "Đề xuất chuyển sang bước mua và nhập kho.",
              confirmLabel: "Duyệt chi",
              variant: "primary",
            }}
            onConfirm={() => act.mutate({ action: "fund", note: trimmedNote })}
          >
            Duyệt chi
          </ConfirmButton>
        ) : null}
        {can("deliver") ? (
          <ConfirmButton
            tone="primary"
            pending={act.isPending}
            disabled={counted === 0 || !costDigits}
            spec={{
              title: `Nhập kho ${counted} ${row.type_name} và bàn giao?`,
              description: `${counted} tài nguyên được tạo, giao cho ${row.requester.full_name} và gắn với ${row.code}. Người đề xuất sẽ xác nhận đã nhận.`,
              confirmLabel: `Nhập kho ${counted} ${row.type_name}`,
              variant: "primary",
              count: counted,
            }}
            onConfirm={() =>
              act.mutate({
                action: "deliver",
                lines,
                actual_cost: Number(costDigits),
                supplier: supplier.trim() || null,
                note: trimmedNote,
              })
            }
          >
            Nhập kho {counted || row.quantity} {row.type_name} & bàn giao
          </ConfirmButton>
        ) : null}
        {can("confirm") ? (
          <ConfirmButton
            tone="primary"
            pending={act.isPending}
            spec={{
              title: "Xác nhận đã nhận đủ tài nguyên?",
              description: "Đề xuất được đánh dấu hoàn tất.",
              confirmLabel: "Đã nhận",
              variant: "primary",
            }}
            onConfirm={() => act.mutate({ action: "confirm", note: trimmedNote })}
          >
            Xác nhận đã nhận
          </ConfirmButton>
        ) : null}
        {can("reject") ? (
          <ConfirmButton
            tone="danger"
            pending={act.isPending}
            disabled={!trimmedNote}
            spec={{
              title: `Từ chối đề xuất ${row.code}?`,
              description: "Đề xuất dừng lại, người đề xuất thấy lý do bạn ghi.",
              confirmLabel: "Từ chối",
              variant: "destructive",
            }}
            onConfirm={() => act.mutate({ action: "reject", note: trimmedNote })}
          >
            Từ chối
          </ConfirmButton>
        ) : null}
        {can("cancel") ? (
          <ConfirmButton
            tone="secondary"
            pending={act.isPending}
            spec={{
              title: `Huỷ đề xuất ${row.code}?`,
              description: "Đề xuất dừng lại và không xử lý tiếp.",
              confirmLabel: "Huỷ đề xuất",
              variant: "destructive",
            }}
            onConfirm={() => act.mutate({ action: "cancel", note: trimmedNote })}
          >
            Huỷ đề xuất
          </ConfirmButton>
        ) : null}
      </div>
    </section>
  );
}
