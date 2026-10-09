"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type CreatedInvite } from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { Select } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { disableInviteConfirmation } from "@/lib/confirmations";
import { formatWhen, roleLabel } from "@/lib/labels";

/** The base roles that may invite at all (the server refuses everyone else). */
export const INVITER_ROLES = new Set(["TEAM_LEAD", "ADMIN", "OWNER"]);

/** Whether this account may invite: a team lead and up, or a Trưởng phòng /
 * Leader of a ban in ORD whatever their system role. */
export function mayInvite(me: {
  role: string;
  units: Array<{ code: string; role?: string; is_lead?: boolean }>;
}): boolean {
  return (
    INVITER_ROLES.has(me.role) ||
    me.units.some((unit) => unit.code === "ADS" && (unit.role === "HEAD" || unit.is_lead))
  );
}

const FIELD =
  "min-h-10 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";

/** `https://t.me/<bot>?start=<code>`, or null when the bot's name is unknown. */
export function inviteDeepLink(botUsername: string | null | undefined, code: string): string | null {
  const bot = (botUsername ?? "").replace(/^@/, "").trim();
  if (!bot) return null;
  return `https://t.me/${bot}?start=${encodeURIComponent(code)}`;
}

/**
 * "Mời thành viên": create an invite code (shown once, with a copy button and
 * the bot's deep link when the API names the bot), and the person's open
 * invites with a revoke button. A team lead invites staff; OWNER / ADMIN may
 * also invite a team lead. A stream lead's invitee joins that lead's stream,
 * reporting to them (`joins_label`); an OWNER / ADMIN invitee joins **no
 * stream** and is tagged afterwards, from "Chưa có luồng".
 */
