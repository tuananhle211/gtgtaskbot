"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { LogoMark } from "@/components/logo";

const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--accent)]";

/**
 * Sign in with the Telegram numeric id and a password.
 *
 * The password goes in the POST body and nowhere else: the server answers by
 * setting the same HttpOnly session cookie the Telegram link sets, so this page
 * holds no credential once the request is sent. Every refusal reads the
 * server's own sentence - a wrong id and a wrong password look the same (401),
 * and a locked account says so (429).
 *
 * An account still on the default password (or on a temporary one from a
 * reset) is sent straight to the account screen to change it; the API refuses
 * everything else until it does.
 *
 * "Quên mật khẩu?" swaps the form for a single "ID Telegram" field. The server
 * sends a temporary password to that account's Telegram and answers every
 * request with the same sentence, which is all this page shows.
 */
export default function LoginPage() {
  const [forgot, setForgot] = useState(false);
  const [username, setUsername] = useState("");
  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
      <BrandHero />
      <div className="flex items-center justify-center px-4 py-10 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="panel p-6 sm:p-8">
            <div className="flex items-center gap-3">
              <LogoMark size={44} className="shrink-0" />
              <div className="min-w-0 leading-tight">
                <p className="brand-eyebrow">Creative Ops</p>
                <h1 className="mt-0.5 text-xl font-semibold tracking-tight">
                  {forgot ? "Quên mật khẩu" : "Đăng nhập APM Management"}
                </h1>
              </div>
            </div>

            {forgot ? (
              <ForgotPasswordForm
                initialUsername={username}
                onBack={(id) => {
                  setUsername(id);
                  setForgot(false);
                }}
              />
            ) : (
              <LoginForm
                username={username}
                onUsername={setUsername}
                onForgot={() => setForgot(true)}
              />
            )}

            <div className="mt-6 border-t border-[var(--border)] pt-4 text-sm text-[var(--text-muted)]">
              <p>
                Hoặc đăng nhập bằng Telegram: nhắn{" "}
                <code className="rounded bg-[var(--surface-muted)] px-1.5 py-0.5 text-[var(--text)]">/web</code>{" "}
                cho bot để nhận link
              </p>
              <p className="mt-2 text-xs">
                ID Telegram là dãy số của tài khoản Telegram (không phải @username).
              </p>
            </div>
          </div>
          <nav aria-label="Thông tin pháp lý" className="mt-4 flex justify-center gap-4 text-xs text-[var(--text-muted)]">
            <Link href="/terms" className="hover:underline">
              Điều khoản sử dụng
            </Link>
            <Link href="/privacy" className="hover:underline">
              Chính sách quyền riêng tư
            </Link>
          </nav>
        </div>
      </div>
    </main>
  );
}

/** The guideline's five words for what every piece of work should feel like. */
const BRAND_VALUES = ["Sạch", "Cao cấp", "Đồng bộ", "Tin cậy", "Chuẩn y khoa"];

/**
 * The navy band beside the form on wide screens: the brand's "A" peak with the
 * medical cross at its heart, and the guideline's values. Decoration only - on
 * a phone it is left out and the form is the whole page.
 */
function BrandHero() {
  return (
    <section
      aria-hidden="true"
      className="brand-rail relative hidden overflow-hidden before:hidden lg:flex lg:flex-col lg:justify-between lg:p-12 xl:p-16"
    >
      <div className="flex items-center gap-3">
        <LogoMark size={40} className="shrink-0" />
        <span className="font-display text-lg font-semibold tracking-tight">APM Management</span>
      </div>

      <svg
        viewBox="0 0 100 100"
        className="pointer-events-none absolute -right-16 top-1/2 h-[min(78vh,640px)] -translate-y-1/2 opacity-[0.16]"
        fill="none"
      >
        <defs>
          <linearGradient id="login-peak" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#afe8ff" />
            <stop offset="1" stopColor="#88afea" stopOpacity="0.2" />
          </linearGradient>
        </defs>
        <path d="M50 6 94 92H72L50 46 28 92H6z" fill="url(#login-peak)" />
        <path d="M46 58h8v8h8v8h-8v8h-8v-8h-8v-8h8z" fill="#fa9ae1" />
      </svg>

      <div className="relative max-w-md">
        <p className="brand-eyebrow !text-[var(--brand-pastel)]">Luồng PR · Luồng Order</p>
        <p className="mt-4 font-display text-4xl font-bold leading-[1.1] tracking-tight xl:text-5xl">
          Một bảng việc,
          <br />
          <span className="bg-gradient-to-r from-[#afe8ff] via-[#88afea] to-[#fa9ae1] bg-clip-text text-transparent">
            chuẩn mực đỉnh cao.
          </span>
        </p>
        <p className="mt-5 text-base leading-relaxed text-[var(--text-muted)]">
          Order, duyệt, sản xuất và báo cáo của cả đội Creative Ops - nhất quán, chuyên nghiệp,
          dễ theo dõi.
        </p>
      </div>

      <ul className="relative flex flex-wrap gap-2">
        {BRAND_VALUES.map((value) => (
          <li
            key={value}
            className="rounded-full border border-white/15 bg-white/[0.06] px-3.5 py-1.5 text-xs font-medium text-white/85 backdrop-blur"
          >
            {value}
          </li>
        ))}
      </ul>
    </section>
  );
}

