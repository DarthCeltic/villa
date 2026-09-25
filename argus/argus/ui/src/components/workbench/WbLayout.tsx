import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { classifyWidth, collapseAll, resetLayout, type WbClass } from "../../lib/wbLayout";

const WbClassContext = createContext<WbClass>("wide");
const WbClassSetter = createContext<(c: WbClass) => void>(() => undefined);

export function useWbClass(): WbClass {
  return useContext(WbClassContext);
}

const ROOT_SELECTOR = ".bench-work";

function initialClass(): WbClass {
  if (typeof window === "undefined") return "wide";
  return classifyWidth(window.innerWidth - 260);
}

export function WbLayoutProvider({ children }: { children: ReactNode }) {
  const [cls, setCls] = useState<WbClass>(initialClass);
  const set = useCallback((next: WbClass) => setCls((cur) => (cur === next ? cur : next)), []);
  return (
    <WbClassContext.Provider value={cls}>
      <WbClassSetter.Provider value={set}>{children}</WbClassSetter.Provider>
    </WbClassContext.Provider>
  );
}

export function useWbRootObserver(): void {
  const setClass = useContext(WbClassSetter);
  const seen = useRef<{ el: Element | null; ro: ResizeObserver | null }>({ el: null, ro: null });
  useLayoutEffect(() => {
    const el = document.querySelector<HTMLElement>(ROOT_SELECTOR);
    if (el === seen.current.el) return;
    seen.current.ro?.disconnect();
    seen.current = { el, ro: null };
    if (!el) return;
    const measure = () => {
      const cs = getComputedStyle(el);
      const w = el.clientWidth - parseFloat(cs.paddingLeft || "0") - parseFloat(cs.paddingRight || "0");
      const next = classifyWidth(w);
      el.dataset.wbClass = next;
      setClass(next);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    seen.current.ro = ro;
  });
  useEffect(
    () => () => {
      seen.current.ro?.disconnect();
      seen.current = { el: null, ro: null };
    },
    [],
  );
}

export type PanelName = "layers" | "inspector";

export function usePanelFocus(panel: PanelName | null, setPanel: (p: PanelName | null) => void) {
  const openerRef = useRef<HTMLElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const previous = useRef<PanelName | null>(null);

  const open = useCallback(
    (name: PanelName, opener?: HTMLElement | null) => {
      openerRef.current = opener ?? (document.activeElement as HTMLElement | null);
      setPanel(name);
    },
    [setPanel],
  );
  const close = useCallback(() => setPanel(null), [setPanel]);
  const toggle = useCallback(
    (name: PanelName, opener?: HTMLElement | null) => {
      if (panel === name) setPanel(null);
      else open(name, opener);
    },
    [panel, open, setPanel],
  );

  useEffect(() => {
    const was = previous.current;
    previous.current = panel;
    if (panel && panel !== was) {
      const id = window.requestAnimationFrame(() => panelRef.current?.focus({ preventScroll: false }));
      return () => window.cancelAnimationFrame(id);
    }
    if (!panel && was) {
      const target =
        (openerRef.current && openerRef.current.isConnected ? openerRef.current : null) ??
        document.querySelector<HTMLElement>(`[data-control="wb.panel.${was}"]`);
      target?.focus({ preventScroll: true });
      openerRef.current = null;
    }
    return undefined;
  }, [panel]);

  const onKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape" && panel) {
        e.stopPropagation();
        setPanel(null);
      }
    },
    [panel, setPanel],
  );

  return { panelRef, open, close, toggle, onKeyDown };
}

export function WbPanel({
  id,
  name,
  title,
  className,
  panelRef,
  onClose,
  onKeyDown,
  children,
}: {
  id: string;
  name: PanelName;
  title: string;
  className: string;
  panelRef: React.MutableRefObject<HTMLElement | null>;
  onClose: () => void;
  onKeyDown: (e: KeyboardEvent) => void;
  children: ReactNode;
}) {
  const cls = useWbClass();
  return (
    <aside
      id={id}
      ref={(el) => {
        panelRef.current = el;
      }}
      className={`wb-panel ${className}`}
      aria-label={title}
      data-wb-chrome="true"
      data-panel-name={name}
      tabIndex={-1}
      onKeyDown={onKeyDown}
    >
      <div className="wb-panel-head">
        <h3 className="wb-panel-title">{title}</h3>
        <div className="wb-panel-actions" role="group" aria-label="Panel controls">
          <button type="button" className="ag-btn wb-panel-btn" data-control="wb.panel.collapseAll" onClick={() => collapseAll(cls)}>
            Collapse all
          </button>
          <button type="button" className="ag-btn wb-panel-btn" data-control="wb.panel.resetLayout" onClick={() => { resetLayout(); onClose(); }}>
            Reset panel layout
          </button>
          <button type="button" className="ag-btn wb-panel-btn wb-panel-close" data-control="wb.panel.close" aria-label={`Close ${title}`} onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      <div className="wb-panel-body">{children}</div>
    </aside>
  );
}
