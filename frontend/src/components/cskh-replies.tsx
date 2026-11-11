"use client";

/**
 * "Nhãn & câu mẫu" (0102, CSKH MANAGE): page labels (filter the inbox, group
 * pages), quick replies (`/ký_hiệu`, `{ten}` = the customer's name; shared by
 * every page or one page's own - Pancake has no API for them), the bulk import,
 * and each page's Pancake tags / staff as Pancake last reported them.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cskhApi, LABEL_TONES, type Label, type QuickReply } from "@/lib/cskh";
import { ErrorLine, LabelChip, fieldClass, smallButton } from "@/components/cskh-common";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

const COLORS = Object.keys(LABEL_TONES);

export function CskhReplies() {
  const state = useQuery({ queryKey: ["cskh", "state"], queryFn: cskhApi.state });
  return (
    <div className="space-y-4">
      <div>
        <p className="brand-eyebrow">Chăm sóc khách hàng</p>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Nhãn &amp; câu mẫu</h1>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <Labels />
        <Replies pages={state.data?.pages.map((p) => ({ id: p.id, name: p.name })) ?? []} />
      </div>
      <section className="panel space-y-2 p-4">
        <h2 className="text-base font-semibold">Thẻ và nhân viên trên Pancake</h2>
        <p className="text-xs text-[var(--text-muted)]">
          Đồng bộ từ Pancake 10 phút/lần (hoặc “Đồng bộ thẻ &amp; nhân viên” ở Kết nối page). Sửa thẻ trên
          Pancake.
        </p>
        {state.isLoading ? <Loading /> : null}
        {state.error ? <ErrorBox error={state.error} /> : null}
        <div className="grid gap-2 md:grid-cols-2">
          {(state.data?.pages ?? []).map((page) => (
            <div
              key={page.id}
              className="rounded-lg border border-[var(--border)] p-2 text-sm"
              data-testid="cskh-page-meta"
            >
              <p className="font-medium">{page.name}</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {page.pancake_tags.map((tag) => (
                  <span
                    key={tag.id}
                    className="rounded px-1.5 text-xs"
                    style={{ background: tag.color, color: "#fff" }}
                  >
                    {tag.text}
                  </span>
                ))}
                {page.pancake_tags.length === 0 ? (
                  <span className="text-xs text-[var(--text-muted)]">Chưa có thẻ</span>
                ) : null}
              </div>
              <p className="mt-1 text-xs text-[var(--text-muted)]">
                Nhân viên: {page.pancake_users.map((u) => u.name).join(", ") || "—"}
              </p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Labels() {
  const client = useQueryClient();
  const labels = useQuery({ queryKey: ["cskh", "labels"], queryFn: cskhApi.labels });
  const [editing, setEditing] = useState<Label | null>(null);
  const [name, setName] = useState("");
  const [color, setColor] = useState("blue");
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["cskh", "labels"] });
    void client.invalidateQueries({ queryKey: ["cskh", "state"] });
  };
  const save = useMutation({
    mutationFn: () =>
      editing
        ? cskhApi.editLabel(editing.id, { name: name.trim(), color })
        : cskhApi.addLabel({ name: name.trim(), color }),
    onSuccess: () => {
      setEditing(null);
      setName("");
      setColor("blue");
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => cskhApi.deleteLabel(id), onSuccess: refresh });
  return (
    <section className="panel space-y-3 p-4">
      <h2 className="text-base font-semibold">Nhãn page</h2>
      <p className="text-xs text-[var(--text-muted)]">
        Gắn cho page ở Kết nối page; hộp chat lọc hội thoại theo nhãn. (Chủ page / nhân viên trực là tài khoản
        APM, gán ở Kết nối page.)
      </p>
      {labels.isLoading ? <Loading /> : null}
      {labels.error ? <ErrorBox error={labels.error} /> : null}
      <ul className="space-y-1">
        {(labels.data?.labels ?? []).map((label) => (
          <li key={label.id} className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2">
              <LabelChip label={label} />
              <span className="text-xs text-[var(--text-muted)]">{label.pages ?? 0} page</span>
            </span>
            <span className="flex gap-1">
              <button
                type="button"
                className={smallButton}
                onClick={() => {
                  setEditing(label);
                  setName(label.name);
                  setColor(label.color);
                }}
              >
                Sửa
              </button>
              <ConfirmButton
                spec={{
                  title: `Xoá nhãn ${label.name}?`,
                  description: "Nhãn được gỡ khỏi mọi page.",
                  confirmLabel: "Xoá nhãn",
                  variant: "destructive",
                }}
                onConfirm={() => remove.mutate(label.id)}
                pending={remove.isPending}
                tone="danger"
                className={smallButton}
              >
                Xoá
              </ConfirmButton>
            </span>
          </li>
        ))}
      </ul>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) save.mutate();
        }}
      >
        <input
          className={`${fieldClass} min-w-0 flex-1`}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Tên nhãn"
          aria-label="Tên nhãn"
        />
        <Select aria-label="Màu nhãn" value={color} onChange={(e) => setColor(e.target.value)}>
          {COLORS.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
        <SecondaryButton type="submit" disabled={!name.trim() || save.isPending}>
          {editing ? "Lưu nhãn" : "+ Thêm nhãn"}
        </SecondaryButton>
        {editing ? (
          <button
            type="button"
            className={smallButton}
            onClick={() => {
              setEditing(null);
              setName("");
            }}
          >
            Huỷ
          </button>
        ) : null}
      </form>
      <ErrorLine error={save.error ?? remove.error} />
    </section>
  );
}

function Replies({ pages }: { pages: { id: string; name: string }[] }) {
  const client = useQueryClient();
  const replies = useQuery({ queryKey: ["cskh", "quick-replies"], queryFn: cskhApi.quickReplies });
  const [editing, setEditing] = useState<QuickReply | "new" | null>(null);
  const [importing, setImporting] = useState(false);
  const [scope, setScope] = useState("");
  const [conflict, setConflict] = useState<{ ids: string[] | null; shortcuts: string[] } | null>(null);
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["cskh", "quick-replies"] });
    void client.invalidateQueries({ queryKey: ["cskh", "state"] });
  };
  const remove = useMutation({
    mutationFn: (id: string) => cskhApi.deleteQuickReply(id),
    onSuccess: refresh,
  });
  const share = useMutation({
    mutationFn: ({ ids, overwrite }: { ids: string[] | null; overwrite: boolean }) =>
      cskhApi.shareQuickReplies(ids, overwrite),
    onSuccess: (result, vars) => {
      setConflict(
        result.conflict.length && !vars.overwrite ? { ids: vars.ids, shortcuts: result.conflict } : null,
      );
      refresh();
    },
  });
  const list = (replies.data?.quick_replies ?? []).filter(
    (r) => !scope || (scope === "*" ? !r.page_id : r.page_id === scope),
  );
  const ownCount = (replies.data?.quick_replies ?? []).filter((r) => r.page_id).length;
  return (
    <section className="panel space-y-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Câu trả lời mẫu</h2>
        <div className="flex flex-wrap gap-2">
          <Select aria-label="Phạm vi câu mẫu" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="">Mọi câu mẫu</option>
            <option value="*">Dùng chung mọi page</option>
            {pages.map((p) => (
              <option key={p.id} value={p.id}>
                Riêng: {p.name}
              </option>
            ))}
          </Select>
          <button type="button" className={smallButton} onClick={() => setImporting(true)}>
            Nhập hàng loạt
          </button>
          {ownCount ? (
            <ConfirmButton
              spec={{
                title: `Chuyển ${ownCount} câu mẫu riêng thành dùng chung?`,
                description:
                  "Câu mẫu riêng của từng page sẽ dùng được cho mọi page. Ký hiệu đã có câu chung khác nội dung sẽ được hỏi lại.",
                confirmLabel: "Dùng cho mọi page",
              }}
              onConfirm={() => share.mutate({ ids: null, overwrite: false })}
              pending={share.isPending}
              tone="secondary"
              className={smallButton}
            >
              ⇄ Dùng cho mọi page
            </ConfirmButton>
          ) : null}
          <PrimaryButton type="button" onClick={() => setEditing("new")}>
            + Câu mẫu
          </PrimaryButton>
        </div>
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Trong ô chat gõ <code>/</code> để chèn; <code>{"{ten}"}</code> được thay bằng tên khách. Câu riêng của
        page thắng câu chung cùng ký hiệu.
      </p>
      {conflict ? (
        <div
          className="space-y-2 rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm"
          data-testid="cskh-share-conflict"
        >
          <p>
            Các ký hiệu đã có câu dùng chung khác nội dung:{" "}
            <strong>{conflict.shortcuts.map((s) => `/${s}`).join(", ")}</strong>.
          </p>
          <ConfirmButton
            spec={{
              title: "Ghi đè câu dùng chung?",
              description: "Nội dung câu riêng sẽ thay câu dùng chung cùng ký hiệu cho mọi page.",
              confirmLabel: "Ghi đè",
              variant: "destructive",
            }}
            onConfirm={() => share.mutate({ ids: conflict.ids, overwrite: true })}
            pending={share.isPending}
            tone="danger"
            className={smallButton}
          >
            Ghi đè
          </ConfirmButton>
        </div>
      ) : null}
      {replies.isLoading ? <Loading /> : null}
      {replies.error ? <ErrorBox error={replies.error} /> : null}
      <ul className="divide-y divide-[var(--border)]">
        {list.map((reply) => (
          <li
            key={reply.id}
            className="flex items-start justify-between gap-2 py-2 text-sm"
            data-testid="cskh-quick-reply"
          >
            <div className="min-w-0">
              <p>
                <span className="font-semibold">/{reply.shortcut}</span>{" "}
                <span className="text-xs text-[var(--text-muted)]">
                  {reply.page_name ? `Riêng: ${reply.page_name}` : "Mọi page"}
                </span>
              </p>
              <p className="whitespace-pre-wrap text-[var(--text-muted)]">{reply.text}</p>
            </div>
            <div className="flex shrink-0 gap-1">
              {reply.page_id ? (
                <button
                  type="button"
                  className={smallButton}
                  title="Dùng cho mọi page"
                  onClick={() => share.mutate({ ids: [reply.id], overwrite: false })}
                >
                  ⇄ Mọi page
                </button>
              ) : null}
              <button type="button" className={smallButton} onClick={() => setEditing(reply)}>
                Sửa
              </button>
              <ConfirmButton
                spec={{
                  title: `Xoá câu mẫu /${reply.shortcut}?`,
                  description: "Không chèn được câu này trong ô chat nữa.",
                  confirmLabel: "Xoá",
                  variant: "destructive",
                }}
                onConfirm={() => remove.mutate(reply.id)}
                pending={remove.isPending}
                tone="danger"
                className={smallButton}
              >
                Xoá
              </ConfirmButton>
            </div>
          </li>
        ))}
      </ul>
      {list.length === 0 && !replies.isLoading ? (
        <p className="text-sm text-[var(--text-muted)]">Chưa có câu mẫu.</p>
      ) : null}
      <ErrorLine error={remove.error ?? share.error} />
      {editing ? (
        <ReplyDialog
          reply={editing === "new" ? null : editing}
          pages={pages}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      ) : null}
      {importing ? (
        <ImportDialog pages={pages} onClose={() => setImporting(false)} onSaved={refresh} />
      ) : null}
    </section>
  );
}

function ReplyDialog({
  reply,
  pages,
  onClose,
  onSaved,
}: {
  reply: QuickReply | null;
  pages: { id: string; name: string }[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [shortcut, setShortcut] = useState(reply?.shortcut ?? "");
  const [text, setText] = useState(reply?.text ?? "");
  const [pageId, setPageId] = useState(reply?.page_id ?? "");
  const save = useMutation({
    mutationFn: () => {
      const body = { shortcut: shortcut.trim(), text: text.trim(), page_id: pageId || null };
      return reply ? cskhApi.editQuickReply(reply.id, body) : cskhApi.addQuickReply(body);
    },
    onSuccess: () => {
      onSaved();
      onClose();
    },
  });
  return (
    <Modal
      open
      onClose={onClose}
      title={reply ? `Sửa /${reply.shortcut}` : "Câu mẫu mới"}
      busy={save.isPending}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <ModalBody className="space-y-3">
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Ký hiệu</span>
            <input
              className={fieldClass}
              value={shortcut}
              onChange={(e) => setShortcut(e.target.value)}
              placeholder="chao"
            />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Nội dung</span>
            <textarea
              className={`${fieldClass} py-2`}
              rows={5}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Phạm vi</span>
            <Select value={pageId} onChange={(e) => setPageId(e.target.value)} className="w-full">
              <option value="">Dùng chung mọi page</option>
              {pages.map((p) => (
                <option key={p.id} value={p.id}>
                  Chỉ page {p.name}
                </option>
              ))}
            </Select>
          </label>
          <ErrorLine error={save.error} />
        </ModalBody>
        <ModalFooter>
          <SecondaryButton type="button" onClick={onClose}>
            Huỷ
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={!shortcut.trim() || !text.trim() || save.isPending}>
            Lưu
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}

function ImportDialog({
  pages,
  onClose,
  onSaved,
}: {
  pages: { id: string; name: string }[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [text, setText] = useState("");
  const [pageId, setPageId] = useState("");
  const run = useMutation({
    mutationFn: () => cskhApi.importQuickReplies(text, pageId || null),
    onSuccess: onSaved,
  });
  return (
    <Modal open onClose={onClose} title="Nhập câu mẫu hàng loạt" size="lg" busy={run.isPending}>
      <ModalBody className="space-y-3">
        <p className="text-xs text-[var(--text-muted)]">
          Mỗi câu một khối: dòng đầu <code>/ký_hiệu | nội dung</code>, các dòng sau nối vào nội dung. Ký hiệu
          đã có trong phạm vi sẽ được bỏ qua.
        </p>
        <textarea
          className={`${fieldClass} py-2 font-mono text-xs`}
          rows={10}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Danh sách câu mẫu"
          placeholder={"/chao | Dạ em chào {ten} ạ\n/diachi | Địa chỉ phòng khám..."}
        />
        <Select
          aria-label="Phạm vi nhập"
          value={pageId}
          onChange={(e) => setPageId(e.target.value)}
          className="w-full"
        >
          <option value="">Dùng chung mọi page</option>
          {pages.map((p) => (
            <option key={p.id} value={p.id}>
              Chỉ page {p.name}
            </option>
          ))}
        </Select>
        {run.data ? (
          <p className="text-sm text-[var(--good)]">
            Đã thêm {run.data.added} câu
            {run.data.skipped.length ? `, bỏ qua ${run.data.skipped.map((s) => `/${s}`).join(", ")}` : ""}.
          </p>
        ) : null}
        <ErrorLine error={run.error} />
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Đóng
        </SecondaryButton>
        <PrimaryButton type="button" disabled={!text.trim() || run.isPending} onClick={() => run.mutate()}>
          Nhập
        </PrimaryButton>
      </ModalFooter>
    </Modal>
  );
}
