"use client";

/** Tài chính › Thu - chi (đợt 3): thực thu sau VAT & phí, chi đã thực hiện, ngân sách, theo pháp nhân và phòng ban. */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { MonthSwitch } from "@/components/finance";
import { PageHeader } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";
import { monthOf } from "@/lib/hr-attendance";
import { money, spendApi, type PnlLine } from "@/lib/finance";

function Table({ lines, total, unit, income = true }: { lines: PnlLine[]; total?: PnlLine; unit: string; income?: boolean }) {
  return (
    <div className="panel table-scroll-x">
      <table className="table-dense min-w-[48rem]">
        <thead>
          <tr>
            <th>{unit}</th>
            {income ? <th className="text-right">Thu (sau VAT & phí)</th> : null}
            <th className="text-right">Chi đã thực hiện</th>
            <th className="text-right">Đang chờ</th>
            <th className="text-right">Ngân sách</th>
            {income ? <th className="text-right">Chênh lệch thu - chi</th> : null}
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.key} data-testid="pnl-row">
              <td>{line.label}</td>
              {income ? <td className="text-right tabular-nums">{money(line.income)}</td> : null}
              <td className="text-right tabular-nums">{money(line.realized)}</td>
              <td className="text-right tabular-nums">{money(line.committed)}</td>
              <td className="text-right tabular-nums">{money(line.budget)}</td>
              {income ? (
                <td className={`text-right font-medium tabular-nums ${line.result < 0 ? "text-[var(--bad)]" : "text-[var(--good)]"}`}>{money(line.result)}</td>
              ) : null}
            </tr>
          ))}
        </tbody>
        {total ? (
          <tfoot>
            <tr className="font-semibold">
              <td>Tổng</td>
              {income ? <td className="text-right tabular-nums">{money(total.income)}</td> : null}
              <td className="text-right tabular-nums">{money(total.realized)}</td>
              <td className="text-right tabular-nums">{money(total.committed)}</td>
              <td className="text-right tabular-nums">{money(total.budget)}</td>
              {income ? <td className="text-right tabular-nums">{money(total.income - total.realized)}</td> : null}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}

export default function FinancePnlPage() {
  const [month, setMonth] = useState(monthOf());
  const report = useQuery({ queryKey: ["finance", "pnl", month], queryFn: () => spendApi.pnl(month), retry: false });
  return (
    <div className="space-y-5">
      <PageHeader title="Thu - chi" subtitle="Thu = thực thu từ CRM sau VAT & phí; chi = hồ sơ chi đã thực hiện trong tháng; so với ngân sách." />
      <MonthSwitch month={month} onChange={setMonth} />
      {report.isPending ? <Loading /> : null}
      {report.isError ? <ErrorBox error={report.error} onRetry={() => report.refetch()} /> : null}
      {report.data ? (
        <>
          <h2 className="text-base font-semibold">Theo pháp nhân</h2>
          <Table lines={report.data.entities} total={report.data.total} unit="Pháp nhân" />
          <h2 className="text-base font-semibold">Chi theo phòng ban</h2>
          <Table lines={report.data.departments} unit="Phòng ban" income={false} />
        </>
      ) : null}
    </div>
  );
}
