"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import {
  CONTENT_TYPE_ORDER,
  PRIORITY_ORDER,
  SELECTABLE_DISTRIBUTION_MODES,
  contentTypeLabel,
  distributionModeLabel,
  priorityLabel,
} from "@/lib/labels";
import { Empty, ErrorBox, Loading } from "@/components/states";
import { PrimaryButton, Select } from "@/components/pr";
import {
  ResourceDraftSection,
  type ResourceDraft,
  resourceDraftPayload,
  resourceDraftProblems,
  serverResourceProblem,
} from "@/components/resource-drafts";

/**
 * The PR create form, shared by the PR content board and the "Tạo order"
 * screen: one door for both units, one form behind it for PR.
 *
 * Deliberately has **no code field**. The server allocates `CNT-YYYY-nnnnnn`, and
 * a form that asked for one would be asking a person to guess at a counter.
 */
export function CreateContentForm({ onCreated }: { onCreated: () => void }) {
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const brands = useQuery({ queryKey: ["brands"], queryFn: api.brands });
  // Active only, asked of the server. Filtering statuses in the browser would
  // be the UI deciding which channels may be published on.
  const channels = useQuery({
    queryKey: ["channels", "ACTIVE"],
    queryFn: () => api.listChannels({ status: "ACTIVE" }),
  });
  const [title, setTitle] = useState("");
  const [brandId, setBrandId] = useState("");
  const [ownerId, setOwnerId] = useState("");
  // Step 1F.2.3d. Pre-set to the default rather than left blank with a "— chọn —"
  // option: most content is ordinary, and making somebody answer a question
  // whose answer is almost always the same is how a form gets ignored.
  const [priority, setPriority] = useState("NORMAL");
  // Step 1F.2.3e. Empty rather than pre-set, unlike priority: there is no
  // sensible default format, and a pre-filled one would be a classification
  // nobody chose. The submit button stays disabled until it is answered.
  const [contentType, setContentType] = useState("");
  const [script, setScript] = useState("");
  const [targets, setTargets] = useState<
    Array<{ channel_id: string; distribution_mode: string }>
  >([]);
  // Step 1F.2.3e.1. Form state and only form state until the create request
  // carries them - see `components/resource-drafts`. Nothing here calls the
  // resource API, because there is no content item to attach anything to yet.
  const [resources, setResources] = useState<ResourceDraft[]>([]);
  // Set on a submit that never left the browser. Cleared on the next attempt,
  // so a corrected draft stops being marked as soon as it is corrected.
  const [draftProblems, setDraftProblems] = useState<Record<string, string>>(
    {},
  );

  // Already-chosen channels drop out of the picker; the server sent only the
  // active ones, so an archived channel is never offered in the first place.
  const selectable = (channels.data ?? []).filter(
    (channel) => !targets.some((t) => t.channel_id === channel.id),
  );
  const chosen = targets
    .map((target) => ({
      target,
      channel: (channels.data ?? []).find((c) => c.id === target.channel_id),
    }))
    .filter((entry) => entry.channel);
  // Which platforms need a mode is the server's answer, carried per channel.
  const missingMode = chosen.filter(
    (entry) =>
      entry.channel!.policy_grounded_platform &&
      entry.target.distribution_mode === "UNSPECIFIED",
  );

  const create = useMutation({
    mutationFn: () =>
      api.createContent({
        title,
        // The id the person's choice stands for. They picked a name; this is
        // what the API has always taken, and it is never typed by hand.
        brand_id: brandId,
        owner_user_id: ownerId,
        priority,
        content_type: contentType,
        script_text: script || null,
        // Step 1F.2: created with its channels, so the piece never exists in
        // the state where nobody has said where it is going. The server
        // validates all of this again and refuses an empty list.
        targets,
        // Step 1F.2.3e.1: one request, one transaction. Creating and then
        // posting each resource separately is what would leave a piece of
        // content behind holding two of its four references.
        initial_resources: resourceDraftPayload(resources),
      }),
    onSuccess: onCreated,
  });

  // Which draft the server refused, if it named one. When it did, the sentence
  // belongs under that draft rather than in the form's error box - a person who
  // prepared five references should not have to work out which.
  const refused = serverResourceProblem(create.error, resources);
  const problems = {
    ...draftProblems,
    ...(refused ? { [refused.key]: refused.message } : {}),
  };

  const submit = () => {
    // The same rules the server applies, applied early enough that an obviously
    // incomplete draft costs no request. The server remains the authority: it
    // re-validates every one of these.
    const found = resourceDraftProblems(resources);
    setDraftProblems(found);
    if (Object.keys(found).length > 0) return;
    create.mutate();
  };

  if (brands.isPending) {
    return <Loading label="Đang tải danh sách thương hiệu…" />;
  }
  if (brands.isError) {
    return <ErrorBox error={brands.error} onRetry={() => brands.refetch()} />;
  }
  if (brands.data.length === 0) {
    // Better than a form that cannot succeed. Every content item belongs to a
    // brand, so with none on file there is nothing to create against - and the
    // fix is an admin task, not something to guess a UUID for.
    return (
      <Empty message="Chưa có thương hiệu nào đang hoạt động, nên chưa tạo được nội dung. Bạn nhờ quản trị viên thêm thương hiệu trước nhé." />
    );
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className="space-y-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          Tiêu đề
          <input
            required
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm">
          Người phụ trách
          <Select
            required
            value={ownerId}
            onChange={(event) => setOwnerId(event.target.value)}
            className="mt-1 w-full"
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
          Mức độ ưu tiên
          {/* Beside the person responsible rather than under an "advanced"
              disclosure: it is a decision about the work, and the people who
              need it are the ones filling in this half of the form. Ordered
              default-first - see PRIORITY_ORDER. */}
          <Select
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
            className="mt-1 w-full"
          >
            {PRIORITY_ORDER.map((code) => (
              <option key={code} value={code}>
                {priorityLabel(code)}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          Loại nội dung
          {/* Required, and with no pre-selected answer: there is no sensible
              default format, and Step 1F.2.3e would rather refuse than record a
              classification nobody chose. */}
          <Select
            required
            value={contentType}
            onChange={(event) => setContentType(event.target.value)}
            className="mt-1 w-full"
          >
            <option value="">— chọn —</option>
            {CONTENT_TYPE_ORDER.map((code) => (
              <option key={code} value={code}>
                {contentTypeLabel(code)}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm sm:col-span-2">
          Thương hiệu
          <Select
            required
            value={brandId}
            onChange={(event) => setBrandId(event.target.value)}
            className="mt-1 w-full"
          >
            <option value="">— chọn —</option>
            {(brands.data ?? []).map((brand) => (
              <option key={brand.id} value={brand.id}>
                {brand.name}
              </option>
            ))}
          </Select>
        </label>
        <div className="text-sm sm:col-span-2">
          Kênh dự kiến
          <div className="mt-1 space-y-2">
            {chosen.map(({ target, channel }) => (
              <div
                key={target.channel_id}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--border)] p-2"
              >
                <span className="min-w-40 text-sm">{channel!.name}</span>
                {channel!.policy_grounded_platform ? (
                  <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                    Hình thức đăng
                    <Select
                      value={
                        target.distribution_mode === "UNSPECIFIED"
                          ? ""
                          : target.distribution_mode
                      }
                      onChange={(event) =>
                        setTargets((rows) =>
                          rows.map((row) =>
                            row.channel_id === target.channel_id
                              ? {
                                  ...row,
                                  distribution_mode: event.target.value,
                                }
                              : row,
                          ),
                        )
                      }
                      aria-label={`Hình thức đăng cho ${channel!.name}`}
                    >
                      <option value="" disabled>
                        {distributionModeLabel("UNSPECIFIED")}
                      </option>
                      {SELECTABLE_DISTRIBUTION_MODES.map((mode) => (
                        <option key={mode} value={mode}>
                          {distributionModeLabel(mode)}
                        </option>
                      ))}
                    </Select>
                  </label>
                ) : null}
                <button
                  type="button"
                  onClick={() =>
                    setTargets((rows) =>
                      rows.filter(
                        (row) => row.channel_id !== target.channel_id,
                      ),
                    )
                  }
                  className="min-h-11 rounded-lg border border-[var(--border)] px-3 text-xs"
                >
                  Xóa
                </button>
              </div>
            ))}

            <Select
              value=""
              onChange={(event) =>
                event.target.value &&
                setTargets((rows) => [
                  ...rows,
                  {
                    channel_id: event.target.value,
                    distribution_mode: "UNSPECIFIED",
                  },
                ])
              }
              aria-label="Chọn kênh"
              className="w-full"
            >
              <option value="">+ Chọn kênh</option>
              {selectable.map((channel) => (
                <option key={channel.id} value={channel.id}>
                  {channel.name}
                </option>
              ))}
            </Select>
          </div>
          {targets.length === 0 ? (
            <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
              Vui lòng chọn ít nhất một kênh dự kiến.
            </p>
          ) : null}
          {missingMode.length > 0 ? (
            <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
              Chọn Organic hay Quảng cáo trả phí cho {missingMode.length} kênh —
              chính sách áp dụng khác nhau giữa hai hình thức.
            </p>
          ) : null}
        </div>

        <label className="text-sm sm:col-span-2">
          Kịch bản (không bắt buộc)
          <textarea
            rows={4}
            value={script}
            onChange={(event) => setScript(event.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--border)] bg-transparent px-3 py-2"
          />
        </label>
      </div>

      {/* Step 1F.2.3e.1. After the script and before the submit, because that is
          the order somebody works in: what the piece is, where it goes, what it
          says, and then what was used to write it. */}
      <ResourceDraftSection
        drafts={resources}
        onChange={setResources}
        problems={problems}
      />

      <p className="text-xs text-[var(--text-muted)]">
        Mã nội dung do hệ thống sinh — bạn không cần nhập.
      </p>
      {/* A refusal the server pinned to one draft is already rendered under it;
          repeating it here would say the same thing twice, in the place this
          step exists to stop saying it. */}
      {create.isError && !refused ? <ErrorBox error={create.error} /> : null}
      <PrimaryButton
        type="submit"
        disabled={
          create.isPending ||
          !contentType ||
          targets.length === 0 ||
          missingMode.length > 0
        }
      >
        {create.isPending ? "Đang tạo…" : "Tạo nội dung"}
      </PrimaryButton>
    </form>
  );
}