export function InvitePanel({ role }: { role: string }) {
  const queryClient = useQueryClient();
  const mayInviteLead = role === "OWNER" || role === "ADMIN";
  const [inviteRole, setInviteRole] = useState("EMPLOYEE");
  const [note, setNote] = useState("");
  const [created, setCreated] = useState<CreatedInvite | null>(null);
  const [copied, setCopied] = useState<"code" | "link" | null>(null);
  const invites = useQuery({ queryKey: ["invites"], queryFn: api.listInvites, retry: false });
  const create = useMutation({
    mutationFn: () =>
      api.createInvite({
        role: mayInviteLead ? inviteRole : "EMPLOYEE",
        note: note.trim() || null,
      }),
    onSuccess: (invite) => {
      setCreated(invite);
      setCopied(null);
      setNote("");
      void queryClient.invalidateQueries({ queryKey: ["invites"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.disableInvite(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["invites"] }),
  });
  const copy = async (text: string, what: "code" | "link") => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(what);
    } catch {
      setCopied(null);
    }
  };
  const bot = created?.bot_username ?? invites.data?.bot_username ?? null;
  const link = created ? inviteDeepLink(bot, created.code) : null;
  const open = (invites.data?.items ?? []).filter((invite) => invite.active);

  return (
    <section className="space-y-4" aria-labelledby="account-invites">
      <div>
        <h2 id="account-invites" className="text-lg font-semibold tracking-tight">
          Mời thành viên
        </h2>
        <p className="mt-0.5 text-sm text-[var(--text-muted)]">
          {mayInviteLead
            ? "Người được mời sẽ chưa thuộc luồng nào cho đến khi được gắn luồng."
            : "Người được mời tự vào luồng của bạn; ở ORD, vào đúng ban của bạn với bạn là trưởng quản lý."}
        </p>
      </div>

      <form
        className="panel grid gap-3 p-5 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_auto] sm:items-end"
        onSubmit={(event) => event.preventDefault()}
      >
        <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
          Vai trò
          <Select
            value={mayInviteLead ? inviteRole : "EMPLOYEE"}
            onChange={(event) => setInviteRole(event.target.value)}
            disabled={!mayInviteLead}
            className="min-h-10"
          >
            <option value="EMPLOYEE">{roleLabel("EMPLOYEE")}</option>
            {mayInviteLead ? <option value="TEAM_LEAD">{roleLabel("TEAM_LEAD")}</option> : null}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-[var(--text-muted)]">
          Ghi chú (không bắt buộc)
          <input
            value={note}
            maxLength={500}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Ví dụ: Dựng mới tháng 10"
            className={FIELD}
          />
        </label>
        <ConfirmButton
          spec={{
            title: "Tạo mã mời?",
            description: mayInviteLead
              ? "Mã mời chỉ hiện một lần, ngay sau khi tạo. Người dùng mã để tham gia qua bot Telegram và sẽ chưa thuộc luồng nào cho đến khi được gắn luồng."
              : "Mã mời chỉ hiện một lần, ngay sau khi tạo. Người dùng mã để tham gia qua bot Telegram và tự vào luồng của bạn (ở ORD: đúng ban của bạn, bạn là trưởng quản lý).",
            confirmLabel: "Tạo mã mời",
          }}
          onConfirm={() => create.mutate()}
          pending={create.isPending}
          error={create.error}
          tone="primary"
          onOpenChange={(next) => {
            if (next) create.reset();
          }}
        >
          Tạo mã mời
        </ConfirmButton>
      </form>

      {created ? (
        <div
          role="status"
          aria-label="Mã mời vừa tạo"
          className="panel space-y-3 border-[var(--accent)] p-5"
        >
          <p className="text-sm font-medium">
            Mã mời ({created.role_label}). Sao chép ngay: mã chỉ hiện một lần.
          </p>
          <p className="text-sm text-[var(--text-muted)]" data-testid="invite-joins">
            {created.joins_label
              ? `Người được mời vào: ${created.joins_label}`
              : "Người được mời chưa thuộc luồng nào - gắn luồng ở Quản lý thành viên."}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="rounded-lg bg-[var(--surface-muted)] px-3 py-2 font-mono text-base font-semibold tracking-wider">
              {created.code}
            </code>
            <button
              type="button"
              onClick={() => void copy(created.code, "code")}
              className="inline-flex min-h-10 items-center rounded-lg border border-[var(--border)] px-3 text-sm hover:bg-[var(--surface-muted)]"
            >
              {copied === "code" ? "Đã sao chép" : "Sao chép mã"}
            </button>
          </div>
          {link ? (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <a
                href={link}
                target="_blank"
                rel="noreferrer"
                className="break-all font-medium text-[var(--accent)]"
              >
                {link}
              </a>
              <button
                type="button"
                onClick={() => void copy(link, "link")}
                className="inline-flex min-h-10 items-center rounded-lg border border-[var(--border)] px-3 text-sm hover:bg-[var(--surface-muted)]"
              >
                {copied === "link" ? "Đã sao chép" : "Sao chép link"}
              </button>
            </div>
          ) : (
            <p className="text-xs text-[var(--text-muted)]">
              Gửi mã cho người được mời; họ nhập mã khi bắt đầu chat với bot Telegram.
            </p>
          )}
        </div>
      ) : null}

      <div className="panel">
        <header className="border-b border-[var(--border)] px-5 py-3">
          <h3 className="text-sm font-semibold">Mã mời còn hiệu lực</h3>
        </header>
        {invites.isPending ? <Loading label="Đang tải mã mời…" /> : null}
        {invites.isError ? (
          <div className="p-4">
            <ErrorBox error={invites.error} onRetry={() => invites.refetch()} />
          </div>
        ) : null}
        {invites.data && open.length === 0 ? (
          <p className="px-5 py-4 text-sm text-[var(--text-muted)]">Chưa có mã mời nào còn hiệu lực.</p>
        ) : null}
        {open.length > 0 ? (
          <ul aria-label="Mã mời còn hiệu lực" className="divide-y divide-[var(--border)]">
            {open.map((invite) => (
              <li key={invite.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 text-sm">
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2">
                    <Pill tone="neutral">{invite.role_label}</Pill>
                    <span className="text-[var(--text-muted)]">
                      Đã dùng {invite.use_count}/{invite.max_uses}
                      {invite.expires_at ? ` · hết hạn ${formatWhen(invite.expires_at)}` : ""}
                    </span>
                  </span>
                  {invite.joins_label ? (
                    <span className="mt-1 block text-xs">Vào: {invite.joins_label}</span>
                  ) : null}
                  {invite.note ? <span className="mt-1 block">{invite.note}</span> : null}
                  <span className="mt-0.5 block text-xs text-[var(--text-muted)]">
                    Tạo {formatWhen(invite.created_at)}
                  </span>
                </span>
                <ConfirmButton
                  spec={disableInviteConfirmation()}
                  onConfirm={() => revoke.mutate(invite.id)}
                  pending={revoke.isPending && revoke.variables === invite.id}
                  error={revoke.variables === invite.id ? revoke.error : undefined}
                  tone="danger"
                  className="min-h-9 px-3 text-xs"
                  ariaLabel={`Thu hồi mã mời ${invite.note ?? invite.role_label}`}
                >
                  Thu hồi
                </ConfirmButton>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
