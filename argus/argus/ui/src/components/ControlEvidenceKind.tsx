export function EvidenceRoleChip(_props: { view?: string; role?: string }) {
  return null;
}

export function ControlEvidenceKind({ where }: { where: "workbench" | "evidence" }) {
  return (
    <section className="ce-kinds" data-control={`control.evidence-kinds.${where}`} aria-label="Kinds of control evidence">
      <div className="meta">
        Control-evidence kinds are not bundled with the public build: the controls they classify come from research data that is not shipped here. This is not a claim that there are no controls, and nothing on this screen should be read as one.
      </div>
    </section>
  );
}
