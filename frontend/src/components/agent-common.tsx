"use client";

import Link from "next/link";
import type { AgentInfo } from "@/lib/api";

/** Shared by "Chat riêng" (0091) and "Phòng họp" (0093). */

const AVATAR_SIZE = { sm: "h-8 w-8", md: "h-10 w-10", lg: "h-14 w-14" } as const;
/** An agent with no picture of its own (a new profile) gets this one. */
export const DEFAULT_AGENT_AVATAR = "/agent-avatars/default.svg";

/** The picture of an agent: `public/agent-avatars/<slug>.svg`. */
export function agentAvatarUrl(slug: string | undefined): string {
  return slug && /^[a-z0-9]{1,40}$/.test(slug) ? `/agent-avatars/${slug}.svg` : DEFAULT_AGENT_AVATAR;
}

/**
 * An agent's avatar (2026-11-06, the user's pick "Kiểu F"): a robot from DiceBear's
 * "Bottts" (Pablo Stanley, free for commercial use), one per agent - seed `apm-<slug>`,
 * on a soft disc in its group's colour (Ban Giám đốc blue, Marketing pink,
 * Kinh doanh green, Hỗ trợ violet), saved as static files under
 * `public/agent-avatars/`. Decorative: the name always sits beside it.
 */
export function Badge({
  agent,
  size = "md",
}: {
  agent: Pick<AgentInfo, "badge" | "name"> & { slug?: string };
  size?: "sm" | "md" | "lg";
}) {
  return (
    <span
      aria-hidden
      data-agent-avatar
      className={`inline-flex ${AVATAR_SIZE[size]} shrink-0 overflow-hidden rounded-full bg-[var(--surface-muted)] ring-1 ring-[var(--border)]`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- a tiny static SVG, no optimiser needed */}
      <img
        src={agentAvatarUrl(agent.slug)}
        alt=""
        width={56}
        height={56}
        loading="lazy"
        decoding="async"
        className="h-full w-full"
        onError={(event) => {
          const img = event.currentTarget;
          if (!img.src.endsWith(DEFAULT_AGENT_AVATAR)) img.src = DEFAULT_AGENT_AVATAR;
        }}
      />
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
