"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TASK_STATUS_ORDER, formatWhen, priorityLabel, taskStatusLabel } from "@/lib/labels";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";
import { Select } from "@/components/pr";
import { ConfirmButton } from "@/components/confirm";
import { assignTaskConfirmation, taskStatusConfirmation } from "@/lib/confirmations";

/** The canonical `PrTaskStatus` codes, in the order the enum declares them. */
const STATUSES = TASK_STATUS_ORDER;

/**
 * Tasks: list, create, assign, move.
 *
 * `overdue` is a server-side filter, not a client-side sort of a page of rows -
 * which statuses count as "finished" is the task matrix's business, and a browser
 * comparing deadlines to `Date.now()` would disagree with it eventually.
 *
 * Assignment is by `user_id` from a picker, never a typed name. That is the same
 * rule the Telegram side enforces by asking when a name is ambiguous; here the
 * ambiguity cannot arise.
 */
export default function TasksPage() {
  const queryClient = useQueryClient();
  const [overdue, setOverdue] = useState(false);
  const [status, setStatus] = useState("");
  const [creating, setCreating] = useState(false);

  const tasks = useQuery({
    queryKey: ["tasks", status, overdue],
    queryFn: () => api.listTasks({ status: status || undefined, overdue }),
  });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const names = new Map((people.data ?? []).map((person) => [person.user_id, person.full_name]));

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["tasks"] });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          disabled={overdue}
          className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-sm"
        >
          <option value="">Tất cả trạng thái</option>
          {STATUSES.map((code) => (
            <option key={code} value={code}>
              {taskStatusLabel(code)}
            </option>
          ))}
        </Select>
        <label className="flex items-center gap-1.5 text-sm">
          <input type="checkbox" checked={overdue} onChange={(event) => setOverdue(event.target.checked)} />
          Chỉ task quá hạn
        </label>
        <button
          type="button"
          onClick={() => setCreating((open) => !open)}
          className="rounded border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-sm"
        >
          {creating ? "Đóng" : "Tạo task"}
        </button>
      </div>

      {creating ? (
        <CreateTaskForm
          onCreated={() => {
            setCreating(false);
            invalidate();
          }}
        />
      ) : null}

      {tasks.isPending ? <Loading /> : null}
      {tasks.isError ? <ErrorBox error={tasks.error} onRetry={() => tasks.refetch()} /> : null}
      {tasks.data?.length === 0 ? (
        <Empty message={overdue ? "Không có task nào quá hạn." : "Chưa có task nào."} />
      ) : null}

      <ul className="space-y-2">
        {tasks.data?.map((task) => (
          <TaskRow key={task.id} taskId={task.id} names={names} onChanged={invalidate} initial={task} />
        ))}
      </ul>
    </div>
  );
}

