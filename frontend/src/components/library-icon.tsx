"use client";

import { avatarHue } from "@/components/avatar";

/**
 * The round chip that leads an entry of the Design team's "Thư viện": what the
 * Leader typed as its icon (an emoji, a letter or two), else the name's
 * initials.
 *
 * The colour is the avatar's: a hue hashed from the entry's name
 * (`avatarHue`), tinted by the `.avatar` rule in `globals.css`, so it follows
 * the light / dark scheme and the same entry has the same colour in the
 * sidebar and on the screen that manages it. Decorative: it always sits next
 * to the name it stands for.
 */

/** The longest icon the server keeps. */
export const LIBRARY_ICON_MAX = 8;
export const LIBRARY_NAME_MAX = 80;
/** How many entries the library may hold. */
export const LIBRARY_MAX_LINKS = 30;

/**
 * Whether a library link may be drawn (and saved): `http://` or `https://`
 * only. Anything else - `javascript:`, `data:`, a bare path - is never made
 * into an anchor, whatever the server sent.
 */
export function isLibraryUrl(url: string | null | undefined): boolean {
  return /^https?:\/\/\S+$/i.test((url ?? "").trim());
}

/** "Brand Guideline" -> "BG", "Nhập môn" -> "NM": the first two words' first letters. */
export function libraryInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean).slice(0, 2);
  if (words.length === 0) return "?";
  return words
    .map((word) => Array.from(word)[0] ?? "")
    .join("")
    .toLocaleUpperCase("vi-VN");
}

/** What the chip shows: the chosen icon, else the name's initials. */
export function libraryIconText(name: string, icon: string | null | undefined): string {
  const chosen = (icon ?? "").trim();
  return chosen || libraryInitials(name);
}

export function LibraryIcon({
  name,
  icon,
  size = 22,
  className = "",
}: {
  name: string;
  icon?: string | null;
  /** Diameter in CSS pixels. */
  size?: number;
  className?: string;
}) {
  const text = libraryIconText(name, icon);
  // One or two letters fill the chip; anything longer is drawn smaller.
  const length = Array.from(text).length;
  const scale = length <= 2 ? 0.44 : length === 3 ? 0.34 : 0.26;
  return (
    <span
      data-library-icon=""
      aria-hidden="true"
      className={`avatar shrink-0 ${className}`}
      style={
        {
          width: size,
          height: size,
          fontSize: Math.max(8, Math.round(size * scale)),
          "--avatar-h": String(avatarHue(name)),
        } as React.CSSProperties
      }
    >
      {text}
    </span>
  );
}
