"use client";

import { Suspense } from "react";
import { AgentsWorkspace } from "@/components/agent-chat";
import { Loading } from "@/components/states";

/** "Đội agent" (0091): chat with the company's Hermes agents. */
export default function AgentsPage() {
  return (
    <Suspense fallback={<Loading label="Đang tải đội agent…" />}>
      <AgentsWorkspace />
    </Suspense>
  );
}
