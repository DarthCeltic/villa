import { useMemo, useState } from "react";
import { EMPTY_SEGMENT_FORM, attachProblem, segmentParams, type SegmentForm } from "../lib/segmentImport";
import { OpsBadge, OpsDetails, OpsDisabled, OpsSection } from "./OpsKit";
import { PlanApprove } from "./PlanApprove";

export function SegmentImportPanel() {
  const [form, setForm] = useState<SegmentForm>(EMPTY_SEGMENT_FORM);
  const params = useMemo(() => segmentParams(form), [form]);
  const problem = attachProblem(form);
  const set = (k: keyof SegmentForm) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));
  return (
    <OpsSection
      control="sources.import"
      title="Bring your own segment"
      hint="Register a local tifxyz directory (x.tif, y.tif, z.tif, meta.json and the volume_source.txt that says which volume it was traced on). It is checked against the scroll and volume records and referenced where it lies; nothing is copied and nothing is run."
      aside={<OpsBadge tone="info">plan, then approve</OpsBadge>}
    >
      <div className="ops-facts" data-control="sources.import.form">
        <label>
          <span className="ops-fact-label">Directory on this machine</span>
          <input
            value={form.path}
            onChange={set("path")}
            placeholder="an absolute path inside an allowed import root"
            data-control="sources.import.path"
          />
        </label>
      </div>
      <OpsDetails
        control="sources.import.attach"
        summary="The segment has no volume_source.txt? Attach its identity, with your name and a reason"
      >
        <div className="ops-facts">
          <label>
            <span className="ops-fact-label">Volume source it was traced on</span>
            <input value={form.attach} onChange={set("attach")} placeholder="…/PHerc…/volumes/<14-digit id>-….zarr" data-control="sources.import.attach.source" />
          </label>
          <label>
            <span className="ops-fact-label">Attested by</span>
            <input value={form.attestedBy} onChange={set("attestedBy")} data-control="sources.import.attach.by" />
          </label>
          <label>
            <span className="ops-fact-label">Reason</span>
            <input value={form.reason} onChange={set("reason")} data-control="sources.import.attach.reason" />
          </label>
        </div>
        <div className="ops-note">
          An attached identity is recorded as ATTESTED, not verified, and can never override a
          volume_source.txt the segment already carries.
        </div>
      </OpsDetails>
      {params ? (
        <PlanApprove
          key={JSON.stringify(params)}
          action="import.segment"
          params={params}
          label="import this segment"
          why="checks the directory, its identity and its compatibility; nothing is registered until you approve"
          disabledReason={problem}
        />
      ) : (
        <OpsDisabled control="sources.import.disabled" label="Import this segment" reason="name a local directory first" />
      )}
    </OpsSection>
  );
}
