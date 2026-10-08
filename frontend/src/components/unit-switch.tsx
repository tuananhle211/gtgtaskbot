"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import type { UnitsMe } from "@/lib/api";
import { currentUnit, unitChoices } from "@/lib/units";

/**
 * The PR / Ads / "Tất cả" switch, set beside a page title ("Quản lý task",
 * "Dashboard"). The unit lives in `?unit=`, so the switch is links; a person
 * tagged into one unit only sees the plain unit tag instead.
 */
export function UnitSwitch({ me }: { me: UnitsMe | undefined }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const unit = currentUnit(params, me);
  const choices = unitChoices(me);
  if (choices.length <= 1) {
    return (
      <span className={`unit-tag align-middle ${unit === "ADS" ? "unit-tag-ads" : "unit-tag-pr"}`}>
        {unit === "ALL" ? "PR + ADS" : unit}
      </span>
    );
  }
  return (
    <nav aria-label="Chọn ban" className="unit-switch align-middle">
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
