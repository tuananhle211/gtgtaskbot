"use client";

/**
 * "Danh mục" (0096): contract types (with their length), how early a contract
 * ending is flagged, and which documents a complete file holds. HCNS only.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { hrApi } from "@/lib/hr";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader, SecondaryButton } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox } from "@/components/states";
import { FIELD } from "@/components/hr-profile";

export default function HrSettingsPage() {
  const queryClient = useQueryClient();
  const access = useQuery({ queryKey: ["hr", "access"], queryFn: hrApi.access });
  const types = useQuery({ queryKey: ["hr", "contract-types"], queryFn: hrApi.contractTypes });
  const [days, setDays] = useState<string | null>(null);
  const [docs, setDocs] = useState<string[] | null>(null);
  const [draft, setDraft] = useState({ code: "", name: "", months: "12" });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["hr"] });
  const saveSettings = useMutation({
    mutationFn: () =>
      hrApi.saveSettings({
        contract_warn_days: Number(days ?? access.data?.settings.contract_warn_days ?? 30),
        required_documents: docs ?? access.data?.settings.required_documents ?? [],
      }),
    onSuccess: () => {
      setDays(null);
      setDocs(null);
      refresh();
    },
  });
  const addType = useMutation({
    mutationFn: () => hrApi.createContractType({ code: draft.code, name: draft.name, months: Number(draft.months) || 0 }),
    onSuccess: () => {
      setDraft({ code: "", name: "", months: "12" });
      refresh();
    },
  });
  const toggle = useMutation({
    mutationFn: (item: { id: string; active: boolean }) => hrApi.updateContractType(item.id, { active: !item.active }),
    onSuccess: refresh,
  });
  if (access.isPending || types.isPending) return <Loading label="Đang tải danh mục…" />;
  if (access.isError) return <ErrorBox error={access.error} onRetry={() => access.refetch()} />;
  if (types.isError) return <ErrorBox error={types.error} onRetry={() => types.refetch()} />;
  const a = access.data;
  if (!a.edit) return <p className="text-sm text-[var(--text-muted)]">Danh mục dành cho phòng Hành chính nhân sự.</p>;
  const required = docs ?? a.settings.required_documents;
  return (
    <div className="space-y-4">
      <PageHeader title="Danh mục nhân sự" subtitle="Loại hợp đồng, cảnh báo hết hạn và tài liệu bắt buộc của hồ sơ." />
      <section className="panel space-y-3 p-4">
        <h2 className="text-base font-semibold">Tự động hoá</h2>
        <label className="block max-w-xs text-xs text-[var(--text-muted)]">
          Cảnh báo hợp đồng sắp hết hạn trước (ngày)
          <input
            value={days ?? String(a.settings.contract_warn_days)}
            inputMode="numeric"
            onChange={(event) => setDays(event.target.value.replace(/\D/g, ""))}
            className={`${FIELD} mt-1`}
          />
        </label>
        <fieldset>
          <legend className="text-xs text-[var(--text-muted)]">Tài liệu bắt buộc (hồ sơ thiếu sẽ được nhắc)</legend>
          <div className="mt-1 flex flex-wrap gap-3">
            {a.choices.document_kinds.map((kind) => (
              <label key={kind.code} className="inline-flex min-h-9 items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={required.includes(kind.code)}
                  onChange={(event) =>
                    setDocs(event.target.checked ? [...required, kind.code] : required.filter((code) => code !== kind.code))
                  }
                />
                {kind.label}
              </label>
            ))}
          </div>
        </fieldset>
        {saveSettings.isError ? <NoticeBox error={saveSettings.error} /> : null}
        <ConfirmButton
          spec={{ title: "Lưu thiết lập hồ sơ?", description: "Áp dụng ngay cho tổng quan và danh sách hồ sơ.", confirmLabel: "Lưu" }}
          onConfirm={() => saveSettings.mutate()}
          pending={saveSettings.isPending}
          error={saveSettings.error}
          disabled={days === null && docs === null}
          tone="primary"
        >
          Lưu thiết lập
        </ConfirmButton>
      </section>

      <section className="panel space-y-3 p-4">
        <h2 className="text-base font-semibold">Loại hợp đồng</h2>
        <div className="table-scroll-x">
          <table className="table-dense min-w-[36rem]">
            <thead>
              <tr>
                <th>Mã</th>
                <th>Tên loại hợp đồng</th>
                <th>Thời hạn</th>
                <th>Trạng thái</th>
                <th><span className="sr-only">Thao tác</span></th>
              </tr>
            </thead>
            <tbody>
              {types.data.types.map((item) => (
                <tr key={item.id} className={item.active ? undefined : "opacity-60"}>
                  <td className="font-mono text-xs">{item.code}</td>
                  <td>{item.name}</td>
                  <td>{item.months ? `${item.months} tháng` : "Không thời hạn"}</td>
                  <td>{item.active ? "Đang dùng" : "Đã tắt"}</td>
                  <td className="text-right">
                    <SecondaryButton type="button" onClick={() => toggle.mutate(item)} disabled={toggle.isPending}>
                      {item.active ? "Tắt" : "Bật lại"}
                    </SecondaryButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid gap-2 sm:grid-cols-[8rem_1fr_8rem_auto] sm:items-end">
          <label className="text-xs text-[var(--text-muted)]">Mã<input value={draft.code} onChange={(event) => setDraft({ ...draft, code: event.target.value })} className={`${FIELD} mt-1 font-mono uppercase`} /></label>
          <label className="text-xs text-[var(--text-muted)]">Tên<input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} className={`${FIELD} mt-1`} /></label>
          <label className="text-xs text-[var(--text-muted)]">Số tháng (0 = không thời hạn)<input value={draft.months} inputMode="numeric" onChange={(event) => setDraft({ ...draft, months: event.target.value.replace(/\D/g, "") })} className={`${FIELD} mt-1`} /></label>
          <ConfirmButton
            spec={{ title: `Thêm loại hợp đồng ${draft.name}?`, description: "Dùng được ngay khi thêm / gia hạn hợp đồng.", confirmLabel: "Thêm" }}
            onConfirm={() => addType.mutate()}
            pending={addType.isPending}
            error={addType.error}
            disabled={!draft.code.trim() || !draft.name.trim()}
            tone="primary"
          >
            Thêm loại
          </ConfirmButton>
        </div>
        {addType.isError || toggle.isError ? <NoticeBox error={addType.error ?? toggle.error} /> : null}
      </section>
    </div>
  );
}
