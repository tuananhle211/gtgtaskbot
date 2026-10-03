/**
 * Where a failed or spent login link lands, and where sign-out goes.
 *
 * It says nothing about *which* of the two happened, and it must not: expired,
 * already used, revoked, or never real all look the same from here. The
 * distinction is only useful to somebody probing, and the useful instruction is
 * identical in every case - ask the bot for a new one.
 *
 * Step 1E.2 kept that and made the *guidance* concrete. "Liên kết không còn dùng
 * được" is true and leaves somebody guessing what they did wrong; naming the two
 * ordinary causes (it timed out, or it was already opened once - including by
 * Telegram's own link preview in another tab) turns a dead end into a next step.
 * Naming both causes together reveals nothing: the page does not say which one
 * applies, because it does not know and does not ask.
 */
export default function LoginFailed() {
  return (
    <div className="mx-auto max-w-lg p-6 sm:p-8">
      <h1 className="text-lg font-semibold">Chưa đăng nhập được</h1>
      <p className="mt-3 text-sm text-[var(--text-muted)]">
        Liên kết đăng nhập này không dùng được nữa. Thường là vì một trong hai lý do:
      </p>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-[var(--text-muted)]">
        <li>liên kết đã hết hạn — mỗi liên kết chỉ sống vài phút;</li>
        <li>hoặc liên kết đã được mở một lần rồi — mỗi liên kết chỉ dùng được một lần.</li>
      </ul>
      {/* Names the command rather than the bot - see the note in states.tsx. */}
      <p className="mt-4 text-sm">
        Gửi lệnh <code className="rounded bg-[var(--surface-muted)] px-1.5 py-0.5">/web</code> trong
        bot Telegram để đăng nhập MeoChat.
      </p>
      <p className="mt-3 text-sm text-[var(--text-muted)]">
        Muốn đăng nhập bằng Chrome hoặc Safari: sao chép liên kết trong tin nhắn rồi dán vào trình
        duyệt đó. Bấm thẳng trong Telegram sẽ mở bằng trình duyệt của Telegram, và phiên đăng nhập
        chỉ nằm trong đó.
      </p>
      <p className="mt-6 text-xs text-[var(--text-muted)]">
        MeoChat chỉ gửi liên kết trong tin nhắn riêng, và không gửi cho ai khác ngoài bạn.
      </p>
    </div>
  );
}
