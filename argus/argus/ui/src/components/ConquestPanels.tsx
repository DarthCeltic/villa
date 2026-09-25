const NOTE = "Renderer-comparison and flattening annotations are not part of the public release.";

export function useConquestStatus(): { data: null } {
  return { data: null };
}

export function RendererStatusPanel(_props: { status: unknown }) {
  return (
    <section aria-label="Renderer status" data-control="renderer.status" data-state="UNAVAILABLE" className="meta">
      {NOTE}
    </section>
  );
}

export function TaskFlatteningPanel(_props: { annotation: unknown }) {
  return (
    <div className="meta" data-control="wb.task.flattening" data-state="UNAVAILABLE">
      {NOTE}
    </div>
  );
}

export function CompareControls({ coordinates }: {
  status: unknown;
  annotation: unknown;
  coordinates: string;
  initial?: unknown;
}) {
  return (
    <section aria-label="Compare renders" data-control="wb.compare" data-state="UNAVAILABLE" className="meta">
      {NOTE} Physical coordinates, unchanged: {coordinates}
    </section>
  );
}

export function TaskConquest(_props: { taskSha: string; coordinates: string }) {
  return (
    <div className="meta" data-control="wb.task.annotations" data-state="UNAVAILABLE">
      {NOTE}
    </div>
  );
}
