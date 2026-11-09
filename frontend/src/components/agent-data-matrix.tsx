"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AgentAdmin } from "@/lib/api";
import { agentDataConfirmation } from "@/lib/confirmations";
import { Badge } from "@/components/agent-common";
import { ConfirmButton } from "@/components/confirm";
import { SecondaryButton } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

/**
 * "Dữ liệu APM agent được đọc" (2026-11-09): every agent against every kind of
 * report the agents read through brain-mcp's `apm_bao_cao`. Access is per agent:
 * whoever chats with an agent may ask it these figures, so the grid above
 * (who sees which agent) is what keeps them in the right department. Drafted
 * here, saved together (one PUT per changed agent).
 */
export function AgentDataMatrix({ agents }: { agents: AgentAdmin[] }) {
  const queryClient = useQueryClient();
  const book = useQuery({ queryKey: ["agents", "admin", "data"], queryFn: api.agentDataAreas, retry: false });
  const [drafts, setDrafts] = useState<Record<string, Set<string>>>({});

  const saved = (slug: string) => new Set(book.data?.agents[slug] ?? []);
  const current = (slug: string) => drafts[slug] ?? saved(slug);
  const same = (a: Set<string>, b: Set<string>) => a.size === b.size && [...a].every((key) => b.has(key));
  const changed = agents.filter((agent) => drafts[agent.slug] && !same(drafts[agent.slug], saved(agent.slug)));

  const toggle = (slug: string, key: string) => {
    const next = new Set(current(slug));
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setDrafts((all) => ({ ...all, [slug]: next }));
  };

  const save = useMutation({
    mutationFn: async () => {
      for (const agent of changed) {
        await api.setAgentDataAreas(agent.slug, [...drafts[agent.slug]]);
      }
    },
    onSuccess: () => {
      setDrafts({});
      queryClient.invalidateQueries({ queryKey: ["agents", "admin", "data"] });
    },
  });

  if (book.isPending) return <Loading label="Đang tải dữ liệu agent được đọc…" />;
  if (book.isError) return <ErrorBox error={book.error} onRetry={() => book.refetch()} />;
  const areas = book.data.areas;

  return (
    <section aria-label="Dữ liệu APM agent được đọc" className="space-y-2">
      <div>
        <h2 className="text-base font-semibold">Dữ liệu APM agent được đọc</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Agent lấy số liệu công ty qua công cụ <code>apm_bao_cao</code> (cùng nguồn với Trợ lý AI báo cáo). Ai chat được
          với agent thì hỏi được các mảng được tick ở đây.
        </p>
      </div>
      {!book.data.gateway_configured ? (
        <div className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
          Máy chủ chưa cấu hình cổng cho agent (AGENT_GATEWAY_TOKEN): agent chưa đọc được dữ liệu APM.
        </div>
      ) : null}
      <div className="panel table-scroll-x">
        <table className="table-dense w-full" data-testid="agent-data-matrix">
          <thead>
            <tr>
              <th className="sticky left-0 z-10 min-w-[13rem] bg-[var(--surface)]">Agent</th>
              {areas.map((area) => (
                <th key={area.key} className="text-center" title={area.description}>
                  <span className="whitespace-nowrap">{area.label}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => {
              const ticks = current(agent.slug);
              const dirty = changed.includes(agent);
              return (
                <tr key={agent.slug} className={dirty ? "bg-[var(--accent-soft)]/50" : undefined}>
                  <td className="sticky left-0 z-10 bg-[var(--surface)]">
                    <div className="flex items-center gap-2">
                      <Badge agent={agent} size="sm" />
                      <p className="truncate font-medium">
                        {agent.name}
                        {dirty ? <span className="ml-1 text-xs text-[var(--accent)]">● chưa lưu</span> : null}
                      </p>
                    </div>
                  </td>
                  {areas.map((area) => (
                    <td key={area.key} className="text-center">
                      <input
                        type="checkbox"
                        className="h-5 w-5"
                        checked={ticks.has(area.key)}
                        onChange={() => toggle(agent.slug, area.key)}
                        aria-label={`${agent.name}: ${area.label}`}
                      />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2">
        <p className="mr-auto text-sm text-[var(--text-muted)]">
          {changed.length ? `${changed.length} agent đã đổi, chưa lưu.` : "Rê chuột lên tên cột để xem mảng đó gồm số liệu gì."}
        </p>
        <SecondaryButton type="button" disabled={!changed.length || save.isPending} onClick={() => setDrafts({})}>
          Huỷ thay đổi
        </SecondaryButton>
        <ConfirmButton
          spec={agentDataConfirmation(changed.map((agent) => agent.name))}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={!changed.length}
          tone="primary"
        >
          Lưu dữ liệu agent
        </ConfirmButton>
      </div>
    </section>
  );
}
