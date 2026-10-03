"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CapabilityGrant, Channel, GrantScope } from "@/lib/api";
import { api } from "@/lib/api";
import {
  CONTENT_TYPE_ORDER,
  GRANT_EXPIRY_PRESETS,
  REVIEW_CAPABILITIES,
  UNASSIGNED_CHANNEL_SCOPE_LABEL,
  UNCLASSIFIED_SCOPE_LABEL,
  capabilityLabel,
  contentTypeLabel,
  formatDay,
  grantExpiryDay,
  roleLabel,
  type GrantExpiryPreset,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { ScopeMultiSelect, Select, type ScopeOption } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import { revokeCapabilityConfirmation } from "@/lib/confirmations";

/**
 * *Quyền duyệt cấp thêm.* Who may approve what, over which content and which
 * channels. The grant surface of Thành viên & Phân quyền, unchanged in what it
 * sends: the routes, the scope shape and the semantics are Step 1F.2.7's.
 *
 * Only the **three grant-backed review capabilities** appear here, because those
 * are the only ones that are grants. The other ten come from a person's role and
 * cannot be handed out - listing them with a "Cấp quyền" button beside them would
 * describe a model that does not exist.
 *
 * What the page states, because people get it wrong in both directions:
 *
 * * a grant is **additive**. It authorises on its own, whatever the holder's
 *   role, and it promotes nobody: they gain this gate over this scope and not one
 *   other capability;
 * * a grant is **exact**. Another channel, another classification or another gate
 *   is refused, and content nobody classified or nobody has given a channel is
 *   refused too unless the grant says otherwise.
 *
 * No authorization happens in this file. It renders what the server stored and
 * sends what somebody picked; every decision is `PrCapabilityService`.
 */

/** A blank scope: `SELECTED` on both axes with nothing selected. Fails closed. */
const EMPTY_SCOPE: GrantScope = {
  content_type_scope: "SELECTED",
  content_types: [],
  include_unclassified_content: false,
  channel_scope: "SELECTED",
  channel_ids: [],
  include_unassigned_channel: false,
};

/** The classification chooser's options: the six, then the unclassified state. */
const CONTENT_TYPE_OPTIONS: ScopeOption[] = [
  ...CONTENT_TYPE_ORDER.map((code) => ({
    value: code,
    label: contentTypeLabel(code),
  })),
  { value: "__UNCLASSIFIED__", label: UNCLASSIFIED_SCOPE_LABEL },
];

/** The sentinel above is a *form* value and never reaches the wire. */
const UNCLASSIFIED_OPTION = "__UNCLASSIFIED__";
const UNASSIGNED_OPTION = "__UNASSIGNED__";

/** One grant's classification scope, as a sentence. */
function contentScopeText(scope: GrantScope): string {
  if (scope.content_type_scope === "ALL") return "Tất cả phân loại";
  const names = scope.content_types.map((code) => contentTypeLabel(code));
  if (scope.include_unclassified_content) names.push(UNCLASSIFIED_SCOPE_LABEL);
  return names.length > 0 ? names.join(", ") : "—";
}

/** One grant's channel scope, as a sentence. Ids the server did not send a
 * channel for render as the id: a name this page cannot verify is worse. */
function channelScopeText(scope: GrantScope, names: Map<string, string>): string {
  if (scope.channel_scope === "ALL") return "Tất cả kênh";
  const labels = scope.channel_ids.map((id) => names.get(id) ?? id);
  if (scope.include_unassigned_channel) labels.push(UNASSIGNED_CHANNEL_SCOPE_LABEL);
  return labels.length > 0 ? labels.join(", ") : "—";
}

export function GrantsTab() {
  const queryClient = useQueryClient();
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const channels = useQuery({
    queryKey: ["channels"],
    queryFn: () => api.listChannels(),
  });
  const grants = useQuery({
    queryKey: ["capabilities"],
    queryFn: () => api.listCapabilities(),
  });

  const names = new Map((people.data ?? []).map((person) => [person.user_id, person.full_name]));
  const channelNames = new Map(
    (channels.data ?? []).map((channel: Channel) => [channel.id, channel.name]),
  );
  const channelOptions: ScopeOption[] = useMemo(
    () => [
      ...(channels.data ?? []).map((channel: Channel) => ({
        value: channel.id,
        label: channel.name,
        hint: channel.code,
      })),
      { value: UNASSIGNED_OPTION, label: UNASSIGNED_CHANNEL_SCOPE_LABEL },
    ],
    [channels.data],
  );

  const [userId, setUserId] = useState("");
  const [capability, setCapability] = useState<string>(REVIEW_CAPABILITIES[0]);
  const [contentTypes, setContentTypes] = useState<string[]>([]);
  const [allContentTypes, setAllContentTypes] = useState(false);
  const [channelIds, setChannelIds] = useState<string[]>([]);
  const [allChannels, setAllChannels] = useState(false);
  const [expiry, setExpiry] = useState<GrantExpiryPreset>("NONE");
  const [customDay, setCustomDay] = useState("");
  const [note, setNote] = useState("");

  // Assembled here rather than in the mutation so the "Cấp quyền" button can be
  // disabled on exactly the condition the server refuses: a scope that covers
  // nothing. Better a greyed button than a 422 after filling in a form.
  const scope: GrantScope = {
    content_type_scope: allContentTypes ? "ALL" : "SELECTED",
    content_types: allContentTypes
      ? []
      : contentTypes.filter((code) => code !== UNCLASSIFIED_OPTION),
    include_unclassified_content: allContentTypes
      ? false
      : contentTypes.includes(UNCLASSIFIED_OPTION),
    channel_scope: allChannels ? "ALL" : "SELECTED",
    channel_ids: allChannels ? [] : channelIds.filter((id) => id !== UNASSIGNED_OPTION),
    include_unassigned_channel: allChannels ? false : channelIds.includes(UNASSIGNED_OPTION),
  };
  const scopeIsEmpty =
    (scope.content_type_scope === "SELECTED" &&
      scope.content_types.length === 0 &&
      !scope.include_unclassified_content) ||
    (scope.channel_scope === "SELECTED" &&
      scope.channel_ids.length === 0 &&
      !scope.include_unassigned_channel);

  const effectiveTo = expiry === "CUSTOM" ? customDay || null : grantExpiryDay(expiry);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["capabilities"] });
    void queryClient.invalidateQueries({ queryKey: ["session"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };

  const grant = useMutation({
    mutationFn: () =>
      api.grantCapability({
        user_id: userId,
        capability,
        scope,
        effective_to: effectiveTo,
        note: note || undefined,
      }),
    onSuccess: () => {
      setNote("");
      setContentTypes([]);
      setChannelIds([]);
      setAllContentTypes(false);
      setAllChannels(false);
      setExpiry("NONE");
      setCustomDay("");
      invalidate();
    },
  });
  const revoke = useMutation({
    mutationFn: (payload: { grant_id: string }) => api.revokeCapability(payload),
    onSuccess: invalidate,
  });

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
        <h2 className="mb-2 text-sm font-semibold">Quyền duyệt đang có hiệu lực</h2>
        {grants.isPending ? <Loading /> : null}
        {grants.isError ? <ErrorBox error={grants.error} onRetry={() => grants.refetch()} /> : null}
        {grants.data?.length === 0 ? (
          <Empty message="Hiện chưa có ai được cấp quyền duyệt ở bất kỳ bước nào." />
        ) : null}
        <ul className="space-y-3 text-sm">
          {grants.data?.map((row: CapabilityGrant) => (
            <li
              key={row.id}
              data-testid="grant-row"
              className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-[var(--border)] p-3"
            >
              <div className="space-y-1">
                <p className="font-medium">{names.get(row.user_id) ?? row.user_id}</p>
                <p>
                  <Pill tone="good">{capabilityLabel(row.capability)}</Pill>
                </p>
                <p className="text-[var(--text-muted)]">
                  <span className="text-[var(--text)]">Phân loại:</span>{" "}
                  {contentScopeText(row.scope)}
                </p>
                <p className="text-[var(--text-muted)]">
                  <span className="text-[var(--text)]">Kênh:</span>{" "}
                  {channelScopeText(row.scope, channelNames)}
                </p>
                <p className="text-[var(--text-muted)]">
                  <span className="text-[var(--text)]">Hết hạn:</span>{" "}
                  {row.effective_to ? formatDay(row.effective_to) : "Không hết hạn"}
                </p>
                {row.requires_role_baseline ? (
                  <p className="text-xs text-[var(--text-muted)]">
                    Quyền cũ: chỉ có hiệu lực nếu vai trò của người này đã có quyền tương ứng.
                  </p>
                ) : null}
              </div>
              {/* Step 1F.2.8. Revoking is instant and takes authority away, so
                  it asks - and the question names the gate and the person,
                  because a row of three grants for three people is exactly where
                  a mis-click lands on the wrong one. */}
              <ConfirmButton
                spec={revokeCapabilityConfirmation(row.capability, names.get(row.user_id) ?? null)}
                tone="secondary"
                pending={revoke.isPending}
                error={revoke.error}
                onConfirm={() => revoke.mutate({ grant_id: row.id })}
              >
                Thu hồi
              </ConfirmButton>
            </li>
          ))}
        </ul>
        {revoke.isError ? <ErrorBox error={revoke.error} /> : null}
      </section>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          grant.mutate();
        }}
        className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
      >
        <h2 className="text-sm font-semibold">Cấp quyền duyệt</h2>
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
                  {person.full_name} ({roleLabel(person.role)})
                </option>
              ))}
            </Select>
          </label>
          <label className="text-sm">
            Quyền duyệt
            <Select
              value={capability}
              onChange={(event) => setCapability(event.target.value)}
              className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            >
              {REVIEW_CAPABILITIES.map((code) => (
                <option key={code} value={code}>
                  {capabilityLabel(code)}
                </option>
              ))}
            </Select>
          </label>

          <ScopeMultiSelect
            legend="Phân loại nội dung"
            options={CONTENT_TYPE_OPTIONS}
            selected={contentTypes}
            onSelectedChange={setContentTypes}
            all={allContentTypes}
            onAllChange={setAllContentTypes}
            allLabel="Tất cả phân loại"
          />
          <ScopeMultiSelect
            legend="Kênh"
            options={channelOptions}
            selected={channelIds}
            onSelectedChange={setChannelIds}
            all={allChannels}
            onAllChange={setAllChannels}
            allLabel="Tất cả kênh"
            emptyHint="Chưa có kênh nào."
          />

          <label className="text-sm">
            Thời hạn
            <Select
              value={expiry}
              onChange={(event) => setExpiry(event.target.value as GrantExpiryPreset)}
              className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            >
              {GRANT_EXPIRY_PRESETS.map((preset) => (
                <option key={preset.value} value={preset.value}>
                  {preset.label}
                </option>
              ))}
            </Select>
          </label>
          {expiry === "CUSTOM" ? (
            <label className="text-sm">
              Ngày hết hạn
              <input
                type="date"
                required
                value={customDay}
                onChange={(event) => setCustomDay(event.target.value)}
                className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
              />
            </label>
          ) : null}

          <label className="text-sm sm:col-span-2">
            Ghi chú (vào lịch sử, không hiện lại ở trang này)
            <input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
            />
          </label>
        </div>
        <div className="rounded bg-[var(--surface-muted)] p-3 text-xs text-[var(--text-muted)]">
          <p>
            <strong>
              Quyền duyệt cấp thêm chỉ áp dụng trong đúng phạm vi đã chọn và không thay đổi vai trò
              nền của thành viên.
            </strong>
          </p>
          <p className="mt-1">
            <strong>Quyền cấp thêm, không đổi vai trò.</strong> Người được cấp duyệt được đúng phần
            đã chọn, kể cả khi vai trò của họ chưa có quyền duyệt — và họ không nhận thêm bất kỳ
            quyền nào khác.
          </p>
          <p className="mt-1">
            <strong>Đúng phạm vi đã chọn.</strong> Kênh khác, phân loại khác hoặc bước duyệt khác
            đều bị từ chối. Nội dung chưa phân loại hoặc chưa gán kênh cũng bị từ chối, trừ khi bạn
            chọn đúng mục đó.
          </p>
          <p className="mt-1">
            Chỉ ba quyền duyệt là quyền được cấp. Các quyền còn lại đi theo vai trò nền — xem thẻ
            “Vai trò & quyền” — và không cấp được ở đây.
          </p>
        </div>
        {scopeIsEmpty ? (
          <p className="text-xs text-[var(--text-muted)]">
            Chọn ít nhất một phân loại và một kênh.
          </p>
        ) : null}
        {grant.isError ? <ErrorBox error={grant.error} /> : null}
        {/* Step 1F.2.8. This form *is* the confirmation - it exists to collect
            the person, the gate, the scope and the expiry, and stacking a second
            dialog on top of a form somebody just filled in adds a click and no
            information. What it does instead is say what it will do: the button
            names the person and the gate, so the last thing read before pressing
            is the action rather than the word "Lưu". */}
        <button
          type="submit"
          disabled={grant.isPending || scopeIsEmpty}
          className="rounded bg-[var(--text)] px-3 py-1.5 text-sm text-[var(--surface)] disabled:opacity-50"
        >
          {grant.isPending
            ? "Đang cấp…"
            : userId
              ? `Cấp quyền ${capabilityLabel(capability)} cho ${names.get(userId) ?? "người này"}`
              : "Cấp quyền"}
        </button>
      </form>
    </div>
  );
}
