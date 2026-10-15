"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AiConnection, type AiKind, type AiOverview, type Uuid } from "@/lib/api";
import { AdminTabs } from "@/components/admin-tabs";
import { ConfirmButton } from "@/components/confirm";
import { PageHeader, Select } from "@/components/pr";
import { ErrorBox, Loading, NoticeBox, Pill } from "@/components/states";
import { formatAgo } from "@/lib/labels";

/**
 * "Quản trị › Kết nối AI" (0064), the OWNER's alone: the AI services the app
 * calls - ChatGPT (OpenAI), Gemini, DeepSeek, or any OpenAI-compatible
 * gateway such as a self-hosted 9router - and, per purpose, which answers
 * first and which take over when it fails. With nothing chosen the server's
 * own `LLM_*` configuration answers, as before.
 *
 * Keys go to the server and never come back: a connection shows "••••abcd".
 * Leaving the key blank on an edit keeps the stored one.
 */
export default function AiConnectionsPage() {
  const overview = useQuery({ queryKey: ["ai"], queryFn: api.aiOverview, retry: false });
  return (
    <div className="space-y-4">
      <AdminTabs active="ai" />
      <PageHeader
        title="Kết nối AI"
        subtitle="Khai báo các AI hệ thống được gọi (ChatGPT, Gemini, DeepSeek, 9router…) và chọn AI cho từng việc, kèm AI dự phòng khi AI chính lỗi hoặc hết credit."
      />
      {overview.isPending ? <Loading label="Đang tải kết nối AI…" /> : null}
      {overview.isError ? (
        <ErrorBox error={overview.error} onRetry={() => overview.refetch()} />
      ) : null}
      {overview.data ? <Workspace data={overview.data} /> : null}
    </div>
  );
}

const FIELD =
  "min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]";
const SECTION = "rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4";

function useRefreshAi() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: ["ai"] });
}

function Workspace({ data }: { data: AiOverview }) {
  // Which purposes each connection serves, from what is saved.
  const usedFor = new Map<Uuid, string[]>();
  for (const route of data.routes) {
    for (const id of route.connection_ids) {
      usedFor.set(id, [...(usedFor.get(id) ?? []), route.label]);
    }
  }
  const unassigned = data.connections.length > 0 && usedFor.size === 0;
  return (
    <>
      {data.secret_ready ? null : (
        <p
          role="alert"
          data-testid="ai-no-secret"
          className="rounded-lg border border-amber-400 bg-amber-500/10 px-3 py-2 text-sm"
        >
          Server chưa có khoá mã hoá (PR_SECRET_ENCRYPTION_KEY) nên chưa lưu được API key. Cần cấu
          hình khoá này trên server trước.
        </p>
      )}
      {unassigned ? (
        <p
          role="alert"
          data-testid="ai-unassigned"
          className="rounded-lg border border-amber-400 bg-amber-500/10 px-3 py-2 text-sm"
        >
          Chưa gán kết nối nào cho việc nào: mọi việc AI (kể cả AI duyệt kịch bản order) vẫn chạy
          bằng cấu hình server ({data.env_provider}
          {data.env_model ? ` · ${data.env_model}` : ""}). Chọn AI chính ở bảng “AI cho từng việc”
          rồi bấm “Lưu phân công AI”.
        </p>
      ) : null}
      <p className="text-sm text-[var(--text-muted)]">
        Khi chưa chọn AI nào cho một việc, hệ thống dùng cấu hình trên server:{" "}
        <span className="font-medium text-[var(--text)]">
          {data.env_provider}
          {data.env_model ? ` · ${data.env_model}` : ""}
        </span>
        .
      </p>
      <Routes data={data} />
      <section aria-label="Danh sách kết nối AI" className={SECTION}>
        <h2 className="text-sm font-semibold">Kết nối</h2>
        {data.connections.length === 0 ? (
          <p className="mt-2 text-sm text-[var(--text-muted)]">Chưa có kết nối AI nào.</p>
        ) : (
          <ul className="mt-3 space-y-3">
            {data.connections.map((connection) => (
              <ConnectionRow
                key={`${connection.id}:${connection.model}:${connection.api_key_hint}:${connection.base_url ?? ""}:${connection.name}`}
                connection={connection}
                kinds={data.kinds}
                usedFor={usedFor.get(connection.id) ?? []}
              />
            ))}
          </ul>
        )}
      </section>
      <AddConnection kinds={data.kinds} disabled={!data.secret_ready} />
      <RouterNote />
    </>
  );
}

