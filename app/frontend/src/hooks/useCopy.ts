import { useCallback, useEffect, useRef, useState } from 'react';

/** Copy text to the clipboard and flag `copied` for a moment. The reset timer
 * is cleared on unmount. Without a clipboard (non-secure origin) it does nothing. */
export function useCopy(resetMs = 1500): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  const copy = useCallback(
    (text: string) => {
      navigator.clipboard?.writeText(text).then(
        () => {
          setCopied(true);
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(() => setCopied(false), resetMs);
        },
        () => undefined
      );
    },
    [resetMs]
  );
  return [copied, copy];
}
