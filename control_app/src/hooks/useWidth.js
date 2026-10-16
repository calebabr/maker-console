import { useLayoutEffect, useState } from 'react';

/**
 * Measured CSS width of an element (callback ref), so an SVG can use a 1 unit = 1 px viewBox
 * and keep round dots and true-size text at any screen width. Falls back to `fallback`
 * where layout isn't available (jsdom, first paint).
 */
export function useWidth(fallback = 300) {
  const [el, setEl] = useState(null);
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    if (!el) return undefined;
    const read = () => {
      const r = el.getBoundingClientRect().width;
      if (r > 0) setW(Math.round(r));
    };
    read();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, w];
}
