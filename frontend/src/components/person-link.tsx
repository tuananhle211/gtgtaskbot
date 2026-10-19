"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import type { MouseEvent } from "react";
import { api } from "@/lib/api";

/**
 * Somebody's name, as a link to their profile page (`/people/<id>`,
 * 2026-10-19) for whoever may open one - the OWNER / ADMIN and department
 * heads (`/api/units/me` `can_view_profiles`) - and plain text for everybody
 * else, so nobody is sent to a page that refuses them.
 *
 * Inside a clickable table row the link is still a link: the row navigation
 * (`useRowNavigation`) leaves anchors alone, and the click does not bubble
 * into a row handler of its own.
 */
export function PersonLink({
  userId,
  name,
  className = "",
}: {
  userId: string | null | undefined;
  name: string | null | undefined;
  className?: string;
}) {
  const me = useQuery({ queryKey: ["units", "me"], queryFn: api.unitsMe, retry: false });
  if (!name) return null;
  if (!userId || !me.data?.can_view_profiles) {
    return <span className={className}>{name}</span>;
  }
  return (
    <Link
      href={`/people/${userId}`}
      onClick={(event: MouseEvent) => event.stopPropagation()}
      className={`${className} underline-offset-2 hover:underline focus-visible:underline`}
      title="Xem trang cá nhân"
    >
      {name}
    </Link>
  );
}
