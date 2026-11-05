"use client";

import Link from "next/link";
import type { AgentInfo } from "@/lib/api";

/** Shared by "Chat riêng" (0091) and "Phòng họp" (0093). */

export function Badge({ agent, size = "md" }: { agent: Pick<AgentInfo, "badge" | "name">; size?: "sm" | "md" }) {
  const box = size === "sm" ? "h-8 w-8 text-[10px]" : "h-10 w-10 text-xs";
  return (
    <span
      aria-hidden
      className={`inline-flex ${box} shrink-0 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--accent-soft)] font-semibold tracking-wide text-[var(--accent-strong)]`}
    >
      {agent.badge || agent.name.slice(0, 2).toUpperCase()}
    </span>
  );
}

/** "Chat riêng" / "Phòng họp" on top of both agent screens. */
export function AgentTabs({ current }: { current: "chat" | "rooms" }) {
  const tab = (key: "chat" | "rooms", href: string, label: string) => (
    <Link
      href={href}
      aria-current={current === key ? "page" : undefined}
      className={`inline-flex min-h-10 items-center rounded-lg px-3 text-sm transition-colors ${
        current === key
          ? "bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
          : "text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]"
      }`}
    >
      {label}
    </Link>
  );
  return (
    <nav aria-label="Đội agent" className="flex gap-1 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-1">
      {tab("chat", "/agents", "Chat riêng")}
      {tab("rooms", "/agents/rooms", "Phòng họp")}
    </nav>
  );
}
