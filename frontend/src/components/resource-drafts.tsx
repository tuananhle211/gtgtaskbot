"use client";

import { ApiError, type ContentResourceInput } from "@/lib/api";
import { RESOURCE_TYPE_ORDER, errorMessage, resourcePlaceholder, resourceTypeLabel } from "@/lib/labels";
import { SecondaryButton, Select } from "@/components/pr";
import { Empty } from "@/components/states";

/**
 * Review material being prepared *before* the content item exists.
 *
 * Step 1F.2.3e.1. Step 1F.2.3e could only attach a brief to a piece that had
 * already been created, which put the person filling the create form in the
 * wrong order: they have the brief, the packshot and two reference videos open
 * in front of them at exactly the moment the form gives them nowhere to put
 * them. This section is that place.
 *
 * ## Form state, and nothing else
 *
 * A draft here is **not a row**. Nothing on this screen calls the resource API:
 * adding one appends to an array, removing one splices it, and both are
 * invisible to the server until the create request carries them all as
 * `initial_resources`. That is what makes "Xóa" on an unsaved draft free - no
 * request, no audit row, nothing to undo - and it is why `key` exists: React
 * needs an identity, and the row has no id to be identified by yet.
 *
 * ## One vocabulary
 *
 * The seven types, their Vietnamese names, their placeholders and the
 * `required_for_review` wording all come from `lib/labels`, which is also where
 * the detail page's resource panel gets them. A second list would be a second
 * answer to "what is this thing called", and the two would drift the first time
 * one of them was worded better.
 *
 * ## The server is still the authority
 *
 * `resourceDraftProblem` mirrors the backend's rules closely enough to catch a
 * blank label or a pasted `javascript:` before a request is made. It decides
 * nothing: every draft is re-validated by
 * `meobot.domain.pr.resources`, and a refusal from there is rendered under the
 * draft it was about - see `serverResourceProblem`.
 */

export interface ResourceDraft {
  /**
   * Identity for React and for *"Tài nguyên 2"*. Never sent to the server, and
   * deliberately not an index: splicing the second of three would otherwise
   * renumber the third's inputs and move the cursor out of whatever was being
   * typed.
   */
  key: string;
  resource_type: string;
  label: string;
  location: string;
  note: string;
  required_for_review: boolean;
}

let sequence = 0;

/** An empty draft, pre-set to the commonest type so one field is already answered. */
export function newResourceDraft(): ResourceDraft {
  sequence += 1;
  return {
    key: `resource-draft-${sequence}`,
    resource_type: "REFERENCE",
    label: "",
    location: "",
    note: "",
    required_for_review: false,
  };
}

/** The drafts as the create request carries them. Empty in, empty out. */
export function resourceDraftPayload(drafts: ResourceDraft[]): ContentResourceInput[] {
  return drafts.map((draft) => ({
    resource_type: draft.resource_type,
    label: draft.label.trim(),
    location: draft.location.trim(),
    // An empty note is no note. The server normalises this too; sending `null`
    // rather than `""` keeps the request honest about which it is.
    note: draft.note.trim() || null,
    required_for_review: draft.required_for_review,
  }));
}

/** A NAS location rather than a URL - the same structural test the server uses. */
const looksLikePath = (location: string) =>
  location.startsWith("/") || location.startsWith("\\\\");

/**
 * Why this draft cannot be sent yet, or `null`.
 *
 * A usability mirror of the backend rules, not a second rulebook: it refuses
 * only what the server certainly refuses, and says nothing about anything it is
 * unsure of. Anything subtler - a Drive type pointing at Dropbox, a location
 * longer than the column - comes back from the server with its own sentence.
 */
export function resourceDraftProblem(draft: ResourceDraft): string | null {
  if (!draft.label.trim()) return "Tên / nhãn là bắt buộc.";
  const location = draft.location.trim();
  if (!location) return "Đường dẫn / liên kết là bắt buộc.";
  if (looksLikePath(location)) return null;
  // Everything else is a URL, and only two schemes are ever accepted -
  // `javascript:` and `data:` are refused here as well as on the server.
  if (!/^https?:\/\/[^/\s]+/i.test(location)) return "Đường dẫn / liên kết không hợp lệ.";
  return null;
}

/** Every draft that is not ready, keyed by draft. Empty when the form may submit. */
export function resourceDraftProblems(drafts: ResourceDraft[]): Record<string, string> {
  const problems: Record<string, string> = {};
  for (const draft of drafts) {
    const problem = resourceDraftProblem(draft);
    if (problem) problems[draft.key] = problem;
  }
  return problems;
}

/**
 * A server refusal that names one draft, placed under that draft.
 *
 * The server sends `initial_resource_index` beside the usual `reason`, which is
 * the whole reason a form holding five drafts does not have to say "one of these
 * is wrong". `null` when the failure was about the content item itself - a
 * missing channel, a blank title - which belongs in the form's own error box.
 */
export function serverResourceProblem(
  error: unknown,
  drafts: ResourceDraft[],
): { key: string; message: string } | null {
  if (!(error instanceof ApiError)) return null;
  const index = error.details?.initial_resource_index;
  if (typeof index !== "number") return null;
  const draft = drafts[index];
  if (!draft) return null;
  return { key: draft.key, message: errorMessage(error.code, error.details) ?? error.message };
}

