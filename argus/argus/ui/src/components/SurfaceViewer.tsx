import { useEffect, useRef } from "react";
import OpenSeadragon from "openseadragon";
import { api } from "../api";
import { Chip } from "./Status";

export function SurfaceViewer({
  path,
  sealed,
  missingBecause,
  label = "Surface",
  next,
}: {
  path: string | null;
  sealed: boolean;
  missingBecause: string;
  label?: string;
  next?: React.ReactNode;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const viewer = useRef<OpenSeadragon.Viewer | null>(null);

  useEffect(() => {
    if (!host.current || !path || sealed) return;
    host.current.style.background = "var(--bg-sunken)";
    viewer.current = OpenSeadragon({
      element: host.current,
      prefixUrl: "https://cdn.jsdelivr.net/npm/openseadragon@5/build/openseadragon/images/",
      tileSources: { type: "image", url: api.fileUrl(path) },
      showNavigator: true,
      navigatorPosition: "BOTTOM_RIGHT",
      maxZoomPixelRatio: 8,
      visibilityRatio: 1,
      constrainDuringPan: true,
      showFullPageControl: false,
    });
    return () => {
      viewer.current?.destroy();
      viewer.current = null;
    };
  }, [path, sealed]);

  if (sealed) {
    return (
      <Frame>
        <Chip tone="active">Sealed</Chip>
        <p className="muted">
          This run belongs to an active blinded experiment. Its images are withheld by the
          service, so this panel has nothing to hide.
        </p>
        <p className="small faint">
          The seal lifts when the experiment's own controller takes its decision — once.
        </p>
      </Frame>
    );
  }

  if (!path) {
    return (
      <Frame>
        <Chip tone="blocked">No {label.toLowerCase()}</Chip>
        <p className="muted">{missingBecause}</p>
        <p className="small faint">
          An absent render is not an absent finding. Nothing has been read here.
        </p>
        {next ?? null}
      </Frame>
    );
  }

  return (
    <div style={{ display: "grid", gridTemplateRows: "auto 1fr auto", height: "100%" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "12px 16px",
          borderBottom: "1px solid var(--line)",
          flexWrap: "wrap",
        }}
      >
        <h2 className="eyebrow" style={{ color: "var(--ink-dim)" }}>
          {label}
        </h2>
        <Chip tone="certified" size="sm">rendered</Chip>
        <div style={{ flex: 1, minWidth: 8 }} />
        <span className="meta">{path.split(/[\\/]/).pop()}</span>
      </div>
      <div ref={host} style={{ width: "100%", height: "100%", minHeight: 0 }} />
      <div
        className="small faint"
        style={{ padding: "10px 16px", borderTop: "1px solid var(--line)" }}
      >
        Displayed as written by the renderer. Contrast is not adjusted here — a restretched
        map may be looked at, and may never feed a number.
      </div>
    </div>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        height: "100%",
        minHeight: 0,
        display: "grid",
        alignContent: "safe center",
        justifyContent: "center",
        justifyItems: "center",
        gap: 10,
        padding: 32,
        textAlign: "center",
        maxWidth: 460,
        margin: "0 auto",
        overflowY: "auto",
        overscrollBehavior: "contain",
      }}
    >
      {children}
    </div>
  );
}
