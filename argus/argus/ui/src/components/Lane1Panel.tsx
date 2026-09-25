export function Lane1Panel(_props: { lane1?: { state: string } & Record<string, unknown>; resources?: Record<string, unknown> }) {
  return (
    <section className="panel" data-control="lane1.public" style={{ padding: 16 }}>
      <p className="meta" style={{ margin: 0 }}>
        This panel is not part of the public release.
      </p>
    </section>
  );
}
