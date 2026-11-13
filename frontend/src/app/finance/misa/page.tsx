"use client";

/**
 * Tài chính › Đối soát MISA (đợt 3; M4: chỉ đối chiếu tổng tháng): nhập file
 * F01-DNN "Bảng cân đối tài khoản" của một pháp nhân, một tháng; so doanh thu
 * (511 trừ 5114) với CRM, chi phí (632 + 642 + 811) với hồ sơ chi đã thực hiện.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ConfirmButton } from "@/components/confirm";
import { MonthSwitch } from "@/components/finance";
import { PageHeader, Select } from "@/components/pr";
import { Empty, ErrorBox, Loading } from "@/components/states";
import { monthOf } from "@/lib/hr-attendance";
import { money, spendApi } from "@/lib/finance";

export default function FinanceMisaPage() {
  const queryClient = useQueryClient();
  const [month, setMonth] = useState(monthOf());
  const [entity, setEntity] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [picked, setPicked] = useState<File | null>(null);
  const catalog = useQuery({ queryKey: ["finance", "spend-catalog"], queryFn: spendApi.catalog });
  const chosen = entity || catalog.data?.entities[0]?.value || "";
  const view = useQuery({
    queryKey: ["finance", "misa", chosen, month],
    queryFn: () => spendApi.misa(chosen, month),
    enabled: Boolean(chosen),
    retry: false,
  });
  const upload = useMutation({
    mutationFn: (file: File) => spendApi.importMisa(chosen, month, file),
    onSuccess: (result) => {
      setPicked(null);
      setMessage(`Đã nhập ${result.lines} dòng tài khoản.`);
      void queryClient.invalidateQueries({ queryKey: ["finance", "misa"] });
    },
    onError: (error: Error) => setMessage(error.message),
  });
  const data = view.data;
  const rows: [string, number, number, number][] = data
    ? [
        ["Doanh thu (MISA: 511 - 5114 · APM: doanh số trước VAT của CRM)", data.misa_revenue, data.apm_revenue, data.revenue_gap],
        ["Chi phí (MISA: 632 + 642 + 811 · APM: hồ sơ chi đã thực hiện)", data.misa_cost, data.apm_cost, data.cost_gap],
      ]
    : [];
  return (
    <div className="space-y-5">
      <PageHeader title="Đối soát MISA" subtitle="Nhập file F01-DNN (Bảng cân đối tài khoản) của từng pháp nhân mỗi tháng, so tổng với APM." />
      <div className="flex flex-wrap items-end gap-3">
        <MonthSwitch month={month} onChange={setMonth} />
        <label className="block w-64 text-xs font-medium text-[var(--text-muted)]">
          Pháp nhân
          <Select aria-label="Pháp nhân" value={chosen} onChange={(event) => setEntity(event.target.value)} className="mt-1 w-full">
            {(catalog.data?.entities ?? []).map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </Select>
        </label>
        {data?.can_import ? (
          <label className="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-[var(--border)] px-4 text-sm hover:bg-[var(--surface-muted)]">
            Chọn file F01-DNN
            <input
              type="file"
              accept=".xlsx"
              className="sr-only"
              aria-label="Nhập file MISA"
              onChange={(event) => {
                setMessage(null);
                setPicked(event.target.files?.[0] ?? null);
                event.target.value = "";
              }}
            />
          </label>
        ) : null}
        {picked ? (
          <ConfirmButton
            spec={{
              title: `Nhập "${picked.name}"?`,
              description: `Thay số MISA tháng ${month.slice(5)}/${month.slice(0, 4)} của pháp nhân đã chọn.`,
              confirmLabel: "Nhập",
            }}
            onConfirm={() => upload.mutate(picked)}
            pending={upload.isPending}
            error={upload.error}
            tone="primary"
          >
            Nhập {picked.name}
          </ConfirmButton>
        ) : null}
      </div>
      {message ? <p className="text-sm" role="status">{message}</p> : null}
      {view.isPending && chosen ? <Loading /> : null}
      {view.isError ? <ErrorBox error={view.error} onRetry={() => view.refetch()} /> : null}
      {data ? (
        <>
          {!data.imported ? <Empty message="Chưa nhập file MISA cho pháp nhân này trong tháng." /> : null}
          <div className="panel table-scroll-x">
            <table className="table-dense min-w-[44rem]">
              <thead>
                <tr>
                  <th>Khoản</th>
                  <th className="text-right">MISA</th>
                  <th className="text-right">APM</th>
                  <th className="text-right">Chênh lệch</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(([label, misa, apm, gap]) => (
                  <tr key={label} data-testid="misa-row">
                    <td>{label}</td>
                    <td className="text-right tabular-nums">{money(misa)}</td>
                    <td className="text-right tabular-nums">{money(apm)}</td>
                    <td className={`text-right font-medium tabular-nums ${gap ? "text-[var(--bad)]" : "text-[var(--good)]"}`}>{money(gap)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.lines.length ? (
            <div className="panel table-scroll-x">
              <table className="table-dense min-w-[48rem]">
                <thead>
                  <tr>
                    <th>Tài khoản</th>
                    <th>Tên</th>
                    <th className="text-right">PS Nợ</th>
                    <th className="text-right">PS Có</th>
                    <th className="text-right">Dư Nợ cuối</th>
                    <th className="text-right">Dư Có cuối</th>
                  </tr>
                </thead>
                <tbody>
                  {data.lines.map((line) => (
                    <tr key={line.account}>
                      <td className="font-medium">{line.account}</td>
                      <td>{line.name}</td>
                      <td className="text-right tabular-nums">{money(line.debit)}</td>
                      <td className="text-right tabular-nums">{money(line.credit)}</td>
                      <td className="text-right tabular-nums">{money(line.closing_debit)}</td>
                      <td className="text-right tabular-nums">{money(line.closing_credit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
