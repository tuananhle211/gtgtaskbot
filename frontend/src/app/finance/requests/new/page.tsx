"use client";

/**
 * Tài chính › Hồ sơ chi › Lập hồ sơ (đợt 3; P6 B: anybody). Pick the pháp nhân,
 * phòng ban, cơ sở, the budget code (its route is shown before sending), the
 * amount (typed in full, with quick suggestions - M6), the month it counts
 * for. A code with no route is refused (F4: configure it first).
 */

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ConfirmButton } from "@/components/confirm";
import { FIELD } from "@/components/finance";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading, Pill } from "@/components/states";
import { monthOf } from "@/lib/hr-attendance";
import { amountSuggestions, money, spendApi, type FinanceCode, type SpendCatalog } from "@/lib/finance";

function RouteSummary({ code, catalog }: { code: FinanceCode; catalog: SpendCatalog }) {
  const label = (value: string) => catalog.roles.find((item) => item.value === value)?.label ?? value;
  const route = code.route;
  if (code.no_route) {
    return <Pill tone="bad">Mã này chưa có tuyến duyệt</Pill>;
  }
  const parts = [
    route.purchase.length ? `Duyệt mua sắm: ${route.purchase.map(label).join(" → ")}` : null,
    route.executor ? `Thực hiện mua sắm: ${label(route.executor)}` : null,
    route.payment.length ? `Duyệt thanh toán: ${route.payment.map(label).join(" → ")}` : null,
    route.cash.length ? `Chi tiền: ${route.cash.map(label).join(" / ")}` : null,
  ].filter(Boolean);
  const docs = route.documents.map(
    (value) => catalog.documents.find((item) => item.value === value)?.label ?? value,
  );
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3 text-sm" data-testid="route-summary">
      {route.no_approval ? <p>Không cần duyệt: ghi nhận và chi trực tiếp.</p> : null}
      {parts.map((part) => (
        <p key={part}>{part}</p>
      ))}
      {docs.length ? <p className="mt-1 text-[var(--text-muted)]">Chứng từ cần có: {docs.join(", ")}</p> : null}
      {route.documents_note ? <p className="text-[var(--text-muted)]">{route.documents_note}</p> : null}
    </div>
  );
}

