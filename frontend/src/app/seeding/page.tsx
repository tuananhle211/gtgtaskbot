"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type SeedingLine,
  type SeedingOther,
  type SeedingPeriod,
  type SeedingPhone,
} from "@/lib/api";
import { pointsDecisionConfirmation } from "@/lib/confirmations";
import { ConfirmButton } from "@/components/confirm";
import { formatScore } from "@/components/performance";
import { PageHeader, PrimaryButton, Select } from "@/components/pr";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { formatDate, formatDateTime } from "@/lib/datetime";

/**
 * "Phòng PR › Seeding" (0085): the seeding group's page.
 *
 * - **Tổng quan**: the team's phone KPI for this week (Monday-Sunday) and
 *   this month - one number for everybody, set by the Trưởng phòng PR - with
 *   the share reached by approved numbers, and each person's figures;
 * - **Nộp SĐT**: one number per line; each line says whether it went in
 *   (waits for the Trưởng phòng), was already in the system, twice here, or
 *   not a number. An approved number is worth 200 points;
 * - **Việc khác**: other work with its points, counted once approved;
 * - **Duyệt** / **Thành viên** (the Trưởng phòng PR): decide what waits, and
 *   who is in the group.
 */
type Tab = "overview" | "phones" | "other" | "review" | "members";

const STATUS: Record<string, { label: string; tone: "warn" | "good" | "bad" }> = {
  PENDING: { label: "Chờ duyệt", tone: "warn" },
  APPROVED: { label: "Đã duyệt", tone: "good" },
  REJECTED: { label: "Không duyệt", tone: "bad" },
};

const LINE_STATUS: Record<SeedingLine["status"], { label: string; tone: "good" | "warn" | "bad" | "neutral" }> = {
  NEW: { label: "Đã nộp, chờ duyệt", tone: "good" },
  DUPLICATE: { label: "Đã có trong hệ thống", tone: "bad" },
  REPEATED: { label: "Trùng trong lần nộp này", tone: "warn" },
  INVALID: { label: "Không phải số điện thoại", tone: "bad" },
};

