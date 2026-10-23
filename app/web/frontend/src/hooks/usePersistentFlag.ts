import { useCallback, useState } from "react";

/** A boolean remembered per viewer in localStorage. Storage may throw or be empty; the default then applies. */
export function usePersistentFlag(key: string, on: string, off: string, initial: boolean): [boolean, (v: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw === on) return true;
      if (raw === off) return false;
    } catch {
      /* storage unavailable */
    }
    return initial;
  });
  const set = useCallback(
    (v: boolean) => {
      setValue(v);
      try {
        localStorage.setItem(key, v ? on : off);
      } catch {
        /* storage unavailable */
      }
    },
    [key, on, off],
  );
  return [value, set];
}
