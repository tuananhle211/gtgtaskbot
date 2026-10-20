"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  api,
  scriptReviewOf,
  type CreateOrderBody,
  type DesignCodeItem,
  type OrderInfo,
  type ScriptCheck,
  type ScriptReview,
} from "@/lib/api";
import { ScriptReviewDialog } from "@/components/script-review-dialog";
import {
  DesignCodesPicker,
  designCodesValid,
  toDesignCodePicks,
} from "@/components/design-codes-picker";
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
import { isPast, toIsoFromLocal, toLocalInput } from "@/lib/deadline";

/**
 * Points ("điểm hiệu suất") next to the video kinds and durations on the
 * form. Hidden for now (the owner's call); the server still snapshots them.
 */
const SHOW_POINTS = false;

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
  // `?edit=<code>`: the orderer fixes an order the head sent back.
  const [editRef, setEditRef] = useState<string | null>(null);
  // `?unit=PR` (the task table's button, the old /pr/content?create=1 link)
  // chooses the unit up front. Read after mount: the page is a client page
  // and must render the same on the server.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const wanted = params.get("unit");
    if (wanted === "PR" || wanted === "ADS") setUnit(wanted);
    setEditRef(params.get("edit"));
  }, []);
  const editing = useQuery({
    queryKey: ["orders", editRef],
    queryFn: () => api.order(editRef ?? ""),
    enabled: editRef !== null,
  });

  if (me.isPending) return <Loading />;
  if (me.isError)
    return <ErrorBox error={me.error} onRetry={() => me.refetch()} />;

  if (isUntagged(me.data)) return <UntaggedState title="Tạo order" />;

  if (editRef !== null) {
    if (editing.isPending) return <Loading />;
    if (editing.isError)
      return (
        <ErrorBox error={editing.error} onRetry={() => editing.refetch()} />
      );
    const order = editing.data.order;
    return (
      <div className="space-y-4">
        <PageHeader
          title={`Sửa order ${order.code}`}
          subtitle="Sửa theo góp ý của Trưởng phòng rồi gửi lại để duyệt"
        />
        <AdsOrderForm
          key={`${order.id}:${order.version}`}
          isHead={unitEntry(me.data, "ADS")?.role === "HEAD"}
          aiReview={unitEntry(me.data, "ADS")?.settings?.script_ai_review === true}
          designCodes={unitEntry(me.data, "ADS")?.settings?.design_codes_enabled !== false}
          editing={order}
        />
      </div>
    );
  }

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
                "Order video cho team Media: Biên kịch, Design, Dựng theo quy trình bạn chọn. Trưởng phòng ADS duyệt order, bạn duyệt final.",
              ],
              [
                "PR",
                STREAM_NAMES.PR,
                "Nội dung PR theo quy trình hiện có: kịch bản, AI review, Trưởng nhóm và Trưởng phòng duyệt, sản xuất, đăng.",
              ],
            ].map(([code, label, hint]) => (
              <label
                key={code}
                className={`flex min-w-[16rem] flex-1 cursor-pointer gap-3 rounded-xl border-2 p-3 ${chosen === code
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
          isHead={unitEntry(me.data, "ADS")?.role === "HEAD"}
          aiReview={unitEntry(me.data, "ADS")?.settings?.script_ai_review === true}
          designCodes={unitEntry(me.data, "ADS")?.settings?.design_codes_enabled !== false}
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

function AdsOrderForm({
  isHead = false,
  aiReview = false,
  designCodes = true,
  editing,
}: {
  /** A Trưởng phòng ADS's own order needs no approval: no approver to pick. */
  isHead?: boolean;
  /**
   * ORD's "AI duyệt kịch bản" is on: the script must be checked with the
   * button (and pass, or the AI be unable to answer) before the order goes.
   */
  aiReview?: boolean;
  /** ORD asks for design codes: a Design-only order must list its "Task công việc". */
  designCodes?: boolean;
  /** A returned order being fixed: the form starts from it and resubmits. */
  editing?: OrderInfo;
}) {
  const router = useRouter();
  // "Người duyệt order": who approves this person's order, from the server.
  // `own_gate` = they approve orders themselves (no field); otherwise the
  // order goes to `default` unless one of `candidates` is picked, and
  // `required` = nobody by default, so one must be picked. Until it answers
  // (or if it cannot), the base role stands in: a head picks nobody.
  const approvers = useQuery({
    queryKey: ["orders", "approvers"],
    queryFn: api.orderApprovers,
    retry: false,
  });
  const ownGate = approvers.data?.own_gate ?? isHead;
  const candidates = (!ownGate && approvers.data?.candidates) || [];
  const defaults = (!ownGate && approvers.data?.default) || [];
  const approverRequired = Boolean(approvers.data && !ownGate && approvers.data.required);
  const [pickedApprover, setPickedApprover] = useState<string | null>(null);
  // Fixing an order starts from the approver it already has, when they may
  // still be chosen.
  const editedApprover =
    editing?.approver_user_id &&
    candidates.some((person) => person.user_id === editing.approver_user_id)
      ? editing.approver_user_id
      : "";
  const approver = pickedApprover ?? editedApprover;
  const offersApprover = !ownGate && (candidates.length > 0 || approverRequired);
  const defaultNames = defaults.map((person) => person.name).join(", ");
  const approverName =
    candidates.find((person) => person.user_id === approver)?.name ?? (defaultNames || null);
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
  const designCodesQ = useQuery({
    queryKey: ["units", "ADS", "design-codes"],
    queryFn: () => api.designCodes(),
  });
  // "Task công việc" of a Design-only order: the design codes it needs.
  const [tasks, setTasks] = useState<DesignCodeItem[]>([]);
  const [form, setForm] = useState({
    title: editing?.title ?? "",
    video_kind_id: editing?.video_kind_id ?? "",
    platform_id: editing?.platform_id ?? "",
    duration_id: editing?.duration_id ?? "",
    script_source: editing?.script_source ?? "AI",
    order_content: editing?.order_content ?? "",
    design_link: editing?.design_link ?? "",
    reference_link: editing?.reference_link ?? "",
    source_link: editing?.source_link ?? "",
    note: editing?.note ?? "",
    desired_deadline: toLocalInput(editing?.desired_deadline_at),
  });
  const initialDeadline = toLocalInput(editing?.desired_deadline_at);
  // "Quy trình": Order is always on and not part of the list; Dựng starts ticked.
  // A returned order keeps its process: its nodes already exist.
  const [ticked, setTicked] = useState<ProcessNode[]>(
    editing ? ((editing.process ?? []) as ProcessNode[]) : ["DUNG"],
  );
  const nodes = orderedProcess(ticked);
  const code = processCode(nodes);
  // Only Design ticked: no ORD approval (straight to the Design Leader), the
  // design codes stand for kind / platform / duration, and the script box is a
  // plain "Nội dung" the AI does not read.
  const designOnly = nodes.length === 1 && nodes[0] === "THIET_KE";
  const reviewsScript = aiReview && !designOnly;
  const create = useMutation({
    mutationFn: (body: CreateOrderBody) =>
      editing
        ? api.orderAction(editing.id, "/resubmit", {
          version: editing.version,
          title: body.title,
          video_kind_id: body.video_kind_id,
          platform_id: body.platform_id,
          duration_id: body.duration_id,
          order_content: body.order_content,
          script_source: body.script_source,
          design_link: body.design_link ?? "",
          reference_link: body.reference_link ?? "",
          source_link: body.source_link ?? "",
          note: body.note ?? "",
          ...(body.approver_user_id ? { approver_user_id: body.approver_user_id } : {}),
          // Sent only when changed: an old wish may lie in the past by now.
          ...(form.desired_deadline !== initialDeadline
            ? { desired_deadline_at: body.desired_deadline_at }
            : {}),
        })
        : api.createOrder(body),
    onSuccess: (detail) => router.push(`/tasks/${detail.order.code}`),
    onError: (error) => {
      if (scriptReviewOf(error)) setRefusedAt(Date.now());
    },
  });
  // "AI duyệt kịch bản": the answer for the text it read. Any edit of the
  // title or the script makes it stale - the button has to be pressed again.
  const checkedText = JSON.stringify([form.title.trim(), form.order_content.trim()]);
  const [checked, setChecked] = useState<{ text: string; result: ScriptCheck } | null>(null);
  const check = useMutation({
    // The text is passed along so the answer is pinned to what was read.
    mutationFn: (text: string) =>
      api.checkOrderScript({ title: form.title, order_content: form.order_content }).then(
        (result) => ({ text, result }),
      ),
    onSuccess: (answer) => {
      setChecked(answer);
      if (answer.result.reviewed && !answer.result.passed) setRefusedAt(Date.now());
    },
  });
  const current = checked && checked.text === checkedText ? checked.result : null;
  // Passed, switched off, or the AI could not answer (the history will say so).
  const mayGo = !reviewsScript || Boolean(current && (current.passed || !current.reviewed));
  // The AI read the script and found it against the rules - with the button,
  // or when the order was sent. Its findings sit under the script field,
  // which is brought into view, and open in a window with a corrected version.
  const review: ScriptReview | null =
    current && current.reviewed && !current.passed
      ? {
          violations: current.violations,
          revised_script: current.revised_script ?? null,
          note: current.note ?? null,
        }
      : scriptReviewOf(create.error);
  const violations = review?.violations ?? [];
  const findings = useRef<HTMLDivElement>(null);
  const refused = violations.length > 0;
  const [showReview, setShowReview] = useState(false);
  // A fresh refusal - from the button or from sending - opens the window and
  // brings the findings into view; typing back an old text does not.
  const [refusedAt, setRefusedAt] = useState(0);
  useEffect(() => {
    if (refusedAt === 0) return;
    findings.current?.scrollIntoView?.({ block: "center" });
    setShowReview(true);
  }, [refusedAt]);
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
  const needsKind = !designOnly && activeKinds.length > 0;
  const needsPlatform = !designOnly && activePlatforms.length > 0;
  const needsDuration = !designOnly && activeDurations.length > 0;
  const toggleNode = (node: ProcessNode) =>
    !editing &&
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
    ...(designOnly
      ? { design_codes: toDesignCodePicks(tasks) }
      : {
        video_kind_id: form.video_kind_id || null,
        platform_id: form.platform_id || null,
        duration_id: form.duration_id || null,
        script_source: form.script_source,
      }),
    order_content: form.order_content,
    design_link: form.design_link || null,
    reference_link: form.reference_link || null,
    source_link: form.source_link || null,
    note: form.note || null,
    desired_deadline_at: toIsoFromLocal(form.desired_deadline),
    ...(offersApprover && approver && !designOnly ? { approver_user_id: approver } : {}),
  };
  // A wish in the past is refused by the server (an unchanged one on a
  // resubmit is not sent at all).
  const deadlinePast =
    isPast(form.desired_deadline) &&
    (!editing || form.desired_deadline !== initialDeadline);
  const ready =
    nodes.length > 0 &&
    form.title.trim() &&
    form.order_content.trim() &&
    (!needsKind || form.video_kind_id) &&
    (!needsPlatform || form.platform_id) &&
    (!needsDuration || form.duration_id) &&
    body.desired_deadline_at &&
    !deadlinePast &&
    (designOnly || !approverRequired || approver) &&
    (!designOnly || designCodesValid(tasks, designCodes)) &&
    mayGo;
  const field =
    "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

  return (
    <form
      className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]"
      onSubmit={(event) => event.preventDefault()}
    >
      <section className="space-y-4 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
        {editing ? (
          <div
            role="status"
            className="rounded-lg border border-[var(--warn)] bg-[var(--warn-soft)] p-3 text-sm text-[var(--warn)]"
          >
            <p className="font-medium">
              Trưởng phòng trả order{" "}
              <span className="font-mono">{editing.code}</span> để sửa
            </p>
            {editing.returned_reason ? (
              <p className="mt-1">Lý do: {editing.returned_reason}</p>
            ) : null}
          </div>
        ) : (
          <p className="text-xs text-[var(--text-muted)]" data-testid="order-code-preview">
            Mã order: 5 ký tự chữ và số, hệ thống tự sinh khi gửi (ví dụ{" "}
            <span className="font-mono">K7Q2M</span>).
          </p>
        )}
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
          <div className="mt-1.5 flex flex-wrap gap-2.5">
            <label className="flex min-h-12 items-center gap-2 rounded-lg border-2 border-dashed border-[var(--border)] px-4 text-[15px] text-[var(--text-muted)]">
              <input
                type="checkbox"
                checked
                disabled
                className="size-[18px]"
                aria-describedby="process-order-hint"
              />
              Order
              <span id="process-order-hint" className="text-xs">
                (luôn có)
              </span>
            </label>
            {PROCESS_NODES.map((item) => (
              <label
                key={item.node}
                className={`flex min-h-12 min-w-28 cursor-pointer items-center gap-2 rounded-lg border-2 px-4 text-[15px] font-semibold transition-colors ${nodes.includes(item.node)
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)] shadow-sm"
                  : "border-[var(--border)] hover:border-[var(--accent)] hover:bg-[var(--accent-soft)]"
                  }`}
              >
                <input
                  type="checkbox"
                  checked={nodes.includes(item.node)}
                  onChange={() => toggleNode(item.node)}
                  disabled={editing !== undefined}
                  className="size-[18px] accent-[var(--accent)]"
                />
                {item.label}
              </label>
            ))}
          </div>
          <p
            className="mt-1.5 text-sm font-medium text-[var(--accent-strong)]"
            data-testid="process-route"
          >
            {route}
          </p>
          {editing ? (
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Quy trình giữ nguyên khi sửa order. Muốn đổi quy trình thì huỷ
              order này và tạo order mới.
            </p>
          ) : null}
          {nodes.length === 0 ? (
            <p role="alert" className="mt-1 text-xs text-[var(--bad)]">
              Chọn ít nhất một công đoạn: Biên kịch, Design hoặc Dựng.
            </p>
          ) : null}
        </fieldset>
        {designOnly ? (
          <div className="space-y-1">
            <p className="text-sm font-medium">3 · Task công việc</p>
            <DesignCodesPicker
              value={tasks}
              onChange={setTasks}
              options={designCodesQ.data?.codes ?? []}
              loading={designCodesQ.isLoading}
              required={designCodes}
              label="Mã task design"
              hint="Chọn các task Design cần làm, kèm số lượng."
            />
            {designCodesQ.isError ? <NoticeBox error={designCodesQ.error} /> : null}
          </div>
        ) : null}
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
                  {SHOW_POINTS ? `${kind.name} · ${formatPoints(kind.points)} điểm` : kind.name}
                </option>
              ))}
            </Select>
            <span className="block text-xs font-normal text-[var(--text-muted)]">
              {SHOW_POINTS ? "Bắt buộc · điểm hiệu suất tính theo loại video" : "Bắt buộc"}
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
                    {SHOW_POINTS ? `${d.name} · ${formatPoints(d.points)} điểm` : d.name}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
        </div>
        <label className="block text-sm font-medium">
          Deadline mong muốn
          <input
            type="datetime-local"
            value={form.desired_deadline}
            onChange={set("desired_deadline")}
            className={`mt-1 ${field} sm:max-w-xs`}
          />
          {deadlinePast ? (
            <span role="alert" className="block text-xs font-normal text-[var(--bad)]">
              Deadline mong muốn đã qua — chọn một thời điểm sau bây giờ.
            </span>
          ) : (
            <span className="block text-xs font-normal text-[var(--text-muted)]">
              Bắt buộc · Trưởng ban dựa vào đây để đặt deadline từng công đoạn
            </span>
          )}
        </label>
        {designOnly ? (
          <p
            data-testid="design-only-route"
            className="rounded-lg bg-[var(--surface-muted)] px-3 py-2 text-xs text-[var(--text-muted)]"
          >
            Order chỉ có Design: không qua Trưởng phòng ORD, gửi là chuyển thẳng tới Trưởng phòng
            Design. Trưởng phòng Design giao việc hoặc nhận việc là đã duyệt order, hoặc huỷ nếu
            không làm. Trước khi được duyệt, bạn vẫn huỷ được order.
          </p>
        ) : ownGate ? (
          <p className="rounded-lg bg-[var(--surface-muted)] px-3 py-2 text-xs text-[var(--text-muted)]">
            Bạn có quyền duyệt order: order của bạn không cần duyệt, gửi là chuyển thẳng tới công
            đoạn đầu.
          </p>
        ) : offersApprover ? (
          <label className="block text-sm font-medium">
            Người duyệt order
            <Select
              value={approver}
              onChange={(event) => setPickedApprover(event.target.value)}
              aria-invalid={(approverRequired && !approver) || undefined}
              className="mt-1 w-full sm:max-w-xs"
            >
              {approverRequired ? (
                <option value="" disabled>
                  Chọn người duyệt…
                </option>
              ) : defaults.length > 0 ? (
                <option value="">{`Tự động: ${defaultNames}`}</option>
              ) : null}
              {candidates.map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.name}
                </option>
              ))}
            </Select>
            {approverRequired ? (
              <span role="note" className="block text-xs font-normal text-[var(--warn)]">
                Trưởng phòng của bạn không có quyền duyệt order: chọn người duyệt.
              </span>
            ) : (
              <span className="block text-xs font-normal text-[var(--text-muted)]">
                Không bắt buộc · không chọn thì order tự chuyển tới trưởng phòng của bạn
              </span>
            )}
          </label>
        ) : null}
        <div className="space-y-1">
          {designOnly ? (
            <label htmlFor="order-content" className="block text-sm font-medium">
              4 · Nội dung
            </label>
          ) : (
            <p className="text-sm font-medium">4 · Khung nhập kịch bản</p>
          )}
          {designOnly ? null : (
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
          )}
          <textarea
            id="order-content"
            aria-label="Nội dung order"
            value={form.order_content}
            onChange={set("order_content")}
            placeholder={
              designOnly
                ? "Nội dung cần thiết kế, yêu cầu, thông điệp…"
                : "Nội dung order, key mong muốn, kịch bản voice (hoàn chỉnh nếu là kịch bản AI)…"
            }
            className={`${field} min-h-32`}
          />
          {reviewsScript ? (
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => check.mutate(checkedText)}
                disabled={!form.order_content.trim() || check.isPending}
                className="min-h-10 rounded-lg border border-[var(--accent)] px-3 text-sm font-medium text-[var(--accent)] disabled:opacity-50"
              >
                {check.isPending ? "AI đang duyệt…" : "AI duyệt kịch bản"}
              </button>
              {current ? (
                <span
                  role="status"
                  data-testid="script-check"
                  className={`text-sm ${
                    current.passed
                      ? "text-[var(--good)]"
                      : current.reviewed
                        ? "text-[var(--bad)]"
                        : "text-[var(--warn)]"
                  }`}
                >
                  {current.message}
                </span>
              ) : (
                <span className="text-xs text-[var(--text-muted)]">
                  {checked
                    ? "Nội dung đã sửa sau lần duyệt trước: bấm duyệt lại."
                    : "Bấm để AI duyệt kịch bản theo bộ quy tắc nội dung; đạt mới gửi được order."}
                </span>
              )}
            </div>
          ) : null}
          {check.isError ? <NoticeBox error={check.error} /> : null}
          {refused ? (
            <div
              ref={findings}
              role="alert"
              data-testid="script-violations"
              className="rounded-lg border border-[var(--bad)] bg-[var(--surface-muted)] p-3 text-sm"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="font-semibold text-[var(--bad)]">
                  AI duyệt kịch bản: chưa đạt ({violations.length} lỗi). Order chưa
                  được gửi, sửa các chỗ dưới đây rồi duyệt lại.
                </p>
                <button
                  type="button"
                  onClick={() => setShowReview(true)}
                  className="min-h-9 shrink-0 rounded-lg border border-[var(--bad)] px-3 text-xs font-medium text-[var(--bad)]"
                >
                  Xem lỗi & bản sửa gợi ý
                </button>
              </div>
              <ol className="mt-2 list-decimal space-y-2 pl-5">
                {violations.map((item, index) => (
                  <li key={`${index}-${item.quote}`}>
                    <span className="font-medium">“{item.quote}”</span>
                    {item.rule > 0 ? (
                      <span className="text-[var(--text-muted)]"> · mục cấm {item.rule}</span>
                    ) : null}
                    {item.reason ? <span className="block">{item.reason}</span> : null}
                    {item.suggestion ? (
                      <span className="block text-[var(--text-muted)]">
                        Gợi ý: {item.suggestion}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          {review ? (
            <ScriptReviewDialog
              open={showReview}
              onClose={() => setShowReview(false)}
              review={review}
              onUse={(script) => setForm((was) => ({ ...was, order_content: script }))}
            />
          ) : null}
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
              Không bắt buộc
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
            7 · Link nguồn
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
            href={editing ? `/tasks/${editing.code}` : "/tasks?unit=ADS"}
            className="inline-flex min-h-11 items-center rounded-lg border border-[var(--border)] px-4 text-sm"
          >
            Huỷ
          </Link>
          <ConfirmButton
            spec={
              editing
                ? {
                  title: `Gửi lại order ${editing.code}?`,
                  description:
                    "Order đã sửa quay lại cho Trưởng phòng ADS duyệt.",
                  confirmLabel: "Gửi lại order",
                }
                : {
                  title: "Gửi order?",
                  description: designOnly
                    ? "Hệ thống sinh mã order và chuyển thẳng tới Trưởng phòng Design: giao việc hoặc nhận việc là duyệt. Order hiện ở bảng task của bạn."
                    : "Hệ thống sinh mã order và báo Trưởng phòng duyệt. Order hiện ở bảng task của bạn.",
                  confirmLabel: "Gửi order",
                }
            }
            onConfirm={() => create.mutate(body)}
            pending={create.isPending}
            error={create.error}
            disabled={!ready}
            tone="primary"
          >
            {editing ? "Gửi lại order" : "Gửi order"}
          </ConfirmButton>
        </div>
        {reviewsScript && !mayGo ? (
          <p data-testid="needs-script-check" className="text-right text-xs text-[var(--warn)]">
            Bấm “AI duyệt kịch bản” ở ô kịch bản: đạt mới gửi được order.
          </p>
        ) : null}
        {create.isPending ? (
          <p role="status" className="text-right text-xs text-[var(--text-muted)]">
            {reviewsScript
              ? "AI đang duyệt kịch bản, vui lòng chờ trong giây lát…"
              : "Đang gửi order…"}
          </p>
        ) : null}
        {create.isError ? <NoticeBox error={create.error} /> : null}
      </section>
      <aside className="space-y-3">
        <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 text-sm">
          <h2 className="font-semibold">
            Đường đi của order{code ? ` · ${code}` : ""}
          </h2>
          <ol className="mt-2 space-y-1">
            <li>ORD lên order</li>
            {designOnly ? (
              <li>Trưởng phòng Design duyệt: giao việc / nhận việc là duyệt, hoặc huỷ</li>
            ) : ownGate ? null : (
              <li>{approverName ?? "Trưởng phòng ADS"} duyệt order</li>
            )}
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
