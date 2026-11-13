"use client";

/**
 * Tài chính › Hồ sơ chi › one request (đợt 3): what it is, its route step by
 * step (who acted when, who it waits on, skipped / escalated steps), the budget
 * line it draws on, the papers (required ones, uploaded files, "đã nhận bản
 * giấy"), the actions open to the viewer, the history. "In phiếu" prints it on
 * A4 from the browser (M3).
 */

import { use, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ConfirmButton } from "@/components/confirm";
import { FIELD, statusTone } from "@/components/finance";
import { PageHeader, SecondaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { money, spendApi, type RequestDetail } from "@/lib/finance";

const ACTION_TEXT: Record<string, string> = {
  submitted: "Gửi hồ sơ",
  resubmitted: "Gửi lại",
  edited: "Sửa hồ sơ",
  approve: "Duyệt",
  reject: "Không duyệt",
  return: "Trả lại để sửa",
  executed: "Mua sắm & nghiệm thu",
  paid: "Đã chi tiền",
  cancelled: "Huỷ hồ sơ",
  document: "Thêm chứng từ",
  document_removed: "Xoá chứng từ",
  paper: "Đã nhận bản giấy",
};

function actionText(action: string): string {
  if (action.startsWith("approved:")) return "Duyệt";
  return ACTION_TEXT[action] ?? action;
}

function Steps({ data }: { data: RequestDetail }) {
  const round = Math.max(...data.steps.map((step) => step.round));
  const steps = data.steps.filter((step) => step.round === round);
  return (
    <section className="panel p-4" aria-label="Các bước">
      <h2 className="text-base font-semibold">Các bước{round > 1 ? ` (lần gửi ${round})` : ""}</h2>
      <ol className="mt-3 space-y-2">
        {steps.map((step) => {
          const open = step.id === data.open_step_id;
          const tone =
            step.status === "DONE"
              ? "good"
              : step.status === "REJECTED"
                ? "bad"
                : step.status === "RETURNED"
                  ? "warn"
                  : "neutral";
          return (
            <li
              key={step.id}
              data-testid="request-step"
              className={`rounded-lg border p-3 text-sm ${
                open ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border)]"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  <span className="text-xs text-[var(--text-muted)]">{step.phase_label} · </span>
                  <span className="font-medium">{step.role_label}</span>
                </span>
                <Pill tone={tone}>
                  {step.status === "DONE"
                    ? "Xong"
                    : step.status === "PENDING"
                      ? "Đang chờ"
                      : step.status === "SKIPPED"
                        ? "Bỏ qua"
                        : step.status === "WAITING"
                          ? "Chưa tới lượt"
                          : step.status === "REJECTED"
                            ? "Không duyệt"
                            : step.status === "RETURNED"
                              ? "Trả lại"
                              : "Dừng"}
                </Pill>
              </div>
              <p className="mt-1 text-[var(--text-muted)]">
                {step.decided_by
                  ? `${step.decided_by} · ${step.decided_at ? formatDateTime(step.decided_at) : ""}`
                  : step.approver
                    ? `${step.approver}${step.standin ? ` (thay: ${step.standin})` : ""}`
                    : ""}
              </p>
              {step.note ? <p className="mt-1">{step.note}</p> : null}
              {step.remark ? <p className="mt-1 text-xs text-[var(--warn)]">{step.remark}</p> : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Documents({ data }: { data: RequestDetail }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState(data.missing[0]?.value ?? "OTHER");
  const [error, setError] = useState<string | null>(null);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["finance", "request", data.id] });
  const upload = useMutation({
    mutationFn: (file: File) => spendApi.uploadDocument(data.id, kind, file),
    onSuccess: refresh,
    onError: (failure: Error) => setError(failure.message),
  });
  const paper = useMutation({ mutationFn: (value: string) => spendApi.paper(data.id, value), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: string) => spendApi.removeDocument(id), onSuccess: refresh });
  const kinds = [
    ...data.required,
    { value: "PAYMENT_PROOF", label: "Chứng từ chi (UNC / phiếu chi)" },
    { value: "OTHER", label: "Hồ sơ liên quan khác" },
  ].filter((item, index, list) => list.findIndex((other) => other.value === item.value) === index);
  return (
    <section className="panel space-y-3 p-4" aria-label="Chứng từ">
      <h2 className="text-base font-semibold">Chứng từ</h2>
      {data.required.length ? (
        <ul className="space-y-1 text-sm" data-testid="required-documents">
          {data.required.map((item) => {
            const have = !data.missing.some((miss) => miss.value === item.value);
            return (
              <li key={item.value} className="flex flex-wrap items-center gap-2">
                <Pill tone={have ? "good" : "warn"}>{have ? "Đủ" : "Thiếu"}</Pill>
                {item.label}
                {!have && data.can_paper ? (
                  <ConfirmButton
                    spec={{ title: `Đã nhận bản giấy "${item.label}"?`, description: "Ghi nhận kế toán đã nhận bản giấy.", confirmLabel: "Ghi nhận" }}
                    onConfirm={() => paper.mutate(item.value)}
                    pending={paper.isPending}
                    error={paper.error}
                    tone="secondary"
                    ariaLabel={`Đã nhận bản giấy ${item.label}`}
                  >
                    Đã nhận bản giấy
                  </ConfirmButton>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {data.documents_note ? <p className="text-xs text-[var(--text-muted)]">{data.documents_note}</p> : null}
      {data.docs_due_on && data.missing.length ? (
        <p className="text-sm text-[var(--bad)]">Hạn bổ sung chứng từ: {formatDate(data.docs_due_on)}</p>
      ) : null}
      {data.documents.length ? (
        <ul className="divide-y divide-[var(--border)] text-sm">
          {data.documents.map((document) => (
            <li key={document.id} className="flex flex-wrap items-center justify-between gap-2 py-2" data-testid="document-row">
              <span>
                <span className="text-xs text-[var(--text-muted)]">{document.kind_label}: </span>
                {document.storage === "paper" ? (
                  document.name
                ) : (
                  <a href={spendApi.documentUrl(document.id)} target="_blank" rel="noreferrer" className="text-[var(--accent)] hover:underline">
                    {document.name}
                  </a>
                )}
                <span className="block text-xs text-[var(--text-muted)]">
                  {document.by} · {formatDateTime(document.at)}
                  {document.storage === "drive" ? " · Google Drive" : ""}
                </span>
              </span>
              <ConfirmButton
                spec={{ title: `Xoá "${document.name}"?`, description: "Không khôi phục được.", confirmLabel: "Xoá", variant: "destructive" }}
                onConfirm={() => remove.mutate(document.id)}
                pending={remove.isPending}
                error={remove.error}
                tone="secondary"
                ariaLabel={`Xoá ${document.name}`}
              >
                Xoá
              </ConfirmButton>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap items-end gap-2" data-print-hide>
        <label className="block min-w-60 text-xs font-medium text-[var(--text-muted)]">
          Loại chứng từ
          <Select aria-label="Loại chứng từ" value={kind} onChange={(event) => setKind(event.target.value)} className="mt-1 w-full">
            {kinds.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-[var(--border)] px-4 text-sm hover:bg-[var(--surface-muted)]">
          {upload.isPending ? "Đang tải lên…" : "Tải tệp lên"}
          <input
            type="file"
            className="sr-only"
            aria-label="Tải tệp chứng từ"
            accept=".pdf,.png,.jpg,.jpeg,.webp,.docx,.xlsx"
            onChange={(event) => {
              const file = event.target.files?.[0];
              setError(null);
              if (file) upload.mutate(file);
              event.target.value = "";
            }}
          />
        </label>
      </div>
      {error ? <p className="text-sm text-[var(--bad)]" role="alert">{error}</p> : null}
    </section>
  );
}

function Actions({ data }: { data: RequestDetail }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [purchase, setPurchase] = useState("");
  const act = useMutation({
    mutationFn: (action: string) =>
      spendApi.act(data.id, {
        action,
        note: note.trim() || null,
        step_id: data.open_step_id,
        purchase_note: purchase.trim() || null,
      }),
    onSuccess: () => {
      setNote("");
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
      void queryClient.invalidateQueries({ queryKey: ["units", "me"] });
    },
  });
  if (!data.can.length) return null;
  const button = (action: string, label: string, title: string, tone: "primary" | "secondary" | "danger" = "secondary", disabled = false) => (
    <ConfirmButton
      key={action}
      spec={{ title, description: `${data.code}: ${data.title} - ${money(data.amount)}`, confirmLabel: label }}
      onConfirm={() => act.mutate(action)}
      pending={act.isPending}
      error={act.error}
      tone={tone}
      disabled={disabled}
    >
      {label}
    </ConfirmButton>
  );
  const needsNote = !note.trim();
  return (
    <section className="panel space-y-3 p-4" aria-label="Xử lý" data-print-hide>
      <h2 className="text-base font-semibold">Xử lý</h2>
      {data.can.includes("execute") ? (
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Báo giá / đơn hàng / bàn giao / nghiệm thu
          <textarea value={purchase} onChange={(event) => setPurchase(event.target.value)} rows={3} className={`mt-1 ${FIELD} py-2`} />
        </label>
      ) : null}
      <label className="block text-xs font-medium text-[var(--text-muted)]">
        Ghi chú / lý do
        <input value={note} onChange={(event) => setNote(event.target.value)} className={`mt-1 ${FIELD}`} />
      </label>
      {data.can.includes("pay") && !data.documents.some((doc) => doc.kind === "PAYMENT_PROOF") ? (
        <p className="text-sm text-[var(--warn)]">Tải ảnh UNC / phiếu chi (loại &quot;Chứng từ chi&quot;) trước khi bấm Đã chi.</p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {data.can.includes("approve") ? button("approve", "Duyệt", "Duyệt hồ sơ chi?", "primary") : null}
        {data.can.includes("execute") ? button("execute", "Đã mua & nghiệm thu", "Ghi nhận đã mua & nghiệm thu?", "primary", !purchase.trim()) : null}
        {data.can.includes("pay") ? button("pay", "Đã chi", "Ghi nhận đã chi tiền?", "primary") : null}
        {data.can.includes("return") ? button("return", "Trả lại để sửa", "Trả hồ sơ về người lập?", "secondary", needsNote) : null}
        {data.can.includes("reject") ? button("reject", "Không duyệt", "Không duyệt hồ sơ này?", "danger", needsNote) : null}
        {data.can.includes("resubmit") ? button("resubmit", "Gửi lại", "Gửi lại hồ sơ?", "primary") : null}
        {data.can.includes("cancel") ? button("cancel", "Huỷ hồ sơ", "Huỷ hồ sơ chi?", "danger") : null}
      </div>
    </section>
  );
}

export default function FinanceRequestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const detail = useQuery({ queryKey: ["finance", "request", id], queryFn: () => spendApi.request(id), retry: false });
  if (detail.isPending) return <Loading />;
  if (detail.isError) return <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />;
  const data = detail.data;
  const facts: [string, string][] = [
    ["Pháp nhân", data.legal_entity],
    ["Phòng ban", data.department || "—"],
    ["Cơ sở", data.branch ?? "—"],
    ["Mã ngân sách", `${data.budget_code} - ${data.code_name}`],
    ["Tháng ngân sách", `${data.budget_month.slice(5, 7)}/${data.budget_month.slice(0, 4)}`],
    ["Người lập", data.requester],
    ["Nhà cung cấp", data.vendor ?? "—"],
    ["Nhận tiền", data.payee ?? "—"],
  ];
  return (
    <div className="space-y-5">
      <PageHeader
        title={`${data.code} · ${data.title}`}
        subtitle={`${money(data.amount)} · lập ${formatDate(data.created_at)}`}
        action={
          <SecondaryButton type="button" onClick={() => window.print()} data-print-hide>
            In phiếu
          </SecondaryButton>
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={statusTone(data.status)}>{data.status_label}</Pill>
        {data.phase_label ? <Pill tone="warn">{data.phase_label}</Pill> : null}
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="space-y-4">
          <section className="panel p-4">
            <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
              {facts.map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
                  <dd className="mt-0.5">{value}</dd>
                </div>
              ))}
            </dl>
            {data.description ? <p className="mt-3 whitespace-pre-line text-sm">{data.description}</p> : null}
            {data.purchase_note ? (
              <p className="mt-3 rounded-lg bg-[var(--surface-muted)] p-3 text-sm">
                <span className="font-medium">Mua sắm & nghiệm thu: </span>
                {data.purchase_note}
              </p>
            ) : null}
          </section>
          <Actions data={data} />
          <Documents data={data} />
          <section className="panel p-4" aria-label="Lịch sử">
            <h2 className="text-base font-semibold">Lịch sử</h2>
            <ul className="mt-2 space-y-1 text-sm">
              {data.events.map((event, index) => (
                <li key={index}>
                  <span className="text-[var(--text-muted)]">{formatDateTime(event.at)} · </span>
                  {event.by ?? "Hệ thống"}: {actionText(event.action)}
                  {event.note ? ` - ${event.note}` : ""}
                </li>
              ))}
            </ul>
          </section>
        </div>
        <div className="space-y-4">
          <Steps data={data} />
          {data.budget ? (
            <section className="panel p-4 text-sm" aria-label="Ngân sách" data-testid="request-budget">
              <h2 className="text-base font-semibold">Ngân sách</h2>
              <dl className="mt-2 space-y-1">
                <div className="flex justify-between"><dt>Ngân sách</dt><dd className="tabular-nums">{money(data.budget.budget)}</dd></div>
                <div className="flex justify-between"><dt>Đang chờ</dt><dd className="tabular-nums">{money(data.budget.committed)}</dd></div>
                <div className="flex justify-between"><dt>Đã thực hiện</dt><dd className="tabular-nums">{money(data.budget.realized)}</dd></div>
                <div className={`flex justify-between font-semibold ${data.budget.remaining < 0 ? "text-[var(--bad)]" : ""}`}>
                  <dt>Còn lại</dt>
                  <dd className="tabular-nums">{money(data.budget.remaining)}</dd>
                </div>
              </dl>
            </section>
          ) : (
            <p className="text-sm text-[var(--text-muted)]">Chưa có ngân sách cho mã chi này trong tháng.</p>
          )}
        </div>
      </div>
    </div>
  );
}
