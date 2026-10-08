"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import type { UnitsMe } from "@/lib/api";
import {
  currentUnit,
  isUntagged,
  unitChoices,
  unitShortLabel,
  unitTagClass,
} from "@/lib/units";

/**
 * The stream switch ("Luồng PR" / "Luồng Order (ORD)" / "Tất cả"), set beside
 * a page title ("Quản lý task", "Dashboard"). The stream lives in `?unit=`,
 * so the switch is links. It lists only the streams the person is tagged in,
 * and "Tất cả" only for somebody who sees every stream (OWNER, ADMIN); a
 * person in one stream sees the plain stream chip instead, and an untagged
 * person sees nothing.
 */
export function UnitSwitch({ me }: { me: UnitsMe | undefined }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const unit = currentUnit(params, me);
  const choices = unitChoices(me);
  if (isUntagged(me)) return null;
  if (choices.length <= 1) {
    const short = me?.units.find((item) => item.code === unit)?.short_label;
    return (
      <span className={`unit-tag align-middle ${unitTagClass(unit)}`}>
        {unitShortLabel(unit, short)}
      </span>
    );
  }
  return (
    <nav aria-label="Chọn luồng" className="unit-switch align-middle">
      {choices.map((choice) => (
        <Link
          key={choice.value}
          href={`${pathname}?unit=${choice.value}`}
          data-unit={choice.value}
          aria-current={unit === choice.value ? "true" : undefined}
        >
          {choice.label}
        </Link>
      ))}
    </nav>
  );
}

/**
 * What an account with no stream sees on the shared screens. Nothing to show
 * yet - the server answers 404 for every stream - so one friendly sentence
 * instead of a page of refusals.
 */
export function UntaggedState({ title }: { title: string }) {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
      <section
        role="status"
        aria-label="Chưa có luồng"
        className="panel flex flex-col items-start gap-2 p-6"
      >
        <p className="text-base font-semibold">
          Tài khoản chưa được gắn luồng. Trưởng nhóm sẽ gắn luồng cho bạn.
        </p>
        <p className="text-sm text-[var(--text-muted)]">
          Sau khi được gắn, task và số liệu của luồng sẽ hiện ở đây.
        </p>
      </section>
    </div>
  );
}
