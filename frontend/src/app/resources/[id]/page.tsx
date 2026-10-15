"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ResourceDetail, type ResourceRow } from "@/lib/api";
import { PageHeader, PrimaryButton, SecondaryButton } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { ResourceFormDialog } from "@/components/resource-form";
import { ChangeHolderDialog, ReviewDialog, UpdateStatusDialog } from "@/components/resource-dialogs";
import {
  SECRET_WARNING,
  alertTone,
  employmentTone,
  formatResourceDay,
  formatResourceWhen,
  statusTone,
} from "@/lib/resources";

/**
 * "Hồ sơ tài nguyên": every field of the resource, its two timelines (the
 * handovers and the statuses, newest first) and the change log, with the
 * Phòng Tài nguyên's buttons: Sửa, Đổi người được bàn giao, Cập nhật trạng
 * thái, Đã rà soát. Outside one's share the server answers 404, shown as the
 * page's error.
 */
export default function ResourcePage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const queryClient = useQueryClient();
  const detail = useQuery({ queryKey: ["resource", id], queryFn: () => api.resource(id) });
  const options = useQuery({ queryKey: ["resources", "options"], queryFn: () => api.resourceOptions() });
  const [dialog, setDialog] = useState<"edit" | "holder" | "status" | "review" | null>(null);

  if (detail.isPending) return <Loading label="Đang tải hồ sơ…" />;
  if (detail.isError) return <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />;
  const data = detail.data;
  const row = data.resource;
  const saved = (next: ResourceDetail) => {
    queryClient.setQueryData(["resource", id], next);
    setDialog(null);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title={`${row.code} · ${row.name}`}
        subtitle={`${row.type.name} · ${row.department.name} · ID ${row.external_id}`}
        action={
          data.can_edit ? (
            <div className="flex flex-wrap items-center gap-2">
              <SecondaryButton type="button" onClick={() => setDialog("edit")}>
                Sửa
              </SecondaryButton>
              <SecondaryButton type="button" onClick={() => setDialog("holder")}>
                Đổi người được bàn giao
              </SecondaryButton>
              <SecondaryButton type="button" onClick={() => setDialog("status")}>
                Cập nhật trạng thái
              </SecondaryButton>
              <PrimaryButton type="button" onClick={() => setDialog("review")}>
                Đã rà soát
              </PrimaryButton>
            </div>
          ) : null
        }
      />
      <p className="text-sm">
        <Link href="/resources" className="text-[var(--accent)] underline">
          ← Danh sách tài nguyên
        </Link>
      </p>

      {row.alerts.length > 0 ? (
        <div
          role="status"
          className={`rounded-lg border p-3 text-sm ${
            row.flagged
              ? "border-red-500/40 bg-red-500/10"
              : "border-amber-500/40 bg-amber-500/10"
          }`}
        >
          <p className="font-medium">Cảnh báo</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {row.alerts.map((alert) => (
              <li key={alert.kind}>
                <Pill tone={alertTone(alert)}>{alert.label}</Pill>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {row.secret_warning ? (
        <div role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          {SECRET_WARNING}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Fields row={row} />
        <div className="space-y-4">
          <Handovers detail={data} />
          <Statuses detail={data} />
        </div>
      </div>
      <Changes detail={data} />

      {dialog === "edit" ? (
        <ResourceFormDialog open onClose={() => setDialog(null)} options={options.data} resource={row} onSaved={saved} />
      ) : null}
      {dialog === "holder" ? (
        <ChangeHolderDialog open onClose={() => setDialog(null)} resource={row} options={options.data} onSaved={saved} />
      ) : null}
      {dialog === "status" ? (
        <UpdateStatusDialog open onClose={() => setDialog(null)} resource={row} options={options.data} onSaved={saved} />
      ) : null}
      {dialog === "review" ? (
        <ReviewDialog open onClose={() => setDialog(null)} resource={row} onSaved={saved} />
      ) : null}
    </div>
  );
}

function Fields({ row }: { row: ResourceRow }) {
  const person = (value: ResourceRow["holder"], empty: string) =>
    value ? (
      <>
        <span className="person">{value.full_name}</span>{" "}
        <Pill tone={employmentTone(value.employment_status)}>{value.employment_status_label}</Pill>
      </>
    ) : (
      <span className="text-[var(--text-muted)]">{empty}</span>
    );
  const items: Array<[string, React.ReactNode]> = [
    ["Mã tài nguyên", <span key="code" className="font-mono font-semibold">{row.code}</span>],
    ["Loại", row.type.name],
    ["ID tài nguyên", <span key="id" className="font-mono">{row.external_id}</span>],
    ["Tên tài nguyên", row.name],
    [
      "Link",
      row.link ? (
        <a key="link" href={row.link} target="_blank" rel="noopener noreferrer" className="text-[var(--accent)] underline break-all">
          {row.link}
        </a>
      ) : (
        "—"
      ),
    ],
    ["Ban quản lý", row.department.name],
    ["Người được bàn giao", person(row.holder, "Phòng Tài nguyên giữ")],
    ["Quyền đang có", row.role?.name ?? "—"],
    ["Email đăng ký", row.email ?? "—"],
    ["SĐT đăng ký", <span key="phone" className="font-mono">{row.phone ?? "—"}</span>],
    ["Bảo mật 2 lớp", row.two_factor?.name ?? "—"],
    ["Người giữ mã 2 lớp", person(row.two_factor_holder, "—")],
    ["Nguồn gốc", row.origin?.name ?? "—"],
    ["Ngày bắt đầu dùng", formatResourceDay(row.started_on)],
    ["Mục đích sử dụng", row.purpose ? <span key="purpose" className="whitespace-pre-line">{row.purpose}</span> : "—"],
    ["Bàn giao về Tài nguyên", row.handover.name],
    [
      "Trạng thái hiện tại",
      <Pill key="status" tone={statusTone(row.status.status_group)}>
        {row.status.name}
      </Pill>,
    ],
    ["Ngày rà soát gần nhất", formatResourceWhen(row.reviewed_at)],
    ["Ghi chú", row.note ? <span key="note" className="whitespace-pre-line">{row.note}</span> : "—"],
  ];
  return (
    <section aria-label="Thông tin tài nguyên" className="panel p-4">
      <h2 className="text-sm font-semibold">Thông tin</h2>
      <dl className="mt-3 grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[11rem_minmax(0,1fr)]">
        {items.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-xs text-[var(--text-muted)] sm:pt-0.5">{label}</dt>
            <dd className="min-w-0 break-words">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Who({ person }: { person: ResourceRow["holder"] }) {
  return person ? <span className="person">{person.full_name}</span> : <span>Phòng Tài nguyên</span>;
}

function Handovers({ detail }: { detail: ResourceDetail }) {
  return (
    <section aria-label="Lịch sử bàn giao" className="panel p-4">
      <h2 className="text-sm font-semibold">Lịch sử bàn giao</h2>
      {detail.handovers.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--text-muted)]">Chưa bàn giao cho ai.</p>
      ) : (
        <ol className="mt-3 space-y-3 text-sm">
          {detail.handovers.map((line) => (
            <li key={line.id} className="border-l-2 border-[var(--border)] pl-3">
              <p>
                <Who person={line.from_person} /> → <Who person={line.to_person} />{" "}
                <Pill tone={line.reason === "RETURN" ? "warn" : "neutral"}>{line.reason_label}</Pill>
              </p>
              <p className="text-xs text-[var(--text-muted)]">
                {formatResourceWhen(line.at)}
                {line.actor ? ` · ${line.actor.full_name}` : ""}
                {line.note ? ` · ${line.note}` : ""}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Statuses({ detail }: { detail: ResourceDetail }) {
  return (
    <section aria-label="Lịch sử trạng thái" className="panel p-4">
      <h2 className="text-sm font-semibold">Lịch sử trạng thái</h2>
      <ol className="mt-3 space-y-3 text-sm">
        {detail.statuses.map((line) => (
          <li key={line.id} className="border-l-2 border-[var(--border)] pl-3">
            <p>
              {line.review_only ? (
                <>
                  <Pill tone="good">Đã rà soát</Pill>{" "}
                  <span className="text-[var(--text-muted)]">giữ {line.to_status.name}</span>
                </>
              ) : (
                <>
                  {line.from_status ? (
                    <>
                      <span className="text-[var(--text-muted)]">{line.from_status.name}</span> →{" "}
                    </>
                  ) : null}
                  <Pill tone={statusTone(line.to_status.status_group)}>{line.to_status.name}</Pill>
                </>
              )}
            </p>
            <p className="text-xs text-[var(--text-muted)]">
              {formatResourceWhen(line.at)}
              {line.actor ? ` · ${line.actor.full_name}` : ""}
              {line.note && line.note !== "Đã rà soát" ? ` · ${line.note}` : ""}
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Changes({ detail }: { detail: ResourceDetail }) {
  return (
    <section aria-label="Nhật ký thay đổi" className="panel">
      <div className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-sm font-semibold">Nhật ký thay đổi</h2>
        <p className="mt-0.5 text-xs text-[var(--text-muted)]">
          Ai, lúc nào, trường nào, giá trị cũ → mới. Không ai sửa hoặc xoá được nhật ký này.
        </p>
      </div>
      <div className="table-scroll-x">
        <table className="table-dense min-w-[40rem]">
          <thead>
            <tr>
              <th scope="col">Lúc</th>
              <th scope="col">Ai</th>
              <th scope="col">Trường</th>
              <th scope="col">Cũ</th>
              <th scope="col">Mới</th>
            </tr>
          </thead>
          <tbody>
            {detail.changes.map((line) => (
              <tr key={line.id}>
                <td className="whitespace-nowrap text-xs tabular-nums">{formatResourceWhen(line.at)}</td>
                <td className="whitespace-nowrap">{line.actor?.full_name ?? "—"}</td>
                <td className="whitespace-nowrap">{line.field_label}</td>
                <td className="max-w-[16rem] break-words text-[var(--text-muted)]">{line.old_value ?? "—"}</td>
                <td className="max-w-[16rem] break-words">{line.new_value ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
