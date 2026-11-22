import { useCallback, useEffect, useState } from 'react';

export interface ElementSize {
  width: number;
  height: number;
}

/** Track an element's rendered size (ResizeObserver). Returns a callback ref to
 * attach and the size (0×0 until measured, or without ResizeObserver).
 * A callback ref, so it re-observes when the element mounts later or remounts. */
export function useElementSize<T extends HTMLElement>(): [(el: T | null) => void, ElementSize] {
  const [el, setEl] = useState<T | null>(null);
  const [size, setSize] = useState<ElementSize>({ width: 0, height: 0 });
  const ref = useCallback((node: T | null) => setEl(node), []);
  useEffect(() => {
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (!r) return;
      setSize((prev) =>
        Math.abs(prev.width - r.width) < 1 && Math.abs(prev.height - r.height) < 1
          ? prev
          : { width: r.width, height: r.height }
      );
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [ref, size];
}

/** Height-only convenience wrapper around useElementSize. */
export function useElementHeight<T extends HTMLElement>(): [(el: T | null) => void, number] {
  const [ref, size] = useElementSize<T>();
  return [ref, size.height];
}

/** Whether a CSS media query currently matches; updates on change. */
export function useMediaQuery(query: string): boolean {
  const get = () => typeof window !== 'undefined' && !!window.matchMedia?.(query).matches;
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}
