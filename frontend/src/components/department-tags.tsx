import type { MemberSeat } from "@/lib/api";

/** What a chip needs: the department's name and the seat's position. */
export type SeatChip = Pick<MemberSeat, "name" | "position"> &
  Partial<Pick<MemberSeat, "department_id" | "code" | "position_label">>;

/** A seat's position is "HEAD" / "MEMBER"; anything else is read as a member. */
export const isHeadSeat = (seat: Pick<SeatChip, "position">) => seat.position === "HEAD";

/**
 * Department chips on a person (2026-10-17): one per seat, the head's with a
 * star. This is what every roster shows instead of the PR / ORD stream tags:
 * the streams follow the seats and are not a thing people are "tagged" into
 * any more. Heads first, then the departments' own order.
 */
export function DepartmentTags({
  seats,
  empty = "Chưa có phòng ban",
  className = "",
}: {
  seats: readonly SeatChip[] | null | undefined;
  /** Drawn, muted, when there is no seat; `null` draws nothing. */
  empty?: string | null;
  className?: string;
}) {
  const list = [...(seats ?? [])].sort(
    (a, b) => Number(isHeadSeat(b)) - Number(isHeadSeat(a)),
  );
  if (list.length === 0) {
    return empty ? (
      <span className={`text-xs text-[var(--text-muted)] ${className}`}>{empty}</span>
    ) : null;
  }
  return (
    <span className={`inline-flex flex-wrap gap-1 ${className}`}>
      {list.map((seat) => (
        <DepartmentTag key={seat.department_id ?? seat.code ?? seat.name} seat={seat} />
      ))}
    </span>
  );
}

/** 2026-10-31: the PR seeding group's member wears a "Seeding" tag beside
 * their departments. */
export function SeedingTag() {
  return (
    <span className="dept-tag" data-kind="seeding" title="Thành viên nhóm Seeding">
      Seeding
    </span>
  );
}

/** One chip: "<tên phòng>", plus ★ ("Trưởng phòng") for the head. */
export function DepartmentTag({ seat }: { seat: SeatChip }) {
  const head = isHeadSeat(seat);
  const title = head ? `Trưởng phòng ${seat.name}` : seat.name;
  return (
    <span className="dept-tag" data-position={head ? "HEAD" : "MEMBER"} title={title}>
      {seat.name}
      {head ? (
        <span className="dept-star" role="img" aria-label="Trưởng phòng">
          ★
        </span>
      ) : null}
    </span>
  );
}
