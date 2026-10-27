"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AdsAccount, type AdsAccounts } from "@/lib/api";
import { money } from "@/components/ads";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import { formatShortDateTime } from "@/lib/datetime";

/**
 * "Phòng Ads › Tài khoản QC" (0078): every ad account synced from the BMs, its
 * brand and the marketer who runs it. A Trưởng phòng ADS (or the OWNER /
 * ADMIN) gives an account to somebody in the department by picking them in the
 * row - saved at once, one field, and changed back the same way. A new account
 * starts "Chưa gán": its spend counts for the department, not for anybody.
 * A marketer sees only their own accounts here.
 */
export default function AdsAccountsPage() {
  const [month, setMonth] = useState<string>("");
  const [connection, setConnection] = useState<string>("");
  const [unassigned, setUnassigned] = useState(false);
  const [search, setSearch] = useState("");
  const params = {
    month: month || undefined,
    connection_id: connection || undefined,
    unassigned,
    q: search.trim() || undefined,
  };
  const list = useQuery({
    queryKey: ["ads", "accounts", params],
    queryFn: () => api.adsAccounts(params),
    retry: false,
    placeholderData: (previous) => previous,
  });
  const connections = list.data
    ? [
        ...new Map(
          list.data.accounts.map((account) => [
            account.connection_id,
            account.connection_name,
          ]),
        ).entries(),
      ]
    : [];
  return (
    <div className="space-y-4">
      <PageHeader
        title="Tài khoản QC"
        subtitle="Các tài khoản quảng cáo đồng bộ từ BM, thương hiệu và người phụ trách. Số chi tiêu của tài khoản chưa gán chỉ tính cho cả phòng."
      />
      <section aria-label="Bộ lọc" className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
          <span>Tháng</span>
          <input
            type="month"
            aria-label="Tháng"
            value={month || list.data?.month || ""}
            onChange={(event) => setMonth(event.target.value)}
            className="min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
          />
        </label>
        {connections.length > 1 ? (
          <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
            <span>Business Manager</span>
            <Select
              aria-label="Business Manager"
              value={connection}
              onChange={(event) => setConnection(event.target.value)}
            >
              <option value="">Tất cả BM</option>
              {connections.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </Select>
          </label>
        ) : null}
        <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
          <span>Tìm</span>
          <input
            type="search"
            aria-label="Tìm tài khoản"
            placeholder="Tên hoặc ID tài khoản"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="min-h-11 w-56 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm"
          />
        </label>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={unassigned}
            onChange={(event) => setUnassigned(event.target.checked)}
          />
          Chỉ tài khoản chưa gán
        </label>
      </section>
      {list.isPending ? (
        <Loading label="Đang tải tài khoản quảng cáo…" />
      ) : null}
      {list.isError ? (
        <ErrorBox error={list.error} onRetry={() => list.refetch()} />
      ) : null}
      {list.data ? <AccountsTable data={list.data} /> : null}
    </div>
  );
}

function AccountsTable({ data }: { data: AdsAccounts }) {
  const unassigned = data.accounts.filter((account) => !account.user_id).length;
  if (data.accounts.length === 0) {
    return (
      <p className="panel p-4 text-sm text-[var(--text-muted)]">
        Chưa có tài khoản nào. Tài khoản xuất hiện sau lượt đồng bộ đầu tiên của
        BM (Phòng Ads › Kết nối BM).
      </p>
    );
  }
  return (
    <>
      {data.can_manage && unassigned > 0 ? (
        <p
          role="status"
          className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] px-3 py-2 text-sm"
        >
          {unassigned} tài khoản chưa gán người phụ trách.
        </p>
      ) : null}
      <div className="panel table-scroll-x">
        <table className="table-dense w-full min-w-[900px]">
          <thead>
            <tr>
              <th className="text-left">Tài khoản quảng cáo</th>
              <th className="text-left">Business Manager</th>
              <th className="text-left">Trạng thái</th>
              <th className="text-left">Thương hiệu</th>
              <th className="text-left">Người phụ trách</th>
              <th className="text-right">Chi tiêu tháng</th>
              <th className="text-right">Hoá đơn tháng</th>
            </tr>
          </thead>
          <tbody>
            {data.accounts.map((account) => (
              <AccountRow
                key={account.account_id}
                account={account}
                data={data}
              />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function AccountRow({
  account,
  data,
}: {
  account: AdsAccount;
  data: AdsAccounts;
}) {
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (body: { user_id?: string | null; brand?: string }) =>
      api.assignAdsAccount(account.account_id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ads"] });
    },
  });
  return (
    <tr data-testid={`ads-account-${account.account_id}`}>
      <td>
        <div className="font-medium">{account.name}</div>
        <div className="text-xs text-[var(--text-muted)]">
          {account.account_id}
          {account.last_seen_at
            ? ` · thấy lúc ${formatShortDateTime(account.last_seen_at)}`
            : ""}
        </div>
        {save.isError ? (
          <NoticeBox error={save.error} onDismiss={() => save.reset()} />
        ) : null}
      </td>
      <td className="text-sm">
        {account.connection_name}
        {account.site ? (
          <div className="text-xs text-[var(--text-muted)]">{account.site}</div>
        ) : null}
      </td>
      <td>
        <Pill tone={account.active ? "good" : "neutral"}>
          {account.active ? "Đang hoạt động" : "Không hoạt động"}
        </Pill>
      </td>
      <td>
        {data.can_manage ? (
          <Select
            aria-label={`Thương hiệu ${account.name}`}
            value={account.brand}
            disabled={save.isPending}
            onChange={(event) => save.mutate({ brand: event.target.value })}
          >
            {data.brands.map((brand) => (
              <option key={brand.value} value={brand.value}>
                {brand.label}
              </option>
            ))}
          </Select>
        ) : (
          account.brand_label
        )}
      </td>
      <td>
        {data.can_manage ? (
          <Select
            aria-label={`Người phụ trách ${account.name}`}
            value={account.user_id ?? ""}
            disabled={save.isPending}
            onChange={(event) =>
              save.mutate({ user_id: event.target.value || null })
            }
          >
            <option value="">Chưa gán</option>
            {data.people.map((person) => (
              <option key={person.user_id} value={person.user_id}>
                {person.name}
                {person.member_code ? ` (${person.member_code})` : ""}
              </option>
            ))}
          </Select>
        ) : (
          (account.user_name ?? "Chưa gán")
        )}
      </td>
      <td className="text-right tabular-nums">{money(account.spend)}</td>
      <td className="text-right tabular-nums">{money(account.charged)}</td>
    </tr>
  );
}
