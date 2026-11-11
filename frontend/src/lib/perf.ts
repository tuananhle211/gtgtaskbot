/** Quản trị › Hiệu năng (0103): a duration the Vietnamese way - "120 ms", "1,2 s", "2,5 phút". */
export function formatMs(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} s`;
  return `${(ms / 60_000).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} phút`;
}
