/**
 * The degraded paths. Every test here is a defect a reviewer proved against the shipped
 * release; the golden-payload suites could not see any of them, because they only ever feed
 * good data to a healthy server.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, RouterProvider, createMemoryRouter } from "react-router-dom";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
// These tests mock the client boundary to drive failures through it; production code never
// imports api/client.ts outside src/api/*.
// eslint-disable-next-line no-restricted-imports
import { fetchJson } from "../../api/client";
import { DashboardResolveProvider } from "../../dashboard/DashboardResolveContext";
import { WidgetContainer } from "../../dashboard/WidgetContainer";
import { TileErrorBoundary } from "../../design/ErrorBoundary";
import { Footer } from "../../pages/Footer";
import { safeNext } from "../../auth/safeNext";
import ScoreboardWidget from "../../widgets/scoreboard/index";
import { decodeNextGameProjectionPayload } from "../../widgets/next_game_projection/decode";
import type { ResolveWidgetRequest } from "../../api/types";
import projectionFixture from "../../../../contracts/fixtures/widget_next_game_projection.json";
import scoreboardFixture from "../../../../contracts/fixtures/widget_scoreboard.json";

vi.mock("../../api/client", () => ({
  fetchJson: vi.fn(),
  ApiError: class ApiError extends Error {
    public constructor(
      public readonly code: string,
      message: string,
    ) {
      super(message);
    }
  },
  CSRF_HEADER: "X-Hardwood-CSRF",
  setCsrfToken: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}));

const mockedFetchJson = vi.mocked(fetchJson);

beforeEach(() => {
  mockedFetchJson.mockReset();
});
afterEach(() => cleanup());

function client(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function withClient(queryClient: QueryClient, node: React.ReactNode): React.ReactElement {
  return createElement(QueryClientProvider, { client: queryClient }, node);
}

// ------------------------------------------------------------------ the error boundary

describe("a widget that throws while rendering", () => {
  it("degrades to one ErrorTile instead of unmounting everything around it", () => {
    function Exploding(): React.ReactElement {
      throw new Error("scoreboard payload.games: expected an array");
    }
    // React logs the caught error; keep the test output readable.
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      createElement(
        "div",
        null,
        createElement("span", null, "Hardwood"),
        createElement(TileErrorBoundary, { size: "medium" }, createElement(Exploding)),
      ),
    );
    spy.mockRestore();

    // The sibling chrome survived — this is the whole point.
    expect(screen.getByText("Hardwood")).toBeTruthy();
    expect(screen.getByText(/did not match what the app expected/)).toBeTruthy();
  });
});

describe("a scoreboard payload with one off-contract field", () => {
  it("renders an ErrorTile rather than throwing out of render", () => {
    const games = (scoreboardFixture as { games: unknown[] }).games;
    const broken = {
      ...(scoreboardFixture as Record<string, unknown>),
      // A score serialised as a string by a future backend change.
      games: [{ ...(games[0] as Record<string, unknown>), homePts: "112" }, ...games.slice(1)],
    };
    expect(() =>
      render(createElement(ScoreboardWidget, { kind: "scoreboard", size: "large", payload: broken })),
    ).not.toThrow();
    expect(screen.getByText(/did not match what the app expected/)).toBeTruthy();
  });
});

// ------------------------------------------------------------------ a failed resolve

describe("the batch resolve failing outright", () => {
  it("writes an error result so the tile shows a message and a retry, not a forever skeleton", async () => {
    mockedFetchJson.mockRejectedValue(new Error("boom"));
    const widget: ResolveWidgetRequest = {
      id: "tile-1",
      kind: "stat_tile",
      size: "small",
      config: {},
    };
    const queryClient = client();
    render(
      withClient(
        queryClient,
        createElement(
          DashboardResolveProvider,
          { widgets: [widget] },
          createElement(WidgetContainer, { widget }),
        ),
      ),
    );

    await waitFor(() => {
      expect(screen.getByText(/Could not reach Hardwood/)).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: /try again/i })).toBeTruthy();
  });
});

// ------------------------------------------------------------------ the footer

describe("the footer's attribution", () => {
  it("actually calls GET /v1/meta on mount and shows what the server says", async () => {
    mockedFetchJson.mockResolvedValue({
      attribution: "Demo data: … generated by nbastats.seed. Not NBA data",
    });
    const queryClient = client();
    render(withClient(queryClient, createElement(MemoryRouter, null, createElement(Footer))));

    await waitFor(() => {
      expect(screen.getByText(/generated by nbastats\.seed/)).toBeTruthy();
    });
    expect(mockedFetchJson).toHaveBeenCalled();
    expect(mockedFetchJson.mock.calls[0][0]).toBe("/meta");
  });

  it("never claims NBA.com data before the server has answered", () => {
    mockedFetchJson.mockReturnValue(new Promise(() => undefined));
    const queryClient = client();
    render(withClient(queryClient, createElement(MemoryRouter, null, createElement(Footer))));
    expect(document.body.textContent).not.toContain("Stats via NBA.com");
    expect(document.body.textContent).toContain("Not endorsed by or affiliated with the NBA.");
  });

  it("still says something honest when /v1/meta fails outright", async () => {
    mockedFetchJson.mockRejectedValue(new Error("offline"));
    const queryClient = client();
    render(withClient(queryClient, createElement(MemoryRouter, null, createElement(Footer))));
    await waitFor(() => {
      expect(document.body.textContent).toContain("Not endorsed by or affiliated with the NBA.");
    });
    expect(document.body.textContent).not.toContain("Stats via NBA.com");
  });

  it("does not bounce a signed-out visitor to /sign-in when /v1/meta 401s", () => {
    mockedFetchJson.mockReturnValue(new Promise(() => undefined));
    const queryClient = client();
    render(withClient(queryClient, createElement(MemoryRouter, null, createElement(Footer))));
    expect(mockedFetchJson.mock.calls[0][1]).toMatchObject({ onUnauthorized: "ignore" });
  });
});

// ------------------------------------------------------------------ theme + sign-out

describe("AuthProvider", () => {
  const sessionFor = (userId: string, theme: "system" | "light" | "dark") => ({
    user: {
      userId,
      email: `${userId}@zed.test`,
      emailVerified: true,
      displayName: null,
      isPrivateRelay: false,
      hasPassword: true,
      identities: [],
      favoritePlayerId: null,
      favoriteTeamId: null,
      selectedDashboardId: null,
      theme,
      createdAt: "2026-01-01T00:00:00Z",
    },
    csrfToken: "t",
    methods: {
      password: { enabled: true, signupMode: "invite" },
      google: { enabled: false, reason: null },
      apple: { enabled: false, reason: null },
      mail: { delivers: false },
    },
  });

  afterEach(() => {
    delete document.documentElement.dataset.theme;
  });

  it("applies the stored theme preference to document.documentElement", async () => {
    mockedFetchJson.mockResolvedValue(sessionFor("ada", "light"));
    const { AuthProvider } = await import("../../auth/AuthProvider");
    const queryClient = client();
    render(
      withClient(
        queryClient,
        createElement(MemoryRouter, null, createElement(AuthProvider, null, "body")),
      ),
    );
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("light");
    });
  });

  it('deletes the attribute for "system", so prefers-color-scheme wins', async () => {
    document.documentElement.dataset.theme = "dark";
    mockedFetchJson.mockResolvedValue(sessionFor("ada", "system"));
    const { AuthProvider } = await import("../../auth/AuthProvider");
    const queryClient = client();
    render(
      withClient(
        queryClient,
        createElement(MemoryRouter, null, createElement(AuthProvider, null, "body")),
      ),
    );
    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBeUndefined();
    });
  });

  it("refuses to carry on when the session cookie starts naming a different account", async () => {
    // Forced login: another process on this host (or an on-path device on a plaintext LAN
    // deployment) answers one request with `Set-Cookie: hw_session=<attacker session>`. The
    // request that follows is genuinely authenticated as the attacker, and the derived CSRF
    // token matches it, so nothing server-side can tell this from an honest sign-in.
    mockedFetchJson.mockResolvedValue(sessionFor("victim", "system"));
    const { AuthProvider, useAuth } = await import("../../auth/AuthProvider");
    const queryClient = client();
    queryClient.setQueryData(["dashboards"], [{ id: "la", name: "Victim's board" }]);

    let refresh: (() => Promise<void>) | null = null;
    function Probe(): React.ReactElement {
      refresh = useAuth().refresh;
      return createElement("span", null, "the app");
    }
    render(
      withClient(
        queryClient,
        createElement(MemoryRouter, null, createElement(AuthProvider, null, createElement(Probe))),
      ),
    );
    await waitFor(() => expect(screen.queryByText("the app")).not.toBeNull());

    mockedFetchJson.mockResolvedValue(sessionFor("attacker", "system"));
    await refresh!();

    await waitFor(() => {
      expect(screen.getByText(/You have been signed out/)).toBeTruthy();
    });
    // The app is gone, not merely warned over.
    expect(screen.queryByText("the app")).toBeNull();
    expect(queryClient.getQueryData(["dashboards"])).toBeUndefined();
  });

  it("empties the query cache on sign out, so the next person sees none of it", async () => {
    mockedFetchJson.mockResolvedValue(sessionFor("ada", "system"));
    const { AuthProvider, useAuth } = await import("../../auth/AuthProvider");
    const queryClient = client();
    queryClient.setQueryData(["dashboards"], [{ id: "la", name: "Ada's private board" }]);
    queryClient.setQueryData(["me", "sessions"], [{ sessionId: "s1", userAgent: "Ada's laptop" }]);

    let signOut: (() => Promise<void>) | null = null;
    function Probe(): null {
      signOut = useAuth().logout;
      return null;
    }
    render(
      withClient(
        queryClient,
        createElement(MemoryRouter, null, createElement(AuthProvider, null, createElement(Probe))),
      ),
    );
    await waitFor(() => expect(signOut).not.toBeNull());
    await signOut!();

    expect(queryClient.getQueryData(["dashboards"])).toBeUndefined();
    expect(queryClient.getQueryData(["me", "sessions"])).toBeUndefined();
  });
});

// ------------------------------------------------------------------ a mistyped URL

describe("a path no route matches", () => {
  it("keeps the shell and the NBA attribution footer, and offers a way back", async () => {
    mockedFetchJson.mockRejectedValue(new Error("signed out"));
    const { ROUTES } = await import("../../routes");
    const memoryRouter = createMemoryRouter(ROUTES, { initialEntries: ["/signin"] });
    const queryClient = client();
    render(withClient(queryClient, createElement(RouterProvider, { router: memoryRouter })));

    await waitFor(() => {
      expect(screen.getByText(/That page does not exist/)).toBeTruthy();
    });
    const text = document.body.textContent ?? "";
    // The three things React Router's own default error page destroyed.
    expect(text).toContain("Hardwood");
    expect(text).toContain("Not endorsed by or affiliated with the NBA.");
    expect(text).not.toContain("Unexpected Application Error");
    expect(screen.getByText("Back to Hardwood")).toBeTruthy();
  });
});

// ------------------------------------------------------------------ ?next=

describe("safeNext", () => {
  it("keeps an ordinary in-app path", () => {
    expect(safeNext("/tonight")).toBe("/tonight");
    expect(safeNext("/d/abc?edit=1")).toBe("/d/abc?edit=1");
  });

  it("rejects everything a browser would treat as another origin", () => {
    for (const hostile of [
      "//evil.example",
      "/\\evil.example",
      "https://hardwood.example/settings",
      "%2f%2fevil.example",
      "javascript:alert(1)",
      "/ok\nSet-Cookie: x=1",
      "",
      null,
    ]) {
      expect(safeNext(hostile)).toBe("/");
    }
  });
});

// ------------------------------------------------------------------ the projection clamp

describe("next_game_projection availability", () => {
  it("is clamped to estimated at the decode boundary, whatever the server says", () => {
    const lines = (projectionFixture as { lines: Record<string, unknown>[] }).lines;
    const lying = {
      ...(projectionFixture as Record<string, unknown>),
      lines: lines.map((line) => ({ ...line, availability: "full" })),
    };
    const decoded = decodeNextGameProjectionPayload(lying);
    expect(decoded.lines.length).toBeGreaterThan(0);
    for (const line of decoded.lines) {
      expect(line.availability).toBe("estimated");
    }
  });
});
