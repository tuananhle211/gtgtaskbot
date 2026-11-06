"use client";

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type AgentAdmin, type Department, type Uuid } from "@/lib/api";
import { agentMatrixConfirmation } from "@/lib/confirmations";
import { Badge } from "@/components/agent-common";
import { ConfirmButton } from "@/components/confirm";
import { SecondaryButton } from "@/components/pr";

/**
 * "Ai thấy agent nào" (2026-11-06): every agent against every department in one
 * grid, so the OWNER sets who sees what in one go - a tick = everybody seated in
 * that department chats with the agent; "Mọi người" opens it to every account.
 * Changes are drafted here and saved together (one PUT per changed agent);
 * people one by one and on / off stay in the agent's own dialog ("Chi tiết").
 */

type Draft = { everyone: boolean; departments: Set<Uuid> };

function draftOf(agent: AgentAdmin): Draft {
  return { everyone: agent.everyone, departments: new Set(agent.department_ids) };
}

function sameDraft(a: Draft, b: Draft): boolean {
  if (a.everyone !== b.everyone || a.departments.size !== b.departments.size) return false;
  for (const id of a.departments) if (!b.departments.has(id)) return false;
  return true;
}

export function AgentAccessMatrix({
  agents,
  departments,
  onDetails,
}: {
  agents: AgentAdmin[];
  departments: Department[];
  onDetails: (agent: AgentAdmin) => void;
}) {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const columns = useMemo(
    () =>
      departments
        .filter((dept) => dept.active)
        .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name, "vi")),
    [departments],
  );
  const current = (agent: AgentAdmin) => drafts[agent.slug] ?? draftOf(agent);
  const changed = agents.filter((agent) => drafts[agent.slug] && !sameDraft(drafts[agent.slug], draftOf(agent)));

  const put = (agent: AgentAdmin, next: Draft) => setDrafts((all) => ({ ...all, [agent.slug]: next }));
  const toggleDept = (agent: AgentAdmin, id: Uuid) => {
    const draft = current(agent);
    const departments = new Set(draft.departments);
    if (departments.has(id)) departments.delete(id);
    else departments.add(id);
    put(agent, { ...draft, departments });
  };
  const toggleColumn = (id: Uuid) => {
    const open = agents.filter((agent) => !current(agent).everyone);
    const all = open.every((agent) => current(agent).departments.has(id));
    setDrafts((prev) => {
      const next = { ...prev };
      for (const agent of open) {
        const draft = prev[agent.slug] ?? draftOf(agent);
        const departments = new Set(draft.departments);
        if (all) departments.delete(id);
        else departments.add(id);
        next[agent.slug] = { ...draft, departments };
      }
      return next;
    });
  };

  const save = useMutation({
    mutationFn: async () => {
      for (const agent of changed) {
        const draft = drafts[agent.slug];
        await api.updateAgentAccess(agent.slug, {
          everyone: draft.everyone,
          department_ids: [...draft.departments],
        });
      }
    },
    onSuccess: () => {
      setDrafts({});
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      queryClient.invalidateQueries({ queryKey: ["units", "me"] });
    },
    onError: () => queryClient.invalidateQueries({ queryKey: ["agents", "admin"] }),
  });

  const seenBy = (dept: Department) =>
    agents.filter((agent) => agent.active && (current(agent).everyone || current(agent).departments.has(dept.id))).length;

  return (
    <section aria-label="Ai thấy agent nào" className="space-y-2">
      <div className="panel table-scroll-x">
        <table className="table-dense w-full" data-testid="agent-matrix">
          <thead>
            <tr>
              <th className="sticky left-0 z-10 min-w-[13rem] bg-[var(--surface)]">Agent</th>
              <th className="text-center">Mọi người</th>
              {columns.map((dept) => (
                <th key={dept.id} className="text-center">
                  <button
                    type="button"
                    onClick={() => toggleColumn(dept.id)}
                    title={`${dept.name}: tick / bỏ tick cả cột`}
                    className="min-h-9 whitespace-nowrap rounded-md px-1.5 hover:bg-[var(--surface-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
                  >
                    {dept.name}
                  </button>
                </th>
              ))}
              <th className="text-right">Từng người</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => {
              const draft = current(agent);
              const dirty = changed.includes(agent);
              return (
                <tr key={agent.slug} className={dirty ? "bg-[var(--accent-soft)]/50" : undefined}>
                  <td className="sticky left-0 z-10 bg-[var(--surface)]">
                    <div className="flex items-center gap-2">
                      <Badge agent={agent} size="sm" />
                      <div className="min-w-0 max-w-[12rem]">
                        <p className="truncate font-medium">
                          {agent.name}
                          {dirty ? <span className="ml-1 text-xs text-[var(--accent)]">● chưa lưu</span> : null}
                        </p>
                        <p className="truncate text-xs text-[var(--text-muted)]" title={agent.title}>{agent.active ? agent.title : "Đã tắt"}</p>
                      </div>
                    </div>
                  </td>
                  <td className="text-center">
                    <input
                      type="checkbox"
                      className="h-5 w-5"
                      checked={draft.everyone}
                      onChange={() => put(agent, { ...draft, everyone: !draft.everyone })}
                      aria-label={`${agent.name}: mọi người`}
                    />
                  </td>
                  {columns.map((dept) => (
                    <td key={dept.id} className="text-center">
                      <input
                        type="checkbox"
                        className="h-5 w-5 disabled:opacity-40"
                        checked={draft.everyone || draft.departments.has(dept.id)}
                        disabled={draft.everyone}
                        onChange={() => toggleDept(agent, dept.id)}
                        aria-label={`${agent.name}: ${dept.name}`}
                      />
                    </td>
                  ))}
                  <td className="text-right">
                    <SecondaryButton type="button" onClick={() => onDetails(agent)} className="whitespace-nowrap">
                      {agent.user_ids.length ? `${agent.user_ids.length} người · Chi tiết` : "Chi tiết"}
                    </SecondaryButton>
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td className="sticky left-0 z-10 bg-[var(--surface)] text-xs text-[var(--text-muted)]">Phòng thấy</td>
              <td />
              {columns.map((dept) => (
                <td key={dept.id} className="text-center text-xs tabular-nums text-[var(--text-muted)]">
                  {seenBy(dept)} agent
                </td>
              ))}
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
      <div className="flex flex-wrap items-center justify-end gap-2">
        <p className="mr-auto text-sm text-[var(--text-muted)]">
          {changed.length
            ? `${changed.length} agent đã đổi, chưa lưu.`
            : "Tick vào ô để mở agent cho cả phòng; bấm tên phòng để tick / bỏ cả cột."}
        </p>
        <SecondaryButton type="button" disabled={!changed.length || save.isPending} onClick={() => setDrafts({})}>
          Huỷ thay đổi
        </SecondaryButton>
        <ConfirmButton
          spec={agentMatrixConfirmation(changed.map((agent) => agent.name))}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          disabled={!changed.length}
          tone="primary"
        >
          Lưu thay đổi
        </ConfirmButton>
      </div>
    </section>
  );
}
