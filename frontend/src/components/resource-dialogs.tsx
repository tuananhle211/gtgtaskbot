"use client";

import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type ResourceDetail,
  type ResourceOptions,
  type ResourceRow,
} from "@/lib/api";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { PrimaryButton, SecondaryButton, Select } from "@/components/pr";
import { NoticeBox } from "@/components/states";
import { FIELD } from "@/components/resource-form";
import { optionsOf } from "@/lib/resources";

/**
 * The three actions of a resource's profile that write a history line:
 * "Đổi người được bàn giao" (the handover history), "Cập nhật trạng thái"
 * (the status history, and the review date) and "Đánh dấu đã rà soát" (a
 * status line that says so, the status unchanged). Each is a small form in
 * a dialog whose submit is the step that confirms it.
 */

function useRefresh(resourceId: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["resources"] });
    void queryClient.invalidateQueries({ queryKey: ["resource", resourceId] });
  };
}

export function ChangeHolderDialog({
  open,
  onClose,
  resource,
  options,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  resource: ResourceRow;
  options: ResourceOptions | undefined;
  onSaved: (detail: ResourceDetail) => void;
}) {
  const refresh = useRefresh(resource.id);
  const ids = { holder: useId(), reason: useId(), handover: useId(), note: useId() };
  const people = useQuery({
    queryKey: ["resources", "people"],
    queryFn: () => api.resourcePeople(),
    enabled: open,
  });
  const [holder, setHolder] = useState("");
  const [reason, setReason] = useState(resource.holder ? "TRANSFER" : "NEW");
  const [handover, setHandover] = useState("");
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api.changeResourceHolder(resource.id, {
        holder_user_id: holder || null,
        reason,
        note: note.trim() || null,
        handover_id: handover || null,
      }),
    onSuccess: (detail) => {
      refresh();
      onSaved(detail);
    },
  });
  const reasons = options?.handover_reasons ?? [
    { value: "NEW", label: "Cấp mới" },
    { value: "TRANSFER", label: "Chuyển người" },
    { value: "RETURN", label: "Thu hồi" },
  ];
  const returning = reason === "RETURN";
  const candidates = (people.data?.people ?? []).filter(
    (person) => person.active && person.user_id !== resource.holder?.user_id,
  );
  const ready = returning || holder;
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Đổi người được bàn giao · ${resource.code}`}
      description={
        resource.holder
          ? `Đang giữ: ${resource.holder.full_name}. Để trống người mới khi thu hồi về Phòng Tài nguyên.`
          : "Đang ở Phòng Tài nguyên."
      }
      busy={save.isPending}
    >
      <form
        aria-label="Đổi người được bàn giao"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready && !save.isPending) save.mutate();
        }}
      >
        <ModalBody className="space-y-3">
          <label htmlFor={ids.reason} className="block text-xs text-[var(--text-muted)]">
            Lý do
            <Select
              id={ids.reason}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className="mt-1 w-full text-sm"
            >
              {reasons.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          </label>
          <label htmlFor={ids.holder} className="block text-xs text-[var(--text-muted)]">
            Người giữ mới
            <Select
              id={ids.holder}
              value={holder}
              onChange={(event) => setHolder(event.target.value)}
              className="mt-1 w-full text-sm"
            >
              <option value="">{returning ? "— Về Phòng Tài nguyên —" : "— chọn người —"}</option>
              {candidates.map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.full_name}
                  {person.departments.length > 0
                    ? ` · ${person.departments.map((d) => d.code).join(", ")}`
                    : ""}
                  {person.employment_status !== "WORKING" ? ` (${person.employment_status_label})` : ""}
                </option>
              ))}
            </Select>
          </label>
          <label htmlFor={ids.handover} className="block text-xs text-[var(--text-muted)]">
            Bàn giao về Tài nguyên (tuỳ chọn)
            <Select
              id={ids.handover}
              value={handover}
              onChange={(event) => setHandover(event.target.value)}
              className="mt-1 w-full text-sm"
            >
              <option value="">— Giữ nguyên: {resource.handover.name} —</option>
              {optionsOf(options, "HANDOVER").map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </Select>
          </label>
          <label htmlFor={ids.note} className="block text-xs text-[var(--text-muted)]">
            Ghi chú
            <textarea
              id={ids.note}
              value={note}
              rows={2}
              onChange={(event) => setNote(event.target.value)}
              className={`mt-1 ${FIELD} min-h-[4rem] py-2`}
            />
          </label>
          {save.isError ? <NoticeBox error={save.error} onDismiss={() => save.reset()} /> : null}
        </ModalBody>
        <ModalFooter>
          <SecondaryButton type="button" onClick={onClose} disabled={save.isPending} data-modal-close>
            Thôi
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={!ready || save.isPending}>
            {save.isPending ? "Đang lưu…" : returning && !holder ? "Thu hồi về Phòng TN" : "Đổi người giữ"}
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}

export function UpdateStatusDialog({
  open,
  onClose,
  resource,
  options,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  resource: ResourceRow;
  options: ResourceOptions | undefined;
  onSaved: (detail: ResourceDetail) => void;
}) {
  const refresh = useRefresh(resource.id);
  const ids = { status: useId(), note: useId() };
  const [status, setStatus] = useState(resource.status.id);
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () => api.updateResourceStatus(resource.id, { status_id: status, note: note.trim() || null }),
    onSuccess: (detail) => {
      refresh();
      onSaved(detail);
    },
  });
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Cập nhật trạng thái · ${resource.code}`}
      description={`Đang: ${resource.status.name}. Ngày rà soát gần nhất chuyển sang hôm nay.`}
      busy={save.isPending}
    >
      <form
        aria-label="Cập nhật trạng thái"
        onSubmit={(event) => {
          event.preventDefault();
          if (!save.isPending) save.mutate();
        }}
      >
        <ModalBody className="space-y-3">
          <label htmlFor={ids.status} className="block text-xs text-[var(--text-muted)]">
            Trạng thái mới
            <Select
              id={ids.status}
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="mt-1 w-full text-sm"
            >
              {optionsOf(options, "STATUS", resource.status.id).map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                  {option.status_group_label ? ` · ${option.status_group_label}` : ""}
                </option>
              ))}
            </Select>
          </label>
          <label htmlFor={ids.note} className="block text-xs text-[var(--text-muted)]">
            Ghi chú
            <textarea
              id={ids.note}
              value={note}
              rows={2}
              onChange={(event) => setNote(event.target.value)}
              className={`mt-1 ${FIELD} min-h-[4rem] py-2`}
            />
          </label>
          {save.isError ? <NoticeBox error={save.error} onDismiss={() => save.reset()} /> : null}
        </ModalBody>
        <ModalFooter>
          <SecondaryButton type="button" onClick={onClose} disabled={save.isPending} data-modal-close>
            Thôi
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={save.isPending}>
            {save.isPending ? "Đang lưu…" : "Cập nhật trạng thái"}
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}