function TaskRow({
  taskId,
  names,
  onChanged,
  initial,
}: {
  taskId: string;
  names: Map<string, string>;
  onChanged: () => void;
  initial: Awaited<ReturnType<typeof api.listTasks>>[number];
}) {
  const [open, setOpen] = useState(false);
  const detail = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.getTask(taskId),
    enabled: open,
  });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people, enabled: open });
  const [assignee, setAssignee] = useState("");

  const move = useMutation({
    mutationFn: (status: string) => api.setTaskStatus(taskId, { status }),
    onSuccess: onChanged,
  });
  const assign = useMutation({
    mutationFn: () => api.assignTask(taskId, { user_id: assignee, assignment_role: "PRIMARY" }),
    onSuccess: () => {
      setAssignee("");
      void detail.refetch();
      onChanged();
    },
  });

  return (
    <li className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3">
      <button type="button" onClick={() => setOpen((value) => !value)} className="w-full text-left">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm">
            <code className="text-xs text-[var(--text-muted)]">{initial.code}</code> {initial.title}
          </span>
          <span className="flex items-center gap-2">
            <Pill>{priorityLabel(initial.priority)}</Pill>
            <Pill tone={initial.status === "DONE" ? "good" : "neutral"}>
              {taskStatusLabel(initial.status)}
            </Pill>
          </span>
        </div>
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          {initial.task_type} · hạn {formatWhen(initial.deadline)}
        </p>
      </button>

      {open ? (
        <div className="mt-3 space-y-3 border-t border-[var(--border)] pt-3">
          {/* Step 1F.2.8. A row of four status buttons a thumb's width apart is
              exactly where an accidental "Đã hủy" comes from, so each of them
              asks - and the question names the status it is moving to. */}
          <div className="flex flex-wrap gap-1.5">
            {STATUSES.filter((code) => code !== initial.status).map((code) => (
              <ConfirmButton
                key={code}
                spec={taskStatusConfirmation(code, taskStatusLabel(code))}
                tone="secondary"
                pending={move.isPending}
                error={move.error}
                onConfirm={() => move.mutate(code)}
              >
                {taskStatusLabel(code)}
              </ConfirmButton>
            ))}
          </div>
          {move.isError ? <ErrorBox error={move.error} /> : null}

          <div>
            <h4 className="text-xs font-semibold text-[var(--text-muted)]">Người làm</h4>
            {detail.isPending ? <Loading label="Đang tải…" /> : null}
            {(detail.data?.assignments ?? []).length === 0 && detail.data ? (
              <Empty message="Chưa giao cho ai." />
            ) : (
              <ul className="mt-1 text-sm">
                {detail.data?.assignments.map((row) => (
                  <li key={row.id} className="text-[var(--text-muted)]">
                    {names.get(row.user_id) ?? row.user_id} · {row.assignment_role}
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-2 flex flex-wrap gap-2">
              <Select
                value={assignee}
                onChange={(event) => setAssignee(event.target.value)}
                className="rounded border border-[var(--border)] bg-transparent px-2 py-1 text-sm"
              >
                <option value="">— chọn người —</option>
                {(people.data ?? []).map((person) => (
                  <option key={person.user_id} value={person.user_id}>
                    {person.full_name}
                  </option>
                ))}
              </Select>
              <ConfirmButton
                spec={assignTaskConfirmation(assignee ? names.get(assignee) : null)}
                tone="secondary"
                disabled={!assignee}
                pending={assign.isPending}
                error={assign.error}
                onConfirm={() => assign.mutate()}
              >
                Giao việc
              </ConfirmButton>
            </div>
            {assign.isError ? <ErrorBox error={assign.error} /> : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}

function CreateTaskForm({ onCreated }: { onCreated: () => void }) {
  const [title, setTitle] = useState("");
  const [taskType, setTaskType] = useState("");
  const [deadline, setDeadline] = useState("");
  const create = useMutation({
    mutationFn: () =>
      api.createTask({
        task_type: taskType,
        title,
        deadline: deadline ? new Date(deadline).toISOString() : undefined,
      }),
    onSuccess: onCreated,
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
      className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-sm">
          Tiêu đề
          <input
            required
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
          />
        </label>
        <label className="text-sm">
          Loại việc
          <input
            required
            value={taskType}
            onChange={(event) => setTaskType(event.target.value)}
            placeholder="ví dụ: dựng video"
            className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
          />
        </label>
        <label className="text-sm">
          Hạn
          <input
            type="datetime-local"
            value={deadline}
            onChange={(event) => setDeadline(event.target.value)}
            className="mt-1 w-full rounded border border-[var(--border)] bg-transparent px-2 py-1.5"
          />
        </label>
      </div>
      <p className="text-xs text-[var(--text-muted)]">Mã task do hệ thống sinh.</p>
      {create.isError ? <ErrorBox error={create.error} /> : null}
      <button
        type="submit"
        disabled={create.isPending}
        className="rounded bg-[var(--text)] px-3 py-1.5 text-sm text-[var(--surface)] disabled:opacity-50"
      >
        {create.isPending ? "Đang tạo…" : "Tạo"}
      </button>
    </form>
  );
}
