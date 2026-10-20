"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ResourceImportPreview } from "@/lib/api";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader, SecondaryButton } from "@/components/pr";
import { NoticeBox, Pill } from "@/components/states";

/**
 * "Nhập từ Excel" (Phòng Tài nguyên, IMPORT): two steps from one file.
 *
 * 1. **Xem trước** - the file is read, nothing is written; every row is
 *    listed with its errors (a name that matches no account, a duplicate id,
 *    a missing required field, a value outside the pick-lists);
 * 2. **Ghi** - only when no row has an error, everything is written at once:
 *    old codes kept, the holder as the first handover, the month columns as
 *    the status history. With errors, the file is fixed and uploaded again -
 *    nothing is corrected on screen.
 */
export default function ImportPage() {
  const queryClient = useQueryClient();
  const inputId = useId();
  const [file, setFile] = useState<{ name: string; data: string } | null>(null);
  const [preview, setPreview] = useState<ResourceImportPreview | null>(null);
  // What the file needs and the system lacks may be created on the way in.
  const [createOptions, setCreateOptions] = useState(true);
  const read = useMutation({
    mutationFn: (body: { filename: string; data: string; create_options: boolean }) =>
      api.resourceImportPreview(body),
    onSuccess: setPreview,
  });
  const write = useMutation({
    mutationFn: () =>
      api.resourceImportCommit({
        filename: file?.name ?? "",
        data: file?.data ?? "",
        create_options: createOptions,
      }),
    onSuccess: (result) => {
      setPreview(result);
      void queryClient.invalidateQueries({ queryKey: ["resources"] });
    },
  });

  const pick = (chosen: File | null) => {
    setPreview(null);
    read.reset();
    write.reset();
    if (!chosen) {
      setFile(null);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const data = result.slice(result.indexOf(",") + 1);
      setFile({ name: chosen.name, data });
      read.mutate({ filename: chosen.name, data, create_options: createOptions });
    };
    reader.readAsDataURL(chosen);
  };

  const written = preview?.written === true;
  return (
    <div className="space-y-4">
      <PageHeader
        title="Nhập từ Excel"
        subtitle="File kiểm kê (Apexmed_KhaiBaoTaiNguyen): mỗi tab một ban, tự nhận dòng tiêu đề. Xem trước, báo lỗi từng dòng, rồi mới ghi."
      />
      <div className="panel space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <label htmlFor={inputId} className="block text-sm font-medium">
            Chọn file .xlsx
          </label>
          <a
            href="/api/resources/import/template.xlsx"
            download
            className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm font-medium hover:bg-[var(--surface-muted)]"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 4v11M7 10l5 5 5-5M4 19h16" />
            </svg>
            Tải file mẫu
          </a>
        </div>
        <p className="text-xs text-[var(--text-muted)]">
          File mẫu chỉ có một sheet, mỗi dòng một tài nguyên: Mã NV (bắt buộc), Loại, ID, Tên, Trạng thái, Link,
          Ghi chú. Ban của tài nguyên lấy theo người giữ. Hai dòng ví dụ tự bỏ qua khi nhập.
        </p>
        <input
          id={inputId}
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={(event) => pick(event.target.files?.[0] ?? null)}
          className="block text-sm"
        />
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={createOptions}
              onChange={(event) => {
                setCreateOptions(event.target.checked);
                if (file) read.mutate({ filename: file.name, data: file.data, create_options: event.target.checked });
              }}
            />
            Tự thêm lựa chọn còn thiếu (loại, quyền, nguồn gốc…)
          </label>
        </div>
        <ul className="list-disc space-y-1 pl-5 text-xs text-[var(--text-muted)]">
          <li>File mẫu không có cột ban: ban lấy theo ban của người giữ (Mã NV). Người ở nhiều ban thì ưu tiên ADS, Dựng, Thiết kế, Biên tập, còn lại lấy ban đứng trước. Thiếu Mã NV, mã lạ hoặc người chưa thuộc ban nào thì dòng báo lỗi. File kiểm kê cũ (mỗi tab một ban) vẫn nhập được, ban theo tên tab.</li>
          <li>Mã tài nguyên trong file được giữ nguyên; dòng không có mã, hoặc trùng mã với dòng trước trong file, được cấp mã mới.</li>
          <li>Dòng thiếu ID, loại hoặc tên vẫn được nhập: ID = mã tài nguyên, loại tạm là &quot;Khác&quot;, tên tạm ghép từ loại + ID. Bổ sung sau trên màn hình.</li>
          <li>Người được bàn giao khớp theo cột &quot;Mã NV&quot; = mã thành viên của tài khoản; cột tên chỉ để đọc. Mã NV chưa có trong hệ thống thì dòng báo đỏ và không ghi được: nhờ Chủ sở hữu / Quản trị viên gán mã thành viên cho tài khoản trước, rồi tải lại file. Giá trị danh sách (loại, quyền, trạng thái…) phải có trong Cài đặt.</li>
          <li>Mỗi ô tháng (T10/2026…) có giá trị = một dòng lịch sử trạng thái; ô cuối cùng là trạng thái hiện tại.</li>
          <li>Có lỗi thì sửa trong file rồi tải lại - không sửa trên web.</li>
        </ul>
        {read.isPending ? <p className="text-sm text-[var(--text-muted)]">Đang đọc file…</p> : null}
        {read.isError ? <NoticeBox error={read.error} onDismiss={() => read.reset()} /> : null}
      </div>

      {preview ? (
        <>
          <section aria-label="Kết quả đọc file" className="panel p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold">
                  {written ? "Đã ghi" : "Xem trước"} · {file?.name}
                </h2>
                <p className="mt-0.5 text-sm">
                  <Pill tone={preview.error_count ? "bad" : "good"}>
                    {preview.error_count} dòng lỗi
                  </Pill>{" "}
                  <Pill tone="neutral">{preview.ok_count} dòng hợp lệ</Pill>
                </p>
              </div>
              {written ? (
                <Link
                  href="/resources"
                  className="inline-flex min-h-10 items-center rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)]"
                >
                  Mở danh sách tài nguyên
                </Link>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <SecondaryButton type="button" onClick={() => pick(null)}>
                    Chọn file khác
                  </SecondaryButton>
                  <ConfirmButton
                    spec={{
                      title: `Ghi ${preview.ok_count} tài nguyên từ ${file?.name ?? "file"}?`,
                      description:
                        "Tất cả các dòng được ghi cùng lúc: mã cũ giữ nguyên, người được bàn giao thành dòng bàn giao đầu tiên, các cột tháng thành lịch sử trạng thái. Không ghi được một nửa.",
                      confirmLabel: "Ghi vào hệ thống",
                      count: preview.ok_count > 1 ? preview.ok_count : undefined,
                    }}
                    onConfirm={() => write.mutate()}
                    pending={write.isPending}
                    error={write.error}
                    disabled={preview.error_count > 0 || preview.ok_count === 0}
                    tone="primary"
                    onOpenChange={(open) => {
                      if (open) write.reset();
                    }}
                  >
                    Ghi vào hệ thống
                  </ConfirmButton>
                </div>
              )}
            </div>
            {preview.error_count > 0 && !written ? (
              <p className="mt-2 text-sm text-[var(--bad)]">
                Còn dòng lỗi: sửa trong file Excel rồi chọn lại file. Chưa ghi gì.
              </p>
            ) : null}
            {write.isError ? (
              <div className="mt-2">
                <NoticeBox error={write.error} onDismiss={() => write.reset()} />
              </div>
            ) : null}
            {(preview.missing_options?.length ?? 0) > 0 ? (
              <p className="mt-2 text-sm">
                <span className="font-medium">Lựa chọn chưa có ({preview.missing_options?.length}):</span>{" "}
                {preview.missing_options?.map((item) => `${item.name} (${item.label})`).join(", ")}
                {createOptions ? " — sẽ được thêm khi ghi." : " — bật ô tự thêm hoặc thêm ở Cài đặt."}
              </p>
            ) : null}
            {(preview.skipped_rows ?? 0) > 0 ? (
              <p className="mt-2 text-xs text-[var(--text-muted)]">
                Bỏ qua {preview.skipped_rows} dòng mẫu (dòng ví dụ của file mẫu, hoặc chỉ có mã chưa khai gì).
              </p>
            ) : null}
            <ul className="mt-3 flex flex-wrap gap-2 text-xs">
              {preview.sheets.map((sheet) => (
                <li
                  key={sheet.name}
                  className="rounded-lg border border-[var(--border)] px-2.5 py-1.5"
                  title={sheet.unknown_headers.length ? `Cột không nhận ra: ${sheet.unknown_headers.join(", ")}` : undefined}
                >
                  <span className="font-medium">{sheet.name}</span>
                  {sheet.skipped ? (
                    <span className="text-[var(--text-muted)]"> · bỏ qua</span>
                  ) : (
                    <span className="text-[var(--text-muted)]">
                      {" "}
                      → {sheet.department?.name ?? "không khớp ban"} · {sheet.rows} dòng
                      {sheet.errors ? ` · ${sheet.errors} lỗi` : ""}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </section>

          <section aria-label="Các dòng" className="panel">
            <div className="table-scroll-x">
              <table className="table-dense min-w-[60rem]">
                <thead>
                  <tr>
                    <th scope="col">Tab · dòng</th>
                    <th scope="col">Mã</th>
                    <th scope="col">Loại</th>
                    <th scope="col">ID</th>
                    <th scope="col">Tên</th>
                    <th scope="col">Người được bàn giao</th>
                    <th scope="col">Trạng thái (tháng cuối)</th>
                    <th scope="col">Lỗi / lưu ý</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((row) => (
                    <tr
                      key={`${row.sheet}:${row.row_no}`}
                      data-tone={row.errors.length ? "bad" : undefined}
                      className={row.errors.length ? "bg-red-500/10" : ""}
                    >
                      <td className="whitespace-nowrap text-xs">
                        {row.sheet} · {row.row_no}
                      </td>
                      <td className="font-mono text-xs">{row.code ?? <span className="text-[var(--text-muted)]">cấp mới</span>}</td>
                      <td>{row.raw.type ?? ""}</td>
                      <td className="font-mono text-xs">{row.raw.external_id ?? ""}</td>
                      <td className="max-w-[16rem] truncate" title={row.raw.name}>
                        {row.raw.name ?? ""}
                      </td>
                      <td>{row.raw.holder ?? ""}</td>
                      <td className="text-xs">
                        {row.months.length ? row.months[row.months.length - 1].status : (row.raw.status ?? "")}
                        {row.months.length > 1 ? (
                          <span className="text-[var(--text-muted)]"> · {row.months.length} tháng</span>
                        ) : null}
                      </td>
                      <td className="max-w-[24rem] text-xs">
                        {row.errors.length ? (
                          <ul className="list-disc pl-4 text-[var(--bad)]">
                            {row.errors.map((error) => (
                              <li key={error}>{error}</li>
                            ))}
                          </ul>
                        ) : null}
                        {row.warnings.length ? (
                          <ul className="list-disc pl-4 text-[var(--warn)]">
                            {row.warnings.map((warning) => (
                              <li key={warning}>{warning}</li>
                            ))}
                          </ul>
                        ) : null}
                        {!row.errors.length && !row.warnings.length ? (
                          <span className="text-[var(--good)]">Hợp lệ</span>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