export default function NewFinanceRequestPage() {
  const router = useRouter();
  const catalog = useQuery({ queryKey: ["finance", "spend-catalog"], queryFn: spendApi.catalog });
  const form = useQuery({ queryKey: ["finance", "request-form"], queryFn: spendApi.form });
  const [values, setValues] = useState<Record<string, string>>({});
  const [codeQuery, setCodeQuery] = useState("");
  const create = useMutation({
    mutationFn: () =>
      spendApi.create({
        legal_entity_id: get("legal_entity_id"),
        department_id: get("department_id") || null,
        apm_branch: get("apm_branch") || null,
        budget_code: get("budget_code"),
        title: get("title"),
        description: get("description"),
        vendor: get("vendor") || null,
        payee: get("payee") || null,
        amount: get("amount"),
        budget_month: get("budget_month") || monthOf(),
        needed_on: null,
      }),
    onSuccess: (created) => router.push(`/finance/requests/${created.id}`),
  });
  const defaults = useMemo(
    () => ({
      legal_entity_id: form.data?.my_entity ?? "",
      department_id: form.data?.my_departments[0] ?? "",
      apm_branch: form.data?.my_branch ?? "",
      budget_month: monthOf(),
    }),
    [form.data],
  );
  function get(key: string): string {
    return values[key] ?? (defaults as Record<string, string>)[key] ?? "";
  }
  const set = (key: string) => (event: { target: { value: string } }) =>
    setValues({ ...values, [key]: event.target.value });
  if (catalog.isPending || form.isPending) return <Loading />;
  if (catalog.isError) return <ErrorBox error={catalog.error} onRetry={() => catalog.refetch()} />;
  if (form.isError) return <ErrorBox error={form.error} onRetry={() => form.refetch()} />;
  const leaves = form.data.codes.filter((code) => code.leaf);
  const shownCodes = leaves.filter((code) =>
    `${code.code} ${code.name}`.toLowerCase().includes(codeQuery.trim().toLowerCase()),
  );
  const code = leaves.find((item) => item.code === get("budget_code"));
  const suggestions = amountSuggestions(get("amount"));
  const ready = Boolean(get("legal_entity_id") && code && !code.no_route && get("title").trim() && get("amount"));
  return (
    <div className="max-w-3xl space-y-5">
      <PageHeader title="Lập hồ sơ chi" subtitle="Hồ sơ đi theo tuyến duyệt của mã chi đã chọn." />
      <div className="panel space-y-4 p-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Pháp nhân
            <Select aria-label="Pháp nhân" value={get("legal_entity_id")} onChange={set("legal_entity_id")} className="mt-1 w-full">
              <option value="">Chọn pháp nhân</option>
              {catalog.data.entities.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          </label>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Phòng ban
            <Select aria-label="Phòng ban" value={get("department_id")} onChange={set("department_id")} className="mt-1 w-full">
              <option value="">Chọn phòng ban</option>
              {catalog.data.departments.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          </label>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Cơ sở (nếu có)
            <Select aria-label="Cơ sở" value={get("apm_branch")} onChange={set("apm_branch")} className="mt-1 w-full">
              <option value="">Không chọn</option>
              {catalog.data.branches.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          </label>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Tháng tính ngân sách (YYYY-MM)
            <input value={get("budget_month")} onChange={set("budget_month")} placeholder="2026-11" className={`mt-1 ${FIELD}`} />
          </label>
        </div>
        <div className="space-y-2">
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Tìm mã ngân sách
            <input value={codeQuery} onChange={(event) => setCodeQuery(event.target.value)} placeholder="VD: văn phòng phẩm, CO.06.02" className={`mt-1 ${FIELD}`} />
          </label>
          <Select aria-label="Mã ngân sách" value={get("budget_code")} onChange={set("budget_code")} className="w-full">
            <option value="">Chọn mã ngân sách</option>
            {shownCodes.map((item) => (
              <option key={item.code} value={item.code}>
                {item.code} - {item.name}
                {item.no_route ? " (chưa có tuyến)" : ""}
              </option>
            ))}
          </Select>
          {code ? <RouteSummary code={code} catalog={catalog.data} /> : null}
        </div>
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Nội dung chi
          <input aria-label="Nội dung chi" value={get("title")} onChange={set("title")} className={`mt-1 ${FIELD}`} />
        </label>
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Diễn giải
          <textarea value={get("description")} onChange={set("description")} rows={3} className={`mt-1 ${FIELD} py-2`} />
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Nhà cung cấp
            <input value={get("vendor")} onChange={set("vendor")} className={`mt-1 ${FIELD}`} />
          </label>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Thông tin nhận tiền (tên, STK, ngân hàng)
            <input value={get("payee")} onChange={set("payee")} className={`mt-1 ${FIELD}`} />
          </label>
        </div>
        <div>
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Số tiền (đồng)
            <input aria-label="Số tiền" inputMode="numeric" value={get("amount")} onChange={set("amount")} className={`mt-1 ${FIELD}`} />
          </label>
          {suggestions.length ? (
            <div className="mt-2 flex flex-wrap gap-2" aria-label="Gợi ý số tiền">
              {suggestions.map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setValues({ ...values, amount: String(value) })}
                  className="min-h-9 rounded-full border border-[var(--border)] px-3 text-sm hover:bg-[var(--surface-muted)]"
                >
                  {money(value)}
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <ConfirmButton
          spec={{
            title: `Gửi hồ sơ chi ${get("amount") ? money(Number(get("amount").replace(/\D/g, ""))) : ""}?`,
            description: code ? `${code.code} - ${code.name}. Hồ sơ chuyển tới người duyệt đầu tiên.` : "",
            confirmLabel: "Gửi",
          }}
          disabled={!ready}
          onConfirm={() => create.mutate()}
          pending={create.isPending}
          error={create.error}
          tone="primary"
        >
          Gửi hồ sơ
        </ConfirmButton>
      </div>
    </div>
  );
}
