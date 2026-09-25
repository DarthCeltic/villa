import { useMemo, useState } from "react";
import { type UnrollIndex } from "../../api";
import { partitionFixtures } from "../../lib/fixtures";
import { getJson, failureLine } from "../../lib/http";
import {
  pickTarget,
  receiptsFrom,
  statusTone,
  verdictLine,
  type PacketListRow,
  type PacketVerification,
} from "../../lib/interpretation";
import { usePoll } from "../../lib/poll";
import { OpsBadge, OpsFact, OpsFacts, OpsSection, OpsSource, OpsUnknown } from "../OpsKit";
import { PlanApprove } from "../PlanApprove";


export function PacketVerificationView({ v }: { v: PacketVerification }) {
  return (
    <div data-control="evidence.packets.result" style={{ display: "grid", gap: 6 }}>
      <div>
        <OpsBadge tone={statusTone(v.verdict)} control="evidence.packets.verdict">
          {v.verdict}
        </OpsBadge>{" "}
        {verdictLine(v)}
      </div>
      <div className="ops-note">{v.what_valid_means}</div>
      {v.reasons.length > 0 ? (
        <ul className="ops-list" data-control="evidence.packets.reasons">
          {v.reasons.map((r, i) => (
            <li key={i} className="ops-item" data-tone="bad">
              <div className="ops-item-body">{r}</div>
            </li>
          ))}
        </ul>
      ) : null}
      {v.limitations ? (
        <OpsFacts>
          <OpsFact label="Limitations block">
            <OpsBadge tone={statusTone(v.limitations.status)}>{v.limitations.status}</OpsBadge>
          </OpsFact>
          <OpsFact label="Claim limits">
            <OpsBadge tone={statusTone(v.limitations.claim_limits)}>{v.limitations.claim_limits}</OpsBadge>
          </OpsFact>
          {v.manifest ? (
            <OpsFact label="Manifest hash">
              <OpsBadge tone={statusTone(v.manifest.self_hash)}>{v.manifest.self_hash}</OpsBadge>
            </OpsFact>
          ) : null}
        </OpsFacts>
      ) : null}
      <div className="ops-scroll-x">
        <table className="ops-table" data-control="evidence.packets.files">
          <thead>
            <tr>
              <th>File</th>
              <th>Result</th>
              <th>Exported sha256</th>
            </tr>
          </thead>
          <tbody>
            {v.files.map((f) => (
              <tr key={f.path} data-control={`evidence.packets.file.${f.path}`}>
                <td className="ops-mono">{f.path}</td>
                <td>
                  <OpsBadge tone={statusTone(f.status)}>{f.status}</OpsBadge>
                </td>
                <td className="ops-mono">{(f.sha256_declared ?? "").slice(0, 16)}…</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {v.unlisted_files.length > 0 ? (
        <div className="ops-note">files the manifest does not list: {v.unlisted_files.join(", ")}</div>
      ) : null}
      {v.roots.length + v.citations.length > 0 ? (
        <div className="ops-scroll-x">
          <table className="ops-table" data-control="evidence.packets.citations">
            <thead>
              <tr>
                <th>Cited receipt</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {v.roots.map((r) => (
                <tr key={"root:" + r.path}>
                  <td className="ops-mono">{r.path} (root)</td>
                  <td>
                    <OpsBadge tone={statusTone(r.status)}>{r.status}</OpsBadge>
                  </td>
                </tr>
              ))}
              {v.citations.map((c, i) => (
                <tr key={i}>
                  <td className="ops-mono">{c.declared_path}</td>
                  <td>
                    <OpsBadge tone={statusTone(c.status)}>{c.status}</OpsBadge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ops-note">this packet cites no receipt</div>
      )}
    </div>
  );
}


export function PacketListView({
  packets,
  onVerify,
  busy,
}: {
  packets: PacketListRow[];
  onVerify: (id: string) => void;
  busy: string | null;
}) {
  if (packets.length === 0) {
    return (
      <ul className="ops-list">
        <OpsUnknown what="Exported packets" why="none has been exported for this target yet" />
      </ul>
    );
  }
  return (
    <ul className="ops-list" data-control="evidence.packets.list">
      {packets.map((p) => (
        <li key={p.packet_id} className="ops-item" data-tone="info" data-control={`evidence.packets.row.${p.packet_id}`}>
          <div className="ops-item-head">
            <span className="ops-item-title ops-mono">{p.packet_id}</span>
            <OpsBadge tone="idle">not published</OpsBadge>
          </div>
          <div className="ops-item-body">
            {p.generated_utc ?? "time unknown"} · {p.files ?? "?"} files · {p.citations?.cited ?? 0} cited receipt(s)
          </div>
          <button
            type="button"
            className="ops-btn"
            disabled={busy === p.packet_id}
            data-control={`evidence.packets.verify.${p.packet_id}`}
            onClick={() => onVerify(p.packet_id)}
          >
            {busy === p.packet_id ? "Re-hashing…" : "Reopen and verify"}
          </button>
        </li>
      ))}
    </ul>
  );
}


export function PacketPanel({ scroll }: { scroll: string | null | undefined }) {
  const index = usePoll<UnrollIndex>("/api/unroll", { intervalMs: 120000 });
  const targets = index.data?.targets ?? [];
  const real = useMemo(() => partitionFixtures(targets).real, [targets]);
  const fromScroll = pickTarget(targets, scroll);
  const [chosen, setChosen] = useState("");
  const key = fromScroll?.key ?? (chosen || null);
  return (
    <OpsSection
      control="evidence.packets"
      title="Publication / receipt packets"
      hint="Export what people reviewed, agreed on and proposed for one target, with its evidence roles, cited receipts, claim limits and a hash for every file. Reopen one later to prove it is unchanged. Nothing is published."
    >
      {fromScroll ? null : (
        <div className="ops-note" data-control="evidence.packets.choose">
          {scroll ? `No exported layer stack matches ${scroll}. ` : "No scroll is selected. "}
          Choose the target; this panel does not pick one for you.{" "}
          <select
            value={chosen}
            aria-label="exported target"
            onChange={(e) => setChosen(e.target.value)}
            style={{ font: "inherit" }}
          >
            <option value="">— choose a target —</option>
            {real.map((t) => (
              <option key={t.key} value={t.key}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
      )}
      {key ? (
        <PacketBody target={key} />
      ) : (
        <ul className="ops-list">
          <OpsUnknown what="Packets" why="no target is chosen" />
        </ul>
      )}
    </OpsSection>
  );
}

function PacketBody({ target }: { target: string }) {
  const list = usePoll<{ packets: PacketListRow[]; root: string }>(
    `/api/interpretation/packets?target=${encodeURIComponent(target)}`,
    { intervalMs: 30000 },
  );
  const [receipts, setReceipts] = useState("");
  const [result, setResult] = useState<PacketVerification | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const verify = async (id: string) => {
    setBusy(id);
    setErr(null);
    try {
      const r = await getJson<PacketVerification>(
        `/api/interpretation/packet/verify?target=${encodeURIComponent(target)}&packet=${encodeURIComponent(id)}`,
      );
      if (r.ok) setResult(r.data);
      else setErr(failureLine(r));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ display: "grid", gap: 10 }} data-control="evidence.packets.body">
      <div style={{ display: "grid", gap: 6 }}>
        <div className="ops-note">
          Cite the receipts this packet rests on (one path per line, optional). Every cited path and its sha256 are
          re-walked, recorded, and re-checked when the packet is reopened.
        </div>
        <textarea
          value={receipts}
          rows={2}
          placeholder="artifacts/…/RUN_RECEIPT.json"
          aria-label="receipts to cite"
          data-control="evidence.packets.receipts"
          onChange={(e) => setReceipts(e.target.value)}
          style={{ font: "inherit", padding: "3px 6px" }}
        />
        <PlanApprove
          action="packet.export"
          label="export a packet"
          params={{ target, receipts: receiptsFrom(receipts) }}
          why="the plan lists every file and its hash; nothing is published or uploaded"
          onDone={list.refresh}
        />
      </div>
      <PacketListView packets={list.data?.packets ?? []} onVerify={verify} busy={busy} />
      {list.data ? <div style={{ color: "var(--ink-faint)" }}>packets live under {list.data.root}</div> : null}
      {list.failure && !list.data ? (
        <ul className="ops-list">
          <OpsUnknown what="The packet list" why={failureLine(list.failure)} />
        </ul>
      ) : null}
      {err ? <div className="ops-note">{err}</div> : null}
      {result ? <PacketVerificationView v={result} /> : null}
      <OpsSource route={`/api/interpretation/packets?target=${target}`} field="packets[]" />
    </div>
  );
}
