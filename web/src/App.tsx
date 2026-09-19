/**
 * The app root: one `QueryClient` for the whole tree (WEB_DESIGN.md §7.3 — "Server state:
 * TanStack Query v5. Nothing else.") and the router. `AuthProvider` lives inside `routes.tsx`'s
 * `RootLayout`, not here, because it needs `useNavigate`, which only resolves inside
 * `<RouterProvider>`.
 */
import type { JSX } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { router } from "./routes";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Widget payloads are written into the cache by `api/resolve.ts`, never fetched by a
      // `useQuery` directly (its `queryFn` is `skipToken`) — a default retry would only ever
      // retry `undefined`, so it is off globally and each caller that DOES fetch (session,
      // dashboards, sync) opts back in where it matters.
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App(): JSX.Element {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
