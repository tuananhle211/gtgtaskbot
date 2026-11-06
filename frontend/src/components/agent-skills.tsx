"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type AgentInfo, type AgentSkill, type AgentSkillSet } from "@/lib/api";
import { Badge } from "@/components/agent-common";
import { Modal, ModalBody } from "@/components/modal";

/**
 * What an agent can do ("Đội agent", 2026-11-06): the skills read on the agents'
 * VPS - the ones Emma approved in the Second Brain, the agent's own not approved
 * yet, and Hermes' bundled ones (folded away). The count on an agent's card is
 * the first two: the bundled ones are the same for every agent.
 */

export function useAgentSkills() {
  return useQuery({ queryKey: ["agents", "skills"], queryFn: api.agentSkills, staleTime: 5 * 60_000 });
}

/** The number shown on an agent's card: approved + its own. */
export function skillCount(set: AgentSkillSet | undefined): number | null {
  return set ? set.approved.length + set.own.length : null;
}

export function skillSummary(set: AgentSkillSet): string {
  return `${set.approved.length} skill Apexmed · ${set.own.length} tự học · ${set.builtin.length} có sẵn của Hermes`;
}

/** The agent's skills, grouped. `null` set = the VPS could not say. */
export function AgentSkillList({ set, agent }: { set: AgentSkillSet | null | undefined; agent: AgentInfo }) {
  if (set === undefined) return <p className="text-sm text-[var(--text-muted)]">Đang tải skill…</p>;
  if (set === null) {
    return (
      <p className="rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3 text-sm text-[var(--text-muted)]">
        Chưa đọc được danh sách skill từ máy chủ agent. Thử lại sau.
      </p>
    );
  }
  const groups = new Map<string, AgentSkill[]>();
  for (const skill of set.builtin) {
    const key = skill.group ?? "khác";
    groups.set(key, [...(groups.get(key) ?? []), skill]);
  }
  return (
    <div className="space-y-4 text-left" data-testid="agent-skills">
      <Section
        title="Skill Apexmed"
        hint="Emma đã duyệt trong Bộ não; agent dùng ngay."
        skills={set.approved}
        empty={`${agent.name} chưa có skill Apexmed nào.`}
      />
      {set.own.length > 0 ? (
        <Section
          title="Agent tự học"
          hint="Chưa qua duyệt: agent đề xuất lên Bộ não để Emma xem."
          skills={set.own}
          empty=""
          tone="warn"
        />
      ) : null}
      {set.builtin.length > 0 ? (
        <details className="rounded-lg border border-[var(--border)] bg-[var(--surface)]">
          <summary className="flex min-h-11 cursor-pointer items-center justify-between gap-2 px-3 text-sm font-medium">
            <span>Có sẵn của Hermes</span>
            <span className="text-xs text-[var(--text-muted)]">{set.builtin.length} skill</span>
          </summary>
          <div className="space-y-3 border-t border-[var(--border)] p-3">
            {[...groups.entries()].map(([group, skills]) => (
              <div key={group}>
                <p className="brand-eyebrow mb-1">{group}</p>
                <ul className="space-y-1">
                  {skills.map((skill) => (
                    <SkillRow key={skill.name} skill={skill} />
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function Section({
  title,
  hint,
  skills,
  empty,
  tone = "accent",
}: {
  title: string;
  hint: string;
  skills: AgentSkill[];
  empty: string;
  tone?: "accent" | "warn";
}) {
  return (
    <section>
      <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <h3 className="whitespace-nowrap text-sm font-semibold">
          {title}{" "}
          <span
            className={`ml-1 rounded-full px-2 py-0.5 text-xs font-semibold ${
              tone === "warn"
                ? "bg-[var(--warn-soft)] text-[var(--warn)]"
                : "bg-[var(--accent-soft)] text-[var(--accent-strong)]"
            }`}
          >
            {skills.length}
          </span>
        </h3>
        <p className="text-xs text-[var(--text-muted)]">{hint}</p>
      </div>
      {skills.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">{empty}</p>
      ) : (
        <ul className="space-y-1.5">
          {skills.map((skill) => (
            <SkillRow key={skill.name} skill={skill} />
          ))}
        </ul>
      )}
    </section>
  );
}

function SkillRow({ skill }: { skill: AgentSkill }) {
  return (
    <li className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
      <p className="font-mono text-[13px] font-semibold text-[var(--text)]">{skill.name}</p>
      {skill.description ? <p className="mt-0.5 text-xs text-[var(--text-muted)]">{skill.description}</p> : null}
    </li>
  );
}

/** The list in a dialog (the count on a card, the header of a chat). */
export function AgentSkillsDialog({
  agent,
  set,
  onClose,
}: {
  agent: AgentInfo | null;
  set: AgentSkillSet | null | undefined;
  onClose: () => void;
}) {
  return (
    <Modal
      open={agent !== null}
      onClose={onClose}
      size="lg"
      title={agent ? `Skill của ${agent.name}` : "Skill"}
      description={set ? skillSummary(set) : undefined}
    >
      <ModalBody>
        {agent ? (
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <Badge agent={agent} size="lg" />
              <div className="min-w-0">
                <p className="font-semibold">{agent.name}</p>
                <p className="text-sm text-[var(--text-muted)]">{agent.description}</p>
              </div>
            </div>
            <AgentSkillList set={set} agent={agent} />
          </div>
        ) : null}
      </ModalBody>
    </Modal>
  );
}
