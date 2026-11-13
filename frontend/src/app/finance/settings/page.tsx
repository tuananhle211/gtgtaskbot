"use client";

/**
 * Tài chính › Cấu hình (0105):
 * - "Chi nhánh CRM": each CRM branch -> its pháp nhân and APM cơ sở (a new
 *   CRM branch appears by itself, "Chưa gắn"); changing it moves the old
 *   months too (no effective date);
 * - "Nhân viên CRM": each CRM person -> the APM account they are;
 * - "Thuế VAT": the rate used when a payment's order is not known;
 * - "Đồng bộ CRM": the one run shared with Phòng Ads, and "Đồng bộ ngay".
 * Editing needs FINANCE SETTINGS; others read.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader, SecondaryButton, Select } from "@/components/pr";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { monthOf, shiftMonth } from "@/lib/hr-attendance";
import {
  financeApi,
  spendApi,
  STAFF_ROLE_LABELS,
  type Branches,
  type CrmStaff,
  type FinanceCode,
  type Option,
  type RouteValue,
} from "@/lib/finance";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";

type Tab = "branches" | "staff" | "vat" | "fees" | "kpi" | "debts" | "sync" | "codes" | "roles" | "spend";
const TABS: { key: Tab; label: string }[] = [
  { key: "codes", label: "Mã chi & tuyến duyệt" },
  { key: "roles", label: "Vai trò tài chính" },
  { key: "spend", label: "Chứng từ & ngưỡng" },
  { key: "branches", label: "Chi nhánh CRM" },
  { key: "staff", label: "Nhân viên CRM" },
  { key: "vat", label: "Thuế VAT" },
  { key: "fees", label: "Phí & giá cost" },
  { key: "kpi", label: "KPI doanh thu" },
  { key: "debts", label: "Công nợ" },
  { key: "sync", label: "Đồng bộ CRM" },
];

const field =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-[var(--text)]";

function BranchesTab() {
  const queryClient = useQueryClient();
  const branches = useQuery({ queryKey: ["finance", "branches"], queryFn: financeApi.branches });
  const [edits, setEdits] = useState<Record<number, { legal_entity_id?: string | null; apm_branch?: string | null }>>({});
  const save = useMutation({
    mutationFn: async () => {
      for (const [id, body] of Object.entries(edits)) {
        await financeApi.updateBranch(Number(id), body);
      }
    },
    onSuccess: () => {
      setEdits({});
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
    },
  });
  if (branches.isPending) return <Loading />;
  if (branches.isError) return <ErrorBox error={branches.error} onRetry={() => branches.refetch()} />;
  const data: Branches = branches.data;
  if (!data.branches.length) {
    return <Empty message="Chưa có chi nhánh CRM: chúng tự hiện sau lần đồng bộ đầu tiên." />;
  }
  const value = (id: number, key: "legal_entity_id" | "apm_branch", stored: string | null) =>
    key in (edits[id] ?? {}) ? (edits[id][key] ?? "") : (stored ?? "");
  const set = (id: number, key: "legal_entity_id" | "apm_branch", next: string) =>
    setEdits((current) => ({ ...current, [id]: { ...current[id], [key]: next || null } }));
  const changed = Object.keys(edits).length;
  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--text-muted)]">
        Mỗi chi nhánh CRM gắn với một pháp nhân và một cơ sở của APM. Đổi gắn thì số các tháng trước
        của chi nhánh đó cũng đổi theo.
      </p>
      <div className="panel table-scroll-x">
        <table className="table-dense min-w-[40rem]">
          <thead>
            <tr>
              <th>Chi nhánh CRM</th>
              <th>Pháp nhân</th>
              <th>Cơ sở APM</th>
            </tr>
          </thead>
          <tbody>
            {data.branches.map((branch) => {
              const entity = value(branch.id, "legal_entity_id", branch.legal_entity_id);
              const site = value(branch.id, "apm_branch", branch.apm_branch);
              return (
                <tr key={branch.id} data-testid="branch-row">
                  <td>
                    <span className="font-medium">{branch.name}</span>
                    <span className="block text-xs text-[var(--text-muted)]">ID CRM {branch.id}</span>
                  </td>
                  <td>
                    <Select
                      aria-label={`Pháp nhân của ${branch.name}`}
                      value={entity}
                      disabled={!data.can_edit}
                      onChange={(event) => set(branch.id, "legal_entity_id", event.target.value)}
                      className="w-full"
                    >
                      <option value="">Chưa gắn</option>
                      {data.entities
                        .filter((item) => item.active || item.id === entity)
                        .map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.code} - {item.name}
                          </option>
                        ))}
                    </Select>
                  </td>
                  <td>
                    <Select
                      aria-label={`Cơ sở của ${branch.name}`}
                      value={site}
                      disabled={!data.can_edit}
                      onChange={(event) => set(branch.id, "apm_branch", event.target.value)}
                      className="w-full"
                    >
                      <option value="">Chưa gắn</option>
                      {data.sites.map((item) => (
                        <option key={item.code} value={item.code}>
                          {item.label}
                        </option>
                      ))}
                    </Select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {data.can_edit ? (
        <div className="flex flex-wrap gap-2">
          <ConfirmButton
            spec={{
              title: `Lưu cách gắn của ${changed} chi nhánh CRM?`,
              description:
                "Doanh thu các tháng trước của những chi nhánh này cũng chuyển theo pháp nhân / cơ sở mới.",
              confirmLabel: "Lưu",
            }}
            disabled={!changed}
            onConfirm={() => save.mutate()}
            pending={save.isPending}
            error={save.error}
            tone="primary"
          >
            {changed ? `Lưu thay đổi (${changed})` : "Lưu thay đổi"}
          </ConfirmButton>
          {changed ? (
            <SecondaryButton type="button" onClick={() => setEdits({})}>
              Bỏ thay đổi
            </SecondaryButton>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function StaffRow({
  row,
  people,
  canEdit,
}: {
  row: CrmStaff;
  people: { id: string; name: string; member_code: string | null }[];
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const [pick, setPick] = useState(row.user_id ?? "");
  const save = useMutation({
    mutationFn: () => financeApi.updateStaff(row.id, pick || null),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["finance"] }),
  });
  const chosen = people.find((person) => person.id === pick);
  return (
    <tr data-testid="staff-row">
      <td className="tabular-nums">{row.id}</td>
      <td>
        <span className="font-medium">{row.name}</span>
        {row.department_name ? (
          <span className="block text-xs text-[var(--text-muted)]">{row.department_name}</span>
        ) : null}
      </td>
      <td>
        <div className="flex flex-wrap gap-1">
          {row.roles.map((role) => (
            <Pill key={role}>{STAFF_ROLE_LABELS[role] ?? role}</Pill>
          ))}
        </div>
      </td>
      <td className="min-w-56">
        {canEdit ? (
          <div className="flex items-center gap-2">
            <Select
              aria-label={`Tài khoản APM của ${row.name}`}
              value={pick}
              onChange={(event) => setPick(event.target.value)}
              className="w-full"
            >
              <option value="">Chưa gắn</option>
              {people.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name}
                  {person.member_code ? ` (${person.member_code})` : ""}
                </option>
              ))}
            </Select>
            <ConfirmButton
              spec={{
                title: chosen ? `Gắn ${row.name} với ${chosen.name}?` : `Bỏ gắn ${row.name}?`,
                description: "Báo cáo theo người sẽ ghi tên tài khoản APM này cạnh tên trên CRM.",
                confirmLabel: "Lưu",
              }}
              disabled={pick === (row.user_id ?? "")}
              onConfirm={() => save.mutate()}
              pending={save.isPending}
              error={save.error}
              tone="secondary"
              ariaLabel={`Lưu tài khoản APM của ${row.name}`}
            >
              Lưu
            </ConfirmButton>
          </div>
        ) : (
          (row.user_name ?? <span className="text-[var(--text-muted)]">Chưa gắn</span>)
        )}
      </td>
    </tr>
  );
}

function StaffTab({ canEdit }: { canEdit: boolean }) {
  const [q, setQ] = useState("");
  const [unmapped, setUnmapped] = useState(false);
  const [page, setPage] = useState(1);
  const staff = useQuery({
    queryKey: ["finance", "staff", q, unmapped, page],
    queryFn: () => financeApi.staff({ q, unmapped, page }),
  });
  const people = useQuery({
    queryKey: ["finance", "people"],
    queryFn: financeApi.people,
    enabled: canEdit,
  });
  const pages = staff.data ? Math.max(1, Math.ceil(staff.data.total / staff.data.page_size)) : 1;
  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--text-muted)]">
        Mọi người CRM từng xuất hiện trên khoản thu, doanh thu chia hay công tua. Gắn với tài khoản
        APM để báo cáo theo người ghi đúng nhân viên.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="block min-w-60 flex-1 text-xs font-medium text-[var(--text-muted)]">
          Tìm theo tên / ID CRM
          <input
            value={q}
            onChange={(event) => {
              setQ(event.target.value);
              setPage(1);
            }}
            className={`mt-1 ${field}`}
          />
        </label>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={unmapped}
            onChange={(event) => {
              setUnmapped(event.target.checked);
              setPage(1);
            }}
          />
          Chỉ người chưa gắn
          {staff.data ? <Pill tone="warn">{staff.data.unmapped}</Pill> : null}
        </label>
      </div>
      {staff.isPending ? <Loading /> : null}
      {staff.isError ? <ErrorBox error={staff.error} onRetry={() => staff.refetch()} /> : null}
      {staff.data && !staff.data.rows.length ? <Empty message="Không có ai khớp." /> : null}
      {staff.data && staff.data.rows.length ? (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[48rem]">
            <thead>
              <tr>
                <th>ID CRM</th>
                <th>Tên trên CRM</th>
                <th>Xuất hiện ở</th>
                <th>Tài khoản APM</th>
              </tr>
            </thead>
            <tbody>
              {staff.data.rows.map((row) => (
                <StaffRow key={row.id} row={row} people={people.data ?? []} canEdit={canEdit} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {pages > 1 ? (
        <div className="flex items-center gap-2 text-sm">
          <SecondaryButton type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            ‹ Trước
          </SecondaryButton>
          <span>
            Trang {page}/{pages}
          </span>
          <SecondaryButton type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>
            Sau ›
          </SecondaryButton>
        </div>
      ) : null}
    </div>
  );
}

function VatTab() {
  const queryClient = useQueryClient();
  const vat = useQuery({ queryKey: ["finance", "vat"], queryFn: financeApi.vat });
  const branches = useQuery({ queryKey: ["finance", "branches"], queryFn: financeApi.branches });
  const [draft, setDraft] = useState<{ vat_default: string; vat_by_entity: Record<string, string> } | null>(null);
  const save = useMutation({
    mutationFn: () =>
      financeApi.setVat({
        vat_default: (draft ?? vat.data!).vat_default,
        vat_by_entity: Object.fromEntries(
          Object.entries((draft ?? vat.data!).vat_by_entity).map(([key, rate]) => [key, rate || null]),
        ),
      }),
    onSuccess: () => {
      setDraft(null);
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
    },
  });
  if (vat.isPending || branches.isPending) return <Loading />;
  if (vat.isError) return <ErrorBox error={vat.error} onRetry={() => vat.refetch()} />;
  if (branches.isError) return <ErrorBox error={branches.error} onRetry={() => branches.refetch()} />;
  const current = draft ?? { vat_default: vat.data.vat_default, vat_by_entity: vat.data.vat_by_entity };
  const edit = (next: typeof current) => setDraft(next);
  const canEdit = vat.data.can_edit;
  return (
    <div className="max-w-2xl space-y-4">
      <p className="text-sm text-[var(--text-muted)]">
        VAT trong thực thu lấy theo đơn hàng của khoản thu (CRM ghi VAT từng đơn). Khi không tìm thấy
        đơn, dùng thuế suất của pháp nhân; pháp nhân để trống thì dùng mức mặc định.
      </p>
      <label className="block max-w-xs text-xs font-medium text-[var(--text-muted)]">
        Thuế suất mặc định (%)
        <input
          inputMode="decimal"
          value={current.vat_default}
          disabled={!canEdit}
          onChange={(event) => edit({ ...current, vat_default: event.target.value })}
          className={`mt-1 ${field}`}
        />
      </label>
      <div className="panel table-scroll-x">
        <table className="table-dense min-w-[28rem]">
          <thead>
            <tr>
              <th>Pháp nhân</th>
              <th>Thuế suất (%)</th>
            </tr>
          </thead>
          <tbody>
            {branches.data.entities.map((entity) => (
              <tr key={entity.id}>
                <td>
                  {entity.code} - {entity.name}
                </td>
                <td>
                  <input
                    aria-label={`Thuế suất của ${entity.code}`}
                    inputMode="decimal"
                    placeholder={`Mặc định (${current.vat_default})`}
                    value={current.vat_by_entity[entity.id] ?? ""}
                    disabled={!canEdit}
                    onChange={(event) =>
                      edit({
                        ...current,
                        vat_by_entity: { ...current.vat_by_entity, [entity.id]: event.target.value },
                      })
                    }
                    className={field}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canEdit ? (
        <ConfirmButton
          spec={{
            title: "Lưu thuế suất VAT?",
            description: `Mặc định ${current.vat_default}%. Báo cáo các tháng tính lại theo mức mới.`,
            confirmLabel: "Lưu",
          }}
          disabled={!draft}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          Lưu thuế suất
        </ConfirmButton>
      ) : null}
    </div>
  );
}

function FeesTab() {
  const queryClient = useQueryClient();
  const rules = useQuery({ queryKey: ["finance", "fee-rules"], queryFn: financeApi.feeRules });
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const [q, setQ] = useState("");
  const costs = useQuery({ queryKey: ["finance", "service-costs", q], queryFn: () => financeApi.serviceCosts(q) });
  const saveRules = useMutation({
    mutationFn: () =>
      financeApi.setFeeRules(
        Object.fromEntries(Object.keys(rules.data!.methods).map((key) => [key, (draft ?? {})[key]?.trim() || null])),
      ),
    onSuccess: () => {
      setDraft(null);
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
    },
  });
  if (rules.isPending) return <Loading />;
  if (rules.isError) return <ErrorBox error={rules.error} onRetry={() => rules.refetch()} />;
  const current = draft ?? rules.data.rates;
  const canEdit = rules.data.can_edit;
  return (
    <div className="space-y-5">
      <section className="max-w-xl space-y-3">
        <h2 className="text-base font-semibold">Phí theo phương thức thanh toán (%)</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Phí quẹt tự tính = tiền thu bằng phương thức đó x %. Kế toán sửa được từng dòng ở Bảng kê.
        </p>
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[24rem]">
            <tbody>
              {Object.entries(rules.data.methods).map(([key, label]) => (
                <tr key={key}>
                  <td>{label}</td>
                  <td className="w-40">
                    <input
                      aria-label={`Phí % của ${label}`}
                      inputMode="decimal"
                      placeholder="0"
                      value={current[key] ?? ""}
                      disabled={!canEdit}
                      onChange={(event) => setDraft({ ...current, [key]: event.target.value })}
                      className={field}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {canEdit ? (
          <ConfirmButton
            spec={{
              title: "Lưu % phí theo phương thức?",
              description: "Phí quẹt tự tính của mọi dòng chưa nhập tay sẽ tính lại.",
              confirmLabel: "Lưu",
            }}
            disabled={!draft}
            onConfirm={() => saveRules.mutate()}
            pending={saveRules.isPending}
            error={saveRules.error}
            tone="primary"
          >
            Lưu % phí
          </ConfirmButton>
        ) : null}
      </section>
      <section className="space-y-3">
        <h2 className="text-base font-semibold">Giá cost / viện phí theo dịch vụ</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Danh sách dịch vụ lấy từ các đơn CRM đã đồng bộ. Giá cost tự tính của đơn = số lượng x giá
          cost, ghi vào dòng &quot;Đơn mới&quot;.
        </p>
        <label className="block max-w-md text-xs font-medium text-[var(--text-muted)]">
          Tìm dịch vụ
          <input value={q} onChange={(event) => setQ(event.target.value)} className={`mt-1 ${field}`} />
        </label>
        {costs.isPending ? <Loading /> : null}
        {costs.data && !costs.data.length ? <Empty message="Chưa có dịch vụ nào." /> : null}
        {costs.data && costs.data.length ? (
          <div className="panel table-scroll-x">
            <table className="table-dense min-w-[36rem]">
              <thead>
                <tr>
                  <th>Dịch vụ (CRM)</th>
                  <th>Giá cost / đơn vị</th>
                </tr>
              </thead>
              <tbody>
                {costs.data.slice(0, 300).map((item) => (
                  <ServiceCostRow key={item.service_id} item={item} canEdit={canEdit} />
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function ServiceCostRow({
  item,
  canEdit,
}: {
  item: { service_id: number; name: string; cost: number | null };
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(item.cost !== null ? String(item.cost) : "");
  const save = useMutation({
    mutationFn: () => financeApi.setServiceCost(item.service_id, { name: item.name, cost: value.trim() || null }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["finance"] }),
  });
  return (
    <tr data-testid="service-cost-row">
      <td>
        {item.name}
        <span className="block text-xs text-[var(--text-muted)]">ID {item.service_id}</span>
      </td>
      <td className="min-w-64">
        <div className="flex items-center gap-2">
          <input
            aria-label={`Giá cost của ${item.name}`}
            inputMode="numeric"
            value={value}
            disabled={!canEdit}
            onChange={(event) => setValue(event.target.value)}
            className={field}
          />
          {canEdit ? (
            <ConfirmButton
              spec={{
                title: `Lưu giá cost của ${item.name}?`,
                description: value.trim() ? `Giá cost ${value} / đơn vị.` : "Bỏ giá cost của dịch vụ này.",
                confirmLabel: "Lưu",
              }}
              disabled={value === (item.cost !== null ? String(item.cost) : "")}
              onConfirm={() => save.mutate()}
              pending={save.isPending}
              error={save.error}
              tone="secondary"
              ariaLabel={`Lưu giá cost của ${item.name}`}
            >
              Lưu
            </ConfirmButton>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

function KpiTab() {
  const queryClient = useQueryClient();
  const [month, setMonth] = useState(() => monthOf());
  const kpi = useQuery({ queryKey: ["finance", "kpi", month], queryFn: () => financeApi.kpi(month) });
  const [draft, setDraft] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => financeApi.setKpi(month, Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, v.trim() || null]))),
    onSuccess: () => {
      setDraft({});
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
    },
  });
  if (kpi.isPending) return <Loading />;
  if (kpi.isError) return <ErrorBox error={kpi.error} onRetry={() => kpi.refetch()} />;
  const data = kpi.data;
  const cells = Object.entries(data.cells).flatMap(([dimension, keys]) => keys.map((key) => `${dimension}:${key}`));
  const shown = (cell: string) => draft[cell] ?? (data.values[cell] !== undefined ? String(data.values[cell]) : "");
  return (
    <div className="max-w-2xl space-y-3">
      <p className="text-sm text-[var(--text-muted)]">
        KPI thực thu theo tháng: toàn công ty, từng cơ sở, từng đội Marketing. Màn Doanh thu và Bảng kê
        so thực thu với KPI.
      </p>
      <div className="flex items-center gap-2">
        <SecondaryButton
          type="button"
          aria-label="Tháng trước"
          onClick={() => {
            setMonth(shiftMonth(month, -1));
            setDraft({});
          }}
        >
          ‹
        </SecondaryButton>
        <span className="min-w-28 text-center font-semibold">
          Tháng {month.slice(5, 7)}/{month.slice(0, 4)}
        </span>
        <SecondaryButton
          type="button"
          aria-label="Tháng sau"
          onClick={() => {
            setMonth(shiftMonth(month, 1));
            setDraft({});
          }}
        >
          ›
        </SecondaryButton>
      </div>
      <div className="panel table-scroll-x">
        <table className="table-dense min-w-[28rem]">
          <tbody>
            {cells.map((cell) => (
              <tr key={cell}>
                <td>{data.labels[cell] ?? cell}</td>
                <td className="w-56">
                  <input
                    aria-label={`KPI ${data.labels[cell] ?? cell}`}
                    inputMode="numeric"
                    value={shown(cell)}
                    disabled={!data.can_edit}
                    onChange={(event) => setDraft({ ...draft, [cell]: event.target.value })}
                    className={field}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.can_edit ? (
        <ConfirmButton
          spec={{ title: `Lưu KPI tháng ${month.slice(5)}/${month.slice(0, 4)}?`, description: "Ô để trống là bỏ KPI đó.", confirmLabel: "Lưu" }}
          disabled={!Object.keys(draft).length}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          Lưu KPI
        </ConfirmButton>
      ) : null}
    </div>
  );
}

function DebtsTab({ canEdit }: { canEdit: boolean }) {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["finance", "debt-settings"], queryFn: financeApi.debtSettings });
  const people = useQuery({ queryKey: ["finance", "people"], queryFn: financeApi.people, enabled: canEdit });
  const [marks, setMarks] = useState<string | null>(null);
  const [recipients, setRecipients] = useState<string[] | null>(null);
  const [pick, setPick] = useState("");
  const save = useMutation({
    mutationFn: () =>
      financeApi.setDebtSettings({
        milestones: (marks ?? settings.data!.milestones.join(", "))
          .split(/[,;\s]+/)
          .filter(Boolean)
          .map(Number),
        recipients: recipients ?? settings.data!.recipients,
      }),
    onSuccess: () => {
      setMarks(null);
      setRecipients(null);
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
    },
  });
  if (settings.isPending) return <Loading />;
  if (settings.isError) return <ErrorBox error={settings.error} onRetry={() => settings.refetch()} />;
  const chosen = recipients ?? settings.data.recipients;
  const names = new Map((people.data ?? []).map((person) => [person.id, person.name]));
  return (
    <div className="max-w-2xl space-y-3">
      <p className="text-sm text-[var(--text-muted)]">
        Các mốc tuổi nợ (ngày) để chia nhóm và cảnh báo. Sau lần đồng bộ 5h mỗi ngày, người nhận cảnh
        báo được gửi tóm tắt công nợ quá mốc (thông báo web + Telegram).
      </p>
      <label className="block max-w-xs text-xs font-medium text-[var(--text-muted)]">
        Các mốc (ngày, cách nhau dấu phẩy)
        <input
          value={marks ?? settings.data.milestones.join(", ")}
          disabled={!canEdit}
          onChange={(event) => setMarks(event.target.value)}
          className={`mt-1 ${field}`}
        />
      </label>
      <div className="space-y-2">
        <p className="text-xs font-medium text-[var(--text-muted)]">Người nhận cảnh báo</p>
        <div className="flex flex-wrap gap-2" data-testid="debt-recipients">
          {chosen.length ? (
            chosen.map((id) => (
              <span key={id} className="inline-flex items-center gap-1 rounded-full bg-[var(--accent-soft)] px-3 py-1 text-sm text-[var(--accent-strong)]">
                {names.get(id) ?? "Tài khoản"}
                {canEdit ? (
                  <button
                    type="button"
                    aria-label={`Bỏ ${names.get(id) ?? "người này"}`}
                    onClick={() => setRecipients(chosen.filter((item) => item !== id))}
                  >
                    ×
                  </button>
                ) : null}
              </span>
            ))
          ) : (
            <span className="text-sm text-[var(--text-muted)]">Chưa có ai.</span>
          )}
        </div>
        {canEdit ? (
          <div className="flex max-w-md items-center gap-2">
            <Select aria-label="Thêm người nhận" value={pick} onChange={(event) => setPick(event.target.value)} className="w-full">
              <option value="">Chọn người…</option>
              {(people.data ?? [])
                .filter((person) => !chosen.includes(person.id))
                .map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name}
                  </option>
                ))}
            </Select>
            <SecondaryButton
              type="button"
              disabled={!pick}
              onClick={() => {
                setRecipients([...chosen, pick]);
                setPick("");
              }}
            >
              Thêm
            </SecondaryButton>
          </div>
        ) : null}
      </div>
      {canEdit ? (
        <ConfirmButton
          spec={{ title: "Lưu cài đặt công nợ?", description: "Áp dụng từ lần đồng bộ 5h tới.", confirmLabel: "Lưu" }}
          disabled={marks === null && recipients === null}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          Lưu cài đặt công nợ
        </ConfirmButton>
      ) : null}
    </div>
  );
}

function SyncTab() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["finance", "sync"], queryFn: financeApi.syncStatus });
  const sync = useMutation({
    mutationFn: () => financeApi.sync(),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["finance", "sync"] }),
  });
  if (status.isPending) return <Loading />;
  if (status.isError) return <ErrorBox error={status.error} onRetry={() => status.refetch()} />;
  const data = status.data;
  return (
    <div className="max-w-2xl space-y-3">
      <p className="text-sm text-[var(--text-muted)]">
        Một lần gọi CRM cho cả Phòng Ads và Tài chính: thanh toán, đơn hàng, doanh thu chia, công
        tua của tháng này (ngày 1-5 đọc cả tháng trước). Tự chạy lúc 0h, 5h, 12h, 18h; lần 5h đọc lại
        các đơn còn nợ trong một năm.
      </p>
      <dl className="panel grid grid-cols-1 gap-3 p-4 sm:grid-cols-2">
        <div>
          <dt className="text-xs text-[var(--text-muted)]">Lần gần nhất</dt>
          <dd className="mt-1 font-medium">
            {data.at ? formatDateTime(data.at) : "Chưa đồng bộ lần nào"}{" "}
            {data.status ? (
              <Pill tone={data.status === "SUCCESS" ? "good" : "bad"}>
                {data.status === "SUCCESS" ? "Thành công" : "Có lỗi"}
              </Pill>
            ) : null}
            {data.running ? <Pill tone="warn">Đang chạy</Pill> : null}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-[var(--text-muted)]">Khoảng đã lấy</dt>
          <dd className="mt-1 font-medium">
            {data.synced_from && data.synced_to
              ? `${formatDate(data.synced_from)} - ${formatDate(data.synced_to)}`
              : "—"}
          </dd>
        </div>
        {data.message ? (
          <div className="sm:col-span-2">
            <dt className="text-xs text-[var(--text-muted)]">Kết quả</dt>
            <dd className="mt-1 text-sm">{data.message}</dd>
          </div>
        ) : null}
      </dl>
      {!data.configured ? (
        <div className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
          Máy chủ chưa có key CRM (CRM_API_KEY): chưa lấy được số.
        </div>
      ) : null}
      {data.can_sync ? (
        <ConfirmButton
          spec={{
            title: "Đồng bộ CRM ngay?",
            description:
              "Đọc lại tháng này từ CRM cho cả Phòng Ads và Tài chính. Mất vài phút; số cập nhật khi chạy xong.",
            confirmLabel: "Đồng bộ",
          }}
          disabled={!data.configured || data.running}
          onConfirm={() => sync.mutate()}
          pending={sync.isPending}
          error={sync.error}
          tone="primary"
        >
          Đồng bộ ngay
        </ConfirmButton>
      ) : null}
      {sync.isSuccess ? (
        <p className="text-sm text-[var(--good)]" role="status">
          Đã gửi yêu cầu đồng bộ. Tải lại sau vài phút để xem kết quả.
        </p>
      ) : null}
    </div>
  );
}

function RouteEditor({ code, onClose }: { code: FinanceCode; onClose: () => void }) {
  const queryClient = useQueryClient();
  const catalog = useQuery({ queryKey: ["finance", "spend-catalog"], queryFn: spendApi.catalog });
  const history = useQuery({ queryKey: ["finance", "code-history", code.code], queryFn: () => spendApi.codeHistory(code.code) });
  const [route, setRoute] = useState<RouteValue>(code.route);
  const save = useMutation({
    mutationFn: () => spendApi.updateCode(code.code, { route }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
      onClose();
    },
  });
  const toggle = (list: "purchase" | "payment" | "cash" | "documents", value: string, order: Option[]) => {
    const has = route[list].includes(value);
    const next = has ? route[list].filter((item) => item !== value) : [...route[list], value];
    const rank = (item: string) => order.findIndex((option) => option.value === item);
    setRoute({ ...route, [list]: next.sort((a, b) => rank(a) - rank(b)) });
  };
  const group = (title: string, list: "purchase" | "payment" | "cash" | "documents", options: Option[]) => (
    <fieldset className="space-y-1">
      <legend className="text-xs font-medium text-[var(--text-muted)]">{title}</legend>
      {options.map((option) => (
        <label key={option.value} className="flex min-h-9 items-center gap-2 text-sm">
          <input type="checkbox" checked={route[list].includes(option.value)} onChange={() => toggle(list, option.value, options)} />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
  if (!catalog.data) return null;
  return (
    <Modal open onClose={onClose} title={`Tuyến duyệt ${code.code}`} busy={save.isPending}>
      <ModalBody className="space-y-4">
        <p className="text-sm">{code.name}</p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {group("Duyệt đề nghị mua sắm (theo thứ tự)", "purchase", catalog.data.purchase_roles)}
          {group("Duyệt thanh toán (theo thứ tự)", "payment", catalog.data.payment_roles)}
          <label className="block text-xs font-medium text-[var(--text-muted)]">
            Bộ phận thực hiện mua sắm
            <Select aria-label="Bộ phận thực hiện mua sắm" value={route.executor ?? ""} onChange={(event) => setRoute({ ...route, executor: event.target.value || null })} className="mt-1 w-full">
              <option value="">Không có bước mua sắm</option>
              {catalog.data.executor_roles.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </Select>
          </label>
          {group("Người chi tiền", "cash", catalog.data.cash_roles)}
          {group("Chứng từ cần có", "documents", catalog.data.documents.filter((item) => item.value !== "PAYMENT_PROOF"))}
        </div>
        <label className="block text-xs font-medium text-[var(--text-muted)]">
          Ghi chú chứng từ
          <input value={route.documents_note} onChange={(event) => setRoute({ ...route, documents_note: event.target.value })} className={`mt-1 ${field}`} />
        </label>
        {history.data?.length ? (
          <div>
            <p className="text-xs font-medium text-[var(--text-muted)]">Lịch sử sửa</p>
            <ul className="mt-1 space-y-1 text-xs" data-testid="code-history">
              {history.data.map((item, index) => (
                <li key={index}>{formatDateTime(item.at)} · {item.by ?? "Hệ thống"}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>Huỷ</SecondaryButton>
        <ConfirmButton
          spec={{ title: `Lưu tuyến duyệt ${code.code}?`, description: "Hồ sơ gửi sau đó đi theo tuyến mới; hồ sơ đang chạy giữ tuyến cũ.", confirmLabel: "Lưu" }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          Lưu tuyến
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}

function CodesTab() {
  const codes = useQuery({ queryKey: ["finance", "codes"], queryFn: spendApi.codes });
  const catalog = useQuery({ queryKey: ["finance", "spend-catalog"], queryFn: spendApi.catalog });
  const [editing, setEditing] = useState<FinanceCode | null>(null);
  const [q, setQ] = useState("");
  if (codes.isPending || catalog.isPending) return <Loading />;
  if (codes.isError) return <ErrorBox error={codes.error} onRetry={() => codes.refetch()} />;
  if (catalog.isError) return <ErrorBox error={catalog.error} onRetry={() => catalog.refetch()} />;
  const label = (value: string) => catalog.data.roles.find((item) => item.value === value)?.label ?? value;
  const text = q.trim().toLowerCase();
  const rows = codes.data.codes.filter((item) => !text || `${item.code} ${item.name}`.toLowerCase().includes(text));
  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--text-muted)]">
        Danh mục mã ngân sách và tuyến duyệt theo Sổ tay (tab Phân quyền). Mã chưa có tuyến cần cấu hình trước khi lập hồ sơ.
      </p>
      <label className="block max-w-md text-xs font-medium text-[var(--text-muted)]">
        Tìm mã / tên
        <input value={q} onChange={(event) => setQ(event.target.value)} className={`mt-1 ${field}`} />
      </label>
      <div className="panel table-scroll-x">
        <table className="table-dense min-w-[60rem]">
          <thead>
            <tr>
              <th>Mã</th>
              <th>Tên</th>
              <th>Duyệt mua sắm</th>
              <th>Mua sắm</th>
              <th>Duyệt thanh toán</th>
              <th>Chi tiền</th>
              {codes.data.can_edit ? <th /> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <tr key={item.code} data-testid="code-row" className={item.leaf ? "" : "bg-[var(--surface-muted)] font-semibold"}>
                <td className="whitespace-nowrap">{item.code}</td>
                <td>{item.name}</td>
                {item.leaf ? (
                  item.no_route ? (
                    <td colSpan={4}><Pill tone="warn">Chưa có tuyến duyệt</Pill></td>
                  ) : (
                    <>
                      <td className="text-xs">{item.route.no_approval ? "Không cần duyệt" : item.route.purchase.map(label).join(" → ") || "—"}</td>
                      <td className="text-xs">{item.route.executor ? label(item.route.executor) : "—"}</td>
                      <td className="text-xs">{item.route.payment.map(label).join(" → ") || "—"}</td>
                      <td className="text-xs">{item.route.cash.map(label).join(" / ") || "—"}</td>
                    </>
                  )
                ) : (
                  <td colSpan={4} />
                )}
                {codes.data.can_edit ? (
                  <td>
                    {item.leaf ? (
                      <button type="button" className="text-xs text-[var(--accent)] hover:underline" onClick={() => setEditing(item)} aria-label={`Sửa tuyến ${item.code}`}>
                        Sửa tuyến
                      </button>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing ? <RouteEditor code={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function RolesTab() {
  const queryClient = useQueryClient();
  const roles = useQuery({ queryKey: ["finance", "roles"], queryFn: spendApi.roles });
  const catalog = useQuery({ queryKey: ["finance", "spend-catalog"], queryFn: spendApi.catalog });
  const [draft, setDraft] = useState({ role: "", user_id: "", legal_entity_id: "", apm_branch: "", standin_user_id: "" });
  const add = useMutation({
    mutationFn: () =>
      spendApi.addRole({
        role: draft.role,
        user_id: draft.user_id,
        legal_entity_id: draft.legal_entity_id || null,
        apm_branch: draft.apm_branch || null,
        standin_user_id: draft.standin_user_id || null,
      }),
    onSuccess: () => {
      setDraft({ role: "", user_id: "", legal_entity_id: "", apm_branch: "", standin_user_id: "" });
      void queryClient.invalidateQueries({ queryKey: ["finance", "roles"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: number) => spendApi.removeRole(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["finance", "roles"] }),
  });
  if (roles.isPending || catalog.isPending) return <Loading />;
  if (roles.isError) return <ErrorBox error={roles.error} onRetry={() => roles.refetch()} />;
  if (catalog.isError) return <ErrorBox error={catalog.error} onRetry={() => catalog.refetch()} />;
  const set = (key: keyof typeof draft) => (event: { target: { value: string } }) => setDraft({ ...draft, [key]: event.target.value });
  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--text-muted)]">
        Trưởng phòng và Giám đốc khối lấy theo sơ đồ tổ chức. Các vai trò khác gán ở đây; có thể gán riêng cho một pháp nhân / cơ sở, và chọn người duyệt thay. Mỗi bước một người duyệt (khớp pháp nhân và cơ sở trước).
      </p>
      {roles.data.assignments.length ? (
        <div className="panel table-scroll-x">
          <table className="table-dense min-w-[48rem]">
            <thead>
              <tr>
                <th>Vai trò</th>
                <th>Người giữ</th>
                <th>Pháp nhân</th>
                <th>Cơ sở</th>
                <th>Duyệt thay</th>
                {roles.data.can_edit ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {roles.data.assignments.map((row) => (
                <tr key={row.id} data-testid="role-row">
                  <td>{row.role_label}</td>
                  <td>{row.user_name}</td>
                  <td>{row.legal_entity ?? "Tất cả"}</td>
                  <td>{row.apm_branch ?? "Tất cả"}</td>
                  <td>{row.standin_name ?? "—"}</td>
                  {roles.data.can_edit ? (
                    <td>
                      <ConfirmButton
                        spec={{ title: `Bỏ ${row.user_name} khỏi ${row.role_label}?`, description: "Bước đang chờ người này sẽ tìm lại người giữ vai trò.", confirmLabel: "Bỏ", variant: "destructive" }}
                        onConfirm={() => remove.mutate(row.id)}
                        pending={remove.isPending}
                        error={remove.error}
                        tone="secondary"
                        ariaLabel={`Bỏ ${row.user_name} khỏi ${row.role_label}`}
                      >
                        Bỏ
                      </ConfirmButton>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty message="Chưa gán vai trò nào." />
      )}
      {roles.data.can_edit ? (
        <section className="panel grid grid-cols-1 gap-3 p-4 sm:grid-cols-2">
          <Select aria-label="Vai trò" value={draft.role} onChange={set("role")} className="w-full">
            <option value="">Chọn vai trò</option>
            {catalog.data.assignable.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </Select>
          <Select aria-label="Người giữ" value={draft.user_id} onChange={set("user_id")} className="w-full">
            <option value="">Chọn người</option>
            {roles.data.people.map((person) => (
              <option key={person.id} value={person.id}>{person.name}{person.member_code ? ` (${person.member_code})` : ""}</option>
            ))}
          </Select>
          <Select aria-label="Pháp nhân áp dụng" value={draft.legal_entity_id} onChange={set("legal_entity_id")} className="w-full">
            <option value="">Mọi pháp nhân</option>
            {catalog.data.entities.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </Select>
          <Select aria-label="Cơ sở áp dụng" value={draft.apm_branch} onChange={set("apm_branch")} className="w-full">
            <option value="">Mọi cơ sở</option>
            {catalog.data.branches.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </Select>
          <Select aria-label="Người duyệt thay" value={draft.standin_user_id} onChange={set("standin_user_id")} className="w-full">
            <option value="">Không có người duyệt thay</option>
            {roles.data.people.map((person) => (
              <option key={person.id} value={person.id}>{person.name}</option>
            ))}
          </Select>
          <ConfirmButton
            spec={{ title: "Gán vai trò tài chính?", description: "Các hồ sơ chờ vai trò này sẽ chuyển tới người được gán.", confirmLabel: "Gán" }}
            disabled={!draft.role || !draft.user_id}
            onConfirm={() => add.mutate()}
            pending={add.isPending}
            error={add.error}
            tone="primary"
          >
            Gán vai trò
          </ConfirmButton>
        </section>
      ) : null}
    </div>
  );
}

function SpendSettingsTab() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["finance", "spend-settings"], queryFn: spendApi.spendSettings });
  const [draft, setDraft] = useState<{ ceo_threshold: string; drive_folder_id: string } | null>(null);
  const save = useMutation({
    mutationFn: () =>
      spendApi.setSpendSettings({
        ceo_threshold: draft?.ceo_threshold.trim() || null,
        drive_folder_id: draft?.drive_folder_id.trim() || null,
      }),
    onSuccess: () => {
      setDraft(null);
      void queryClient.invalidateQueries({ queryKey: ["finance"] });
    },
  });
  if (settings.isPending) return <Loading />;
  if (settings.isError) return <ErrorBox error={settings.error} onRetry={() => settings.refetch()} />;
  const current = draft ?? {
    ceo_threshold: settings.data.ceo_threshold ? String(settings.data.ceo_threshold) : "",
    drive_folder_id: settings.data.drive_folder_id ?? "",
  };
  return (
    <div className="max-w-2xl space-y-3">
      <label className="block text-xs font-medium text-[var(--text-muted)]">
        Ngưỡng thêm PTGĐ / CEO duyệt (đồng; để trống = tắt)
        <input aria-label="Ngưỡng thêm CEO" inputMode="numeric" value={current.ceo_threshold} disabled={!settings.data.can_edit} onChange={(event) => setDraft({ ...current, ceo_threshold: event.target.value })} className={`mt-1 ${field}`} />
      </label>
      <label className="block text-xs font-medium text-[var(--text-muted)]">
        Thư mục Google Drive lưu chứng từ (mã thư mục; để trống = lưu trên APM)
        <input aria-label="Thư mục Drive" value={current.drive_folder_id} disabled={!settings.data.can_edit} onChange={(event) => setDraft({ ...current, drive_folder_id: event.target.value })} className={`mt-1 ${field}`} />
      </label>
      <p className="text-xs text-[var(--text-muted)]">
        Thư mục nên nằm trong Shared Drive của công ty, chia sẻ cho tài khoản dịch vụ của APM và không chia sẻ công khai; người dùng mở chứng từ qua APM theo quyền.
      </p>
      {settings.data.can_edit ? (
        <ConfirmButton
          spec={{ title: "Lưu cài đặt chứng từ & ngưỡng?", description: "Áp dụng cho hồ sơ gửi sau.", confirmLabel: "Lưu" }}
          disabled={!draft}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          Lưu cài đặt
        </ConfirmButton>
      ) : null}
    </div>
  );
}

export default function FinanceSettingsPage() {
  const [tab, setTab] = useState<Tab>("codes");
  const access = useQuery({ queryKey: ["finance", "access"], queryFn: financeApi.access });
  if (access.isPending) return <Loading />;
  if (access.isError) return <ErrorBox error={access.error} onRetry={() => access.refetch()} />;
  if (!access.data.revenue && !access.data.settings && !access.data.codes) {
    return <Empty message="Bạn chưa có quyền ở mục Tài chính." />;
  }
  return (
    <div className="space-y-5">
      <PageHeader
        title="Cấu hình tài chính"
        subtitle={
          access.data.settings
            ? "Mã chi & tuyến duyệt, vai trò tài chính, chứng từ, chi nhánh / nhân viên CRM, VAT, phí, KPI, công nợ, đồng bộ CRM."
            : "Bạn chỉ xem được; người có quyền cấu hình Tài chính mới sửa được."
        }
      />
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Cấu hình tài chính">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
            className={`min-h-11 rounded-lg border px-3 text-sm ${
              tab === item.key
                ? "border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-strong)]"
                : "border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-muted)]"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      {tab === "branches" ? <BranchesTab /> : null}
      {tab === "staff" ? <StaffTab canEdit={access.data.settings} /> : null}
      {tab === "codes" ? <CodesTab /> : null}
      {tab === "roles" ? <RolesTab /> : null}
      {tab === "spend" ? <SpendSettingsTab /> : null}
      {tab === "vat" ? <VatTab /> : null}
      {tab === "fees" ? <FeesTab /> : null}
      {tab === "kpi" ? <KpiTab /> : null}
      {tab === "debts" ? <DebtsTab canEdit={access.data.settings} /> : null}
      {tab === "sync" ? <SyncTab /> : null}
    </div>
  );
}
