// Fixed-row-height virtualization (owner P08) shared by TreeTable and DataGrid. Deterministic (no layout
// measurement needed beyond the viewport height), so it behaves the same in browsers and in jsdom tests.
import { useCallback, useEffect, useRef, useState, type UIEvent } from "react";

export interface VirtualWindow {
  start: number;
  end: number; // exclusive
  offsetTop: number;
  totalHeight: number;
}

export function computeWindow(count: number, rowHeight: number, scrollTop: number, viewport: number, overscan = 8): VirtualWindow {
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const visible = Math.ceil(viewport / rowHeight) + overscan * 2;
  const end = Math.min(count, first + visible);
  return { start: first, end, offsetTop: first * rowHeight, totalHeight: count * rowHeight };
}

export function useFixedVirtual(count: number, rowHeight: number, viewportHeight: number, overscan = 8) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(viewportHeight);

  useEffect(() => {
    const el = ref.current;
    if (el && el.clientHeight > 0) setViewport(el.clientHeight);
  }, []);

  const onScroll = useCallback((e: UIEvent<HTMLDivElement>) => setScrollTop(e.currentTarget.scrollTop), []);

  /** Make row `index` visible (keyboard navigation). */
  const scrollToIndex = useCallback(
    (index: number) => {
      const el = ref.current;
      const top = index * rowHeight;
      let next = scrollTop;
      if (top < scrollTop) next = top;
      else if (top + rowHeight > scrollTop + viewport) next = top + rowHeight - viewport;
      if (next !== scrollTop) {
        if (el) el.scrollTop = next;
        setScrollTop(next);
      }
    },
    [rowHeight, scrollTop, viewport],
  );

  const win = computeWindow(count, rowHeight, scrollTop, viewport, overscan);
  return { ref, onScroll, scrollToIndex, ...win, viewport };
}
