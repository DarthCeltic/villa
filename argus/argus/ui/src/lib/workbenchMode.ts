import { useEffect, useId } from "react";

export function useWorkbenchMode(focus: boolean): void {
  const owner = useId();

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.wbModeOwner = owner;
    root.dataset.wbFocus = focus ? "true" : "false";

    return () => {
      if (root.dataset.wbModeOwner !== owner) return;
      delete root.dataset.wbModeOwner;
      delete root.dataset.wbFocus;
    };
  }, [focus, owner]);
}
