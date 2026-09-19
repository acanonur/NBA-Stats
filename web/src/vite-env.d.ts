/// <reference types="vite/client" />

/**
 * Restores a *global* `JSX` namespace.
 *
 * `@types/react` 19 stopped declaring `JSX` globally — every type in `react/index.d.ts` now
 * lives under `declare namespace React { ... namespace JSX { ... } }`, and the automatic
 * `jsx: "react-jsx"` transform resolves JSX syntax (`<div>`, `<App />`) per file straight from
 * `react/jsx-runtime` without ever needing a global name. That per-file resolution is
 * invisible here and needs nothing from this file.
 *
 * What still breaks without this shim is code that spells the type out by name — a function
 * typed to return `JSX.Element`, or a union that mentions it, as `generated/registry.ts`'s
 * `WidgetComponent` does (`(props: WidgetViewProps) => JSX.Element | null`). That file is
 * generated from `contracts/tools/gen_web_contracts.py` and this package does not own it
 * (WEB_DESIGN.md §9 draws that line), so the fix belongs here, once, rather than as a request
 * to reshape a generator six agents already build against. Every widget, page and primitive
 * this project's authors write against the bare name `JSX.Element` — the ordinary, pre-React-19
 * convention this whole codebase was designed around — keeps compiling unchanged.
 */
declare global {
  namespace JSX {
    type Element = React.JSX.Element;
    type ElementClass = React.JSX.ElementClass;
    type ElementType = React.JSX.ElementType;
    // eslint-disable-next-line @typescript-eslint/no-empty-object-type -- mirrors React.JSX exactly
    interface IntrinsicAttributes extends React.JSX.IntrinsicAttributes {}
    // eslint-disable-next-line @typescript-eslint/no-empty-object-type -- mirrors React.JSX exactly
    interface IntrinsicElements extends React.JSX.IntrinsicElements {}
  }
}

export {};
