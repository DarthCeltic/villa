import type { ReactNode } from "react";

export function UnavailableState({
  control,
  what,
  stillAvailable,
  consequence,
  repair,
  technical,
}: {
  control: string;
  what: ReactNode;
  stillAvailable?: ReactNode;
  consequence?: ReactNode;
  repair?: ReactNode;
  technical?: ReactNode;
}) {
  return (
    <div className="unavailable-state" role="status" data-control={control} data-novice="missing">
      <p className="unavailable-what"><b>{what}</b></p>
      {stillAvailable ? <p className="unavailable-line"><span className="unavailable-key">Still available:</span> {stillAvailable}</p> : null}
      {consequence ? <p className="unavailable-line"><span className="unavailable-key">What this means:</span> {consequence}</p> : null}
      {repair ? <p className="unavailable-line"><span className="unavailable-key">To fix it:</span> {repair}</p> : null}
      {technical ? (
        <details className="unavailable-tech" data-control={`${control}.technical`}>
          <summary>Technical details</summary>
          <div className="unavailable-tech-body">{technical}</div>
        </details>
      ) : null}
    </div>
  );
}
