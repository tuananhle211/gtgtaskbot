"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ResourceDetail, type ResourceRow } from "@/lib/api";
import { ConfirmDialog } from "@/components/confirm";
import { DangerButton } from "@/components/pr";
import { RowMenu, type RowMenuItem } from "@/components/row-menu";
import { ResourceFormDialog } from "@/components/resource-form";
import { ChangeHolderDialog, ReviewDialog, UpdateStatusDialog } from "@/components/resource-dialogs";

/**
 * A resource's quick actions (2026-10-21): "Sửa", "Cập nhật trạng thái",
 * "Đã rà soát", "Đổi người được bàn giao", "Xoá" - each only when the row's
 * own flags allow it (`can_edit`, `can_handover`, `can_delete`, decided by the
 * server). Drawn as the "⋯" menu at the end of a table row; the dialogs are
 * the same ones the profile page opens.
 */
type Dialog = "edit" | "status" | "review" | "holder" | "delete" | null;

export function ResourceRowActions({
  row,
  onDeleted,
}: {
  row: ResourceRow;
  /** After a deletion (the profile page leaves for the list). */
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<Dialog>(null);
  const options = useQuery({
    queryKey: ["resources", "options"],
    queryFn: () => api.resourceOptions(),
    enabled: dialog !== null && dialog !== "delete" && dialog !== "review",
  });
  const remove = useMutation({
    mutationFn: () => api.deleteResource(row.id),
    onSuccess: () => {
      setDialog(null);
      void queryClient.invalidateQueries({ queryKey: ["resources"] });
      queryClient.removeQueries({ queryKey: ["resource", row.id] });
      onDeleted?.();
    },
  });
  const saved = (next: ResourceDetail) => {
    queryClient.setQueryData(["resource", row.id], next);
    void queryClient.invalidateQueries({ queryKey: ["resources"] });
    setDialog(null);
  };
  const items: RowMenuItem[] = [];
  if (row.can_edit) {
    items.push({ key: "edit", label: "Sửa", onSelect: () => setDialog("edit") });
    items.push({ key: "status", label: "Cập nhật trạng thái", onSelect: () => setDialog("status") });
    items.push({ key: "review", label: "Đã rà soát", onSelect: () => setDialog("review") });
  }
  if (row.can_handover) {
    items.push({ key: "holder", label: "Đổi người được bàn giao", onSelect: () => setDialog("holder") });
  }
  if (row.can_delete) {
    items.push({ key: "delete", label: "Xoá", tone: "danger", onSelect: () => setDialog("delete") });
  }
  if (items.length === 0) return null;
  return (
    <>
      <RowMenu label={`Thao tác với ${row.code}`} items={items} />
      {dialog === "edit" ? (
        <ResourceFormDialog
          open
          onClose={() => setDialog(null)}
          options={options.data}
          resource={row}
          onSaved={saved}
          limited={!row.can_change_identity}
        />
      ) : null}
      {dialog === "status" ? (
        <UpdateStatusDialog open onClose={() => setDialog(null)} resource={row} options={options.data} onSaved={saved} />
      ) : null}
      {dialog === "review" ? (
        <ReviewDialog open onClose={() => setDialog(null)} resource={row} onSaved={saved} />
      ) : null}
      {dialog === "holder" ? (
        <ChangeHolderDialog open onClose={() => setDialog(null)} resource={row} options={options.data} onSaved={saved} />
      ) : null}
      <DeleteResourceDialog
        open={dialog === "delete"}
        row={row}
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate()}
        onCancel={() => {
          remove.reset();
          setDialog(null);
        }}
      />
    </>
  );
}

/** "Xoá" on the profile page: the button, its confirmation and the request. */
export function DeleteResourceButton({ row, onDeleted }: { row: ResourceRow; onDeleted: () => void }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const remove = useMutation({
    mutationFn: () => api.deleteResource(row.id),
    onSuccess: () => {
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["resources"] });
      queryClient.removeQueries({ queryKey: ["resource", row.id] });
      onDeleted();
    },
  });
  return (
    <>
      <DangerButton type="button" onClick={() => setOpen(true)}>
        Xoá
      </DangerButton>
      <DeleteResourceDialog
        open={open}
        row={row}
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate()}
        onCancel={() => {
          remove.reset();
          setOpen(false);
        }}
      />
    </>
  );
}

export function DeleteResourceDialog({
  open,
  row,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  row: ResourceRow;
  pending: boolean;
  error: unknown;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <ConfirmDialog
      open={open}
      spec={{
        title: `Xoá ${row.code} · ${row.name}?`,
        description:
          "Tài nguyên biến mất khỏi mọi danh sách, báo cáo và tài nguyên của người giữ. Lịch sử bàn giao và trạng thái vẫn được giữ lại; ID được dùng lại cho tài nguyên khác.",
        confirmLabel: "Xoá tài nguyên",
        variant: "destructive",
      }}
      pending={pending}
      error={error}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