/**
 * *Tài nguyên & tham khảo* on the create form.
 *
 * Starts empty and stays empty until somebody asks for a draft: seven blank
 * resource forms on an already-dense create screen would be seven things to
 * scroll past for the majority of items that need none.
 */
export function ResourceDraftSection({
  drafts,
  onChange,
  problems = {},
}: {
  drafts: ResourceDraft[];
  onChange: (drafts: ResourceDraft[]) => void;
  /** What to say under each draft, keyed by `ResourceDraft.key`. */
  problems?: Record<string, string>;
}) {
  const update = (key: string, patch: Partial<ResourceDraft>) =>
    onChange(drafts.map((draft) => (draft.key === key ? { ...draft, ...patch } : draft)));

  return (
    <section
      aria-label="Tài nguyên & tham khảo"
      className="rounded-lg border border-[var(--border)] p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Tài nguyên &amp; tham khảo</h3>
        <SecondaryButton type="button" onClick={() => onChange([...drafts, newResourceDraft()])}>
          + Thêm tài nguyên
        </SecondaryButton>
      </div>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        Tài liệu, hình ảnh hoặc liên kết cần dùng khi viết và duyệt nội dung. Không bắt buộc.
      </p>

      {drafts.length === 0 ? (
        <div className="mt-3">
          <Empty message="Chưa có tài nguyên tham khảo." />
        </div>
      ) : (
        <ul className="mt-3 space-y-3">
          {drafts.map((draft, index) => (
            <li key={draft.key}>
              <ResourceDraftEditor
                draft={draft}
                position={index + 1}
                problem={problems[draft.key]}
                onChange={(patch) => update(draft.key, patch)}
                onRemove={() => onChange(drafts.filter((row) => row.key !== draft.key))}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * One draft resource, as a compact bordered block.
 *
 * Every control carries `Tài nguyên N — …` as its accessible name. With three
 * drafts on screen there are three boxes labelled *"Tên / nhãn"*, and a screen
 * reader moving between them would otherwise have no way to say which resource
 * it had reached. The visible text stays short because the number is already on
 * screen, directly above.
 */
function ResourceDraftEditor({
  draft,
  position,
  problem,
  onChange,
  onRemove,
}: {
  draft: ResourceDraft;
  position: number;
  problem?: string;
  onChange: (patch: Partial<ResourceDraft>) => void;
  onRemove: () => void;
}) {
  const named = (field: string) => `Tài nguyên ${position} — ${field}`;

  return (
    <div className="rounded-lg border border-[var(--border)] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium text-[var(--text-muted)]">Tài nguyên {position}</p>
        {/* `type="button"`, like every control in here: this block lives inside
            the create form, and a bare button would submit it. */}
        <SecondaryButton
          type="button"
          onClick={onRemove}
          aria-label={`Xóa tài nguyên ${position}`}
        >
          Xóa
        </SecondaryButton>
      </div>

      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <label className="text-sm">
          Loại tài nguyên
          <Select
            required
            aria-label={named("Loại tài nguyên")}
            value={draft.resource_type}
            onChange={(event) => onChange({ resource_type: event.target.value })}
            className="mt-1 w-full"
          >
            {RESOURCE_TYPE_ORDER.map((code) => (
              <option key={code} value={code}>
                {resourceTypeLabel(code)}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          Tên / nhãn
          {/* Deliberately not `required`. The browser's own validation would
              stop the submit before `resourceDraftProblem` ran, and its bubble
              says "please fill in this field" in the browser's language with no
              way to say *which* resource - which is the whole thing this form
              has to get right. Blank is still refused, in Vietnamese, under the
              draft it belongs to, and by the server after that. */}
          <input
            aria-label={named("Tên / nhãn")}
            value={draft.label}
            onChange={(event) => onChange({ label: event.target.value })}
            placeholder="Brief khách hàng"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm sm:col-span-2">
          Đường dẫn / liên kết
          <input
            aria-label={named("Đường dẫn / liên kết")}
            value={draft.location}
            onChange={(event) => onChange({ location: event.target.value })}
            placeholder={resourcePlaceholder(draft.resource_type)}
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
        <label className="text-sm sm:col-span-2">
          Ghi chú (không bắt buộc)
          <input
            aria-label={named("Ghi chú")}
            value={draft.note}
            onChange={(event) => onChange({ note: event.target.value })}
            placeholder="Dùng packshot số 3"
            className="mt-1 min-h-11 w-full rounded-lg border border-[var(--border)] bg-transparent px-3"
          />
        </label>
      </div>

      <label className="mt-2 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          aria-label={named("Bắt buộc xem khi duyệt")}
          checked={draft.required_for_review}
          onChange={(event) => onChange({ required_for_review: event.target.checked })}
          className="h-4 w-4"
        />
        Bắt buộc xem khi duyệt
      </label>

      {problem ? (
        // Under the draft it is about, never at the top of the form: with five
        // references prepared, "một tài nguyên không hợp lệ" is a sentence that
        // makes somebody re-read all five.
        <p role="alert" className="mt-2 text-xs text-red-700 dark:text-red-300">
          {problem}
        </p>
      ) : null}
    </div>
  );
}
