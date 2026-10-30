"use client";

import { useId, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type AdsCrmAccount,
  type AdsCrmAccountUpdate,
  type AdsCrmOverview,
} from "@/lib/api";
import { money } from "@/components/ads";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import { formatDate, formatDateTime } from "@/lib/datetime";

/**
 * "Phòng Ads › Tài khoản CRM" (0083): the CRM's marketing accounts and whose
 * thực thu each is. A person holds several (their HCM accounts fill "Doanh số
 * SG", their HN ones "Doanh số HN"); an account can instead feed a ◆ source,
 * or count for nobody. The region comes from the account name ("HN …", "SG
 * …" / "HCM …") and can be changed. "Nhập từ file Excel" reads the "Gắn mã"
 * workbook; "Đồng bộ ngay" reads the CRM again (it runs every hour anyway).
 * A Trưởng phòng ADS, the OWNER / an ADMIN.
 */
export default function AdsCrmPage() {
  const [month, setMonth] = useState("");
  const crm = useQuery({
    queryKey: ["ads", "crm", month],
    queryFn: () => api.adsCrm(month || undefined),
    retry: false,
  });
  return (
    <div className="space-y-5">
      <PageHeader
        title="Tài khoản CRM"
        subtitle="Doanh thu lấy từ CRM là thực thu (khách đã trả), tính theo ngày thanh toán. Một người có nhiều tài khoản CRM: tổng các tài khoản là doanh thu của người đó."
      />
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex w-fit flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
          <span>Tháng</span>
          <input
            type="month"
            aria-label="Tháng"
            value={month || crm.data?.month || ""}
            onChange={(event) => setMonth(event.target.value)}
            className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
          />
        </label>
      </div>
      {crm.isPending ? <Loading label="Đang tải tài khoản CRM…" /> : null}
      {crm.isError ? (
        <ErrorBox error={crm.error} onRetry={() => crm.refetch()} />
      ) : null}
      {crm.data ? <Workspace data={crm.data} month={month} /> : null}
    </div>
  );
}

function Status({ data }: { data: AdsCrmOverview }) {
  const failed = data.last_status === "FAILED";
  return (
    <section
      aria-label="Trạng thái đồng bộ"
      className="panel flex flex-wrap items-start justify-between gap-4 p-4"
    >
      <div className="space-y-1 text-sm">
        {!data.configured ? (
          <p className="font-semibold text-[var(--bad)]">
            Máy chủ chưa có CRM_API_KEY: chưa lấy được doanh thu từ CRM.
          </p>
        ) : null}
        <p>
          <span className="text-[var(--text-muted)]">Đã lấy dữ liệu: </span>
          {data.synced_from && data.synced_to ? (
            <span className="font-semibold">
              {formatDate(data.synced_from)} – {formatDate(data.synced_to)}
            </span>
          ) : (
            <span className="font-semibold">chưa đồng bộ lần nào</span>
          )}
        </p>
        {data.last_sync_at ? (
          <p className="text-[var(--text-muted)]">
            Lần gần nhất {formatDateTime(data.last_sync_at)}
            {data.last_message ? (
              <>
                {" · "}
                <span className={failed ? "text-[var(--bad)]" : undefined}>
                  {data.last_message}
                </span>
              </>
            ) : null}
          </p>
        ) : null}
        <p className="text-xs text-[var(--text-muted)]">
          Tự đồng bộ mỗi giờ (07:10 – 23:10). Trong khoảng đã lấy, người / nguồn
          có tài khoản CRM dùng số CRM; ô nhập tay của họ bị khoá.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <ImportButton />
        <SyncButton disabled={!data.configured} />
      </div>
    </section>
  );
}

