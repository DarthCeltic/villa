import { OpsSection } from "./OpsKit";

export function AttachPlanView(_props: { plan: unknown }) {
  return (
    <div className="ops-note" data-control="science.attach.plan-view">
      This view is not part of the public release.
    </div>
  );
}

export function ScienceAttachPanel(_props: { onDone?: () => void }) {
  return (
    <OpsSection control="science.attach" title="Attach local material">
      <div className="ops-note" data-control="science.attach.public">
        This panel is not part of the public release.
      </div>
    </OpsSection>
  );
}