export default function SeedingPage() {
  const [tab, setTab] = useState<Tab>("overview");
  const overview = useQuery({ queryKey: ["seeding", "overview"], queryFn: () => api.seedingOverview(), retry: false });
  if (overview.isPending) return <Loading label="Đang tải seeding…" />;
  if (overview.isError) {
    if (overview.error instanceof ApiError && overview.error.status === 403) {
      return (
        <section role="alert" className="panel p-6">
          <h1 className="text-lg font-semibold tracking-tight">Không xem được trang này</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">
            Chỉ thành viên nhóm seeding và Trưởng phòng PR vào được mục này.
          </p>
        </section>
      );
    }
    return <ErrorBox error={overview.error} onRetry={() => overview.refetch()} />;
  }
  const data = overview.data;
  const manage = data.access.manage;
  const tabs: Array<{ key: Tab; label: string }> = [
    { key: "overview", label: "Tổng quan" },
    { key: "phones", label: "Nộp SĐT" },
    { key: "other", label: "Việc khác" },
    ...(manage
      ? [
          { key: "review" as Tab, label: "Duyệt" },
          { key: "members" as Tab, label: "Thành viên" },
        ]
      : []),
  ];
  return (
    <div className="space-y-4">
      <PageHeader
        title="Seeding"
        subtitle={`Mỗi số điện thoại được duyệt = ${formatScore(data.phone_points)} điểm. KPI tính cho cả team theo tuần và tháng.`}
      />
      <div role="tablist" aria-label="Phần của seeding" className="flex flex-wrap gap-1">
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
            className={`min-h-9 rounded-full border px-3 text-sm font-medium ${
              tab === item.key
                ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)]"
                : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      {tab === "overview" ? <Overview manage={manage} day={data.day} /> : null}
      {tab === "phones" ? <PhonesTab /> : null}
      {tab === "other" ? <OtherTab /> : null}
      {tab === "review" && manage ? <ReviewTab /> : null}
      {tab === "members" && manage ? <MembersTab /> : null}
    </div>
  );
}

// --- Tổng quan ----------------------------------------------------------------

function Overview({ manage, day }: { manage: boolean; day: string }) {
  const overview = useQuery({ queryKey: ["seeding", "overview"], queryFn: () => api.seedingOverview() });
  if (!overview.data) return <Loading />;
  const data = overview.data;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2">
        <KpiCard title="KPI tuần" period={data.week} manage={manage} day={day} />
        <KpiCard title="KPI tháng" period={data.month} manage={manage} day={day} />
      </div>
      <section className="panel overflow-hidden" aria-label="Từng thành viên">
        {data.members.length === 0 ? (
          <div className="p-4">
            <Empty message="Chưa có thành viên seeding." />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-muted)]">
                  <th className="px-4 py-2 font-medium">Thành viên</th>
                  <th className="px-4 py-2 text-right font-medium">SĐT tuần</th>
                  <th className="px-4 py-2 text-right font-medium">SĐT tháng</th>
                  <th className="px-4 py-2 text-right font-medium">Chờ duyệt</th>
                  <th className="px-4 py-2 text-right font-medium">Điểm SĐT tháng</th>
                  <th className="px-4 py-2 text-right font-medium">Điểm việc khác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {data.members.map((row) => (
                  <tr key={row.person.user_id}>
                    <td className="px-4 py-2.5">
                      <span className="font-medium">{row.person.name}</span>
                      <p className="text-xs text-[var(--text-muted)]">{row.person.departments.join(", ")}</p>
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{row.approved_week}</td>
                    <td className="px-4 py-2.5 text-right font-semibold tabular-nums">{row.approved_month}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-[var(--text-muted)]">{row.pending}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{formatScore(row.phone_points_month)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{formatScore(row.other_points_month)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function KpiCard({ title, period, manage, day }: { title: string; period: SeedingPeriod; manage: boolean; day: string }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(period.target === null ? "" : String(period.target));
  const save = useMutation({
    mutationFn: () =>
      api.setSeedingTarget({ kind: period.kind, day, target: value.trim() === "" ? null : Number(value) }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["seeding"] }),
  });
  const share = period.target ? period.approved / period.target : null;
  const met = share !== null && share >= 1;
  const number = Number(value);
  const valid = value.trim() === "" || (Number.isInteger(number) && number >= 0 && number <= 1_000_000);
  return (
    <article className="panel p-4" aria-label={title}>
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        <span className="text-xs text-[var(--text-muted)]">
          {formatDate(period.start)} - {formatDate(period.end)}
        </span>
      </div>
      <p className="mt-2 text-3xl font-semibold tabular-nums">
        {formatScore(period.approved)}
        <span className="text-base font-normal text-[var(--text-muted)]">
          {" "}
          / {period.target === null ? "chưa giao" : formatScore(period.target)} SĐT
        </span>
      </p>
      <div
        role="meter"
        aria-label={`${title} đạt được`}
        aria-valuemin={0}
        aria-valuemax={period.target ?? 0}
        aria-valuenow={period.approved}
        className="mt-3 h-2 overflow-hidden rounded-full bg-[var(--surface-muted)] ring-1 ring-inset ring-[var(--border)]"
      >
        <span
          className="block h-full rounded-full"
          style={{
            width: `${Math.min(share ?? 0, 1) * 100}%`,
            background: met ? "var(--good)" : "var(--accent)",
          }}
        />
      </div>
      <p className="mt-1 text-xs text-[var(--text-muted)]">
        {share === null ? "Chưa giao KPI" : `Đạt ${Math.round(share * 100)}%`} · {period.pending} số chờ duyệt
      </p>
      {manage ? (
        <div className="mt-3 flex items-end gap-2">
          <label className="text-xs font-medium">
            Giao KPI (số SĐT)
            <input
              type="number"
              inputMode="numeric"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              className="mt-1 block min-h-9 w-32 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 text-sm tabular-nums"
            />
          </label>
          <ConfirmButton
            spec={{
              title: `Giao ${title.toLowerCase()} ${value.trim() === "" ? "(bỏ KPI)" : `${value} số`}?`,
              description: "Thanh % của cả team tính theo con số này.",
              confirmLabel: "Lưu KPI",
              variant: "primary",
            }}
            tone="secondary"
            disabled={!valid}
            pending={save.isPending}
            error={save.error}
            onConfirm={() => save.mutate()}
          >
            Lưu
          </ConfirmButton>
        </div>
      ) : null}
    </article>
  );
}

// --- Nộp SĐT ------------------------------------------------------------------

function PhonesTab() {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [result, setResult] = useState<SeedingLine[] | null>(null);
  const mine = useQuery({ queryKey: ["seeding", "phones", "mine"], queryFn: () => api.seedingPhones({ mine: true }) });
  const submit = useMutation({
    mutationFn: () => api.submitSeedingPhones(text),
    onSuccess: (answer) => {
      setResult(answer.lines);
      setText("");
      void queryClient.invalidateQueries({ queryKey: ["seeding"] });
    },
  });
  const count = text.split("\n").filter((line) => line.trim()).length;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="panel space-y-3 p-4" aria-labelledby="seeding-submit">
        <h2 id="seeding-submit" className="text-base font-semibold">
          Nộp số điện thoại
        </h2>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={10}
          placeholder={"Mỗi số một dòng, ví dụ:\n0912 345 678\n+84 987 654 321"}
          aria-label="Số điện thoại, mỗi số một dòng"
          className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 font-mono text-sm"
        />
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-[var(--text-muted)]">{count} dòng</span>
          <PrimaryButton type="button" disabled={count === 0 || submit.isPending} onClick={() => submit.mutate()}>
            {submit.isPending ? "Đang nộp…" : "Nộp"}
          </PrimaryButton>
        </div>
        {submit.isError ? <ErrorBox error={submit.error} /> : null}
        {result ? (
          <div aria-label="Kết quả nộp" className="space-y-1">
            <p className="text-sm font-semibold">
              {result.filter((line) => line.status === "NEW").length}/{result.length} số được nhận
            </p>
            <ul className="max-h-64 space-y-1 overflow-y-auto text-xs">
              {result.map((line) => (
                <li key={line.number} className="flex items-center justify-between gap-2">
                  <span className="font-mono">{line.phone ?? line.raw}</span>
                  <Pill tone={LINE_STATUS[line.status].tone}>{LINE_STATUS[line.status].label}</Pill>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>
      <section className="panel overflow-hidden" aria-label="Số tôi đã nộp">
        <h2 className="border-b border-[var(--border)] px-4 py-3 text-base font-semibold">Số tôi đã nộp</h2>
        <PhoneList rows={mine.data ?? []} loading={mine.isPending} />
      </section>
    </div>
  );
}

function PhoneList({
  rows,
  loading,
  selected,
  onToggle,
}: {
  rows: SeedingPhone[];
  loading?: boolean;
  selected?: Set<string>;
  onToggle?: (id: string) => void;
}) {
  if (loading) return <Loading />;
  if (rows.length === 0) {
    return (
      <div className="p-4">
        <Empty message="Chưa có số nào." />
      </div>
    );
  }
  return (
    <ul className="max-h-[28rem] divide-y divide-[var(--border)] overflow-y-auto">
      {rows.map((row) => (
        <li key={row.id} className="flex items-center gap-3 px-4 py-2 text-sm">
          {onToggle ? (
            <input
              type="checkbox"
              aria-label={`Chọn ${row.phone}`}
              checked={selected?.has(row.id) ?? false}
              onChange={() => onToggle(row.id)}
            />
          ) : null}
          <span className="font-mono">{row.phone}</span>
          <span className="min-w-0 flex-1 truncate text-xs text-[var(--text-muted)]">
            {onToggle ? `${row.name} · ` : ""}
            {formatDateTime(row.submitted_at)}
          </span>
          <Pill tone={STATUS[row.status].tone}>{STATUS[row.status].label}</Pill>
        </li>
      ))}
    </ul>
  );
}

// --- Việc khác ----------------------------------------------------------------

function OtherTab() {
  const queryClient = useQueryClient();
  const [description, setDescription] = useState("");
  const [points, setPoints] = useState("");
  const mine = useQuery({ queryKey: ["seeding", "other", "mine"], queryFn: () => api.seedingOthers({ mine: true }) });
  const submit = useMutation({
    mutationFn: () => api.submitSeedingOther({ description: description.trim(), points: Number(points) }),
    onSuccess: () => {
      setDescription("");
      setPoints("");
      void queryClient.invalidateQueries({ queryKey: ["seeding"] });
    },
  });
  const number = Number(points);
  const valid = description.trim() !== "" && Number.isInteger(number) && number >= 1 && number <= 100_000;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="panel space-y-3 p-4" aria-labelledby="seeding-other">
        <h2 id="seeding-other" className="text-base font-semibold">
          Báo cáo việc khác
        </h2>
        <label className="block text-sm font-medium">
          Việc đã làm
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            maxLength={2000}
            className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm"
          />
        </label>
        <label className="block text-sm font-medium">
          Số điểm
          <input
            type="number"
            inputMode="numeric"
            value={points}
            onChange={(event) => setPoints(event.target.value)}
            className="mt-1 block min-h-10 w-40 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm tabular-nums"
          />
        </label>
        <div className="flex justify-end">
          <ConfirmButton
            spec={{
              title: `Gửi việc khác (${points || 0} điểm)?`,
              description: "Trưởng phòng PR duyệt rồi mới tính điểm, vào mục Việc khác của báo cáo.",
              confirmLabel: "Gửi",
              variant: "primary",
            }}
            disabled={!valid}
            pending={submit.isPending}
            error={submit.error}
            onConfirm={() => submit.mutate()}
          >
            Gửi duyệt
          </ConfirmButton>
        </div>
      </section>
      <section className="panel overflow-hidden" aria-label="Việc khác của tôi">
        <h2 className="border-b border-[var(--border)] px-4 py-3 text-base font-semibold">Việc khác của tôi</h2>
        <OtherList rows={mine.data ?? []} loading={mine.isPending} />
      </section>
    </div>
  );
}

function OtherList({
  rows,
  loading,
  selected,
  onToggle,
}: {
  rows: SeedingOther[];
  loading?: boolean;
  selected?: Set<string>;
  onToggle?: (id: string) => void;
}) {
  if (loading) return <Loading />;
  if (rows.length === 0) {
    return (
      <div className="p-4">
        <Empty message="Chưa có mục nào." />
      </div>
    );
  }
  return (
    <ul className="divide-y divide-[var(--border)]">
      {rows.map((row) => (
        <li key={row.id} className="flex items-start gap-3 px-4 py-2.5 text-sm">
          {onToggle ? (
            <input
              type="checkbox"
              aria-label={`Chọn việc của ${row.name}`}
              checked={selected?.has(row.id) ?? false}
              onChange={() => onToggle(row.id)}
              className="mt-1"
            />
          ) : null}
          <span className="min-w-0 flex-1">
            <span className="block">{row.description}</span>
            <span className="text-xs text-[var(--text-muted)]">
              {onToggle ? `${row.name} · ` : ""}
              {formatDateTime(row.submitted_at)}
            </span>
          </span>
          <span className="font-semibold tabular-nums">{formatScore(row.points)}đ</span>
          <Pill tone={STATUS[row.status].tone}>{STATUS[row.status].label}</Pill>
        </li>
      ))}
    </ul>
  );
}

// --- Duyệt (Trưởng phòng PR) -----------------------------------------------------

function useSelection() {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setSelected((was) => {
      const next = new Set(was);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return { selected, setSelected, toggle };
}

function ReviewTab() {
  const queryClient = useQueryClient();
  const phones = useQuery({ queryKey: ["seeding", "phones", "pending"], queryFn: () => api.seedingPhones({ status: "PENDING" }) });
  const others = useQuery({ queryKey: ["seeding", "other", "pending"], queryFn: () => api.seedingOthers({ status: "PENDING" }) });
  const phoneSelection = useSelection();
  const otherSelection = useSelection();
  const after = () => {
    phoneSelection.setSelected(new Set());
    otherSelection.setSelected(new Set());
    void queryClient.invalidateQueries({ queryKey: ["seeding"] });
    void queryClient.invalidateQueries({ queryKey: ["performance"] });
  };
  const decidePhones = useMutation({
    mutationFn: (approve: boolean) => api.decideSeedingPhones({ ids: [...phoneSelection.selected], approve }),
    onSuccess: after,
  });
  const decideOthers = useMutation({
    mutationFn: (approve: boolean) => api.decideSeedingOthers({ ids: [...otherSelection.selected], approve }),
    onSuccess: after,
  });
  const phoneRows = phones.data ?? [];
  const otherRows = others.data ?? [];
  const phoneCount = phoneSelection.selected.size;
  const otherCount = otherSelection.selected.size;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="panel overflow-hidden" aria-label="SĐT chờ duyệt">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-base font-semibold">SĐT chờ duyệt · {phoneRows.length}</h2>
          <button
            type="button"
            className="text-xs text-[var(--accent)] hover:underline"
            onClick={() => phoneSelection.setSelected(new Set(phoneRows.map((row) => row.id)))}
          >
            Chọn tất cả
          </button>
        </header>
        <PhoneList rows={phoneRows} loading={phones.isPending} selected={phoneSelection.selected} onToggle={phoneSelection.toggle} />
        <DecisionBar
          count={phoneCount}
          what={`${phoneCount} số điện thoại`}
          pending={decidePhones.isPending}
          error={decidePhones.error}
          onDecide={(approve) => decidePhones.mutate(approve)}
        />
      </section>
      <section className="panel overflow-hidden" aria-label="Việc khác chờ duyệt">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-base font-semibold">Việc khác chờ duyệt · {otherRows.length}</h2>
          <button
            type="button"
            className="text-xs text-[var(--accent)] hover:underline"
            onClick={() => otherSelection.setSelected(new Set(otherRows.map((row) => row.id)))}
          >
            Chọn tất cả
          </button>
        </header>
        <OtherList rows={otherRows} loading={others.isPending} selected={otherSelection.selected} onToggle={otherSelection.toggle} />
        <DecisionBar
          count={otherCount}
          what={`${otherCount} việc khác`}
          pending={decideOthers.isPending}
          error={decideOthers.error}
          onDecide={(approve) => decideOthers.mutate(approve)}
        />
      </section>
    </div>
  );
}

function DecisionBar({
  count,
  what,
  pending,
  error,
  onDecide,
}: {
  count: number;
  what: string;
  pending: boolean;
  error: unknown;
  onDecide: (approve: boolean) => void;
}) {
  return (
    <div className="flex justify-end gap-2 border-t border-[var(--border)] px-4 py-3">
      <ConfirmButton
        spec={pointsDecisionConfirmation({ approve: false, what })}
        tone="danger"
        disabled={count === 0}
        pending={pending}
        error={error}
        onConfirm={() => onDecide(false)}
      >
        Không duyệt
      </ConfirmButton>
      <ConfirmButton
        spec={pointsDecisionConfirmation({ approve: true, what })}
        disabled={count === 0}
        pending={pending}
        error={error}
        onConfirm={() => onDecide(true)}
      >
        Duyệt {count ? count : ""}
      </ConfirmButton>
    </div>
  );
}

// --- Thành viên (Trưởng phòng PR) ---------------------------------------------------

function MembersTab() {
  const queryClient = useQueryClient();
  const members = useQuery({ queryKey: ["seeding", "members"], queryFn: api.seedingMembers });
  const candidates = useQuery({ queryKey: ["seeding", "candidates"], queryFn: api.seedingCandidates });
  const [chosen, setChosen] = useState("");
  const after = () => {
    setChosen("");
    void queryClient.invalidateQueries({ queryKey: ["seeding"] });
    void queryClient.invalidateQueries({ queryKey: ["units", "me"] });
  };
  const add = useMutation({ mutationFn: () => api.addSeedingMember(chosen), onSuccess: after });
  const remove = useMutation({ mutationFn: (userId: string) => api.removeSeedingMember(userId), onSuccess: after });
  const name = useMemo(
    () => candidates.data?.find((person) => person.user_id === chosen)?.name ?? "",
    [candidates.data, chosen],
  );
  return (
    <section className="panel space-y-3 p-4" aria-label="Thành viên seeding">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm font-medium">
          Thêm người trong phòng PR
          <Select
            aria-label="Thêm người trong phòng PR"
            value={chosen}
            onChange={(event) => setChosen(event.target.value)}
            className="mt-1 min-w-64"
          >
            <option value="">Chọn người…</option>
            {(candidates.data ?? []).map((person) => (
              <option key={person.user_id} value={person.user_id}>
                {person.name}
              </option>
            ))}
          </Select>
        </label>
        <ConfirmButton
          spec={{
            title: `Thêm ${name} vào nhóm seeding?`,
            description: "Người này sẽ thấy mục Seeding, nộp được số điện thoại và việc khác.",
            confirmLabel: "Thêm",
            variant: "primary",
          }}
          disabled={!chosen}
          pending={add.isPending}
          error={add.error}
          onConfirm={() => add.mutate()}
        >
          Thêm
        </ConfirmButton>
      </div>
      {members.isPending ? (
        <Loading />
      ) : (members.data ?? []).length === 0 ? (
        <Empty message="Nhóm seeding chưa có ai." />
      ) : (
        <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
          {(members.data ?? []).map((person) => (
            <li key={person.user_id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
              <span>
                <span className="font-medium">{person.name}</span>
                <span className="ml-2 text-xs text-[var(--text-muted)]">{person.departments.join(", ")}</span>
              </span>
              <ConfirmButton
                spec={{
                  title: `Bỏ ${person.name} khỏi nhóm seeding?`,
                  description: "Người này không còn thấy mục Seeding. Điểm đã được duyệt vẫn giữ.",
                  confirmLabel: "Bỏ khỏi nhóm",
                  variant: "destructive",
                }}
                tone="danger"
                ariaLabel={`Bỏ ${person.name} khỏi nhóm`}
                pending={remove.isPending}
                onConfirm={() => remove.mutate(person.user_id)}
              >
                Bỏ
              </ConfirmButton>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
