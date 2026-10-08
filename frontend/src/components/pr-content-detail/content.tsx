"use client";

import { type ContentTarget, type ContentVersion } from "@/lib/api";
import { distributionModeLabel } from "@/lib/labels";
import { Empty } from "@/components/states";
import { SecondaryButton } from "@/components/pr";
import { ReviseForm } from "./review";

/**
 * Where this piece is going, on the tab people now land on.
 *
 * Step 1F.2. The draft tab is the default, and AI review is blocked by *target*
 * data - so without this somebody would read the blocker on the action panel
 * and have no idea which screen to fix it on. Editing still lives in one place;
 * this links to it rather than duplicating the editor.
 *
 * Legacy content with no targets says so plainly. Hiding it would make a piece
 * that cannot be policy-reviewed look identical to one that can.
 */
export function TargetSummary({
  targets,
  onEditTargets,
}: {
  targets: ContentTarget[];
  onEditTargets: () => void;
}) {
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Kênh dự kiến</h2>
        <SecondaryButton onClick={onEditTargets}>Sửa kênh</SecondaryButton>
      </div>
      {targets.length === 0 ? (
        <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
          Chưa có kênh dự kiến. Cần chọn ít nhất một kênh trước khi AI review.
        </p>
      ) : (
        <ul className="mt-2 space-y-1 text-sm">
          {targets.map((target) => (
            <li key={target.id}>
              {target.channel_name ?? target.channel_code}
              {target.policy_grounded_platform ? (
                <span
                  className={
                    target.distribution_mode === "UNSPECIFIED"
                      ? " text-amber-700 dark:text-amber-300"
                      : " text-[var(--text-muted)]"
                  }
                >
                  {" · "}
                  {distributionModeLabel(target.distribution_mode)}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The draft, and the one way to change it.
 *
 * Editing is offered only when the server listed `EDIT_CONTENT`, which it decides
 * from the domain's editable-stage table - the same one `revise_content`
 * enforces on the way in. Saving
 * appends a new version; the previous one is never touched, which is why the
 * button says what number it will create.
 */
export function ContentTab({
  contentId,
  version,
  editable,
  editing,
  onToggleEditing,
  onDone,
}: {
  contentId: string;
  version: ContentVersion | null;
  editable: boolean;
  editing: boolean;
  onToggleEditing: (open: boolean) => void;
  onDone: () => void;
}) {
  if (!version) {
    return <Empty message="Nội dung này chưa có bản nháp nào." />;
  }
  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 className="text-sm font-semibold">
        Kịch bản hiện tại (v{version.version_no})
      </h2>
      <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-[var(--surface-muted)] p-3 text-sm">
        {version.script_text ?? "(chưa có kịch bản)"}
      </pre>
      {editable ? (
        editing ? (
          <ReviseForm
            contentId={contentId}
            expectedVersion={version.version_no}
            initial={version.script_text ?? ""}
            onCancel={() => onToggleEditing(false)}
            onDone={() => {
              onToggleEditing(false);
              onDone();
            }}
          />
        ) : (
          <SecondaryButton
            className="mt-3"
            onClick={() => onToggleEditing(true)}
          >
            {version.script_text ? "Chỉnh sửa nội dung" : "Viết nội dung"}
          </SecondaryButton>
        )
      ) : (
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          Bản nháp không sửa được ở bước này — có người hoặc AI đang xét bản
          hiện tại.
        </p>
      )}
      <p className="mt-3 text-xs text-[var(--text-muted)]">
        Bản nháp là bất biến — sửa nội dung tạo phiên bản mới, không ghi đè bản
        cũ.
      </p>
    </section>
  );
}
