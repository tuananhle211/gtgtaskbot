import { describe, expect, it } from "vitest";
import {
  formatDate,
  formatDateTime,
  formatShortDate,
  formatShortDateTime,
} from "@/lib/datetime";

describe("the app's one date format (dd/mm/yyyy HH:mm, 24h, Vietnam time)", () => {
  it("writes the day first, the year in full and a 24-hour clock", () => {
    expect(formatDateTime("2026-10-12T06:05:00Z")).toBe("12/10/2026 13:05");
    // Past midnight in Vietnam: the next day.
    expect(formatDateTime("2026-10-12T20:30:00+00:00")).toBe("13/10/2026 03:30");
    expect(formatDateTime("2026-12-31T17:00:00Z")).toBe("01/01/2027 00:00");
  });

  it("keeps a bare day a calendar day", () => {
    expect(formatDate("2026-10-05")).toBe("05/10/2026");
    expect(formatDateTime("2026-10-05")).toBe("05/10/2026");
    expect(formatDate("2026-10-12T20:30:00Z")).toBe("13/10/2026");
  });

  it("says so when there is nothing", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDate("")).toBe("—");
    expect(formatDateTime("không phải ngày")).toBe("không phải ngày");
  });
});

describe("the short forms for table cells", () => {
  it("drops the year: dd/mm HH:mm and dd/mm", () => {
    expect(formatShortDateTime("2026-10-22T08:40:00Z")).toBe("22/10 15:40");
    expect(formatShortDate("2026-10-22T08:40:00Z")).toBe("22/10");
    expect(formatShortDateTime(null)).toBe("—");
  });
});