function SyncButton({ disabled }: { disabled: boolean }) {
  const queryClient = useQueryClient();
  const sync = useMutation({
    mutationFn: () => api.syncAdsCrm(),
    onSuccess: () => {
      // The run takes a few seconds in the worker.
      window.setTimeout(
        () => void queryClient.invalidateQueries({ queryKey: ["ads"] }),
        5000,
      );
    },
  });
  return (
    <ConfirmButton
      tone="primary"
      disabled={disabled}
      pending={sync.isPending}
      error={sync.error}
      onOpenChange={(open) => (open ? sync.reset() : undefined)}
      onConfirm={() => sync.mutate()}
      spec={{
        title: "Đồng bộ CRM ngay?",
        description:
          "Đọc lại thực thu từ đầu tháng (đầu tháng trước nếu đang mùng 1-5) đến hôm nay. Số trên dashboard cập nhật sau vài giây.",
        confirmLabel: "Đồng bộ ngay",
      }}
    >
      {sync.isSuccess ? "Đã gửi lệnh đồng bộ" : "Đồng bộ ngay"}
    </ConfirmButton>
  );
}

function ImportButton() {
  const queryClient = useQueryClient();
  const inputId = useId();
  const [file, setFile] = useState<{ name: string; data: string } | null>(null);
  const upload = useMutation({
    mutationFn: (data: string) => api.importAdsCrm(data),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["ads"] }),
  });
  const problems =
    upload.error instanceof ApiError &&
    Array.isArray(upload.error.details?.problems)
      ? (upload.error.details.problems as string[])
      : [];
  const pick = (chosen: File | null) => {
    upload.reset();
    if (!chosen) return setFile(null);
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      setFile({ name: chosen.name, data: result.slice(result.indexOf(",") + 1) });
    };
    reader.readAsDataURL(chosen);
  };
  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <label
          htmlFor={inputId}
          className="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-[var(--border)] bg-[var(--surface)] px-4 text-sm font-medium hover:bg-[var(--surface-muted)]"
        >
          {file ? file.name : "Chọn file gắn mã (.xlsx)"}
        </label>
        <input
          id={inputId}
          type="file"
          accept=".xlsx"
          className="sr-only"
          onChange={(event) => pick(event.target.files?.[0] ?? null)}
        />
        <ConfirmButton
          tone="secondary"
          disabled={!file}
          pending={upload.isPending}
          error={problems.length ? undefined : upload.error}
          onOpenChange={(open) => (open ? upload.reset() : undefined)}
          onConfirm={() => file && upload.mutate(file.data)}
          spec={{
            title: "Nhập mapping từ file?",
            description:
              "Mỗi dòng có CRM ID sẽ gắn theo cột “Mã nhân viên APM” hoặc “Hoặc tính vào nguồn ◆”; để trống cả hai là không tính. Có dòng lỗi thì không lưu gì.",
            confirmLabel: "Nhập file",
          }}
        >
          Nhập từ file Excel
        </ConfirmButton>
      </div>
      {upload.data ? (
        <p role="status" className="text-sm text-[var(--good)]">
          Đã đọc {upload.data.rows} dòng: {upload.data.changed} tài khoản đổi,{" "}
          {upload.data.created} tài khoản mới.
        </p>
      ) : null}
      {problems.length ? (
        <div
          role="alert"
          className="max-w-xl rounded-lg border border-[var(--bad)]/30 bg-[var(--bad-soft)] p-3 text-sm"
        >
          <p className="font-semibold">
            {upload.error instanceof Error ? upload.error.message : ""}
          </p>
          <ul className="mt-1 list-disc pl-5">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The select's value: `u:<user id>`, `s:<source>`, `former` (a marketer who
 * left, counted under the CRM name) or "" (counted for nobody).
 */
function ownerOf(account: AdsCrmAccount): string {
  if (account.user_id) return `u:${account.user_id}`;
  if (account.source) return `s:${account.source}`;
  if (account.former) return "former";
  return "";
}

function Workspace({ data, month }: { data: AdsCrmOverview; month: string }) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [only, setOnly] = useState<"all" | "loose" | "region">("all");
  const update = useMutation({
    mutationFn: ({ crmId, body }: { crmId: number; body: AdsCrmAccountUpdate }) =>
      api.updateAdsCrmAccount(crmId, body, month || undefined),
    onSuccess: (saved) => {
      queryClient.setQueryData(["ads", "crm", month], saved);
      void queryClient.invalidateQueries({ queryKey: ["ads"] });
    },
  });
  // A hotline goes where it was served (the branch): it needs no region.
  const needsRegion = (a: AdsCrmAccount) =>
    Boolean(ownerOf(a)) && !a.region && a.source !== "HOTLINE";
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return data.accounts.filter((account) => {
      if (only === "loose" && ownerOf(account)) return false;
      if (only === "region" && !needsRegion(account)) return false;
      if (!needle) return true;
      return [account.name, account.user_name, account.member_code, String(account.crm_id)]
        .filter(Boolean)
        .some((text) => String(text).toLowerCase().includes(needle));
    });
  }, [data.accounts, search, only]);
  const noRegion = data.accounts.filter(needsRegion).length;
  const [year, monthNumber] = data.month.split("-");
  const pickOwner = (account: AdsCrmAccount, value: string) => {
    const body: AdsCrmAccountUpdate = value.startsWith("u:")
      ? { user_id: value.slice(2), source: null, former: false }
      : value.startsWith("s:")
        ? { source: value.slice(2), user_id: null, former: false }
        : value === "former"
          ? { former: true, user_id: null, source: null }
          : { user_id: null, source: null, former: false };
    update.mutate({ crmId: account.crm_id, body });
  };
  return (
    <>
      <Status data={data} />
      <section aria-label="Tổng tháng" className="grid gap-3 sm:grid-cols-3">
        <div className="panel p-4">
          <p className="text-xs text-[var(--text-muted)]">
            Thực thu đã gán · tháng {Number(monthNumber)}/{year}
          </p>
          <p className="text-2xl font-semibold tabular-nums">
            {money(data.mapped_total)}
          </p>
        </div>
        <div className="panel p-4" data-testid="crm-unassigned">
          <p className="text-xs text-[var(--text-muted)]">
            Chưa gán ai (không tính)
          </p>
          <p className="text-2xl font-semibold tabular-nums">
            {money(data.unassigned_total)}
          </p>
          <p className="text-xs text-[var(--text-muted)]">
            {data.unassigned_accounts} tài khoản có thực thu trong tháng
          </p>
        </div>
        <div className="panel p-4">
          <p className="text-xs text-[var(--text-muted)]">Thiếu khu vực</p>
          <p
            className={`text-2xl font-semibold tabular-nums ${noRegion ? "text-[var(--warn)]" : ""}`}
          >
            {noRegion}
          </p>
          <p className="text-xs text-[var(--text-muted)]">
            Tài khoản đã gán nhưng chưa rõ HN / HCM (đang tính vào SG)
          </p>
        </div>
      </section>
      <section aria-label="Tài khoản CRM" className="space-y-2">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
            <span>Tìm</span>
            <input
              aria-label="Tìm tài khoản"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Tên, mã nhân viên, CRM ID"
              className="min-h-11 w-64 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
            <span>Hiện</span>
            <Select
              aria-label="Hiện"
              value={only}
              onChange={(event) => setOnly(event.target.value as typeof only)}
            >
              <option value="all">Tất cả</option>
              <option value="loose">Chưa gán</option>
              <option value="region">Đã gán, thiếu khu vực</option>
            </Select>
          </label>
          {update.isError ? (
            <span role="alert" className="text-sm text-[var(--bad)]">
              {update.error instanceof Error ? update.error.message : "Lỗi"}
            </span>
          ) : null}
        </div>
        <div className="panel table-scroll-x">
          <table className="table-dense w-full min-w-[920px]">
            <thead>
              <tr>
                <th className="text-left">CRM ID</th>
                <th className="text-left">Tên trên CRM</th>
                <th className="text-left">Khu vực</th>
                <th className="text-left">Tính cho</th>
                <th className="text-right">Số khoản</th>
                <th className="text-right">Thực thu tháng</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((account) => (
                <tr
                  key={account.crm_id}
                  data-testid={`crm-${account.crm_id}`}
                  className={!account.active ? "text-[var(--text-muted)]" : undefined}
                >
                  <td className="tabular-nums">{account.crm_id}</td>
                  <td>
                    <div className="font-medium">{account.name}</div>
                    <div className="flex flex-wrap gap-1">
                      {!account.active ? <span className="st st-slate">Đã nghỉ</span> : null}
                      {!account.in_directory ? (
                        <span className="st st-amber">Ngoài DS marketing</span>
                      ) : null}
                    </div>
                  </td>
                  <td>
                    <select
                      aria-label={`Khu vực ${account.name}`}
                      value={account.region ?? ""}
                      disabled={update.isPending}
                      onChange={(event) =>
                        update.mutate({
                          crmId: account.crm_id,
                          body: {
                            region: (event.target.value || null) as AdsCrmAccountUpdate["region"],
                          },
                        })
                      }
                      className={`min-h-10 rounded-lg border bg-[var(--surface)] px-2 text-sm ${
                        needsRegion(account)
                          ? "border-[var(--warn)]"
                          : "border-[var(--border)]"
                      }`}
                    >
                      <option value="">—</option>
                      {data.regions.map((region) => (
                        <option key={region.value} value={region.value}>
                          {region.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <Select
                      aria-label={`Tính cho ${account.name}`}
                      value={ownerOf(account)}
                      disabled={update.isPending}
                      onChange={(event) => pickOwner(account, event.target.value)}
                      className="min-w-[240px]"
                    >
                      <option value="">— Không tính —</option>
                      <optgroup label="Người">
                        {data.people.map((p) => (
                          <option key={p.user_id} value={`u:${p.user_id}`}>
                            {p.name}
                            {p.member_code ? ` · ${p.member_code}` : ""}
                            {p.in_ads ? "" : " (ngoài Phòng Ads)"}
                          </option>
                        ))}
                      </optgroup>
                      <optgroup label="Nguồn ◆">
                        {data.sources.map((source) => (
                          <option key={source.value} value={`s:${source.value}`}>
                            ◆ {source.label}
                          </option>
                        ))}
                      </optgroup>
                      <optgroup label="Khác">
                        <option value="former">Nhân viên đã nghỉ (dùng tên CRM)</option>
                      </optgroup>
                    </Select>
                    {account.source === "HOTLINE" ? (
                      <select
                        aria-label={`Loại hotline ${account.name}`}
                        value={account.hotline_kind ?? ""}
                        disabled={update.isPending}
                        onChange={(event) =>
                          update.mutate({
                            crmId: account.crm_id,
                            body: { hotline_kind: event.target.value || null },
                          })
                        }
                        className="mt-1 min-h-10 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-sm"
                      >
                        <option value="">Loại hotline…</option>
                        {(data.hotline_kinds ?? []).map((kind) => (
                          <option key={kind.value} value={kind.value}>
                            {kind.label}
                          </option>
                        ))}
                      </select>
                    ) : null}
                  </td>
                  <td className="text-right tabular-nums">{account.payments || "—"}</td>
                  <td className="text-right font-semibold tabular-nums">
                    {account.total ? money(account.total) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 ? (
            <p className="p-4 text-sm text-[var(--text-muted)]">
              {data.accounts.length
                ? "Không có tài khoản khớp bộ lọc."
                : "Chưa có tài khoản CRM: bấm “Đồng bộ ngay” hoặc nhập file gắn mã."}
            </p>
          ) : null}
        </div>
      </section>
    </>
  );
}
