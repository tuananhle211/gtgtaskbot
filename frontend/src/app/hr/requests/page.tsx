"use client";

/**
 * "Duyệt hồ sơ" (0096, Q13): the staff's own contact changes waiting for HCNS
 * - old value -> new value, approve (the profile changes) or refuse with a reason.
 */

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { hrApi, type HrRequest } from "@/lib/hr";
import { formatDateTime } from "@/lib/datetime";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader, TabStrip } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";

function Row({ item, decide }: { item: HrRequest; decide: boolean }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const run = useMutation({
    mutationFn: (approve: boolean) => hrApi.decide(item.id, approve, note || undefined),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["hr"] });
      void queryClient.invalidateQueries({ queryKey: ["units", "me"] });
    },
  });
  const tone = item.status === "APPROVED" ? "good" : item.status === "PENDING" ? "warn" : "neutral";
  return (
    <tr>
      <td>
        <Link href={`/hr/profiles/${item.user_id}`} className="font-medium hover:underline">{item.full_name}</Link>
        <span className="block font-mono text-xs text-[var(--text-muted)]">{item.member_code}</span>
      </td>
      <td>{item.field_label}</td>
      <td className="text-[var(--text-muted)] line-through">{item.old_value || "—"}</td>
      <td className="font-medium">{item.new_value || "—"}</td>
      <td className="whitespace-nowrap text-xs">{formatDateTime(item.created_at)}</td>
      <td>
        <Pill tone={tone}>{item.status_label}</Pill>
        {item.note ? <span className="mt-1 block text-xs text-[var(--text-muted)]">Lý do: {item.note}</span> : null}
      </td>
      {decide ? (
        <td className="whitespace-nowrap">
          {item.status === "PENDING" ? (
            <div className="flex flex-wrap gap-2">
              <ConfirmButton
                spec={{
                  title: `Duyệt thay đổi ${item.field_label.toLowerCase()} của ${item.full_name}?`,
                  description: `Hồ sơ đổi thành "${item.new_value ?? ""}".`,
                  confirmLabel: "Duyệt",
                }}
                onConfirm={() => run.mutate(true)}
                pending={run.isPending}
                error={run.error}
                tone="primary"
              >
                Duyệt
              </ConfirmButton>
              <ConfirmButton
                spec={{
                  title: `Từ chối yêu cầu của ${item.full_name}?`,
                  description: "Hồ sơ giữ nguyên; người gửi thấy lý do.",
                  confirmLabel: "Từ chối",
                  variant: "destructive",
                  details: (
                    <label className="block text-xs text-[var(--text-muted)]">
                      Lý do (bắt buộc)
                      <textarea
                        value={note}
                        onChange={(event) => setNote(event.target.value)}
                        rows={2}
                        className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2 text-sm"
                      />
                    </label>
                  ),
                }}
                confirmDisabled={!note.trim()}
                onConfirm={() => run.mutate(false)}
                pending={run.isPending}
                error={run.error}
                tone="danger"
              >
                Từ chối
              </ConfirmButton>
            </div>
          ) : (
            "—"
          )}
        </td>
      ) : null}
    </tr>
  );
}

export default function HrRequestsPage() {
  const [tab, setTab] = useState("PENDING");
  const list = useQuery({
    queryKey: ["hr", "requests", tab],
    queryFn: () => hrApi.requests(tab === "ALL" ? undefined : tab),
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Duyệt hồ sơ"
        subtitle="Nhân viên tự cập nhật SĐT, email, địa chỉ trên trang của mình; thay đổi chỉ ghi vào hồ sơ sau khi phòng Hành chính nhân sự duyệt."
      />
      <TabStrip
        tabs={[
          { key: "PENDING", label: "Chờ duyệt" },
          { key: "APPROVED", label: "Đã duyệt" },
          { key: "REJECTED", label: "Từ chối" },
          { key: "ALL", label: "Tất cả" },
        ]}
        active={tab}
        onSelect={setTab}
        label="Lọc yêu cầu"
      />
      {list.isPending ? (
        <Loading label="Đang tải yêu cầu…" />
      ) : list.isError ? (
        <ErrorBox error={list.error} onRetry={() => list.refetch()} />
      ) : list.data.items.length === 0 ? (
        <p className="panel p-4 text-sm text-[var(--text-muted)]">Không có yêu cầu nào.</p>
      ) : (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[52rem] [&_td]:align-middle">
            <thead>
              <tr>
                <th>Nhân viên</th>
                <th>Trường</th>
                <th>Giá trị cũ</th>
                <th>Giá trị mới</th>
                <th>Ngày gửi</th>
                <th>Trạng thái</th>
                {list.data.can_decide ? <th>Thao tác</th> : null}
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((item) => (
                <Row key={item.id} item={item} decide={list.data.can_decide} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {list.isError ? <NoticeBox error={list.error} /> : null}
    </div>
  );
}