export function ReviewDialog({
  open,
  onClose,
  resource,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  resource: ResourceRow;
  onSaved: (detail: ResourceDetail) => void;
}) {
  const refresh = useRefresh(resource.id);
  const noteId = useId();
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () => api.reviewResource(resource.id, { note: note.trim() || null }),
    onSuccess: (detail) => {
      refresh();
      onSaved(detail);
    },
  });
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Đánh dấu đã rà soát · ${resource.code}`}
      description={`Trạng thái giữ nguyên (${resource.status.name}); lịch sử ghi một dòng "Đã rà soát" và ngày rà soát chuyển sang hôm nay.`}
      busy={save.isPending}
    >
      <form
        aria-label="Đánh dấu đã rà soát"
        onSubmit={(event) => {
          event.preventDefault();
          if (!save.isPending) save.mutate();
        }}
      >
        <ModalBody className="space-y-3">
          <label htmlFor={noteId} className="block text-xs text-[var(--text-muted)]">
            Ghi chú (tuỳ chọn)
            <textarea
              id={noteId}
              value={note}
              rows={2}
              onChange={(event) => setNote(event.target.value)}
              className={`mt-1 ${FIELD} min-h-[4rem] py-2`}
            />
          </label>
          {save.isError ? <NoticeBox error={save.error} onDismiss={() => save.reset()} /> : null}
        </ModalBody>
        <ModalFooter>
          <SecondaryButton type="button" onClick={onClose} disabled={save.isPending} data-modal-close>
            Thôi
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={save.isPending}>
            {save.isPending ? "Đang lưu…" : "Đã rà soát"}
          </PrimaryButton>
        </ModalFooter>
      </form>
    </Modal>
  );
}