const NUMERIC_ID_HINT = "ID Telegram chỉ gồm chữ số, ví dụ 123456789.";

function LoginForm({
  username,
  onUsername,
  onForgot,
}: {
  username: string;
  onUsername: (value: string) => void;
  onForgot: () => void;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [hint, setHint] = useState<string | null>(null);

  const login = useMutation({
    mutationFn: () => api.passwordLogin(username.trim(), password),
    onSuccess: (result) => {
      // A session query cached while signed out would otherwise greet the
      // next screen with "Bạn cần đăng nhập lại".
      queryClient.clear();
      router.replace(result?.must_change_password ? "/account?doi-mat-khau=1" : "/dashboard");
    },
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const id = username.trim();
    if (!/^\d+$/.test(id)) {
      setHint(NUMERIC_ID_HINT);
      return;
    }
    if (!password) {
      setHint("Nhập mật khẩu.");
      return;
    }
    setHint(null);
    login.mutate();
  };

  const failure = login.error
    ? login.error instanceof ApiError
      ? login.error.message
      : "Không kết nối được máy chủ. Bạn thử lại sau ít phút."
    : null;

  return (
    <form className="mt-6 space-y-4" onSubmit={submit} noValidate>
      <label className="block text-sm font-medium">
        ID Telegram
        <input
          name="username"
          type="text"
          inputMode="numeric"
          autoComplete="username"
          autoCapitalize="off"
          spellCheck={false}
          required
          value={username}
          onChange={(event) => onUsername(event.target.value)}
          className={`${FIELD} mt-1.5`}
        />
      </label>
      <div>
        <label htmlFor="login-password" className="block text-sm font-medium">
          Mật khẩu
        </label>
        <div className="relative mt-1.5">
          <input
            id="login-password"
            name="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className={`${FIELD} pr-16`}
          />
          <button
            type="button"
            onClick={() => setShowPassword((shown) => !shown)}
            aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
            aria-pressed={showPassword}
            className="absolute inset-y-0 right-0 min-w-14 rounded-r-lg px-3 text-xs font-medium text-[var(--accent)] hover:bg-[var(--surface-muted)]"
          >
            {showPassword ? "Ẩn" : "Hiện"}
          </button>
        </div>
      </div>

      {hint ? (
        <p role="alert" className="text-sm text-[var(--warn)]">
          {hint}
        </p>
      ) : null}
      {!hint && failure ? (
        <p
          role="alert"
          className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm font-medium"
        >
          {failure}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={login.isPending}
        className="min-h-11 w-full rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)] transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:opacity-50"
      >
        {login.isPending ? "Đang đăng nhập…" : "Đăng nhập"}
      </button>
      <p className="text-center">
        <button
          type="button"
          onClick={onForgot}
          className="min-h-11 px-2 text-sm font-medium text-[var(--accent)] hover:underline"
        >
          Quên mật khẩu?
        </button>
      </p>
    </form>
  );
}

/**
 * "Quên mật khẩu?": one field, one request, one sentence back. Whatever the
 * id, the server answers the same; the temporary password itself only ever
 * reaches that account's Telegram chat with the bot.
 */
function ForgotPasswordForm({
  initialUsername,
  onBack,
}: {
  initialUsername: string;
  onBack: (username: string) => void;
}) {
  const [username, setUsername] = useState(initialUsername);
  const [hint, setHint] = useState<string | null>(null);
  const reset = useMutation({
    mutationFn: () => api.requestPasswordReset(username.trim()),
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!/^\d+$/.test(username.trim())) {
      setHint(NUMERIC_ID_HINT);
      return;
    }
    setHint(null);
    reset.mutate();
  };

  const failure = reset.error
    ? reset.error instanceof ApiError
      ? reset.error.message
      : "Không kết nối được máy chủ. Bạn thử lại sau ít phút."
    : null;

  return (
    <form className="mt-6 space-y-4" onSubmit={submit} noValidate>
      <p className="text-sm text-[var(--text-muted)]">
        Nhập ID Telegram của bạn. APM Management sẽ gửi mật khẩu tạm vào tin nhắn riêng trên Telegram; đăng
        nhập bằng mật khẩu đó rồi đổi mật khẩu mới.
      </p>
      <label className="block text-sm font-medium">
        ID Telegram
        <input
          name="username"
          type="text"
          inputMode="numeric"
          autoComplete="username"
          autoCapitalize="off"
          spellCheck={false}
          required
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          className={`${FIELD} mt-1.5`}
        />
      </label>

      {hint ? (
        <p role="alert" className="text-sm text-[var(--warn)]">
          {hint}
        </p>
      ) : null}
      {!hint && failure ? (
        <p
          role="alert"
          className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm font-medium"
        >
          {failure}
        </p>
      ) : null}
      {reset.isSuccess && reset.data?.message ? (
        <p
          role="status"
          className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm"
        >
          {reset.data.message}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={reset.isPending}
        className="min-h-11 w-full rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)] transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:opacity-50"
      >
        {reset.isPending ? "Đang gửi…" : "Gửi mật khẩu tạm"}
      </button>
      <p className="text-center">
        <button
          type="button"
          onClick={() => onBack(username)}
          className="min-h-11 px-2 text-sm font-medium text-[var(--accent)] hover:underline"
        >
          Quay lại đăng nhập
        </button>
      </p>
    </form>
  );
}
