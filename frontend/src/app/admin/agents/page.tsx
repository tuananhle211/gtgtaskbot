"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AgentAdmin, type Department, type DirectoryUser, type Uuid } from "@/lib/api";
import { agentAccessConfirmation } from "@/lib/confirmations";
import { AdminTabs } from "@/components/admin-tabs";
import { AgentAccessMatrix } from "@/components/agent-access-matrix";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PageHeader, SecondaryButton } from "@/components/pr";
import { ErrorBox, Loading } from "@/components/states";

/**
 * "Quản trị › Đội agent" (0091), the OWNER's: which Hermes agents are on, and
 * who may chat with each - its departments (everybody seated there), single
 * people, or everyone. Since 2026-11-06 the departments are ticked in one grid
 * (`AgentAccessMatrix`); people and on / off stay in the agent's dialog. Whoever holds "Chat với mọi agent" (Phòng ban / Quyền)
 * sees every agent whatever this says.
 */
export default function AgentsAdminPage() {
  const agents = useQuery({ queryKey: ["agents", "admin"], queryFn: api.agentsAdmin, retry: false });
  const departments = useQuery({ queryKey: ["departments"], queryFn: api.departments });
  const directory = useQuery({ queryKey: ["units", "directory"], queryFn: api.unitDirectory });
  const [editing, setEditing] = useState<AgentAdmin | null>(null);


  return (
    <div className="space-y-4">
      <AdminTabs active="agents" />
      <PageHeader
        title="Đội agent"
        subtitle="Ai thấy agent nào: tick phòng ban cho từng agent (cả phòng thấy), “Mọi người” để mở cho tất cả, “Chi tiết” để thêm từng người hoặc bật / tắt agent. Chủ sở hữu, quản trị viên và người có quyền “Chat với mọi agent” luôn thấy tất cả."
      />
      {agents.data && !agents.data.configured ? (
        <div className="rounded-lg border border-[var(--warn)]/30 bg-[var(--warn-soft)] p-3 text-sm">
          Máy chủ chưa cấu hình kết nối tới agent (HERMES_RELAY_URL / HERMES_RELAY_TOKEN): mọi người chưa gửi tin được.
        </div>
      ) : null}
      {agents.isPending ? <Loading label="Đang tải đội agent…" /> : null}
      {agents.isError ? <ErrorBox error={agents.error} onRetry={() => agents.refetch()} /> : null}
      {agents.data ? (
        <AgentAccessMatrix
          agents={agents.data.agents}
          departments={departments.data?.departments ?? []}
          onDetails={setEditing}
        />
      ) : null}
      {editing ? (
        <AccessDialog
          key={editing.slug}
          agent={editing}
          departments={departments.data?.departments ?? []}
          people={directory.data ?? []}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </div>
  );
}

function AccessDialog({
  agent,
  departments,
  people,
  onClose,
}: {
  agent: AgentAdmin;
  departments: Department[];
  people: DirectoryUser[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [active, setActive] = useState(agent.active);
  const [everyone, setEveryone] = useState(agent.everyone);
  const [depts, setDepts] = useState<Set<Uuid>>(new Set(agent.department_ids));
  const [users, setUsers] = useState<Set<Uuid>>(new Set(agent.user_ids));
  const [query, setQuery] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api.updateAgentAccess(agent.slug, {
        active,
        everyone,
        department_ids: [...depts],
        user_ids: [...users],
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      queryClient.invalidateQueries({ queryKey: ["units", "me"] });
      onClose();
    },
  });
  const toggle = (set: Set<Uuid>, id: Uuid, put: (next: Set<Uuid>) => void) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    put(next);
  };
  const fold = (text: string) => text.normalize("NFD").replace(/\p{M}/gu, "").replace(/đ/gi, "d").toLowerCase();
  const found = people
    .filter((person) => person.active && (users.has(person.user_id) || fold(person.full_name).includes(fold(query.trim()))))
    .slice(0, 40);
  const summary = !active
    ? `${agent.name} sẽ tắt: không ai thấy agent này nữa (các cuộc trò chuyện cũ vẫn được giữ).`
    : everyone
      ? `Mọi tài khoản đăng nhập đều chat được với ${agent.name}.`
      : `${agent.name} mở cho ${depts.size} phòng ban và ${users.size} người (cùng chủ sở hữu, quản trị viên).`;

  return (
    <Modal open onClose={onClose} title={`Quyền chat · ${agent.name}`} busy={save.isPending} size="lg">
      <ModalBody className="space-y-4">
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} />
          Bật agent này
        </label>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" checked={everyone} onChange={(event) => setEveryone(event.target.checked)} />
          Mọi người đều chat được
        </label>
        <fieldset disabled={everyone} className="space-y-1 disabled:opacity-50">
          <legend className="text-sm font-semibold">Phòng ban</legend>
          <div className="grid gap-1 sm:grid-cols-2">
            {departments
              .filter((dept) => dept.active || depts.has(dept.id))
              .map((dept) => (
                <label key={dept.id} className="flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm hover:bg-[var(--surface-muted)]">
                  <input type="checkbox" checked={depts.has(dept.id)} onChange={() => toggle(depts, dept.id, setDepts)} />
                  {dept.name}
                </label>
              ))}
          </div>
        </fieldset>
        <fieldset disabled={everyone} className="space-y-1 disabled:opacity-50">
          <legend className="text-sm font-semibold">Thêm từng người</legend>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tìm theo tên…"
            aria-label="Tìm người"
            className="min-h-11 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 text-sm text-[var(--text)]"
          />
          <div className="max-h-56 overflow-y-auto">
            {found.map((person) => (
              <label key={person.user_id} className="flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm hover:bg-[var(--surface-muted)]">
                <input
                  type="checkbox"
                  checked={users.has(person.user_id)}
                  onChange={() => toggle(users, person.user_id, setUsers)}
                />
                {person.full_name}
              </label>
            ))}
          </div>
        </fieldset>
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose} disabled={save.isPending}>
          Thôi
        </SecondaryButton>
        <ConfirmButton
          spec={agentAccessConfirmation(agent.name, summary)}
          onConfirm={() => save.mutate()}
          pending={save.isPending}
          error={save.error}
          tone="primary"
        >
          Lưu quyền
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}
