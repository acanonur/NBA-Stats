/**
 * Measures a container element's content-box width in whole pixels, so a fixed-`viewBox` SVG
 * chart (`design/svg/**`, which takes literal pixel `width`/`height` props rather than a CSS
 * percentage) can be given the tile's actual rendered width instead of a guessed constant. A
 * widget tile's height is fixed by its size (`ESTIMATED_HEIGHT`); only the width is fluid with
 * the grid column span and the viewport, so only the width needs measuring.
 *
 * This lives in `design/` rather than in a widget directory because four widgets need it —
 * `trend_chart`, `team_efficiency`, `career_arc` and `shot_profile`. The parallel build phase
 * (WEB_DESIGN.md §9) produced four private copies of it that had already begun to diverge: three
 * read `contentRect.width` and floored it, the fourth preferred `contentBoxSize[0].inlineSize`
 * and rounded, so the same tile could hand two charts widths one pixel apart. Measuring is not a
 * per-widget policy, so there is now exactly one implementation of it.
 *
 * `contentBoxSize` is preferred over `contentRect` because it is the specified modern field and
 * is unaffected by any CSS transform on an ancestor; `contentRect` is the fallback for older
 * engines. Falls back to `fallbackWidth` — and never throws — when `ResizeObserver` is
 * unavailable (jsdom under vitest, or a very old browser), so a chart still renders
 * deterministically at a fixed width under test.
 */
import { useEffect, useRef, useState, type RefObject } from "react";

/** Returns `[ref, width]` — attach `ref` to the element whose content width should drive the
 * chart, and pass `width` to the chart as its pixel width. */
export function useChartWidth(fallbackWidth: number): readonly [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(fallbackWidth);

  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const measured = entry.contentBoxSize?.[0]?.inlineSize ?? entry.contentRect.width;
      if (Number.isFinite(measured) && measured > 0) setWidth(Math.round(measured));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [ref, width] as const;
}
