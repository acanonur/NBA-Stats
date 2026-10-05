/**
 * The routing table — WEB_DESIGN.md §7.2, `createBrowserRouter` (library mode, no loaders per
 * §7.9). `RootLayout` is every route's element ancestor (it owns `AuthProvider` and the shared
 * chrome — header, `<Outlet>`, footer); `RequireAuth` gates the `session`-tier routes.
 */
import { createBrowserRouter, type RouteObject } from "react-router-dom";
import { RootLayout } from "./pages/RootLayout";
import { RequireAuth } from "./auth/RequireAuth";
import Landing from "./pages/Landing";
import SignIn from "./pages/SignIn";
import SignUp from "./pages/SignUp";
import Verify from "./pages/Verify";
import Forgot from "./pages/Forgot";
import Reset from "./pages/Reset";
import LinkConflict from "./pages/LinkConflict";
import Terms from "./pages/legal/Terms";
import Privacy from "./pages/legal/Privacy";
import Dashboard from "./pages/Dashboard";
import Presets from "./pages/Presets";
import Tonight from "./pages/Tonight";
import GameBox from "./pages/GameBox";
import Today from "./pages/Today";
import Player from "./pages/Player";
import Team from "./pages/Team";
import Fantasy from "./pages/Fantasy";
import Leaders from "./pages/Leaders";
import Settings from "./pages/Settings";
import NotFound from "./pages/NotFound";

/** The route objects, separately from the browser router built from them, so a test can mount
 * the real table in a memory router (a browser router cannot be pointed at an arbitrary URL). */
export const ROUTES: RouteObject[] = [
  {
    path: "/",
    element: <RootLayout />,
    // Without this, a throw anywhere below — a widget whose payload drifted, a rejected
    // `navigate`, a mistyped URL the server answered with index.html — unmounted `RootLayout`
    // itself and replaced the whole document, footer and all, with React Router's default
    // "Unexpected Application Error!" page. `errorElement` on the root route renders in
    // `RootLayout`'s `<Outlet>` instead, so the chrome and the NBA attribution survive.
    errorElement: <RootLayout hasError />,
    children: [
      { index: true, element: <Landing /> },
      { path: "sign-in", element: <SignIn /> },
      { path: "sign-up", element: <SignUp /> },
      { path: "forgot", element: <Forgot /> },
      { path: "reset", element: <Reset /> },
      { path: "verify", element: <Verify /> },
      { path: "auth/link-conflict", element: <LinkConflict /> },
      { path: "legal/terms", element: <Terms /> },
      { path: "legal/privacy", element: <Privacy /> },
      {
        element: <RequireAuth />,
        children: [
          { path: "d/:layoutId", element: <Dashboard /> },
          { path: "presets", element: <Presets /> },
          { path: "tonight", element: <Tonight /> },
          { path: "tonight/:gameId", element: <GameBox /> },
          { path: "today", element: <Today /> },
          { path: "player/:playerId", element: <Player /> },
          { path: "team/:teamId", element: <Team /> },
          { path: "fantasy", element: <Fantasy /> },
          { path: "leaders", element: <Leaders /> },
          { path: "settings", element: <Settings /> },
        ],
      },
      // Last, so it only ever catches what nothing above claimed.
      { path: "*", element: <NotFound /> },
    ],
  },
];

export const router = createBrowserRouter(ROUTES);
