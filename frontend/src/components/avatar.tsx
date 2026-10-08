"use client";

import { useEffect, useState } from "react";

/**
 * A person's picture: the uploaded avatar when there is one, else their
 * initials on a colour derived from the name.
 *
 * The colour is a hue picked by hashing the name, so the same person has the
 * same colour on every screen and two people side by side rarely share one.
 * The tint itself (light background + dark letters, or the reverse in dark
 * mode) lives in `globals.css` under `.avatar`, driven by `--avatar-h`, so it
 * follows the colour scheme without a class per hue.
 *
 * Decorative by default (`alt=""`): it always sits next to the name it
 * pictures. Pass `label` when it stands alone.
 *
 * A picture that fails to load (removed since the page loaded, say) falls back
 * to the initials rather than a broken-image icon.
 */
export function Avatar({
  name,
  src,
  size = 32,
  label,
  className = "",
}: {
  name: string;
  src?: string | null;
  /** Diameter in CSS pixels. 24 top bar, 32 tables, 64 settings, 96 profile. */
  size?: number;
  /** An accessible name, for an avatar shown without its name beside it. */
  label?: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  // A new picture gets a new chance to load.
  useEffect(() => setFailed(false), [src]);
  const showImage = Boolean(src) && !failed;

  return (
    <span
      data-avatar=""
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={`avatar ${className}`}
      style={
        {
          width: size,
          height: size,
          fontSize: Math.max(9, Math.round(size * 0.38)),
          "--avatar-h": String(avatarHue(name)),
        } as React.CSSProperties
      }
    >
      {showImage ? (
        // A plain <img>: the picture is a same-origin API response with its own
        // cache headers, and next/image would only re-encode a 256px file.
        <img
          src={src ?? undefined}
          alt=""
          width={size}
          height={size}
          draggable={false}
          onError={() => setFailed(true)}
        />
      ) : (
        initials(name)
      )}
    </span>
  );
}

/** "Tuấn Anh Lê" -> "TL": first and last word. "?" for an empty name. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  const first = Array.from(words[0])[0] ?? "";
  const last = words.length > 1 ? (Array.from(words[words.length - 1])[0] ?? "") : "";
  return (first + last).toLocaleUpperCase("vi-VN");
}

/**
 * Hues that read well both as a pale tint with dark letters and as a deep tint
 * with light letters. Muddy ones (olive, brown) are left out on purpose.
 */
const HUES = [212, 232, 258, 284, 322, 350, 14, 32, 152, 168, 186, 198];

/** A stable hue for a name: the same name always lands on the same colour. */
export function avatarHue(name: string): number {
  let hash = 0;
  for (const char of name.trim().toLocaleLowerCase("vi-VN")) {
    hash = (hash * 31 + (char.codePointAt(0) ?? 0)) >>> 0;
  }
  return HUES[hash % HUES.length];
}
