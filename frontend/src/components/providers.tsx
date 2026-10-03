"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "@/lib/api";

/**
 * The query client, with one deliberate retry rule.
 *
 * A failed request is retried once - **except** for 401, 403, 404 and 409. None
 * of those improves on a second attempt: the session is gone, the person is not
 * allowed, the record does not exist, or the record moved. Retrying a 409 in
 * particular is actively wrong, because it would re-send an approval whose whole
 * problem was that the draft changed underneath it.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: (attempt, error) => {
              if (error instanceof ApiError && [401, 403, 404, 409].includes(error.status)) {
                return false;
              }
              return attempt < 1;
            },
            staleTime: 15_000,
            refetchOnWindowFocus: false,
          },
          // Never automatic. A retried mutation is a second approval, a second
          // task, or a second grant.
          mutations: { retry: false },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
