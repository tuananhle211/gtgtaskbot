"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type CreateOrderBody } from "@/lib/api";
import {
  hasUnit,
  isUntagged,
  STREAM_NAMES,
  unitEntry,
  unitShortLabel,
  unitTagClass,
} from "@/lib/units";
import { UntaggedState } from "@/components/unit-switch";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";
import { CreateContentForm } from "@/components/pr-create-content";
import {
  formatPoints,
  orderedProcess,
  PROCESS_NODES,
  processCode,
  type ProcessNode,
} from "@/lib/ads-process";

/** Who each node is routed to first, to hand out to their team. */
const NODE_HEADS: Record<string, string> = {
  BIEN_TAP: "Trưởng phòng Biên kịch",
  THIET_KE: "Trưởng phòng Design",
  DUNG: "Trưởng phòng Dựng",
};

const NODE_LABELS: Record<string, string> = Object.fromEntries(
  PROCESS_NODES.map((item) => [item.node, item.label]),
);

/**
 * The create form: pick the unit, then fill that unit's fields.
 *
 * Only ORD creates here - its order is a new record. A PR piece keeps its
 * existing create flow on the content screen, so the PR choice is a link.
 */
export default function NewOrderPage() {
  const router = useRouter();
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe });
  const [unit, setUnit] = useState<string | null>(null);
  // `?unit=PR` (the task table's button, the old /pr/content?create=1 link)
  // chooses the unit up front. Read after mount: the page is a client page
  // and must render the same on the server.
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("unit");
    if (wanted === "PR" || wanted === "ADS") setUnit(wanted);
  }, []);

  if (me.isPending) return <Loading />;
  if (me.isError)
    return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;

  if (isUntagged(me.data)) return <UntaggedState title="Tạo order" />;

  const ads = hasUnit(me.data, "ADS");
  const pr = hasUnit(me.data, "PR");
  const chosen = unit ?? (ads ? "ADS" : "PR");

  return (
    <div className="space-y-4">
      <PageHeader
        title={chosen === "ADS" ? "Tạo order mới" : "Tạo nội dung PR"}
        subtitle={chosen === "ADS" ? "Mã order tự sinh khi gửi" : undefined}
      />
      {ads && pr ? (
        <fieldset className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
          <legend className="px-1 text-sm font-semibold">
            Order cho luồng nào?
          </legend>
          <div className="flex flex-wrap gap-3">
            {[
              [
                "ADS",
                STREAM_NAMES.ADS,
                "Order video cho team Media: Biên kịch, Design, Dựng theo quy trình bạn chọn. Trưởng phòng ORD duyệt order, bạn duyệt final.",
              ],
              [
                "PR",
                STREAM_NAMES.PR,
                "Nội dung PR theo quy trình hiện có: kịch bản, AI review, Trưởng nhóm và Trưởng phòng duyệt, sản xuất, đăng.",
              ],
            ].map(([code, label, hint]) => (
              <label
                key={code}
                className={`flex min-w-[16rem] flex-1 cursor-pointer gap-3 rounded-xl border-2 p-3 ${
                  chosen === code
                    ? "border-[var(--accent)]"
                    : "border-[var(--border)]"
                }`}
              >
                <input
                  type="radio"
                  name="unit"
                  value={code}
                  checked={chosen === code}
                  onChange={() => setUnit(code)}
                  className="mt-1"
                />
                <span>
                  <span className="flex items-center gap-2 font-medium">
                    <span className={`unit-tag ${unitTagClass(code)}`}>
                      {unitShortLabel(code)}
                    </span>
                    {label}
                  </span>
                  <span className="mt-1 block text-xs text-[var(--text-muted)]">
                    {hint}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}
      {chosen === "ADS" ? (
        <AdsOrderForm
          memberCode={unitEntry(me.data, "ADS")?.member_code ?? null}
        />
      ) : (
        <PrCreateSection onCreated={() => router.push("/tasks?unit=PR")} />
      )}
    </div>
  );
}

/**
 * PR creates here too. The form is the PR module's own (title, brand, owner,
 * format, script, channels, resources) and the request is unchanged; only the
 * door moved. A new piece lands on the shared task table under PR.
 */
function PrCreateSection({ onCreated }: { onCreated: () => void }) {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
      <CreateContentForm onCreated={onCreated} />
      <aside className="panel h-fit space-y-3 bg-[var(--surface-muted)] p-4 text-xs text-[var(--text-muted)]">
        <p className="font-medium text-[var(--text)]">Sau khi tạo</p>
        <ol className="list-decimal space-y-1.5 pl-4">
          <li>
            AI review, Trưởng nhóm rồi Trưởng phòng duyệt — qua web hoặc
            Telegram.
          </li>
          <li>Giao người dựng, duyệt nội bộ, sẵn sàng đăng.</li>
          <li>
            Theo dõi trên Quản lý task (khung 5 pha); bảng PR chi tiết giữ đủ 14
            bước.
          </li>
        </ol>
        <p>Mã nội dung do hệ thống sinh.</p>
        <Link
          href="/tasks?unit=PR"
          className="inline-block text-[var(--accent)]"
        >
          Xem bảng task PR →
        </Link>
      </aside>
    </div>
  );
}

function AdsOrderForm({ memberCode }: { memberCode: string | null }) {
  const router = useRouter();
  const kinds = useQuery({
    queryKey: ["units", "ADS", "video-kinds"],
    queryFn: () => api.unitVideoKinds("ADS"),
  });
  const platformsQ = useQuery({
    queryKey: ["units", "ADS", "platforms"],
    queryFn: () => api.unitPlatforms("ADS"),
  });
  const durationsQ = useQuery({
    queryKey: ["units", "ADS", "durations"],
    queryFn: () => api.unitDurations("ADS"),
  });
  const [form, setForm] = useState({
    title: "",
    video_kind_id: "",
    platform_id: "",
    duration_id: "",
    script_source: "AI",
    order_content: "",
    design_link: "",
    reference_link: "",
    source_link: "",
    note: "",
  });
  // "Quy trình": Order is always on and not part of the list; Dựng starts ticked.
  const [ticked, setTicked] = useState<ProcessNode[]>(["DUNG"]);
  const create = useMutation({
    mutationFn: (body: CreateOrderBody) => api.createOrder(body),
    onSuccess: (detail) => router.push(`/tasks/${detail.order.code}`),
  });
  const nodes = orderedProcess(ticked);
  const code = processCode(nodes);
  const route = [
    "Order",
    ...nodes.map((node) => NODE_LABELS[node]),
    "Người order duyệt final",
  ].join(" › ");
  const activeKinds = (kinds.data?.kinds ?? [])
    .filter((kind) => kind.active)
    .sort((a, b) => a.sort_order - b.sort_order);
  const activePlatforms = (platformsQ.data?.platforms ?? [])
    .filter((p) => p.active)
    .sort((a, b) => a.sort_order - b.sort_order);
  const activeDurations = (durationsQ.data?.durations ?? [])
    .filter((d) => d.active)
    .sort((a, b) => a.sort_order - b.sort_order);
  const needsKind = activeKinds.length > 0;
  const needsPlatform = activePlatforms.length > 0;
  const needsDuration = activeDurations.length > 0;
  // Dựng without Design works from a design the orderer already has.
  const needsDesignLink = nodes.includes("DUNG") && !nodes.includes("THIET_KE");
  const toggleNode = (node: ProcessNode) =>
    setTicked((current) =>
      current.includes(node)
        ? current.filter((item) => item !== node)
        : [...current, node],
    );
  const set =
    (key: keyof typeof form) =>
    (
      event: React.ChangeEvent<
        HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
      >,
    ) =>
      setForm((current) => ({ ...current, [key]: event.target.value }));
  const body: CreateOrderBody = {
    title: form.title,
    process: nodes,
    video_kind_id: form.video_kind_id || null,
    platform_id: form.platform_id || null,
    duration_id: form.duration_id || null,
    order_content: form.order_content,
    script_source: form.script_source,
    design_link: form.design_link || null,
    reference_link: form.reference_link || null,
    source_link: form.source_link || null,
    note: form.note || null,
  };
  const ready =
    nodes.length > 0 &&
    form.title.trim() &&
    form.order_content.trim() &&
    (!needsKind || form.video_kind_id) &&
    (!needsPlatform || form.platform_id) &&
    (!needsDuration || form.duration_id) &&
    (!needsDesignLink || form.design_link.trim());
  const field =
    "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

  return (
    <form
      className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]"
      onSubmit={(event) => event.preventDefault()}
    >
      <section className="space-y-4 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <p className="text-xs text-[var(--text-muted)]">
          Mã order:{" "}
          <span className="font-mono" data-testid="order-code-preview">
            {`${memberCode ?? "MÃ"}-${code || "?"}-yymmdd-nn`}
          </span>
          {memberCode
            ? null
            : " — bạn chưa có mã thành viên, hệ thống sẽ lấy từ tên của bạn (Trưởng phòng đổi được ở Quản trị đơn vị)."}
        </p>
        <label className="block text-sm font-medium">
          1 · Tên kịch bản / Ý tưởng / Key truyền thông
          <input
            type="text"
            value={form.title}
            onChange={set("title")}
            className={`mt-1 ${field}`}
          />
        </label>
        <fieldset>
          <legend className="text-sm font-medium">2 · Quy trình</legend>
          <div className="mt-1 flex flex-wrap gap-2">
            <label className="flex min-h-11 items-center gap-2 rounded-lg border border-[var(--border)] px-3 text-sm text-[var(--text-muted)]">
              <input type="checkbox" checked disabled aria-describedby="process-order-hint" />
              Order
              <span id="process-order-hint" className="text-xs">
                (luôn có)
              </span>
            </label>
            {PROCESS_NODES.map((item) => (
              <label
                key={item.node}
                className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border-2 px-3 text-sm ${
                  nodes.includes(item.node)
                    ? "border-[var(--accent)]"
                    : "border-[var(--border)]"
                }`}
              >
                <input
                  type="checkbox"
                  checked={nodes.includes(item.node)}
                  onChange={() => toggleNode(item.node)}
                />
                {item.label}
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-[var(--text-muted)]" data-testid="process-route">
            {route}
          </p>
          {nodes.length === 0 ? (
            <p role="alert" className="mt-1 text-xs text-[var(--bad)]">
              Chọn ít nhất một công đoạn: Biên kịch, Design hoặc Dựng.
            </p>
          ) : null}
        </fieldset>
        {needsKind ? (
          <label className="block text-sm font-medium">
            3 · Loại video
            <Select
              value={form.video_kind_id}
              onChange={set("video_kind_id")}
              className="mt-1 w-full"
            >
              <option value="">Chọn loại video…</option>
              {activeKinds.map((kind) => (
                <option key={kind.id} value={kind.id}>
                  {`${kind.name} · ${formatPoints(kind.points)} điểm`}
                </option>
              ))}
            </Select>
            <span className="block text-xs font-normal text-[var(--text-muted)]">
              Bắt buộc · điểm hiệu suất tính theo loại video
            </span>
          </label>
        ) : null}
        <div className="grid gap-3 sm:grid-cols-2">
          {needsPlatform ? (
            <label className="block text-sm font-medium">
              Nền tảng
              <Select
                value={form.platform_id}
                onChange={set("platform_id")}
                className="mt-1 w-full"
              >
                <option value="">Chọn nền tảng…</option>
                {activePlatforms.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
          {needsDuration ? (
            <label className="block text-sm font-medium">
              Thời lượng
              <Select
                value={form.duration_id}
                onChange={set("duration_id")}
                className="mt-1 w-full"
              >
                <option value="">Chọn thời lượng…</option>
                {activeDurations.map((d) => (
                  <option key={d.id} value={d.id}>
                    {`${d.name} · ${formatPoints(d.points)} điểm`}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
        </div>
        <div className="space-y-1">
          <p className="text-sm font-medium">4 · Khung nhập kịch bản</p>
          <div className="flex gap-4 text-sm">
            {[
              ["AI", "AI"],
              ["REAL", "Quay thực tế"],
            ].map(([value, label]) => (
              <label
                key={value}
                className="inline-flex min-h-11 items-center gap-2"
              >
                <input
                  type="radio"
                  name="script_source"
                  value={value}
                  checked={form.script_source === value}
                  onChange={set("script_source")}
                />
                {label}
              </label>
            ))}
          </div>
          <textarea
            aria-label="Nội dung order"
            value={form.order_content}
            onChange={set("order_content")}
            placeholder="Nội dung order, key mong muốn, kịch bản voice (hoàn chỉnh nếu là kịch bản AI)…"
            className={`${field} min-h-32`}
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="block text-sm font-medium">
            5 · Link thiết kế
            <input
              type="url"
              value={form.design_link}
              onChange={set("design_link")}
              className={`mt-1 ${field}`}
            />
            <span className="block text-xs font-normal text-[var(--text-muted)]">
              {needsDesignLink
                ? "Bắt buộc khi có Dựng mà không có Design"
                : "Không bắt buộc"}
            </span>
          </label>
          <label className="block text-sm font-medium">
            6 · Link tham khảo
            <input
              type="url"
              value={form.reference_link}
              onChange={set("reference_link")}
              className={`mt-1 ${field}`}
            />
          </label>
          <label className="block text-sm font-medium">
            7 · Link source
            <input
              type="url"
              value={form.source_link}
              onChange={set("source_link")}
              className={`mt-1 ${field}`}
            />
          </label>
        </div>
        <label className="block text-sm font-medium">
          8 · Note yêu cầu
          <textarea
            value={form.note}
            onChange={set("note")}
            placeholder="Yêu cầu thêm, lưu ý cho team sản xuất…"
            className={`mt-1 ${field} min-h-20`}
          />
        </label>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {nodes.length === 0 ? (
            <span className="text-xs text-[var(--text-muted)]">
              Chưa chọn công đoạn sản xuất nào.
            </span>
          ) : null}
          <Link
            href="/tasks?unit=ADS"
            className="inline-flex min-h-11 items-center rounded-lg border border-[var(--border)] px-4 text-sm"
          >
            Huỷ
          </Link>
          <ConfirmButton
            spec={{
              title: "Gửi order?",
              description:
                "Hệ thống sinh mã order và báo Trưởng phòng duyệt. Order hiện ở bảng task của bạn.",
              confirmLabel: "Gửi order",
            }}
            onConfirm={() => create.mutate(body)}
            pending={create.isPending}
            error={create.error}
            disabled={!ready}
            tone="primary"
          >
            Gửi order
          </ConfirmButton>
        </div>
        {create.isError ? <NoticeBox error={create.error} /> : null}
      </section>
      <aside className="space-y-3">
        <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 text-sm">
          <h2 className="font-semibold">
            Đường đi của order{code ? ` · ${code}` : ""}
          </h2>
          <ol className="mt-2 space-y-1">
            <li>ORD lên order</li>
            <li>Trưởng phòng ORD duyệt order</li>
            {nodes.map((node, index) => (
              <li key={node}>
                {NODE_LABELS[node]} · tự giao cho {NODE_HEADS[node]} để phân
                công
                {index === nodes.length - 1
                  ? " · người làm nộp luôn link sản phẩm"
                  : ""}
              </li>
            ))}
            <li>Bạn (người order) duyệt final</li>
          </ol>
        </section>
        <section className="rounded-xl border border-[var(--border)] bg-[var(--surface-muted)] p-4 text-xs text-[var(--text-muted)]">
          <p>
            Sau khi gửi: mã sinh tự động, Trưởng phòng nhận thông báo, duyệt
            xong order lần lượt sang các công đoạn đã chọn. Cuối cùng chính
            người order duyệt final.
          </p>
        </section>
      </aside>
    </form>
  );
}