/** Per purpose: the main AI, then up to `max_chain - 1` fallbacks. */
function Routes({ data }: { data: AiOverview }) {
  const refresh = useRefreshAi();
  const stored = Object.fromEntries(data.routes.map((route) => [route.purpose, route.connection_ids]));
  const [draft, setDraft] = useState<Record<string, Uuid[]> | null>(null);
  const routes = draft ?? stored;
  const save = useMutation({
    mutationFn: () => api.setAiRoutes(routes),
    onSuccess: () => {
      setDraft(null);
      refresh();
    },
  });
  const set = (purpose: string, slot: number, value: string) => {
    const chain = [...(routes[purpose] ?? [])];
    if (value) chain[slot] = value;
    else chain.splice(slot);
    setDraft({ ...routes, [purpose]: chain.filter(Boolean) });
  };
  const slots = Array.from({ length: data.max_chain }, (_, index) => index);
  const name = (id: Uuid) => data.connections.find((item) => item.id === id);
  return (
    <section aria-label="AI cho từng việc" className={SECTION}>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">AI cho từng việc</h2>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            AI chính trả lời trước; nếu lỗi (sai key, hết credit, quá thời gian) thì chuyển sang
            dự phòng theo thứ tự. Thay đổi có hiệu lực trong khoảng 20 giây, không cần deploy.
          </p>
        </div>
        <ConfirmButton
          spec={{
            title: "Lưu phân công AI?",
            description: "Các việc sẽ gọi AI theo thứ tự đã chọn, áp dụng ngay cho bot, web và tác vụ nền.",
            confirmLabel: "Lưu",
          }}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={draft === null}
          tone="primary"
        >
          Lưu phân công AI
        </ConfirmButton>
      </div>
      {draft !== null ? (
        <p role="status" className="mt-2 text-xs font-medium text-[var(--warn)]">
          Có thay đổi chưa lưu: bấm “Lưu phân công AI” để áp dụng.
        </p>
      ) : null}
      <div className="mt-3 overflow-x-auto">
        <table className="table-dense">
          <thead>
            <tr>
              <th scope="col">Việc</th>
              {slots.map((slot) => (
                <th key={slot} scope="col">
                  {slot === 0 ? "AI chính" : `Dự phòng ${slot}`}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.routes.map((route) => {
              const chain = routes[route.purpose] ?? [];
              return (
                <tr key={route.purpose}>
                  <th scope="row" className="text-left font-medium">
                    {route.label}
                  </th>
                  {slots.map((slot) => {
                    // A fallback slot opens once the slot before it is filled.
                    const open = slot <= chain.length;
                    const taken = new Set(chain.filter((_, index) => index !== slot));
                    return (
                      <td key={slot}>
                        {open ? (
                          <Select
                            aria-label={`${route.label} · ${slot === 0 ? "AI chính" : `Dự phòng ${slot}`}`}
                            value={chain[slot] ?? ""}
                            onChange={(event) => set(route.purpose, slot, event.target.value)}
                            className="min-h-9 min-w-40 text-xs"
                          >
                            <option value="">
                              {slot > 0
                                ? "—"
                                : route.purpose === "DEFAULT"
                                  ? "Cấu hình server"
                                  : "Dùng Mặc định"}
                            </option>
                            {data.connections
                              .filter((item) => !taken.has(item.id))
                              .map((item) => (
                                <option key={item.id} value={item.id}>
                                  {item.name}
                                  {item.active ? "" : " (đang tắt)"}
                                </option>
                              ))}
                          </Select>
                        ) : (
                          <span className="text-xs text-[var(--text-muted)]">–</span>
                        )}
                        {chain[slot] && name(chain[slot])?.last_test_ok === false ? (
                          <span className="mt-0.5 block text-[11px] text-[var(--bad)]">
                            Lần kiểm tra gần nhất lỗi
                          </span>
                        ) : null}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {save.isError ? <NoticeBox error={save.error} /> : null}
    </section>
  );
}

function TestPill({ connection }: { connection: AiConnection }) {
  if (connection.last_test_ok === null) return <Pill tone="neutral">Chưa kiểm tra</Pill>;
  return connection.last_test_ok ? (
    <Pill tone="good">Kết nối được</Pill>
  ) : (
    <Pill tone="bad">Lỗi</Pill>
  );
}

function ConnectionRow({
  connection,
  kinds,
  usedFor,
}: {
  connection: AiConnection;
  kinds: AiKind[];
  /** The purposes whose saved chain includes it. */
  usedFor: string[];
}) {
  const refresh = useRefreshAi();
  const [editing, setEditing] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const test = useMutation({
    mutationFn: () => api.testAiConnection(connection.id),
    onSuccess: (outcome) => {
      setResult(
        outcome.ok
          ? `${outcome.message} (${(outcome.latency_ms / 1000).toFixed(1)} giây)`
          : outcome.message,
      );
      refresh();
    },
  });
  const toggle = useMutation({
    mutationFn: () => api.updateAiConnection(connection.id, { active: !connection.active }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteAiConnection(connection.id),
    onSuccess: refresh,
  });
  return (
    <li
      data-testid="ai-connection"
      className={`rounded-lg border border-[var(--border)] p-3 ${connection.active ? "" : "opacity-60"}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">
            {connection.name}{" "}
            <span className="text-xs font-normal text-[var(--text-muted)]">
              · {connection.kind_label}
            </span>
          </p>
          <p className="mt-0.5 break-all text-xs text-[var(--text-muted)]">
            Model <span className="font-mono text-[var(--text)]">{connection.model}</span> ·{" "}
            {connection.endpoint} · Key{" "}
            <span className="font-mono">{connection.api_key_hint}</span>
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
            <TestPill connection={connection} />
            {connection.active ? null : <Pill tone="warn">Đang tắt</Pill>}
            {connection.last_tested_at ? (
              <span className="text-[var(--text-muted)]">{formatAgo(connection.last_tested_at)}</span>
            ) : null}
            {connection.last_test_ok === false && connection.last_test_message ? (
              <span className="text-[var(--bad)]">{connection.last_test_message}</span>
            ) : null}
          </div>
          <p className="mt-1 text-xs" data-testid="ai-used-for">
            {usedFor.length > 0 ? (
              <>Đang dùng cho: {usedFor.join(", ")}</>
            ) : (
              <span className="text-[var(--warn)]">Chưa dùng cho việc nào</span>
            )}
          </p>
          {result ? (
            <p role="status" className="mt-1 text-xs">
              {result}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => {
              setResult(null);
              test.mutate();
            }}
            disabled={test.isPending}
            className="min-h-9 rounded-lg border border-[var(--border)] px-3 text-xs font-medium disabled:opacity-50"
          >
            {test.isPending ? "Đang kiểm tra…" : "Kiểm tra kết nối"}
          </button>
          <button
            type="button"
            onClick={() => setEditing((open) => !open)}
            aria-expanded={editing}
            className="min-h-9 rounded-lg border border-[var(--border)] px-3 text-xs font-medium"
          >
            {editing ? "Đóng" : "Sửa"}
          </button>
          <ConfirmButton
            spec={{
              title: connection.active ? `Tắt ${connection.name}?` : `Bật lại ${connection.name}?`,
              description: connection.active
                ? "Hệ thống sẽ bỏ qua kết nối này trong mọi việc, chuyển sang AI kế tiếp."
                : "Kết nối được gọi lại ở những việc đang chọn nó.",
              confirmLabel: connection.active ? "Tắt" : "Bật lại",
            }}
            onConfirm={() => toggle.mutate()}
            pending={toggle.isPending}
            error={toggle.error}
            tone="secondary"
            className="min-h-9 px-3 text-xs"
          >
            {connection.active ? "Tắt" : "Bật lại"}
          </ConfirmButton>
          <ConfirmButton
            spec={{
              title: `Xoá kết nối ${connection.name}?`,
              description: "API key bị xoá khỏi hệ thống và kết nối được gỡ khỏi mọi việc đang dùng nó.",
              confirmLabel: "Xoá",
              variant: "destructive",
            }}
            onConfirm={() => remove.mutate()}
            pending={remove.isPending}
            error={remove.error}
            tone="danger"
            ariaLabel={`Xoá kết nối ${connection.name}`}
            className="min-h-9 px-3 text-xs"
          >
            Xoá
          </ConfirmButton>
        </div>
      </div>
      {test.isError ? <NoticeBox error={test.error} /> : null}
      {editing ? (
        <EditConnection
          connection={connection}
          kind={kinds.find((item) => item.kind === connection.kind)}
          onDone={() => setEditing(false)}
        />
      ) : null}
    </li>
  );
}

function EditConnection({
  connection,
  kind,
  onDone,
}: {
  connection: AiConnection;
  kind: AiKind | undefined;
  onDone: () => void;
}) {
  const refresh = useRefreshAi();
  const [name, setName] = useState(connection.name);
  const [model, setModel] = useState(connection.model);
  const [baseUrl, setBaseUrl] = useState(connection.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api.updateAiConnection(connection.id, {
        name,
        model,
        base_url: baseUrl.trim() || null,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      }),
    onSuccess: () => {
      refresh();
      onDone();
    },
  });
  return (
    <form
      className="mt-3 grid gap-3 border-t border-[var(--border)] pt-3 sm:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <label className="text-sm">
        Tên
        <input value={name} onChange={(event) => setName(event.target.value)} className={`mt-1 ${FIELD}`} />
      </label>
      <label className="text-sm">
        Model
        <input
          value={model}
          onChange={(event) => setModel(event.target.value)}
          placeholder={kind?.model_hint}
          className={`mt-1 font-mono ${FIELD}`}
        />
      </label>
      <label className="text-sm">
        Địa chỉ API
        <input
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          placeholder={kind?.base_url ?? "http://9router:20128/v1"}
          className={`mt-1 font-mono ${FIELD}`}
        />
      </label>
      <label className="text-sm">
        API key mới
        <input
          type="password"
          autoComplete="off"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          placeholder={`Để trống: giữ key ${connection.api_key_hint}`}
          className={`mt-1 font-mono ${FIELD}`}
        />
      </label>
      {save.isError ? (
        <div className="sm:col-span-2">
          <NoticeBox error={save.error} />
        </div>
      ) : null}
      <div className="sm:col-span-2">
        <button
          type="submit"
          disabled={save.isPending}
          className="min-h-11 rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)] disabled:opacity-50"
        >
          Lưu thay đổi
        </button>
      </div>
    </form>
  );
}

function AddConnection({ kinds, disabled }: { kinds: AiKind[]; disabled: boolean }) {
  const refresh = useRefreshAi();
  const [kind, setKind] = useState(kinds[0]?.kind ?? "OPENAI");
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const info = kinds.find((item) => item.kind === kind);
  const create = useMutation({
    mutationFn: () =>
      api.createAiConnection({
        kind,
        name: name.trim() || info?.label || kind,
        model,
        api_key: apiKey.trim(),
        base_url: baseUrl.trim() || null,
      }),
    onSuccess: () => {
      setName("");
      setModel("");
      setBaseUrl("");
      setApiKey("");
      refresh();
    },
  });
  const needsUrl = info?.base_url === null;
  const ready = model.trim() !== "" && apiKey.trim() !== "" && (!needsUrl || baseUrl.trim() !== "");
  return (
    <section aria-label="Thêm kết nối AI" className={SECTION}>
      <h2 className="text-sm font-semibold">Thêm kết nối</h2>
      <form
        className="mt-3 grid gap-3 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready && !disabled) create.mutate();
        }}
      >
        <label className="text-sm">
          Loại
          <Select
            value={kind}
            onChange={(event) => setKind(event.target.value)}
            className={`mt-1 ${FIELD}`}
          >
            {kinds.map((item) => (
              <option key={item.kind} value={item.kind}>
                {item.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          Tên hiển thị
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={info?.label}
            className={`mt-1 ${FIELD}`}
          />
        </label>
        <label className="text-sm">
          Model
          <input
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder={`Ví dụ: ${info?.model_hint ?? ""}`}
            className={`mt-1 font-mono ${FIELD}`}
          />
        </label>
        <label className="text-sm">
          Địa chỉ API{needsUrl ? "" : " (không bắt buộc)"}
          <input
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder={info?.base_url ?? "http://9router:20128/v1"}
            className={`mt-1 font-mono ${FIELD}`}
          />
        </label>
        <div className="text-sm sm:col-span-2">
          <label htmlFor="ai-new-key">API key</label>
          <input
            id="ai-new-key"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            aria-describedby={info ? "ai-new-key-hint" : undefined}
            className={`mt-1 font-mono ${FIELD}`}
          />
          {info ? (
            <span id="ai-new-key-hint" className="mt-1 block text-xs text-[var(--text-muted)]">
              {info.key_hint}
            </span>
          ) : null}
        </div>
        {create.isError ? (
          <div className="sm:col-span-2">
            <NoticeBox error={create.error} />
          </div>
        ) : null}
        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={!ready || disabled || create.isPending}
            className="min-h-11 rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-[var(--accent-text)] disabled:opacity-50"
          >
            Thêm kết nối
          </button>
        </div>
      </form>
    </section>
  );
}

/** What 9router is for here, and what not to use it for. */
function RouterNote() {
  return (
    <section aria-label="Về 9router" className={`${SECTION} text-sm`}>
      <h2 className="font-semibold">Dùng 9router</h2>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-[var(--text-muted)]">
        <li>
          Chạy 9router trên cùng server (Docker), bật <code>REQUIRE_API_KEY</code>, đổi mật khẩu
          dashboard mặc định, không mở cổng 20128 ra internet.
        </li>
        <li>
          Thêm kết nối loại “9router / chuẩn OpenAI khác” với địa chỉ{" "}
          <code>http://9router:20128/v1</code>, API key tạo trong dashboard 9router, model là tên
          model hoặc tên combo của 9router.
        </li>
        <li>
          Chỉ nên nối 9router với các tài khoản API key (DeepSeek, Gemini, OpenAI, OpenRouter…).
          Dùng gói thuê bao cá nhân (ChatGPT/Codex, Claude Code, Copilot…) qua proxy cho hệ thống
          công ty có thể trái điều khoản của hãng và bị khoá tài khoản.
        </li>
      </ul>
    </section>
  );
}
