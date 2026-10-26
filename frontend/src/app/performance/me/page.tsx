"use client";

import { useState } from "react";
import { PageHeader } from "@/components/pr";
import { MonthField, PerformancePanel, monthOf } from "@/components/performance";
import { monthLabel } from "@/lib/labels";

/**
 * "Hiệu suất › Của tôi": the person's own points this month - every step
 * counted, with its codes, and what is still under way (dự kiến).
 */
export default function MyPerformancePage() {
  const [month, setMonth] = useState(() => monthOf(new Date()));
  return (
    <div className="space-y-4">
      <PageHeader
        title="Điểm hiệu suất của tôi"
        subtitle={`${monthLabel(month)} · điểm tính khi bước được duyệt xong, theo mã task và hình thức.`}
        action={<MonthField value={month} onChange={setMonth} />}
      />
      <PerformancePanel userId="me" month={month} />
    </div>
  );
}
