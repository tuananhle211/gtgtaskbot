"use client";

import { type ReactNode, Suspense, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type Channel,
  type ChannelAnalytics,
  type ChannelConnectionState,
  type ChannelFollowerTrend,
  type ChannelMetricSnapshot,
  type ChannelMetrics,
  type MetricCapabilities,
  type MetricChange,
  type Platform,
  type TikTokOverview,
  type TikTokScope,
  type TikTokVideo,
  type TopPost,
  type UnavailableMetric,
} from "@/lib/api";
import {
  CHANNEL_CATEGORIES,
  channelCategoryLabel,
  channelStatusLabel,
  formatAverage,
  formatComparisonBasis,
  formatCount,
  formatDay,
  formatDelta,
  formatDeltaPercent,
  formatRatioPercent,
  formatWhen,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { Select } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import {
  closeChannelAssignmentConfirmation,
  disconnectChannelConfirmation,
  replaceConnectionConfirmation,
} from "@/lib/confirmations";

/**
 * How loudly to draw a data-status badge.
 *
 * Step 1F.2.4b. `ACTION_REQUIRED` is deliberately a warning rather than a
 * dimmer version of connected: the credential has stopped working and the
 * numbers stopped with it, and a green badge over three-week-old data is the
 * most expensive thing this list could say. The words themselves are the
 * server's - this only picks a colour, and never replaces the label.
 */
function metricsStatusTone(status: string): "good" | "warn" | "neutral" {
  if (status === "CONNECTED_API") return "good";
  if (status === "ACTION_REQUIRED") return "warn";
  if (status === "MANUAL") return "neutral";
  return "neutral";
}

const ASSIGNMENT_ROLES = [
  "CHANNEL_OWNER",
  "CONTENT_OWNER",
  "PRODUCTION_OWNER",
  "SEEDING_OWNER",
  "ANALYTICS_OWNER",
  "APPROVER",
];

/**
 * Channels and who runs them.
 *
 * The interval note matters and is repeated on screen: `effective_to` is the
 * **last day in force**, inclusive. The overlap rule in the Python domain layer
 * reads it that way, so a person told otherwise would keep hitting refusals that
 * looked like off-by-one bugs. No date arithmetic happens in this file - the
 * server refuses an overlap and says so.
 *
 * Step 1F.2.4a turned this screen from a list of names into a channel record.
 * Three rules run through the additions:
 *
 * - **every label comes from the server.** The platform badge, the data status
 *   and the source of a reading are rendered from `*_label` fields, so there is
 *   one Vietnamese wording per value and it is the one the Telegram bot and the
 *   assistant also use;
 * - **no arithmetic here.** The follower delta, the percentage and "how many
 *   days since the last reading" are all computed server-side. A browser that
 *   worked them out would eventually disagree with the API about the same
 *   numbers, and this file already may not do date arithmetic on assignment
 *   intervals for the same reason;
 * - **a snapshot is never dressed up as live data.** Every figure is shown with
 *   the moment it was captured and the source it came from, and the words are
 *   "Chỉ số gần nhất" - never "Live". Nothing in TasksBot fetches from a platform
 *   yet, and a screen implying otherwise would be the most expensive kind of
 *   wrong.
 */
export default function ChannelsPage() {
  // `useSearchParams` suspends during prerender and Next refuses to build a
  // page that reads it outside a boundary. The fallback is what a reader sees
  // for the length of one render - including, after Step 1F.2.9, the render
  // immediately following a return from TikTok's consent screen - so it says
  // what is happening rather than flashing an empty list.
  return (
    <Suspense fallback={<Loading label="Đang tải kênh…" />}>
      <ChannelWorkspace />
    </Suspense>
  );
}

/**
 * Which channel is open, and what just happened to its connection.
 *
 * Step 1F.2.9 moved the selection out of `useState` and into `?channel=`, which
 * looks like a refactor and is really the fix for a broken loop. The OAuth
 * callback has always redirected to `/pr/channels?connection=connected&channel=…`
 * - the server has built that URL since Step 1F.2.4b - and this page read
 * neither parameter. So consent succeeded, the browser came back, and the
 * screen showed a channel list with nothing selected and no acknowledgement:
 * the reviewer-visible half of the flow simply did not exist.
 *
 * With selection in the URL, the return lands on the same channel detail the
 * person left from, the notice explains what happened, and a channel page is
 * shareable and survives Back - all of which fall out of the same change.
 */
function ChannelWorkspace() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const channels = useQuery({
    queryKey: ["channels"],
    queryFn: () => api.listChannels(),
  });
  const platforms = useQuery({
    queryKey: ["platforms"],
    queryFn: () => api.listPlatforms(),
  });
  // Whether to offer the create actions is a capability question, and the
  // dashboard is where the server already answers it. Asking here rather than
  // showing the buttons to everybody and letting the 403 explain: a control
  // somebody cannot use is a worse answer than no control.
  const dashboard = useQuery({
    queryKey: ["dashboard"],
    queryFn: api.dashboard,
  });
  const mayManage = (dashboard.data?.my_capabilities ?? []).includes(
    "PR_CHANNEL_MANAGE",
  );
  const selected = params.get("channel");
  const [creating, setCreating] = useState<"platform" | "channel" | null>(null);

  /**
   * Open a channel, and clear whatever the last connection attempt said.
   *
   * `replace` rather than `push`: choosing a channel is not a navigation
   * somebody wants to walk back through one card at a time, and a Back button
   * that steps through six channels before leaving the page is a worse Back
   * button.
   */
  const select = (channelId: string | null) => {
    const next = new URLSearchParams();
    if (channelId) next.set("channel", channelId);
    const rendered = next.toString();
    router.replace(rendered ? `${pathname}?${rendered}` : pathname);
  };

  /** Keep the channel open, drop the notice. What dismissing the banner does. */
  const clearNotice = () => select(selected);

  const noPlatforms = platforms.data?.length === 0;
  const openChannel = channels.data?.find((row) => row.id === selected) ?? null;

  return (
    <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
      <div>
        {mayManage ? (
          <div className="mb-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setCreating("platform")}
              className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
            >
              + Tạo nền tảng
            </button>
            <button
              type="button"
              disabled={noPlatforms}
              onClick={() => setCreating("channel")}
              title={noPlatforms ? "Hãy tạo nền tảng trước." : undefined}
              className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
            >
              + Tạo kênh
            </button>
          </div>
        ) : null}
        {mayManage && noPlatforms ? (
          <p className="mb-3 text-xs text-[var(--text-muted)]">
            Hãy tạo nền tảng trước. Mỗi kênh phải thuộc về một nền tảng.
          </p>
        ) : null}

        {creating === "platform" ? (
          <CreatePlatformForm onDone={() => setCreating(null)} />
        ) : null}
        {creating === "channel" ? (
          <CreateChannelForm
            platforms={platforms.data ?? []}
            onDone={(channelId) => {
              setCreating(null);
              if (channelId) select(channelId);
            }}
          />
        ) : null}

        {channels.isPending ? <Loading /> : null}
        {channels.isError ? (
          <ErrorBox error={channels.error} onRetry={() => channels.refetch()} />
        ) : null}
        {channels.data?.length === 0 ? (
          <Empty
            message={
              mayManage
                ? "Chưa có kênh nào. Hãy tạo nền tảng và kênh đầu tiên để dùng khi lập nội dung."
                : "Chưa có kênh nào."
            }
          />
        ) : null}
        <ul className="space-y-2">
          {channels.data?.map((channel) => (
            <li key={channel.id}>
              <button
                type="button"
                onClick={() => select(channel.id)}
                className={`w-full rounded-lg border p-3 text-left ${
                  selected === channel.id
                    ? "border-[var(--text-muted)] bg-[var(--surface)]"
                    : "border-[var(--border)] bg-[var(--surface)]"
                }`}
              >
                <code className="text-[10px] text-[var(--text-muted)]">
                  {channel.code}
                </code>
                {/* The platform, as words. A colour or an icon alone would be
                    unreadable to somebody who cannot tell TikTok's teal from
                    Instagram's pink, and this is the field the whole screen is
                    organised around. */}
                <p className="text-xs font-medium text-[var(--text-muted)]">
                  {channel.platform_label}
                </p>
                <p className="text-sm">{channel.name}</p>
                <p className="mt-1 flex flex-wrap gap-1">
                  <Pill>{channelCategoryLabel(channel.category)}</Pill>
                  <Pill tone={channel.status === "ACTIVE" ? "good" : "neutral"}>
                    {channelStatusLabel(channel.status)}
                  </Pill>
                  {/* From the latest snapshot and the channel's connection,
                      both resolved for the whole page in one query each. A card
                      never loads history. The tone follows the server's state:
                      a broken credential is a warning, not a quieter green. */}
                  <Pill tone={metricsStatusTone(channel.metrics_status)}>
                    {channel.metrics_status_label}
                  </Pill>
                </p>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="space-y-3">
        {/* Step 1F.2.9. What the OAuth callback came back to say, in TasksBot's
            own words. The query string carries a short status token the server
            chose from a closed set - never a provider message, and never a
            destination - and this turns it into one Vietnamese sentence. */}
        <ConnectionNotice
          status={params.get("connection")}
          platformLabel={openChannel?.platform_label ?? null}
          onDismiss={clearNotice}
        />
        {selected ? (
          <ChannelDetail channelId={selected} />
        ) : (
          <Empty message="Chọn một kênh để xem phân công." />
        )}
      </div>
    </div>
  );
}

/**
 * What the connection status token in the URL means, as a sentence.
 *
 * The tokens are the server's, from `_connection_redirect`, and the set is
 * closed: `connected`, `rebound`, `choose`, `denied`, `failed`, `unconfigured`.
 * Anything else renders nothing rather than being echoed - a query parameter is
 * attacker-controlled text and this banner is the one place on the page that
 * would otherwise print it.
 *
 * The platform name is filled in from the channel the callback returned to,
 * which is the same `platform_label` the card and the badge use. When the
 * channel has not loaded yet the sentence says "nền tảng" and is still true.
 */
function ConnectionNotice({
  status,
  platformLabel,
  onDismiss,
}: {
  status: string | null;
  platformLabel: string | null;
  onDismiss: () => void;
}) {
  if (!status) return null;
  const platform = platformLabel ?? "nền tảng";
  const notices: Record<string, { tone: "good" | "warn"; text: string }> = {
    connected: {
      tone: "good",
      text: `Đã kết nối ${platform} thành công. Số liệu tài khoản sẽ hiện ngay bên dưới.`,
    },
    rebound: {
      tone: "warn",
      text: `Đã kết nối ${platform}, nhưng tài khoản vừa cấp quyền khác với tài khoản kênh này đang gắn. Hãy kiểm tra lại phần thông tin tài khoản.`,
    },
    choose: {
      tone: "warn",
      text: `Đã cấp quyền ${platform}. Hãy chọn đúng tài khoản mà kênh này đại diện.`,
    },
    denied: {
      tone: "warn",
      text: `Bạn đã huỷ cấp quyền trên ${platform}. Kênh vẫn chưa kết nối.`,
    },
    failed: {
      tone: "warn",
      text: `Kết nối ${platform} không hoàn tất. Hãy thử kết nối lại từ đầu.`,
    },
    unconfigured: {
      tone: "warn",
      text: `Hệ thống chưa cấu hình kết nối ${platform}. Người quản trị máy chủ cần thiết lập trước.`,
    },
  };
  const notice = notices[status];
  if (!notice) return null;

  return (
    <div
      role="status"
      className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm ${
        notice.tone === "good"
          ? "border-emerald-500/40 bg-emerald-500/10"
          : "border-amber-500/40 bg-amber-500/10"
      }`}
    >
      <span>{notice.text}</span>
      <button
        type="button"
        onClick={onDismiss}
        className="min-h-11 rounded border border-[var(--border)] px-3 text-xs"
      >
        Đóng
      </button>
    </div>
  );
}

/**
 * Register a platform.
 *
 * The code is typed, never derived from the name. `FACEBOOK` is what the Step
 * 1F.1 policy system matches on, so a form that filled it in from "Facebook
 * Việt Nam" would be inferring a legal obligation from a display name. The
 * server refuses anything that is not a canonical code and says why.
 */
function CreatePlatformForm({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");

  const create = useMutation({
    mutationFn: () => api.createPlatform({ code, name }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["platforms"] });
      onDone();
    },
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
      aria-label="Tạo nền tảng"
      className="mb-3 space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <h3 className="text-sm font-semibold">Tạo nền tảng</h3>
      <label className="block text-sm">
        Mã nền tảng
        <input
          required
          value={code}
          onChange={(event) => setCode(event.target.value.toUpperCase())}
          placeholder="FACEBOOK"
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5 font-mono"
        />
      </label>
      <p className="text-xs text-[var(--text-muted)]">
        Dùng đúng <strong>FACEBOOK</strong> hoặc <strong>TIKTOK</strong> nếu
        muốn nội dung trên nền tảng đó được AI review đối chiếu với chính sách
        chính thức.
      </p>
      <label className="block text-sm">
        Tên
        <input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Facebook"
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      {create.isError ? <ErrorBox error={create.error} /> : null}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onDone}
          className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
        >
          Hủy
        </button>
        <button
          type="submit"
          disabled={create.isPending || !code.trim() || !name.trim()}
          className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
        >
          {create.isPending ? "Đang lưu…" : "Tạo nền tảng"}
        </button>
      </div>
    </form>
  );
}

/**
 * Register a channel.
 *
 * There is no code field: `CH-nnnn` is allocated server-side, the same way
 * content codes are, so nothing here invents an identifier. Status is not asked
 * for either - a channel is created active, and pausing one is a later
 * decision with its own screen.
 */
function CreateChannelForm({
  platforms,
  onDone,
}: {
  platforms: Platform[];
  onDone: (channelId?: string) => void;
}) {
  const queryClient = useQueryClient();
  const brands = useQuery({ queryKey: ["brands"], queryFn: api.brands });
  const [platformId, setPlatformId] = useState("");
  const [brandId, setBrandId] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState<string>(CHANNEL_CATEGORIES[0]);
  const [url, setUrl] = useState("");
  const [externalId, setExternalId] = useState("");
  const [handle, setHandle] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.createChannel({
        name,
        platform_id: platformId,
        category,
        brand_id: brandId || null,
        url: url || null,
        external_id: externalId || null,
        handle: handle || null,
      }),
    onSuccess: async (detail) => {
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
      onDone(detail.channel.id);
    },
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
      aria-label="Tạo kênh"
      className="mb-3 space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <h3 className="text-sm font-semibold">Tạo kênh</h3>
      <label className="block text-sm">
        Nền tảng *
        <Select
          required
          value={platformId}
          onChange={(event) => setPlatformId(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        >
          <option value="">— chọn —</option>
          {platforms.map((platform) => (
            <option key={platform.id} value={platform.id}>
              {platform.name}
            </option>
          ))}
        </Select>
      </label>
      <label className="block text-sm">
        Thương hiệu
        <Select
          value={brandId}
          onChange={(event) => setBrandId(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        >
          <option value="">— không thuộc thương hiệu nào —</option>
          {(brands.data ?? []).map((brand) => (
            <option key={brand.id} value={brand.id}>
              {brand.name}
            </option>
          ))}
        </Select>
      </label>
      <label className="block text-sm">
        Tên kênh
        <input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Apexmed Facebook"
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      <label className="block text-sm">
        Danh mục
        <Select
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        >
          {CHANNEL_CATEGORIES.map((code) => (
            <option key={code} value={code}>
              {channelCategoryLabel(code)}
            </option>
          ))}
        </Select>
      </label>
      <label className="block text-sm">
        URL / trang kênh
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      <label className="block text-sm">
        Handle
        <input
          value={handle}
          onChange={(event) => setHandle(event.target.value)}
          placeholder="@drtien"
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      <p className="text-xs text-[var(--text-muted)]">
        Handle là tên hiển thị của tài khoản, không bắt buộc. Ghi sao lưu vậy -
        có hay không có <strong>@</strong> đều được.
      </p>
      <label className="block text-sm">
        ID tài khoản trên nền tảng
        <input
          value={externalId}
          onChange={(event) => setExternalId(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      <p className="text-xs text-[var(--text-muted)]">
        Mã kênh do hệ thống cấp. Kênh mới luôn ở trạng thái{" "}
        <strong>{channelStatusLabel("ACTIVE")}</strong> và sẽ xuất hiện ngay
        trong mục Kênh dự kiến khi lập nội dung.
      </p>
      {create.isError ? <ErrorBox error={create.error} /> : null}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onDone()}
          className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
        >
          Hủy
        </button>
        <button
          type="submit"
          disabled={create.isPending || !platformId || !name.trim()}
          className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
        >
          {create.isPending ? "Đang lưu…" : "Tạo kênh"}
        </button>
      </div>
    </form>
  );
}

function ChannelDetail({ channelId }: { channelId: string }) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["channel", channelId],
    queryFn: () => api.getChannel(channelId),
  });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const names = new Map(
    (people.data ?? []).map((person) => [person.user_id, person.full_name]),
  );

  const [userId, setUserId] = useState("");
  const [role, setRole] = useState(ASSIGNMENT_ROLES[0]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["channel", channelId] });
    void queryClient.invalidateQueries({ queryKey: ["channels"] });
  };

  const assign = useMutation({
    mutationFn: () =>
      api.assignChannel(channelId, {
        user_id: userId,
        assignment_role: role,
        effective_from: from,
        effective_to: to || null,
      }),
    onSuccess: () => {
      setUserId("");
      setTo("");
      invalidate();
    },
  });
  const close = useMutation({
    mutationFn: ({ id, day }: { id: string; day: string }) =>
      api.closeChannelAssignment(channelId, id, day),
    onSuccess: invalidate,
  });

  if (detail.isPending) return <Loading />;
  if (detail.isError)
    return <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />;

  const channel = detail.data.channel;

  return (
    <div className="space-y-4">
      <ChannelIdentity
        channel={channel}
        canEdit={detail.data.can_edit_channel}
        onSaved={invalidate}
      />

      <ChannelConnectionPanel channelId={channelId} />

      <ChannelMetricsPanel channelId={channelId} />

      <section className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
        <h3 className="mb-2 text-sm font-semibold">Phân công</h3>
        {detail.data.assignments.length === 0 ? (
          <Empty message="Chưa phân công cho ai." />
        ) : (
          <ul className="space-y-2 text-sm">
            {detail.data.assignments.map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-center justify-between gap-2"
              >
                <span>
                  {names.get(row.user_id) ?? row.user_id} ·{" "}
                  {row.assignment_role} ·{" "}
                  <span className="text-[var(--text-muted)]">
                    {formatDay(row.effective_from)} →{" "}
                    {row.effective_to ? formatDay(row.effective_to) : "đang mở"}
                  </span>
                </span>
                {row.effective_to === null ? (
                  <CloseButton
                    who={names.get(row.user_id) ?? row.user_id}
                    onClose={(day) => close.mutate({ id: row.id, day })}
                    pending={close.isPending}
                    error={close.error}
                  />
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {close.isError ? <ErrorBox error={close.error} /> : null}
      </section>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          assign.mutate();
        }}
        className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
      >
        <h3 className="text-sm font-semibold">Thêm phân công</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            Người
            <Select
              required
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            >
              <option value="">— chọn —</option>
              {(people.data ?? []).map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.full_name}
                </option>
              ))}
            </Select>
          </label>
          <label className="text-sm">
            Vai trò
            <Select
              value={role}
              onChange={(event) => setRole(event.target.value)}
              className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            >
              {ASSIGNMENT_ROLES.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </Select>
          </label>
          <label className="text-sm">
            Từ ngày
            <input
              required
              type="date"
              value={from}
              onChange={(event) => setFrom(event.target.value)}
              className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            />
          </label>
          <label className="text-sm">
            Đến ngày (bao gồm ngày này)
            <input
              type="date"
              value={to}
              onChange={(event) => setTo(event.target.value)}
              className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            />
          </label>
        </div>
        <p className="text-xs text-[var(--text-muted)]">
          Để trống &quot;đến ngày&quot; nghĩa là phân công còn mở. Ngày kết thúc
          là ngày <strong>cuối cùng còn hiệu lực</strong>. Hệ thống sẽ từ chối
          nếu khoảng thời gian trùng với một phân công khác cùng vai trò.
        </p>
        {assign.isError ? <ErrorBox error={assign.error} /> : null}
        <button
          type="submit"
          disabled={assign.isPending}
          className="rounded bg-[var(--text)] px-3 py-1.5 text-sm text-[var(--surface)] disabled:opacity-50"
        >
          {assign.isPending ? "Đang lưu…" : "Phân công"}
        </button>
      </form>
    </div>
  );
}

function CloseButton({
  who,
  onClose,
  pending,
  error,
}: {
  /** Whose assignment this is, so the confirmation can name them. */
  who: string;
  onClose: (day: string) => void;
  pending: boolean;
  error?: unknown;
}) {
  const [day, setDay] = useState("");
  return (
    <span className="flex items-center gap-1">
      <input
        type="date"
        value={day}
        onChange={(event) => setDay(event.target.value)}
        className="rounded border border-[var(--border)] bg-transparent px-2 py-1 text-xs"
        aria-label={`Ngày kết thúc phân công của ${who}`}
      />
      {/* Step 1F.2.8. The date field is a parameter, not a confirmation: picking
          a day says nothing about whose assignment ends. The dialog names the
          person, which is the part a row of five assignments makes easy to get
          wrong. */}
      <ConfirmButton
        spec={closeChannelAssignmentConfirmation(who)}
        tone="secondary"
        disabled={!day}
        pending={pending}
        error={error}
        onConfirm={() => onClose(day)}
      >
        Kết thúc
      </ConfirmButton>
    </span>
  );
}

/**
 * Who this channel is: platform, name, handle, link, data status.
 *
 * The block a person reads first, and the one that answers "which account am I
 * even looking at". The platform is rendered as text - `platform_label` from
 * the server - because a badge that only differs by colour tells a
 * colour-blind reader nothing, and this is the field everything else on the
 * screen is organised around.
 *
 * A channel on a platform outside the canonical six says "Chưa xác định" and,
 * where there is one, the name it was actually registered under. That is not an
 * error state: nothing guessed a platform from the URL, so nobody has said yet.
 * A manager gets an edit form; everybody else gets the truth and no button.
 */
function ChannelIdentity({
  channel,
  canEdit,
  onSaved,
}: {
  channel: Channel;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);

  if (editing) {
    return (
      <EditChannelForm
        channel={channel}
        onDone={() => {
          setEditing(false);
          onSaved();
        }}
      />
    );
  }

  return (
    <header className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <code className="text-xs text-[var(--text-muted)]">
            {channel.code}
          </code>
          <p className="text-sm font-medium">{channel.platform_label}</p>
          <h2 className="text-lg font-semibold">{channel.name}</h2>
          {channel.handle ? (
            <p className="text-sm text-[var(--text-muted)]">{channel.handle}</p>
          ) : null}
          {channel.url ? (
            <a
              href={channel.url}
              className="text-xs underline"
              rel="noreferrer noopener"
              target="_blank"
            >
              {channel.url}
            </a>
          ) : null}
        </div>
        {canEdit ? (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
          >
            Sửa thông tin kênh
          </button>
        ) : null}
      </div>

      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-[var(--text-muted)]">Dữ liệu</dt>
          <dd>{channel.metrics_status_label}</dd>
        </div>
        <div>
          <dt className="text-[var(--text-muted)]">Cập nhật chỉ số</dt>
          {/* Always shown beside the figure it belongs to. A manual reading is
              as old as this says, and hiding it would let a three-week-old
              number read as today's. */}
          <dd>
            {channel.latest_captured_at
              ? formatWhen(channel.latest_captured_at)
              : "—"}
          </dd>
        </div>
        {channel.external_id ? (
          <div>
            <dt className="text-[var(--text-muted)]">ID tài khoản</dt>
            <dd className="font-mono">{channel.external_id}</dd>
          </div>
        ) : null}
        <div>
          <dt className="text-[var(--text-muted)]">Danh mục</dt>
          <dd>{channelCategoryLabel(channel.category)}</dd>
        </div>
      </dl>

      {channel.platform === null ? (
        <p className="mt-3 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          Kênh này <strong>chưa xác định nền tảng</strong> chuẩn. Kênh vẫn dùng
          bình thường để lập nội dung và ghi nhận bài đăng
          {channel.platform_name
            ? ` (đang đăng ký trên "${channel.platform_name}")`
            : ""}
          .{canEdit ? " Hãy chọn lại nền tảng để hiển thị đúng." : ""}
        </p>
      ) : null}
    </header>
  );
}

/**
 * Change what a channel is. Management only, and the code is not on it.
 *
 * The platform is editable here and was not before Step 1F.2.4a. That is a real
 * change and it carries a real warning: readings already recorded were taken
 * under the old platform and **stay exactly where they are**. Nothing is
 * rewritten and nothing is deleted - what changes is only what the channel is
 * called from now on.
 */
function EditChannelForm({
  channel,
  onDone,
}: {
  channel: Channel;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const platforms = useQuery({
    queryKey: ["platforms"],
    queryFn: () => api.listPlatforms(),
  });
  // Only to decide whether to warn before a platform change. The panel below
  // has already fetched it, so this is a cache read rather than a request.
  const metrics = useQuery({
    queryKey: ["channel-metrics", channel.id],
    queryFn: () => api.channelMetrics(channel.id),
  });

  const [platformId, setPlatformId] = useState(channel.platform_id);
  const [name, setName] = useState(channel.name);
  const [handle, setHandle] = useState(channel.handle ?? "");
  const [url, setUrl] = useState(channel.url ?? "");
  const [externalId, setExternalId] = useState(channel.external_id ?? "");

  const save = useMutation({
    mutationFn: () =>
      api.updateChannel(channel.id, {
        name,
        platform_id: platformId,
        handle: handle || null,
        url: url || null,
        external_id: externalId || null,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["channels"] });
      await queryClient.invalidateQueries({
        queryKey: ["channel", channel.id],
      });
      onDone();
    },
  });

  const movingPlatform = platformId !== channel.platform_id;
  const hasHistory = metrics.data?.has_history ?? false;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
      aria-label="Sửa thông tin kênh"
      className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <h3 className="text-sm font-semibold">Sửa thông tin kênh</h3>
      <label className="block text-sm">
        Nền tảng *
        <Select
          required
          value={platformId}
          onChange={(event) => setPlatformId(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        >
          {platforms.data?.map((platform) => (
            <option key={platform.id} value={platform.id}>
              {platform.name}
            </option>
          ))}
        </Select>
      </label>
      {movingPlatform && hasHistory ? (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          Kênh này đã có lịch sử chỉ số. Đổi nền tảng <strong>không xoá</strong>{" "}
          và
          <strong> không sửa</strong> các lần ghi nhận cũ - chúng vẫn thuộc về
          kênh này, nhưng được đo trên nền tảng cũ, nên cách hiểu con số có thể
          khác.
        </p>
      ) : null}
      <label className="block text-sm">
        Tên kênh *
        <input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      <label className="block text-sm">
        Handle
        <input
          value={handle}
          onChange={(event) => setHandle(event.target.value)}
          placeholder="@drtien"
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      <label className="block text-sm">
        URL / trang kênh
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      <label className="block text-sm">
        ID tài khoản trên nền tảng
        <input
          value={externalId}
          onChange={(event) => setExternalId(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      {save.isError ? <ErrorBox error={save.error} /> : null}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onDone}
          className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
        >
          Hủy
        </button>
        <button
          type="submit"
          disabled={save.isPending || !name.trim()}
          className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
        >
          {save.isPending ? "Đang lưu…" : "Lưu"}
        </button>
      </div>
    </form>
  );
}

/**
 * Every field the manual form offers, in the order it offers them.
 *
 * Step 1F.2.4d added six. The form offers them for the same reason it offers
 * the other fourteen: a channel on a platform with no connector is measured by
 * somebody reading the numbers off a screen, and a metric TasksBot can store but
 * cannot be told is a column nobody outside a Facebook Page can ever fill.
 */
const METRIC_FIELDS: Array<{ key: string; label: string }> = [
  { key: "followers", label: "Followers" },
  { key: "fans", label: "Page Likes" },
  { key: "following", label: "Following" },
  { key: "posts_count", label: "Số bài (tổng)" },
  { key: "posts_count_7d", label: "Số bài 7 ngày" },
  { key: "posts_count_30d", label: "Số bài 30 ngày" },
  { key: "views_7d", label: "Views 7 ngày" },
  { key: "views_30d", label: "Views 30 ngày" },
  { key: "video_views_7d", label: "Video views 7 ngày" },
  { key: "video_views_30d", label: "Video views 30 ngày" },
  { key: "reach_7d", label: "Reach 7 ngày" },
  { key: "reach_30d", label: "Reach 30 ngày" },
  { key: "impressions_7d", label: "Impressions 7 ngày" },
  { key: "impressions_30d", label: "Impressions 30 ngày" },
  { key: "engagements_7d", label: "Engagements 7 ngày" },
  { key: "engagements_30d", label: "Engagements 30 ngày" },
  { key: "reactions_30d", label: "Reactions 30 ngày" },
  { key: "likes_30d", label: "Likes 30 ngày" },
  { key: "comments_30d", label: "Comments 30 ngày" },
  { key: "shares_30d", label: "Shares 30 ngày" },
];

/**
 * What a response from a server that predates this milestone carries.
 *
 * `capabilities` is typed non-optional because every current response has one,
 * and every reader below is written as though it does. This is the one place
 * that admits the wire can disagree: a browser tab left open across a deploy,
 * or a cached response, arrives with the field simply absent, and `undefined`
 * reaching `.unavailable.map` is a blank page rather than a missing sentence.
 *
 * All-`false` is the right fallback and not merely the safe one. It says "we
 * cannot show that any of this was available", so the panel renders every
 * number it was given and explains nothing it cannot justify.
 */
const NO_CAPABILITIES: MetricCapabilities = {
  reach_available: false,
  impressions_available: false,
  reactions_available: false,
  comments_available: false,
  shares_available: false,
  video_views_available: false,
  page_views_available: false,
  top_post_rankable: false,
  post_fields: {},
  insight_metrics_available: [],
  window_30d_end: null,
  unavailable: [],
};

function capabilitiesOf(analytics: ChannelAnalytics): MetricCapabilities {
  // The cast is the point: the type says this is always here, the wire says it
  // is here on every response this build will ever see, and neither is a reason
  // to render `undefined.unavailable` to somebody mid-deploy.
  return (
    (analytics.capabilities as MetricCapabilities | undefined) ??
    NO_CAPABILITIES
  );
}

/**
 * The glyph for a direction, and nothing else.
 *
 * Deliberately not a colour. The design system has `--text`, `--text-muted`,
 * `--border` and `--accent` and no semantic success/danger pair, so painting a
 * follower drop red would be inventing a token *and* a judgement: a fall in
 * engagements after a campaign ends is not a failure, and a dashboard that
 * colours it as one teaches people to argue with the chart. The arrow reports
 * the direction; the reader decides what it means.
 */
const TREND_ARROWS: Record<string, string> = { UP: "↑", DOWN: "↓", FLAT: "→" };

/**
 * What a number did since a comparable reading, or that there is no such reading.
 *
 * The two branches are the same rule the whole panel follows. A comparison
 * TasksBot could make prints the change **and what it actually compared against**;
 * one it could not prints "Chưa đủ dữ liệu" and never `0%`. A channel connected
 * last Tuesday has not been flat for a month - nobody knows what it did, and a
 * `0%` there is a measurement nobody took.
 */
function TrendLine({ change }: { change: MetricChange | null }) {
  if (!change) {
    return <p className="text-xs text-[var(--text-muted)]">Chưa đủ dữ liệu</p>;
  }
  return (
    <p className="text-xs text-[var(--text-muted)]">
      {/* The sign in "+4,2%" already carries the direction, so the glyph is
          decoration for the eye and hidden from a screen reader rather than
          read out as "mũi tên lên". */}
      <span aria-hidden="true">{TREND_ARROWS[change.direction] ?? "→"}</span>{" "}
      {change.delta_pct === null
        ? formatDelta(change.delta)
        : formatDeltaPercent(change.delta_pct)}{" "}
      {formatComparisonBasis(change.window_days, change.baseline_age_days)}
    </p>
  );
}

/**
 * One number, its label, and what it changed by.
 *
 * Always rendered, even with nothing in it. Step 1F.2.4d changed the rule the
 * panel had followed since 1F.2.4a - *draw a card only for a metric the reading
 * carries* - and the reason is that a manager comparing two channels needs the
 * same cards in the same places on both. A missing card reads as "this channel
 * is different"; a card reading "—" reads as "nobody has this number", which is
 * the truth.
 *
 * `—` and never `0`. `formatCount` already draws the distinction and this
 * component never substitutes a zero for a blank.
 *
 * `reason` is for a blank the server can explain - "Chưa có quyền đọc" under an
 * em dash. It is deliberately a *short* chip rather than the full sentence: the
 * sentence lives once, in the information area at the bottom, because repeating
 * a two-line warning on four cards is how a panel stops being read at all.
 */
function MetricCard({
  label,
  value,
  hint,
  change,
  reason,
  title,
}: {
  label: string;
  value: string;
  hint?: string | null;
  change?: MetricChange | null;
  reason?: string | null;
  /** Hover text for the label. Used for ratios TasksBot defines itself. */
  title?: string;
}) {
  return (
    <div className="rounded border border-[var(--border)] p-3">
      <p className="text-xs text-[var(--text-muted)]" title={title}>
        {label}
      </p>
      <p className="text-lg font-semibold">{value}</p>
      {change === undefined ? null : <TrendLine change={change} />}
      {reason ? (
        <p className="text-xs text-[var(--text-muted)]">{reason}</p>
      ) : null}
      {hint ? <p className="text-xs text-[var(--text-muted)]">{hint}</p> : null}
    </div>
  );
}

/** Three cards under one heading, which is what makes nine readable. */
function MetricGroup({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold text-[var(--text-muted)]">
        {title}
      </h4>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{children}</div>
    </div>
  );
}

/**
 * The nine numbers a PR manager reads first, in the three questions they answer.
 *
 * Everything here comes from `analytics`, which the **server** computed. The
 * browser does no arithmetic on a metric: no dividing engagements by followers,
 * no subtracting one snapshot from another, no working out how many days apart
 * two readings were. That rule is the same one `days_since_capture` follows and
 * it exists because two implementations of "how many days" eventually disagree
 * about the boundary - and because a growth figure has to be reproducible in a
 * report, which a number computed in a browser is not.
 *
 * Why nine and not fifteen
 * ------------------------
 *
 * The panel used to draw twelve fixed cards and up to five conditional ones, and
 * a manager opening it could not tell which three mattered. Step 1F.2.5 groups
 * the nine that answer a question somebody actually asks - *is it growing, is
 * engagement moving, how much did we publish and who saw it* - and moves the
 * rest behind "Chi tiết dữ liệu", which is one click and no ambiguity.
 *
 * Nothing was deleted to get there. Every number the old grid drew is still on
 * this screen; four of them are in the disclosure, and the ones that can never
 * fill are in an information area that says why instead of showing an em dash
 * where a number belongs.
 */
function AnalyticsCards({
  analytics,
  trend,
}: {
  analytics: ChannelAnalytics;
  trend: ChannelFollowerTrend | null;
}) {
  const capabilities = capabilitiesOf(analytics);
  const growth = analytics.follower_growth_30d;
  const hasDistribution =
    analytics.reach_30d !== null ||
    analytics.impressions_30d !== null ||
    analytics.views_30d !== null;
  return (
    // A labelled group, so the cards are one addressable region rather than
    // loose text: the history table below repeats several of these headings,
    // and a reader - a screen reader or a test - has to be able to tell "the
    // Followers card" from "the Followers column".
    <div className="space-y-4" role="group" aria-label="Chỉ số tổng hợp">
      <MetricGroup title="Khán giả">
        <MetricCard
          label="Followers"
          value={formatCount(analytics.followers)}
          change={growth ?? null}
          /* No 30-day baseline near enough to compare against - a channel
             connected last week, or one somebody records by hand every few
             months. The previous-reading comparison is still true and still
             says exactly what it compared, so it is shown instead of nothing.
             It is never shown *beside* the growth figure: two deltas under one
             number is two answers to one question. */
          hint={
            growth || !trend
              ? undefined
              : `${formatDelta(trend.delta)}${
                  trend.delta_pct === null
                    ? ""
                    : ` (${formatDeltaPercent(trend.delta_pct)})`
                } so với lần ghi trước`
          }
        />
        <MetricCard
          label="Lượt thích Trang"
          value={formatCount(analytics.fans)}
        />
        <MetricCard
          label="Tăng Followers 30 ngày"
          value={growth ? formatDelta(growth.delta) : "—"}
          hint={
            growth
              ? formatComparisonBasis(
                  growth.window_days,
                  growth.baseline_age_days,
                )
              : "Chưa đủ dữ liệu"
          }
        />
      </MetricGroup>

      <MetricGroup title="Tương tác">
        <MetricCard
          label="Tương tác 7 ngày"
          value={formatCount(analytics.engagements_7d)}
          change={analytics.engagement_change_7d}
        />
        <MetricCard
          label="Tương tác 30 ngày"
          value={formatCount(analytics.engagements_30d)}
          change={analytics.engagement_change_30d}
        />
        <MetricCard
          label="Tương tác / Followers"
          value={formatRatioPercent(analytics.engagement_per_follower_30d)}
          /* TasksBot's own management ratio, and it says so. Facebook publishes
             no "engagement rate" for a Page and this is not it: it is the
             month's engagements over today's follower count, which is a useful
             internal comparison between two channels and not a figure to quote
             to a client as Meta's. */
          title="Tỷ lệ tương tác 30 ngày so với số Followers hiện tại. Đây là tỷ lệ nội bộ của TasksBot, không phải chỉ số chính thức của Facebook."
          hint="tỷ lệ nội bộ, không phải chỉ số của Facebook"
        />
      </MetricGroup>

      <MetricGroup title="Nội dung & lượt xem">
        <MetricCard
          label="Bài đăng 30 ngày"
          value={formatCount(analytics.posts_count_30d)}
          hint={
            analytics.posts_count_7d === null
              ? undefined
              : `7 ngày: ${formatCount(analytics.posts_count_7d)}`
          }
        />
        <MetricCard
          label="Lượt xem video 30 ngày"
          value={formatCount(analytics.video_views_30d)}
          hint={
            analytics.video_views_7d === null
              ? undefined
              : `7 ngày: ${formatCount(analytics.video_views_7d)}`
          }
        />
        <MetricCard
          label="Lượt xem Trang 30 ngày"
          value={formatCount(analytics.page_views_30d)}
          /* Profile views, under their own name. Never filed as "Views": that
             column means watch time's cousin on every other platform in the
             channel list, and one heading meaning two things is how a dashboard
             starts lying quietly. */
          hint={
            analytics.page_views_7d === null
              ? undefined
              : `7 ngày: ${formatCount(analytics.page_views_7d)}`
          }
        />
      </MetricGroup>

      {/* The fourth group, and the only conditional one. Reach, impressions and
          account-level views are what differ by **platform** rather than by
          luck: an Instagram account reports all three, a Facebook Page will
          never report any of them again because Graph v23 retired them, and a
          card that can never fill trains people to ignore that position in the
          grid. So the group is drawn where the platform answers and replaced by
          a sentence where it does not - see `UnavailableMetrics`. */}
      {hasDistribution ? (
        <MetricGroup title="Tiếp cận & hiển thị">
          {analytics.reach_30d === null ? null : (
            <MetricCard
              label="Reach 30 ngày"
              value={formatCount(analytics.reach_30d)}
            />
          )}
          {analytics.impressions_30d === null ? null : (
            <MetricCard
              label="Impressions 30 ngày"
              value={formatCount(analytics.impressions_30d)}
            />
          )}
          {analytics.views_30d === null ? null : (
            <MetricCard
              label="Views 30 ngày"
              value={formatCount(analytics.views_30d)}
            />
          )}
        </MetricGroup>
      ) : null}

      <AnalyticsDetail analytics={analytics} capabilities={capabilities} />
      <UnavailableMetrics entries={capabilities.unavailable} />
    </div>
  );
}

/**
 * Everything true but secondary, one click away.
 *
 * A disclosure rather than a second grid. These are the numbers somebody looks
 * up when they already have a question - *how many of those engagements were
 * shares, how many posts last week* - and putting them on the front of the
 * panel costs the nine cards above their legibility for readers who never ask.
 *
 * Reactions and Comments live here rather than in the main grid on purpose, and
 * not because they are unimportant: with the current Facebook grant they cannot
 * be read at all, and a permanently blank card in a primary row trains people
 * to ignore that position in the grid. When the grant covers them they carry
 * real numbers here, and promoting them is then a one-line change.
 */
function AnalyticsDetail({
  analytics,
  capabilities,
}: {
  analytics: ChannelAnalytics;
  capabilities: MetricCapabilities;
}) {
  /* The chip under a blank, for the cards whose blank has a known cause. The
     server already named the reason for every metric it can explain, so this is
     a lookup rather than a second opinion. */
  const reasonFor = (metric: string): string | null =>
    capabilities.unavailable.find((entry) => entry.metric === metric)
      ?.availability_label ?? null;

  return (
    <details className="rounded border border-[var(--border)] p-3">
      <summary className="cursor-pointer text-sm font-semibold">
        Chi tiết dữ liệu
      </summary>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <MetricCard
          label="Bài đăng 7 ngày"
          value={formatCount(analytics.posts_count_7d)}
        />
        <MetricCard
          label="Lượt xem video 7 ngày"
          value={formatCount(analytics.video_views_7d)}
        />
        <MetricCard
          label="Lượt xem Trang 7 ngày"
          value={formatCount(analytics.page_views_7d)}
        />
        <MetricCard
          label="Reactions 30 ngày"
          value={formatCount(analytics.reactions_30d)}
          reason={
            analytics.reactions_30d === null ? reasonFor("reactions_30d") : null
          }
        />
        <MetricCard
          label="Comments 30 ngày"
          value={formatCount(analytics.comments_30d)}
          reason={
            analytics.comments_30d === null ? reasonFor("comments_30d") : null
          }
        />
        <MetricCard
          label="Shares 30 ngày"
          value={formatCount(analytics.shares_30d)}
          reason={
            analytics.shares_30d === null ? reasonFor("shares_30d") : null
          }
        />
        <MetricCard
          label="Trung bình tương tác / bài"
          value={formatAverage(analytics.average_engagement_per_post_30d)}
          hint="reactions + comments + shares trên số bài 30 ngày"
        />
        {/* The platform-specific tail. Drawn only where the platform reports
            the metric at all: a Facebook Page has no lifetime post count and no
            like count distinct from its reactions, and a card that can never
            fill is worse than no card even in a disclosure.

            Reach, impressions and account-level views are **not** here - they
            are a group of their own above, because on a platform that reports
            them they are primary information rather than a footnote. Their
            7-day halves are, because a weekly reach figure genuinely is one. */}
        {analytics.reach_7d === null ? null : (
          <MetricCard
            label="Reach 7 ngày"
            value={formatCount(analytics.reach_7d)}
          />
        )}
        {analytics.impressions_7d === null ? null : (
          <MetricCard
            label="Impressions 7 ngày"
            value={formatCount(analytics.impressions_7d)}
          />
        )}
        {analytics.views_7d === null ? null : (
          <MetricCard
            label="Views 7 ngày"
            value={formatCount(analytics.views_7d)}
          />
        )}
        {analytics.likes_30d === null ? null : (
          <MetricCard
            label="Likes 30 ngày"
            value={formatCount(analytics.likes_30d)}
          />
        )}
        {analytics.posts_count === null ? null : (
          <MetricCard
            label="Số bài (tổng)"
            value={formatCount(analytics.posts_count)}
          />
        )}
      </div>
      {capabilities.insight_metrics_available.length > 0 ? (
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          Chỉ số Insights lần đồng bộ này đọc được:{" "}
          {capabilities.insight_metrics_available.join(", ")}
        </p>
      ) : null}
    </details>
  );
}

/**
 * The cards that are not drawn, and why not.
 *
 * This section exists because of a support question rather than a design idea.
 * A manager who sees "Reach —" concludes that TasksBot is broken - which is the
 * reasonable conclusion and the wrong one - and asks. An em dash where a number
 * belongs *is* a defect unless something says otherwise, so rather than draw a
 * card that can never fill, the panel names the metric here and says which of
 * two very different things happened to it:
 *
 * - **Không còn được Meta cung cấp** - the platform retired it. Nothing fixes
 *   this and nobody should keep asking;
 * - **Chưa có quyền đọc** - this connection's grant does not cover it. A person
 *   reauthorizing with a wider consent fixes it, and waiting does not.
 *
 * Grouped by reason so the long sentence appears once. Five metrics sharing one
 * explanation printed five times is a paragraph nobody reads.
 *
 * Every word is the server's. A browser that decided which platforms retired
 * reach would be holding platform knowledge one layer too high, and would be
 * wrong the quarter after Meta changed its mind.
 */
function UnavailableMetrics({ entries }: { entries: UnavailableMetric[] }) {
  if (entries.length === 0) return null;
  const groups = new Map<
    string,
    { label: string; note: string; metrics: string[] }
  >();
  for (const entry of entries) {
    const group = groups.get(entry.availability) ?? {
      label: entry.availability_label,
      note: entry.note,
      metrics: [],
    };
    group.metrics.push(entry.label);
    groups.set(entry.availability, group);
  }
  return (
    <div
      className="space-y-2 rounded border border-dashed border-[var(--border)] p-3"
      role="group"
      aria-label="Chỉ số chưa hiển thị"
    >
      <h4 className="text-xs font-semibold text-[var(--text-muted)]">
        Chỉ số chưa hiển thị
      </h4>
      {[...groups.entries()].map(([availability, group]) => (
        <div key={availability}>
          <p className="text-xs">
            {group.metrics.join(" · ")} — {group.label}
          </p>
          <p className="text-xs text-[var(--text-muted)]">{group.note}</p>
        </div>
      ))}
    </div>
  );
}

/**
 * The month's best-performing post.
 *
 * A link and four numbers. The excerpt is the server's, already truncated -
 * this panel is a summary of a month, not a place to read a post.
 *
 * **Drawn only when the ranking means something.** The server withholds
 * `top_post_30d` entirely when the reaction and comment summaries could not be
 * read: the connector still has to pick a post and it picks the most-shared
 * one, but calling that "bài tốt nhất" on a screen a manager quotes to a client
 * would be TasksBot misleading them rather than Meta. That decision is the
 * server's and this component never second-guesses it - an object here is an
 * object that may be labelled a ranking.
 */
function TopPostCard({ post }: { post: TopPost }) {
  return (
    <div className="rounded border border-[var(--border)] p-3">
      <p className="text-xs text-[var(--text-muted)]">Bài tốt nhất 30 ngày</p>
      <p className="text-sm">
        {post.permalink_url ? (
          <a
            href={post.permalink_url}
            target="_blank"
            rel="noreferrer noopener"
            className="underline"
          >
            {post.excerpt ?? post.post_id}
          </a>
        ) : (
          (post.excerpt ?? post.post_id)
        )}
      </p>
      <p className="text-xs text-[var(--text-muted)]">
        {formatCount(post.engagements)} tương tác ·{" "}
        {formatCount(post.reactions)} reactions · {formatCount(post.comments)}{" "}
        comments · {formatCount(post.shares)} shares
        {post.created_at ? ` · đăng ${formatWhen(post.created_at)}` : ""}
      </p>
    </div>
  );
}

/**
 * What this channel measured, and when.
 *
 * Headed "Chỉ số gần nhất" and never "Live": the numbers were read at the moment
 * printed underneath them, by a sync or by a person, and either way that moment
 * is in the past. The heading, the capture time and the source line are all part
 * of the same promise, which is why none of them is optional.
 *
 * Step 1F.2.4d moved the cards onto `analytics`, which the server computes from
 * this channel's own stored readings. Nothing on this screen is a number the
 * browser worked out.
 */
function ChannelMetricsPanel({ channelId }: { channelId: string }) {
  const queryClient = useQueryClient();
  const metrics = useQuery({
    queryKey: ["channel-metrics", channelId],
    queryFn: () => api.channelMetrics(channelId),
  });
  const [recording, setRecording] = useState(false);

  if (metrics.isPending) return <Loading />;
  if (metrics.isError) {
    return <ErrorBox error={metrics.error} onRetry={() => metrics.refetch()} />;
  }

  const data: ChannelMetrics = metrics.data;
  // The panel's own answer, from the panel's own response. It is the same
  // capability the channel detail reports, asked of the endpoint that would
  // accept the write - so the button and the write cannot come apart.
  const mayRecord = data.can_record_metrics;
  const latest = data.latest;
  const analytics = data.analytics;
  /**
   * Step 1F.2.9. Whether a platform API is already filling this channel in.
   *
   * The server's own badge, not a guess: `CONNECTED_API` means a live connector
   * is feeding the history. Where it is, hand-entering the same numbers is at
   * best redundant and at worst a second, contradictory figure in one timeline
   * - so the manual form stops being the headline action and becomes the
   * fallback it is.
   *
   * It is **not** removed. A connected channel still needs manual entry for
   * anything its API does not serve, and for backfilling a period from before
   * the connection existed. What changes is which control leads.
   */
  const apiConnected = data.status === "CONNECTED_API";

  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: ["channel-metrics", channelId],
    });
    void queryClient.invalidateQueries({ queryKey: ["channel", channelId] });
    void queryClient.invalidateQueries({ queryKey: ["channels"] });
  };

  return (
    <section className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Chỉ số gần nhất</h3>
        {mayRecord ? (
          <button
            type="button"
            onClick={() => setRecording((open) => !open)}
            title={
              apiConnected
                ? "Kênh này đang lấy số liệu tự động. Chỉ nhập tay khi cần bổ sung chỉ số nền tảng không trả về."
                : undefined
            }
            className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
          >
            {recording
              ? "Đóng"
              : apiConnected
                ? "+ Nhập tay (bổ sung)"
                : "+ Ghi nhận chỉ số"}
          </button>
        ) : null}
      </div>

      {recording ? (
        <RecordMetricsForm
          channelId={channelId}
          onDone={() => {
            setRecording(false);
            refresh();
          }}
        />
      ) : null}

      {/* Both, and `!` rather than `=== null`: a response from a server that
          predates this step carries no `analytics` at all, and the panel must
          fall back to its empty state rather than throw on a page load. */}
      {!latest || !analytics ? (
        <Empty
          message={
            apiConnected
              ? "Chưa có dữ liệu chỉ số đã lưu. Bấm “Đồng bộ lại” ở phần kết nối để lấy số liệu từ nền tảng."
              : mayRecord
                ? "Chưa có dữ liệu chỉ số. Bấm “Ghi nhận chỉ số” để nhập lần đo đầu tiên."
                : "Chưa có dữ liệu chỉ số."
          }
        />
      ) : (
        <>
          <AnalyticsCards analytics={analytics} trend={data.trend} />

          {analytics.top_post_30d ? (
            <TopPostCard post={analytics.top_post_30d} />
          ) : null}

          {/* Step 1F.2.4b. Two different times, said as two different things.
              "Ghi nhận" is when TasksBot fetched; the window below is what the
              numbers actually cover, which for YouTube Analytics ends a couple
              of days back because that is when the data settles. Calling both
              "cập nhật lúc" would make a 30-day figure look like a snapshot of
              this morning. */}
          {analyticsWindow(latest) ? (
            <p className="text-xs text-[var(--text-muted)]">
              Views/Reach 30 ngày: dữ liệu YouTube Analytics{" "}
              {analyticsWindow(latest)}
            </p>
          ) : null}

          {/* Step 1F.2.4d. Why the reach and impressions cards are absent
              rather than blank. The sentence is the server's - a browser that
              knew which platforms report reach would be deciding something the
              server is the authority on.

              Step 1F.2.5 made this a fallback rather than the answer. The
              "Chỉ số chưa hiển thị" area above now says the same thing per
              metric and says which of two causes applied, so printing this
              paragraph as well would be the same warning twice. It still
              appears for a response that carries no structured explanation -
              an older server, or a tab open across a deploy. */}
          {analytics.limitation_note &&
          capabilitiesOf(analytics).unavailable.length === 0 ? (
            <p className="text-xs text-[var(--text-muted)]">
              {analytics.limitation_note}
            </p>
          ) : null}

          {/* Step 1F.2.5. What the 30-day figures actually cover.

              Meta Insights settles up to about 48 hours behind, so the window
              these numbers describe ends a couple of days before the sync that
              fetched them. "Ghi nhận" below is when TasksBot asked; this is what
              the answer was about, and calling both "cập nhật lúc" would make a
              monthly total look like a snapshot of this morning. */}
          {capabilitiesOf(analytics).window_30d_end ? (
            <p
              className="text-xs text-[var(--text-muted)]"
              title="Meta Insights có thể chậm tối đa khoảng 48 giờ, nên cửa sổ 30 ngày kết thúc trước thời điểm đồng bộ."
            >
              Số liệu 30 ngày tính đến hết ngày{" "}
              {formatDay(capabilitiesOf(analytics).window_30d_end)}
            </p>
          ) : null}

          <p className="text-xs text-[var(--text-muted)]">
            Ghi nhận: {formatWhen(latest.captured_at)} · Nguồn:{" "}
            {latest.source_label}
            {latest.recorded_by_name
              ? ` · Người ghi nhận: ${latest.recorded_by_name}`
              : ""}
            {data.days_since_capture !== null && data.days_since_capture > 0
              ? ` · Đã ${data.days_since_capture} ngày chưa cập nhật`
              : ""}
          </p>

          <MetricHistoryTable history={data.history} total={data.total} />
        </>
      )}
    </section>
  );
}

/**
 * The Analytics window a reading covers, in Vietnamese, or `null`.
 *
 * Read out of `extra_metrics`, which is exactly what that field is for -
 * platform-specific values with no canonical column. The summary cards never
 * read it; this is presentation metadata about where the canonical numbers came
 * from, not a metric of its own.
 */
function analyticsWindow(snapshot: ChannelMetricSnapshot): string | null {
  const extra = snapshot.extra_metrics;
  if (!extra) return null;
  const start = extra["youtube_analytics_start_30d"];
  const end = extra["youtube_analytics_end_30d"];
  if (typeof start !== "string" || typeof end !== "string") return null;
  return `từ ${formatDay(start)} đến ${formatDay(end)}`;
}

/**
 * Every reading, newest first, with who wrote it down.
 *
 * Four columns of numbers and two of provenance. The provenance is the point:
 * a figure with no source and no author is a rumour, and "ai nhập chỉ số này?"
 * is answered here rather than by searching an audit log.
 *
 * Both names are resolved by the server, so this table makes no request of its
 * own however many distinct people appear in it.
 */
function MetricHistoryTable({
  history,
  total,
}: {
  history: ChannelMetricSnapshot[];
  total: number;
}) {
  if (history.length === 0) return null;
  return (
    <div className="space-y-1">
      <h4 className="text-sm font-semibold">Lịch sử ghi nhận</h4>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[40rem] text-left text-xs">
          <thead className="text-[var(--text-muted)]">
            <tr>
              <th className="py-1 pr-3 font-normal">Thời điểm</th>
              <th className="py-1 pr-3 font-normal">Followers</th>
              <th className="py-1 pr-3 font-normal">Views 30 ngày</th>
              <th className="py-1 pr-3 font-normal">Reach 30 ngày</th>
              <th className="py-1 pr-3 font-normal">Engagements 30 ngày</th>
              <th className="py-1 pr-3 font-normal">Nguồn</th>
              <th className="py-1 font-normal">Người ghi nhận</th>
            </tr>
          </thead>
          <tbody>
            {history.map((row) => (
              <tr key={row.id} className="border-t border-[var(--border)]">
                <td className="py-1 pr-3">{formatWhen(row.captured_at)}</td>
                <td className="py-1 pr-3">{formatCount(row.followers)}</td>
                <td className="py-1 pr-3">{formatCount(row.views_30d)}</td>
                <td className="py-1 pr-3">{formatCount(row.reach_30d)}</td>
                <td className="py-1 pr-3">
                  {formatCount(row.engagements_30d)}
                </td>
                <td className="py-1 pr-3">{row.source_label}</td>
                <td className="py-1">{row.recorded_by_name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > history.length ? (
        <p className="text-xs text-[var(--text-muted)]">
          Đang hiển thị {history.length} lần ghi nhận gần nhất trên tổng số{" "}
          {total}.
        </p>
      ) : null}
    </div>
  );
}

/**
 * Write down one reading.
 *
 * Only the time is required, and at least one number - the server refuses an
 * empty reading rather than storing that somebody opened a form. Nothing else
 * is compulsory, because no platform reports all fourteen and demanding them
 * would collect zeros where the honest answer is silence.
 *
 * There is no source field and no author field. This posts what a person typed;
 * the server records it as manual and attributes it to the session. A form that
 * offered "nguồn: API" would let the panel claim an integration that does not
 * exist.
 *
 * Blank stays blank on the way out: an untouched box is omitted from the body
 * entirely rather than sent as `0`.
 */
function RecordMetricsForm({
  channelId,
  onDone,
}: {
  channelId: string;
  onDone: () => void;
}) {
  const [capturedAt, setCapturedAt] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});

  const record = useMutation({
    mutationFn: () => {
      // Blank boxes are left out of the body rather than sent as zeros - see
      // the note under the grid, which says the same thing to the person.
      const metrics: Record<string, number> = {};
      for (const field of METRIC_FIELDS) {
        const raw = (values[field.key] ?? "").trim();
        if (raw !== "") metrics[field.key] = Number(raw);
      }
      return api.recordChannelMetrics(channelId, {
        captured_at: new Date(capturedAt).toISOString(),
        ...metrics,
      });
    },
    onSuccess: onDone,
  });

  const anyValue = METRIC_FIELDS.some(
    (field) => (values[field.key] ?? "").trim() !== "",
  );

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        record.mutate();
      }}
      aria-label="Ghi nhận chỉ số"
      className="space-y-3 rounded border border-[var(--border)] p-3"
    >
      <label className="block text-sm">
        Thời điểm *
        <input
          required
          type="datetime-local"
          value={capturedAt}
          onChange={(event) => setCapturedAt(event.target.value)}
          className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
        />
      </label>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {METRIC_FIELDS.map((field) => (
          <label key={field.key} className="block text-sm">
            {field.label}
            <input
              type="number"
              min={0}
              step={1}
              inputMode="numeric"
              value={values[field.key] ?? ""}
              onChange={(event) =>
                setValues((current) => ({
                  ...current,
                  [field.key]: event.target.value,
                }))
              }
              className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            />
          </label>
        ))}
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Nhập ít nhất một chỉ số. Ô để trống nghĩa là{" "}
        <strong>không có dữ liệu</strong>, khác với số 0. Mỗi lần ghi nhận là
        một dòng mới - nhập sai thì ghi nhận lại một lần đúng, hệ thống không
        sửa đè lịch sử.
      </p>
      {record.isError ? <ErrorBox error={record.error} /> : null}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onDone}
          className="min-h-11 rounded border border-[var(--border)] px-3 text-sm"
        >
          Hủy
        </button>
        <button
          type="submit"
          disabled={record.isPending || !capturedAt || !anyValue}
          className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
        >
          {record.isPending ? "Đang lưu…" : "Lưu chỉ số"}
        </button>
      </div>
    </form>
  );
}

/**
 * What a channel's platform is called in the connect controls.
 *
 * The one place a provider code becomes a button label. Everything else on this
 * screen renders a `*_label` the server sent; these are control *verbs* the
 * server has no reason to compose, so they live here - and the platform name
 * inside them is the same word the badge uses.
 */
const CONNECT_LABELS: Record<
  string,
  { connect: string; account: string; chooser: string }
> = {
  YOUTUBE: {
    connect: "Kết nối YouTube",
    account: "Kênh",
    chooser: "Chọn kênh YouTube",
  },
  FACEBOOK: {
    connect: "Kết nối Facebook",
    account: "Trang",
    chooser: "Chọn Trang Facebook",
  },
  INSTAGRAM: {
    connect: "Kết nối Instagram",
    account: "Tài khoản",
    chooser: "Chọn tài khoản Instagram",
  },
  /* Step 1F.2.6. `chooser` is present for the shape's sake and is never shown:
     TikTok consent authorizes exactly one account, so the provider does not
     implement account selection and a TikTok connection never reaches
     PENDING_SELECTION. Somebody who connected the wrong TikTok account fixes it
     by signing out of TikTok and pressing "Kết nối lại". */
  TIKTOK: {
    connect: "Kết nối TikTok",
    account: "Tài khoản",
    chooser: "Chọn tài khoản TikTok",
  },
};

/**
 * What each platform calls the id a connection is bound to.
 *
 * Worth being exact about rather than collapsing to "Account ID": this is the
 * string somebody compares against `meobot-tiktok-probe` output or against a
 * platform's own console when a connection looks wrong, and a panel that calls
 * an `open_id` an "Account ID" makes that comparison one guess longer.
 */
const ACCOUNT_ID_LABELS: Record<string, string> = {
  YOUTUBE: "Channel ID",
  TIKTOK: "Open ID",
};

/** The provider segment for a channel's platform, as the API path spells it. */
const providerPath = (platform: string | null) =>
  (platform ?? "").toLowerCase();

/**
 * Whether this channel's numbers come from the platform, and how that is going.
 *
 * Step 1F.2.4b for YouTube; Step 1F.2.4c added Facebook and Instagram and
 * **changed the shape of this component in one place only** - the account
 * chooser, which exists because Meta consent reaches every Page a person
 * manages and binding the first one would be a coin flip with somebody's brand.
 *
 * Four screens behind one component, and telling them apart is the point:
 *
 * - **no connector exists** for this platform - a TikTok channel - which is a
 *   sentence rather than a disabled button. A "Kết nối TikTok" control that
 *   always fails teaches people the panel lies;
 * - **a connector exists but this deployment has not set it up** - an operator
 *   has environment variables to fill in, which is a different problem and a
 *   different message;
 * - **consent done, nothing bound** - the chooser;
 * - **connected**, in which case the identity, the health and the controls.
 *
 * Every label is the server's. The browser never maps a state code to
 * Vietnamese, never decides who may press a button, and never renders a
 * provider's own error text - the API sends a sentence TasksBot wrote.
 */
function ChannelConnectionPanel({ channelId }: { channelId: string }) {
  const queryClient = useQueryClient();
  const state = useQuery({
    queryKey: ["channel-connection", channelId],
    queryFn: () => api.channelConnection(channelId),
  });

  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: ["channel-connection", channelId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["channel-accounts", channelId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["channel-metrics", channelId],
    });
    void queryClient.invalidateQueries({ queryKey: ["channels"] });
  };

  const data: ChannelConnectionState | undefined = state.data;
  const provider = providerPath(data?.provider ?? null);
  const words = CONNECT_LABELS[data?.provider ?? ""] ?? {
    connect: "Kết nối",
    account: "Tài khoản",
    chooser: "Chọn tài khoản",
  };

  const connect = useMutation({
    mutationFn: () => api.authorizeConnection(channelId, provider),
    // The consent screen is a full navigation, not a fetch: the browser has to
    // arrive there itself so the person can see the account they are
    // authorizing and the scopes they are granting.
    onSuccess: (result) => {
      window.location.assign(result.authorization_url);
    },
  });
  const sync = useMutation({
    mutationFn: () => api.syncChannelMetrics(channelId),
    onSuccess: refresh,
  });
  const disconnect = useMutation({
    mutationFn: () => api.disconnectConnection(channelId, provider),
    onSuccess: refresh,
  });

  if (state.isPending) return <Loading />;
  if (state.isError)
    return <ErrorBox error={state.error} onRetry={() => state.refetch()} />;
  if (!data) return null;

  if (!data.supported) {
    return (
      <section className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
        <h3 className="text-sm font-semibold">Đồng bộ tự động</h3>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          Đồng bộ API tự động chưa được hỗ trợ cho nền tảng này. Số liệu của
          kênh vẫn ghi nhận thủ công như bình thường.
        </p>
      </section>
    );
  }

  const connection = data.connection;
  const pending = connection?.state === "PENDING_SELECTION";
  // The provider code, not a platform name typed on the channel: the server
  // derives it from the platform's own code and sends it, and this is the one
  // comparison on the page that decides which extra panel to draw.
  const isTikTok = data.provider === "TIKTOK";

  return (
    <section className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">
          {data.provider_label ?? data.provider}
        </h3>
        {connection ? (
          <Pill tone={connection.state === "CONNECTED" ? "good" : "warn"}>
            {connection.state_label}
          </Pill>
        ) : null}
      </div>

      {!data.configured ? (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
          Cấu hình {data.provider_label ?? data.provider} chưa sẵn sàng trên hệ
          thống này. Người quản trị máy chủ cần thiết lập OAuth trước khi kết
          nối được.
        </p>
      ) : null}

      {connection === null ? (
        <>
          <p className="text-sm text-[var(--text-muted)]">
            Chưa kết nối. Kết nối để TasksBot tự lấy số liệu kênh mỗi ngày.
          </p>
          {data.can_manage_connection ? (
            <button
              type="button"
              disabled={!data.configured || connect.isPending}
              onClick={() => connect.mutate()}
              className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
            >
              {connect.isPending ? "Đang mở…" : words.connect}
            </button>
          ) : null}
        </>
      ) : pending ? (
        <AccountChooser
          channelId={channelId}
          title={words.chooser}
          canManage={data.can_manage_connection}
          onChosen={refresh}
        />
      ) : (
        <>
          <dl className="grid gap-2 text-xs sm:grid-cols-2">
            <div>
              <dt className="text-[var(--text-muted)]">{words.account}</dt>
              <dd>{connection.provider_account_name ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-[var(--text-muted)]">
                {ACCOUNT_ID_LABELS[data.provider ?? ""] ?? "Account ID"}
              </dt>
              <dd className="font-mono">{connection.provider_account_id}</dd>
            </div>
            {connection.provider_account_handle ? (
              <div>
                <dt className="text-[var(--text-muted)]">Tên hiển thị</dt>
                <dd>{connection.provider_account_handle}</dd>
              </div>
            ) : null}
            <div>
              <dt className="text-[var(--text-muted)]">Đồng bộ gần nhất</dt>
              {/* "Đồng bộ lúc" is when TasksBot fetched. The reporting window the
                  numbers cover is a different fact and lives on the metrics
                  panel, so the two are never conflated into "cập nhật lúc". */}
              <dd>
                {connection.last_sync_succeeded_at
                  ? formatWhen(connection.last_sync_succeeded_at)
                  : "Chưa đồng bộ lần nào"}
                {connection.days_since_success !== null &&
                connection.days_since_success > 0
                  ? ` · Đã ${connection.days_since_success} ngày chưa đồng bộ thành công`
                  : ""}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--text-muted)]">Tự động</dt>
              <dd>
                {connection.auto_sync_enabled && data.auto_sync_label
                  ? data.auto_sync_label
                  : "Tắt"}
              </dd>
            </div>
          </dl>

          {connection.state === "ACTION_REQUIRED" ? (
            <p className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
              <strong>
                {data.provider_label ?? data.provider} cần kết nối lại để tiếp
                tục tự động đồng bộ.
              </strong>{" "}
              Số liệu đã lấy trước đó vẫn còn nguyên, nhưng sẽ không cập nhật
              thêm cho tới khi kết nối lại.
            </p>
          ) : null}

          {connection.sync_status === "FAILED" &&
          connection.last_sync_error_message ? (
            <p className="rounded border border-[var(--border)] p-2 text-xs">
              {connection.sync_status_label}:{" "}
              {connection.last_sync_error_message}
            </p>
          ) : null}

          {data.can_manage_connection ? (
            <div className="flex flex-wrap gap-2">
              {/* Step 1F.2.9. A TikTok channel has no "Đồng bộ ngay" of its own
                  because its account panel below already owns "Đồng bộ lại",
                  which asks for the *same* snapshot through the *same* claim
                  and additionally re-reads the account in the same press. Two
                  buttons that both mean "fetch now", one of which also updates
                  the screen, would be a choice with a wrong answer. */}
              {isTikTok ? null : (
                <button
                  type="button"
                  disabled={sync.isPending || connection.state !== "CONNECTED"}
                  onClick={() => sync.mutate()}
                  className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
                >
                  {sync.isPending ? "Đang gửi…" : "Đồng bộ ngay"}
                </button>
              )}
              {/* Step 1F.2.8. The *first* connection opens the provider's own
                  consent screen and asks nothing here - that screen is the
                  authorization step, and a TasksBot dialog in front of it would
                  be a click that authorises nothing. Reconnecting is different:
                  there is already a working connection, and this replaces it. */}
              <ConfirmButton
                spec={replaceConnectionConfirmation(data.provider_label ?? data.provider ?? "tài khoản")}
                tone="secondary"
                disabled={!data.configured}
                pending={connect.isPending}
                error={connect.error}
                onConfirm={() => connect.mutate()}
              >
                Kết nối lại
              </ConfirmButton>
              <ConfirmButton
                spec={disconnectChannelConfirmation(data.provider_label ?? data.provider ?? "tài khoản")}
                tone="secondary"
                pending={disconnect.isPending}
                error={disconnect.error}
                onConfirm={() => disconnect.mutate()}
              >
                Ngắt kết nối
              </ConfirmButton>
            </div>
          ) : null}
          <p className="text-xs text-[var(--text-muted)]">
            Ngắt kết nối không xoá số liệu đã ghi nhận. Toàn bộ lịch sử chỉ số
            vẫn giữ nguyên.
          </p>

          {/* Step 1F.2.9. The connected state of the TikTok panel, filled in.
              Rendered here rather than as its own section on the page, because
              a second place that says whether TikTok is connected is a second
              place that can be wrong. Only for a live connection: an
              ACTION_REQUIRED credential cannot read anything, and asking TikTok
              with it would produce an error box under a panel that already
              says "kết nối lại". */}
          {isTikTok && connection.state === "CONNECTED" ? (
            <TikTokAccountPanel
              channelId={channelId}
              canManage={data.can_manage_connection}
            />
          ) : null}
        </>
      )}

      {connect.isError ? <ErrorBox error={connect.error} /> : null}
      {sync.isError ? <ErrorBox error={sync.error} /> : null}
      {disconnect.isError ? <ErrorBox error={disconnect.error} /> : null}
    </section>
  );
}

/**
 * A connected TikTok account, read live and shown as evidence.
 *
 * Step 1F.2.9, and it lives **inside** `ChannelConnectionPanel` rather than
 * beside it. That is the whole design constraint: TikTok already had a
 * connection panel on this page, and a second "TikTok dashboard" somewhere else
 * would mean two screens telling somebody whether their account is connected.
 * This is the connected state of the panel that already existed, filled in.
 *
 * ## Why it exists at all
 *
 * TikTok app review asks an applicant to demonstrate that each scope they
 * request is used for what they said it was used for. Before this step the only
 * way to see any of it was `meobot-tiktok-probe` in a terminal, which is not
 * something a reviewer has or should need. Every block below maps to one scope:
 *
 * - **`user.info.basic`** - the avatar, the display name, the Open ID;
 * - **`user.info.profile`** - the handle, the profile link, the bio, the
 *   verification pill;
 * - **`user.info.stats`** - the four counters;
 * - **`video.list`** - the recent videos and their per-video counters.
 *
 * ## The rule that runs through every field
 *
 * **An unavailable number is never a zero.** TikTok gates `user.info.stats` and
 * `video.list` behind app review, so a healthy connection can be missing a whole
 * block - and the reviewer looking at this screen is quite likely to be looking
 * at exactly that case. `formatCount(null)` renders "—", every availability word
 * comes from the server with its own Vietnamese sentence, and nothing here
 * substitutes a plausible-looking figure for a refusal.
 *
 * Nothing on this screen is derived, either. `likes_count` is TikTok's
 * **lifetime** total across every video the account has posted; it is labelled
 * "Tổng lượt thích" and is never divided into a week or a month. The Display API
 * has no reporting window at all, so any windowed figure here would be invented.
 */
function TikTokAccountPanel({
  channelId,
  canManage,
}: {
  channelId: string;
  canManage: boolean;
}) {
  const queryClient = useQueryClient();
  /**
   * The pages "Xem thêm" has added, kept beside the query rather than in it.
   *
   * The first page is the query's own data; this accumulates the rest. Keeping
   * them apart is what makes "Đồng bộ lại" simple - it replaces the query's
   * data and clears this, so a refresh always starts from a fresh first page
   * rather than stitching new videos onto stale ones.
   */
  const [extra, setExtra] = useState<{
    videos: TikTokVideo[];
    cursor: number | null;
    hasMore: boolean;
    pages: number;
  } | null>(null);

  const overview = useQuery({
    queryKey: ["tiktok-overview", channelId],
    queryFn: () => api.tiktokOverview(channelId),
    // A live TikTok call, unlike every other query on this page - which is why
    // this one is the only query here that is deliberately not eager.
    //
    // Refetching because a window regained focus would spend somebody's rate
    // limit on a tab they alt-tabbed back to, and a minute of staleness costs
    // nothing: these are lifetime counters that move slowly, and the person who
    // wants this second's figure has "Đồng bộ lại" right there.
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });

  const loadMore = useMutation({
    mutationFn: (cursor: number) => api.tiktokOverview(channelId, { cursor }),
    onSuccess: (next) =>
      setExtra((prev) => ({
        videos: [...(prev?.videos ?? []), ...next.videos],
        cursor: next.videos_cursor,
        hasMore: next.videos_has_more,
        pages: (prev?.pages ?? 1) + 1,
      })),
  });

  const refresh = useMutation({
    mutationFn: () => api.refreshTiktokAccount(channelId),
    onSuccess: (next) => {
      setExtra(null);
      // The response *is* the new panel, so it is written straight into the
      // cache rather than triggering a second identical TikTok call.
      queryClient.setQueryData(["tiktok-overview", channelId], next);
      // The snapshot half landed on the worker. These are the panels that will
      // show it once it does.
      void queryClient.invalidateQueries({
        queryKey: ["channel-connection", channelId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["channel-metrics", channelId],
      });
      void queryClient.invalidateQueries({ queryKey: ["channels"] });
    },
  });

  if (overview.isPending) return <Loading label="Đang tải dữ liệu TikTok…" />;
  if (overview.isError) {
    return (
      <ErrorBox error={overview.error} onRetry={() => overview.refetch()} />
    );
  }

  const data: TikTokOverview = overview.data;
  const account = data.account;
  const videos = [...data.videos, ...(extra?.videos ?? [])];
  const cursor = extra ? extra.cursor : data.videos_cursor;
  const hasMore = extra ? extra.hasMore : data.videos_has_more;
  const pagesLoaded = extra?.pages ?? 1;
  // The ceiling is the server's, sent with the payload. A page that decided its
  // own bound would be a second answer to "how much history may this load".
  const canLoadMore =
    hasMore && cursor !== null && pagesLoaded < data.max_video_pages;

  return (
    <div className="space-y-4 border-t border-[var(--border)] pt-4">
      {/* --- Identity and profile: user.info.basic + user.info.profile --- */}
      <div className="flex flex-wrap items-start gap-3">
        {account.avatar_url ? (
          // A plain <img>, not next/image: the source is a TikTok CDN URL that
          // expires within hours, so there is nothing worth optimising and a
          // remote-pattern allowlist would be one more thing to keep in step
          // with whichever CDN host TikTok answers from today.
          <img
            src={account.avatar_url}
            alt=""
            referrerPolicy="no-referrer"
            className="h-16 w-16 shrink-0 rounded-full border border-[var(--border)] object-cover"
          />
        ) : (
          <div
            aria-hidden
            className="h-16 w-16 shrink-0 rounded-full border border-[var(--border)]"
          />
        )}
        <div className="min-w-0 flex-1 space-y-1">
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            {account.display_name ?? "—"}
            {/* Three states, not two. `null` means user.info.profile was not
                granted, and printing "Chưa xác minh" over it would be TasksBot
                asserting something TikTok never said. */}
            {account.is_verified === true ? (
              <Pill tone="good">Đã xác minh</Pill>
            ) : account.is_verified === false ? (
              <Pill>Chưa xác minh</Pill>
            ) : null}
          </p>
          <p className="text-xs text-[var(--text-muted)]">
            {account.handle ?? "—"}
          </p>
          {account.bio ? (
            <p className="text-xs whitespace-pre-line">{account.bio}</p>
          ) : null}
          <p className="text-[10px] text-[var(--text-muted)]">
            Open ID: <span className="font-mono">{account.open_id}</span>
          </p>
          {account.profile_url ? (
            <a
              href={account.profile_url}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-block text-xs underline"
            >
              Mở hồ sơ trên TikTok
            </a>
          ) : null}
        </div>
      </div>

      {/* The server's sentence, not a guess assembled from missing fields. */}
      {data.profile_availability !== "available" ? (
        <p className="rounded border border-[var(--border)] p-2 text-xs text-[var(--text-muted)]">
          Hồ sơ TikTok (tên người dùng, tiểu sử, trạng thái xác minh):{" "}
          {data.profile_availability_label}
        </p>
      ) : null}

      {/* --- Stats: user.info.stats. Lifetime totals, no window. --------- */}
      <div>
        <h4 className="mb-2 text-sm font-semibold">Thống kê tài khoản</h4>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard
            label="Người theo dõi"
            value={formatCount(data.stats.follower_count)}
          />
          <MetricCard
            label="Đang theo dõi"
            value={formatCount(data.stats.following_count)}
          />
          <MetricCard
            label="Tổng lượt thích"
            value={formatCount(data.stats.likes_count)}
            /* Said on the card, because this is the number most likely to be
               mistaken for a windowed one. TikTok's `likes_count` is every like
               the account has ever received; filing it as "likes 30 ngày" would
               make it thousands of times too large. */
            hint="Tổng tích luỹ từ trước tới nay"
            title="TikTok chỉ trả về tổng lượt thích tích luỹ, không có số liệu theo tuần hay theo tháng."
          />
          <MetricCard
            label="Video"
            value={formatCount(data.stats.video_count)}
          />
        </div>
        {data.stats.availability !== "available" ? (
          <p className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
            Chưa hiển thị được thống kê tài khoản: {data.stats.availability_label}{" "}
            (quyền <code>user.info.stats</code>).
          </p>
        ) : null}
      </div>

      {/* --- Permissions: the scopes, granted or not --------------------- */}
      <TikTokPermissions scopes={data.scopes} />

      {/* --- Recent videos: video.list ----------------------------------- */}
      <div>
        <h4 className="mb-2 text-sm font-semibold">Video gần đây</h4>
        {videos.length === 0 ? (
          <Empty
            message={
              data.videos_availability === "empty"
                ? "Tài khoản này chưa có video công khai nào."
                : `Chưa hiển thị được danh sách video: ${data.videos_availability_label} (quyền video.list).`
            }
          />
        ) : (
          <>
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {videos.map((video) => (
                <TikTokVideoCard key={video.video_id} video={video} />
              ))}
            </ul>
            {data.video_counters_availability !== "available" ? (
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                Chỉ số từng video: {data.video_counters_availability_label}. Các
                video vẫn hiển thị, chỉ thiếu phần số liệu.
              </p>
            ) : null}
            {canLoadMore ? (
              <button
                type="button"
                disabled={loadMore.isPending}
                onClick={() => cursor !== null && loadMore.mutate(cursor)}
                className="mt-3 min-h-11 rounded border border-[var(--border)] px-3 text-sm"
              >
                {loadMore.isPending ? "Đang tải…" : "Xem thêm"}
              </button>
            ) : null}
            {hasMore && !canLoadMore ? (
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                Đã tải tối đa {data.max_video_pages} trang video gần nhất.
                TasksBot không tải toàn bộ lịch sử kênh.
              </p>
            ) : null}
          </>
        )}
        {loadMore.isError ? <ErrorBox error={loadMore.error} /> : null}
      </div>

      {/* --- Refresh ----------------------------------------------------- */}
      <div className="space-y-2">
        {canManage ? (
          /* No ConfirmDialog. Re-reading numbers changes no business state and
             destroys nothing, so a confirmation would be a click that means
             nothing - and would teach people to click through the dialogs that
             do mean something. Reconnecting and disconnecting still confirm. */
          <button
            type="button"
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
            className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
          >
            {refresh.isPending ? "Đang lấy dữ liệu…" : "Đồng bộ lại"}
          </button>
        ) : null}
        {/* Two different times, said as two different things. "Đọc từ TikTok"
            is when this panel asked; "Lưu chỉ số" is when a snapshot last
            landed in the history. Calling both "cập nhật lúc" would make a
            three-week-old reading look like this morning's. */}
        <p className="text-xs text-[var(--text-muted)]">
          Đọc từ TikTok: {formatWhen(data.fetched_at)}
          {" · "}
          Lưu chỉ số gần nhất:{" "}
          {data.last_sync_succeeded_at
            ? formatWhen(data.last_sync_succeeded_at)
            : "Chưa có"}
        </p>
        {refresh.isSuccess && !refresh.isPending ? (
          <p role="status" className="text-xs text-emerald-400">
            Đã cập nhật dữ liệu TikTok.
            {refresh.data.sync_requested
              ? " Chỉ số đang được ghi vào lịch sử."
              : " Một lượt đồng bộ chỉ số đang chạy, hệ thống sẽ ghi lại khi xong."}
          </p>
        ) : null}
        {/* The server's sentence for the failure class. TikTok's own error text
            never reaches this screen - the API refuses to carry it. */}
        {refresh.isError ? <ErrorBox error={refresh.error} /> : null}
      </div>
    </div>
  );
}

/**
 * Which permissions this connection actually holds.
 *
 * Collapsed by default, because on an ordinary day nobody needs it and it is
 * four more rows between somebody and their follower count. Expandable, because
 * on the day it matters - a blank stats block, or a TikTok reviewer checking
 * that the scopes match the screens - it is the whole answer.
 *
 * Every requested scope is listed, **including the ones TikTok refused**. A
 * list that quietly omitted a refusal would make an app awaiting review look
 * identical to one fully approved, which is the opposite of what this block is
 * for. The raw scope string sits under the Vietnamese name so the two can be
 * compared against TikTok's own developer console without a translation step.
 *
 * A scope name is not a secret - it is the line somebody read on the consent
 * screen. Tokens are, and none of them is in this payload at all.
 */
function TikTokPermissions({ scopes }: { scopes: TikTokScope[] }) {
  if (scopes.length === 0) return null;
  return (
    <details className="rounded border border-[var(--border)] p-3">
      <summary className="cursor-pointer text-sm font-semibold">
        Quyền truy cập
      </summary>
      <ul className="mt-2 space-y-2">
        {scopes.map((scope) => (
          <li key={scope.scope} className="text-xs">
            <p className={scope.granted ? "" : "text-[var(--text-muted)]"}>
              {/* aria-hidden on the glyph and a word beside it: a tick and a
                  circle are indistinguishable to a screen reader, and this is
                  the block whose whole job is to say which is which. */}
              <span aria-hidden>{scope.granted ? "✓" : "○"}</span>{" "}
              {scope.label}{" "}
              <span className="text-[var(--text-muted)]">
                · {scope.granted ? "đã cấp" : "chưa được cấp"}
              </span>
            </p>
            <p className="text-[var(--text-muted)]">{scope.description}</p>
            <code className="text-[10px] text-[var(--text-muted)]">
              {scope.scope}
            </code>
          </li>
        ))}
      </ul>
    </details>
  );
}

/**
 * One recent video, with whatever TikTok actually served about it.
 *
 * The counters are `formatCount`, so an absent one renders "—" rather than "0".
 * That distinction is the point of the whole card: a video with no comments and
 * a video whose `comment_count` this app is not served must not read the same.
 *
 * The cover is a TikTok CDN URL that expires within hours. It is rendered
 * straight from the response and stored nowhere - a copy saved anywhere would
 * be a broken image by tomorrow.
 */
function TikTokVideoCard({ video }: { video: TikTokVideo }) {
  const caption = video.title ?? video.description ?? "(không có tiêu đề)";
  return (
    <li className="flex flex-col gap-2 rounded border border-[var(--border)] p-2">
      {video.cover_image_url ? (
        <img
          src={video.cover_image_url}
          alt=""
          referrerPolicy="no-referrer"
          className="aspect-[9/16] w-full rounded object-cover"
        />
      ) : null}
      <p className="line-clamp-2 text-xs font-medium">{caption}</p>
      {video.created_at ? (
        <p className="text-[10px] text-[var(--text-muted)]">
          Đăng {formatWhen(video.created_at)}
        </p>
      ) : null}
      <p className="text-xs">{formatCount(video.view_count)} lượt xem</p>
      <p className="text-[10px] text-[var(--text-muted)]">
        {formatCount(video.like_count)} thích ·{" "}
        {formatCount(video.comment_count)} bình luận ·{" "}
        {formatCount(video.share_count)} chia sẻ
      </p>
      {video.share_url ? (
        <a
          href={video.share_url}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-auto text-xs underline"
        >
          Xem trên TikTok
        </a>
      ) : null}
    </li>
  );
}

/**
 * Which Page or Instagram account this channel means.
 *
 * Step 1F.2.4c. Meta consent reaches every Page a person manages, and one
 * marketing manager routinely has a dozen. Binding the first result would be a
 * coin flip with somebody's brand, so the flow stops here.
 *
 * Each row shows the name, the provider account id and - for Instagram - the
 * Facebook Page the account was reached through, because two accounts with
 * similar names are exactly the case this screen exists for. No row carries a
 * token: the server keeps them, and this list is ids and names.
 */
function AccountChooser({
  channelId,
  title,
  canManage,
  onChosen,
}: {
  channelId: string;
  title: string;
  canManage: boolean;
  onChosen: () => void;
}) {
  const accounts = useQuery({
    queryKey: ["channel-accounts", channelId],
    queryFn: () => api.connectionAccounts(channelId),
  });
  const choose = useMutation({
    mutationFn: (accountId: string) =>
      api.selectConnectionAccount(channelId, accountId),
    onSuccess: onChosen,
  });

  if (accounts.isPending) return <Loading />;
  if (accounts.isError) {
    return (
      <ErrorBox error={accounts.error} onRetry={() => accounts.refetch()} />
    );
  }

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold">{title}</h4>
      <p className="text-xs text-[var(--text-muted)]">
        Đã cấp quyền xong. Hãy chọn đúng tài khoản mà kênh này đại diện — TasksBot
        chỉ lấy số liệu của tài khoản được chọn.
      </p>
      {accounts.data.accounts.length === 0 ? (
        <Empty message="Không tìm thấy tài khoản phù hợp để kết nối." />
      ) : (
        <ul className="space-y-2">
          {accounts.data.accounts.map((account) => (
            <li
              key={account.account_id}
              className="flex flex-wrap items-center justify-between gap-2 rounded border border-[var(--border)] p-2"
            >
              <span className="text-sm">
                {account.name}
                {account.handle ? (
                  <span className="text-[var(--text-muted)]">
                    {" "}
                    · {account.handle}
                  </span>
                ) : null}
                <br />
                <code className="text-[10px] text-[var(--text-muted)]">
                  {account.account_id}
                </code>
                {account.via ? (
                  <span className="text-[10px] text-[var(--text-muted)]">
                    {" "}
                    · qua Trang {account.via}
                  </span>
                ) : null}
              </span>
              {canManage ? (
                <button
                  type="button"
                  disabled={choose.isPending}
                  onClick={() => choose.mutate(account.account_id)}
                  className="min-h-11 rounded bg-[var(--text)] px-3 text-sm text-[var(--surface)] disabled:opacity-50"
                >
                  {choose.isPending ? "Đang lưu…" : "Chọn"}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {choose.isError ? <ErrorBox error={choose.error} /> : null}
    </div>
  );
}
