"use client";

import { useEffect, useState } from "react";
import { type Theme } from "@/lib/theme";

/** What the page shows now: the saved choice, else the system's. */
function currentTheme(): Theme {
  const saved = document.documentElement.dataset.theme;
  if (saved === "light" || saved === "dark") return saved;
  return typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/**
 * The header switch between light and dark mode. Flips `<html data-theme>`
 * at once and asks the server to remember it, so every later visit opens in
 * the same mode.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null);
  useEffect(() => setTheme(currentTheme()), []);

  const dark = theme === "dark";
  const flip = () => {
    const next: Theme = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    void fetch("/theme", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ theme: next }),
    }).catch(() => undefined);
  };

  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      aria-label="Chế độ tối"
      title={dark ? "Chuyển sang chế độ sáng" : "Chuyển sang chế độ tối"}
      onClick={flip}
      className="relative inline-flex h-8 w-14 shrink-0 items-center rounded-full border border-[var(--border)] bg-[var(--surface-muted)] px-0.5 transition-colors hover:border-[var(--accent)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
    >
      <span
        aria-hidden="true"
        className={`flex h-6 w-6 items-center justify-center rounded-full bg-[var(--surface)] text-[var(--accent-strong)] shadow-sm transition-transform ${
          dark ? "translate-x-6" : "translate-x-0"
        }`}
      >
        {dark ? (
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
          </svg>
        ) : (
          <svg
            viewBox="0 0 24 24"
            className="h-4 w-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </svg>
        )}
      </span>
    </button>
  );
}
