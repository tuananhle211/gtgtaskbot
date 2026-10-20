"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ContentSummary, type Uuid } from "@/lib/api";
import { assignProducerConfirmation } from "@/lib/confirmations";
import { ConfirmButton } from "@/components/confirm";
import { Modal, ModalBody, ModalFooter } from "@/components/modal";
import { SecondaryButton, Select } from "@/components/pr";

/**
 * "Giao sản xuất" from the board: pick the producer, then confirm - the same
 * two steps and the same write (`POST /api/pr/contents/{id}/producer`) as the
 * piece's own page.
 */
export function AssignProducerDialog({
  item,
  people,
  names,
  onClose,
}: {
  item: ContentSummary;
  people: { user_id: Uuid; full_name: string }[];
  names: Map<string, string>;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const current = item.producer_user_id;
  const [chosen, setChosen] = useState(current ?? "");
  const assign = useMutation({
    mutationFn: (userId: string) => api.assignProducer(item.id, userId || null),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["content-lane"] });
      void queryClient.invalidateQueries({ queryKey: ["content-board"] });
      onClose();
    },
  });
  const changed = chosen !== (current ?? "");
  return (
    <Modal
      open
      onClose={onClose}
      title={current ? "Đổi người sản xuất" : "Giao sản xuất"}
      description={item.title}
      busy={assign.isPending}
    >
      <ModalBody>
        <label className="block text-sm font-medium">
          Người sản xuất
          <Select
            value={chosen}
            onChange={(event) => setChosen(event.target.value)}
            disabled={assign.isPending}
            className="mt-1 w-full"
          >
            <option value="">{current ? "Bỏ phân công" : "Chọn người…"}</option>
            {people.map((person) => (
              <option key={person.user_id} value={person.user_id}>
                {person.full_name}
              </option>
            ))}
          </Select>
        </label>
        {current ? (
          <p className="mt-2 text-xs text-[var(--text-muted)]">
            Đang giao cho {names.get(current) ?? "—"}
          </p>
        ) : null}
      </ModalBody>
      <ModalFooter>
        <SecondaryButton type="button" onClick={onClose}>
          Huỷ
        </SecondaryButton>
        <ConfirmButton
          spec={assignProducerConfirmation({
            name: chosen ? (names.get(chosen) ?? "người này") : null,
            current: current ? names.get(current) : null,
          })}
          tone={chosen ? "primary" : "danger"}
          pending={assign.isPending}
          error={assign.error}
          disabled={!changed}
          onConfirm={() => assign.mutate(chosen)}
        >
          {chosen ? "Giao" : "Bỏ phân công"}
        </ConfirmButton>
      </ModalFooter>
    </Modal>
  );
}
