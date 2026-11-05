"use client";

import { Suspense } from "react";
import { AgentRoomsWorkspace } from "@/components/agent-rooms";
import { Loading } from "@/components/states";

/** "Phòng họp" (0093): several agents talk in turns, people read along and write. */
export default function AgentRoomsPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải phòng họp…" />}>
      <AgentRoomsWorkspace />
    </Suspense>
  );
}
