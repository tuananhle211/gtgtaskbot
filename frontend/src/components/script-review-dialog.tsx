"use client";

import { useState } from "react";
import type { ScriptReview } from "@/lib/api";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";

/**
 * What the AI said about a refused script, in a window the orderer can open
 * and close: each offending passage (the forbidden item it breaks and how to
 * rewrite it), then the whole script corrected in its own format - to copy,
 * or to put straight into the script field before checking again.
 */
export function ScriptReviewDialog({
  open,
  onClose,
  review,
  onUse,
}: {
  open: boolean;
  onClose: () => void;
  review: ScriptReview;
  /** Put the corrected script into the form's script field. */
  onUse?: (script: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const count = review.violations.length;
  const copy = async () => {
    if (!review.revised_script) return;
    try {
      await navigator.clipboard.writeText(review.revised_script);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="xl"
      title={`KHÔNG ĐẠT - có ${count} lỗi`}
      description="Sửa theo gợi ý, hoặc dùng bản đã sửa bên dưới rồi bấm “AI duyệt kịch bản” lại."
    >
      <ModalBody className="space-y-4 text-sm">
        <ol className="space-y-3" data-testid="script-review-errors">
          {review.violations.map((item, index) => (
            <li key={`${index}-${item.quote}`}>
              <p className="font-semibold">Lỗi {index + 1}:</p>
              <ul className="mt-1 space-y-0.5 pl-1">
                <li>• Câu/cụm vi phạm: “{item.quote}”</li>
                <li>
                  • Vi phạm:{" "}
                  {item.rule > 0 ? `Mục ${item.rule}` : "Mục cấm"}
                  {item.rule_name ? ` - ${item.rule_name}` : ""}
                  {item.reason ? ` (${item.reason})` : ""}
                </li>
                {item.suggestion ? <li>• Gợi ý sửa: {item.suggestion}</li> : null}
              </ul>
            </li>
          ))}
        </ol>
        {review.note ? (
          <p className="text-[var(--text-muted)]" data-testid="script-review-note">
            ({review.note})
          </p>
        ) : null}
        {review.revised_script ? (
          <section aria-label="Bản content đã sửa">
            <p className="border-t border-[var(--border)] pt-3 font-semibold">
              BẢN CONTENT ĐÃ SỬA:
            </p>
            <pre
              data-testid="script-review-revised"
              className="mt-2 max-h-[45vh] overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3 font-sans text-sm"
            >
              {review.revised_script}
            </pre>
          </section>
        ) : (
          <p className="text-[var(--text-muted)]">AI không gửi kèm bản sửa lần này.</p>
        )}
      </ModalBody>
      <ModalFooter
        start={
          copied ? (
            <span role="status" className="text-xs text-[var(--good)]">
              Đã sao chép bản sửa.
            </span>
          ) : null
        }
      >
        <button
          type="button"
          onClick={onClose}
          className="min-h-11 rounded-lg border border-[var(--border)] px-4 text-sm font-medium"
        >
          Quay lại sửa
        </button>
        {review.revised_script ? (
          <button
            type="button"
            onClick={() => void copy()}
            className="min-h-11 rounded-lg border border-[var(--accent)] px-4 text-sm font-medium text-[var(--accent)]"
          >
            Sao chép bản sửa
          </button>
        ) : null}
        {review.revised_script && onUse ? (
          <button
            type="button"
            data-autofocus=""
            onClick={() => {
              onUse(review.revised_script as string);
              onClose();
            }}
            className="min-h-11 rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)]"
          >
            Dùng bản sửa
          </button>
        ) : null}
      </ModalFooter>
    </Modal>
  );
}
