/**
 * Placeholder root component — see the docstring in `main.tsx`. Deliberately not the app: no
 * router, no `TanStack Query` client, no auth context. It exists so `npm run build` and
 * `npm run dev` have something real to render while WP1-WP5 land, and so this package can
 * prove the generated design tokens actually reach the page (the text below is styled with
 * `--hw-text-primary` / `--hw-space-lg`, not a literal color or margin, which is the one
 * thing every stylesheet in this project is linted for — see `.stylelintrc.json`).
 */
export default function App(): JSX.Element {
  return (
    <main
      style={{
        padding: "var(--hw-space-lg)",
        color: "var(--hw-text-primary)",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <h1>Hardwood</h1>
      <p>The web app scaffold is up. WP1&ndash;WP5 build the actual product on top of it.</p>
    </main>
  );
}
