import { useEffect, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  Activity, BookMarked, Boxes, Cpu, Layers, ScrollText, ShieldAlert, Target,
} from "lucide-react";
import type { RunRecord } from "../../api";
import { usePoll } from "../../lib/poll";
import { useIntegrity } from "../../lib/systemTruth";
import {
  declaredReceipt,
  declaredReceiptUrl,
  type DeclaredReceiptResponse,
} from "../../lib/declaredReceipts";
import type { Universe } from "../ShelfUniverse";
import { Clamshell, NextStep } from "./Clamshell";
import { Private } from "../../lib/publicDemo";
import "../../theme/bench.css";

interface CommandJobs {
  total: number;
  by_state: Record<string, number>;
  jobs: { job_id: string; action: string; state: string; started_utc: string; age_s: number;
          refusal?: string | null; why?: string | null }[];
  stuck: { job_id: string; action: string }[];
  stuck_note?: string;
}
interface Oversight {
  gpu?: { present?: boolean; cards?: { name?: string; memory_total_mib?: number }[] };
  boundary?: { service_read_only?: boolean; ui_can_start_work?: boolean };
}
interface Field<T> { value: T | null; source: string; evidence?: string }
interface ModelRegistry {
  family_count?: number;
  families?: Record<string, {
    repo?: string;
    role?: Field<string>;
    architecture?: Field<string>;
    qualification?: Field<string>;
    checkpoints?: { qualification?: string }[];
  }>;
}
interface UnrollIndex { targets: { key: string; name: string }[] }

function Tile({ icon, label, value, sub, failed, id }: {
  icon: ReactNode; label: string; value: ReactNode; sub?: ReactNode;
  failed?: string | null; id: string;
}) {
  return (
    <div className="bench-tile" data-failed={failed ? "true" : undefined} data-tile={id}>
      <span className="bench-tile-icon" aria-hidden="true">{icon}</span>
      <div className="bench-tile-text">
        <span className="bench-label">{label}</span>
        <span className="bench-value">{failed ? "unread" : value}</span>
        <span className="bench-sub">{failed ? `Data feed failed: ${failed}` : sub}</span>
        {failed ? (
          <NextStep id={`tile.${id}`} to={id === "models" ? "/sources?tab=models" : "/sources?tab=holdings"} label="Open the relevant inventory" />
        ) : null}
      </div>
    </div>
  );
}

function useWorkbenchDiaryReserve() {
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.argusRoom = "workbench";
    const sync = () => {
      const zone = root.dataset.diaryDock;
      if (!zone || root.dataset.diaryCollapsed !== "true") return;
      const panel = document.querySelector<HTMLElement>(".grail-diary");
      const box = panel?.getBoundingClientRect();
      const measured = zone === "BOTTOM" || zone === "SHEET" ? box?.height : box?.width;
      const ext = measured && measured > 0
        ? `${Math.round(measured)}px`
        : getComputedStyle(root).getPropertyValue("--diary-extent").trim();
      if (!ext) return;
      const name = `--wb-diary-reserve-${zone}`;
      if (root.style.getPropertyValue(name) !== ext) root.style.setProperty(name, ext);
    };
    sync();
    const frame = requestAnimationFrame(sync);
    const mo = new MutationObserver(sync);
    mo.observe(root, {
      attributes: true,
      attributeFilter: ["style", "data-diary-dock", "data-diary-collapsed"],
    });
    const panel = document.querySelector<HTMLElement>(".grail-diary");
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(sync);
    if (panel) ro?.observe(panel);
    return () => {
      cancelAnimationFrame(frame);
      mo.disconnect();
      ro?.disconnect();
      if (root.dataset.argusRoom === "workbench") delete root.dataset.argusRoom;
    };
  }, []);
}

function jobWord(state: string): string {
  return state === "SUCCEEDED" ? "finished" : state === "REFUSED" ? "refused"
    : state === "RUNNING" ? "running" : state.toLowerCase();
}

function ago(s: number): string {
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function BenchFrame({ universe, children, rail, title, compact = false }: {
  universe: Universe;
  compact?: boolean;
  runs: RunRecord[];
  children: ReactNode;
  rail?: ReactNode;
  title: string;
}) {
  const jobs = usePoll<CommandJobs>("/api/command-jobs", { intervalMs: 20000 });
  const over = usePoll<Oversight>("/api/oversight", { intervalMs: 60000 });
  const models = usePoll<DeclaredReceiptResponse<ModelRegistry>>(
    declaredReceiptUrl("model_registry"),
    { intervalMs: 300000 },
  );
  const stacks = usePoll<UnrollIndex>("/api/unroll", { intervalMs: 120000 });
  const integrity = useIntegrity();
  useWorkbenchDiaryReserve();

  const c = universe.counts;
  const modelReceipt = declaredReceipt(models.data, "model_registry");
  const modelRegistry = modelReceipt?.present ? modelReceipt.content : null;
  const families = Object.entries(modelRegistry?.families ?? {});
  const qualified = families.filter(([, f]) => f.qualification?.value === "QUALIFIED").length;
  const chain = integrity.data?.chain?.status ?? null;
  const gpu = over.data?.gpu;
  const card = gpu?.cards?.[0];

  const allJobs = <Link to="/jobs" className="bench-more" data-control="bench.jobs.all">All jobs</Link>;
  const registry = <Link to="/system" className="bench-more" data-control="bench.models.all">Registry</Link>;
  const shownJobs = jobs.data ? jobs.data.jobs.slice(0, 6) : [];
  const troubled = shownJobs.filter(
    (j) => j.state === "REFUSED" || jobs.data?.stuck.some((x) => x.job_id === j.job_id),
  ).length;

  const jobsBody = jobs.failure ? (
    <p className="bench-note">
      Could not read the job record: {jobs.failure.message}. This is not "no jobs".{" "}
      <NextStep id="jobs.error" to="/jobs" label="Open Jobs and read the recorded status" />
    </p>
  ) : !jobs.data ? (
    <p className="bench-note">Reading the job record…</p>
  ) : (
    <>
      <ul className="bench-jobs">
        {shownJobs.map((j) => {
          const stuck = jobs.data?.stuck.some((s) => s.job_id === j.job_id);
          return (
            <li key={j.job_id} data-state={stuck ? "STUCK" : j.state}>
              <span className="bench-job-mark" aria-hidden="true">
                {stuck ? "!" : j.state === "SUCCEEDED" ? "●" : j.state === "REFUSED" ? "■" : "◐"}
              </span>
              <span className="bench-job-action">{j.action}</span>
              <span className="bench-job-state">{stuck ? "recorded running, no process" : jobWord(j.state)}</span>
              <span className="bench-job-age">{ago(j.age_s)}</span>
            </li>
          );
        })}
      </ul>
      {troubled ? (
        <p className="bench-note">
          {troubled} of these {shownJobs.length} refused or have no process.{" "}
          <NextStep id="jobs.refused" to="/jobs" label="Read each reason on Jobs" />
        </p>
      ) : null}
    </>
  );

  const headline = (
    <section className="bench-panel bench-headline" aria-label="Text reconstruction">
      <header className="bench-panel-head"><h2>Text reconstruction</h2></header>
      <p className="bench-head"><ShieldAlert size={18} aria-hidden="true" /> Not available.</p>
      <p className="bench-note">
        No reconstructed text is drawn here.
      </p>
      <span className="wb-next">
        <span className="wb-next-lead">Next step:</span>{" "}
        <Link to="/review" className="bench-more wb-next-link" data-control="bench.headline.review">What is owed a decision</Link>
      </span>
    </section>
  );

  const modelsBody = models.failure ? (
    <p className="bench-note">
      Could not read registries/models.json: {models.failure.message}.{" "}
      <NextStep id="models.error" to="/sources?tab=models" label="Open the model inventory" />
    </p>
  ) : !models.data ? (
    <p className="bench-note">Reading the model registry…</p>
  ) : !modelReceipt?.present ? (
    <p className="bench-note">
      The optional model registry is not installed: {modelReceipt?.missing_reason ?? "no registry receipt is present"}.{" "}
      <NextStep id="models.absent" to="/sources?tab=models" label="Open the model inventory" />
    </p>
  ) : (
    <ul className="bench-models">
      {families.map(([name, f]) => {
        const q = f.qualification?.value ?? "UNKNOWN";
        return (
          <li key={name} className="bench-model" data-qual={q}>
            <span className="bench-model-icon" aria-hidden="true"><Cpu size={20} /></span>
            <span className="bench-model-name">{name}</span>
            <span className="bench-model-qual">{q.replace(/_/g, " ").toLowerCase()}</span>
            <span className="bench-model-sub">
              {f.checkpoints?.length ?? 0} checkpoint(s) hashed{f.qualification?.source ? ` · ${f.qualification.source.toLowerCase()}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );

  const kpis = (
    <section className="bench-kpis" aria-label="What ARGUS holds, as measured">
      <Tile id="scrolls" icon={<ScrollText size={30} />} label="Scrolls"
            value={c.registered.value}
            sub={c.firstLetters.value === null
              ? <>known identities · prize eligibility unknown (target registry not installed)</>
              : <>registered · {c.firstLetters.value} First Letters · {c.grandPrize.value} Grand Prize (overlapping)</>}
            failed={universe.failures.length && !c.registered.value ? universe.failures[0] : null} />
      {
}
      {}
      <Private kind="acquisition_order">
      <Tile id="held" icon={<Target size={30} />} label="CT indexed here"
            value={c.firstLettersWithLocalMaterial.value ?? "unknown"}
            sub={c.firstLetters.value === null
              ? <>First Letters targets unknown: the official target registry is not installed. Local material is listed per scroll.</>
              : <>of {c.firstLetters.value} First Letters targets have CT indexed on this machine · {c.firstLettersWithBytesHere.value} of {c.firstLetters.value} have any bytes here (indexed or catalogued)</>} />
      </Private>
      <Tile id="stacks" icon={<Layers size={30} />} label="Layer stacks"
            value={stacks.data?.targets.length ?? "…"}
            sub="exported, each with its own result class"
            failed={stacks.failure ? stacks.failure.message : null} />
      <Tile id="models" icon={<Boxes size={30} />} label="Models"
            value={models.data ? families.reduce((n, [, f]) => n + (f.checkpoints?.length ?? 0), 0) : "…"}
            sub={models.data ? `checkpoints hashed · ${families.length} families · ${qualified} qualified` : "reading the registry"}
            failed={models.failure ? models.failure.message : null} />
      <Tile id="jobs" icon={<Activity size={30} />} label="Jobs"
            value={jobs.data ? jobs.data.total : "…"}
            sub={jobs.data
              ? <>{jobs.data.by_state.RUNNING ?? 0} running{jobs.data.stuck.length ? ` (${jobs.data.stuck.length} with no process)` : ""} · {jobs.data.by_state.REFUSED ?? 0} refused</>
              : "reading the job record"}
            failed={jobs.failure ? jobs.failure.message : null} />
      <div className="bench-status" aria-label="Service state as reported">
        <span className="bench-label">Service</span>
        <dl>
          <dt>Mode</dt><dd>{over.data?.boundary?.service_read_only ? "read-only observer" : over.data ? "writes permitted" : "…"}</dd>
          <dt>Ledger chain</dt><dd data-word={chain ?? "unknown"}>{chain ? chain.toLowerCase() : integrity.failure ? "unreadable" : "…"}</dd>
          <dt>GPU</dt><dd>{gpu ? (gpu.present ? `${card?.name ?? "present"}` : "none reported") : "…"}</dd>
        </dl>
      </div>
    </section>
  );

  return (
    <div className="bench" data-bench="frame" data-compact={compact ? "true" : undefined}>
      <h1 className="bench-sr">{title}</h1>

      {}
      {
}
      <div data-wb-chrome="true">
        {compact ? (
          <Clamshell
            id="figures"
            title="What ARGUS holds"
            className="bench-figures"
            persistIn={["wide"]}
            aside={
              <span className="bench-figures-line" data-control="bench.figures.line">
                {c.registered.value} scrolls
                {}
                <Private kind="acquisition_order">
                  {c.firstLetters.value === null ? (
                    <>{" "}· First Letters targets unknown (target registry not installed)</>
                  ) : (
                    <>
                      {" "}· CT indexed for {c.firstLettersWithLocalMaterial.value} of{" "}
                      {c.firstLetters.value} First Letters · any bytes here for{" "}
                      {c.firstLettersWithBytesHere.value} of {c.firstLetters.value}
                    </>
                  )}
                </Private>
              </span>
            }
          >
            {kpis}
          </Clamshell>
        ) : (
          kpis
        )}
      </div>

      {}
      <div className="bench-grid">
        <section className="bench-panel bench-work" aria-label="Workspace">
          {children}
        </section>

        {compact ? null : (
          <aside className="bench-side" aria-label="Recent jobs and headline" data-wb-chrome="true">
            <section className="bench-panel">
              <header className="bench-panel-head">
                <h2>Recent jobs</h2>
                {allJobs}
              </header>
              {jobsBody}
            </section>
            {headline}
          </aside>
        )}
      </div>

      {rail ? (
        <section className="bench-panel bench-rail" aria-label="Reconstruction route" data-wb-chrome="true">
          <header className="bench-panel-head"><h2>Reconstruction route</h2></header>
          {rail}
        </section>
      ) : null}

      {compact ? (
        <div className="bench-below" data-wb-chrome="true">
          {headline}
          <section className="bench-panel" aria-label="Recent jobs">
            <Clamshell id="jobs" title="Recent jobs" aside={allJobs}>
              {jobsBody}
            </Clamshell>
          </section>
          <section className="bench-panel" aria-label="Model library">
            <Clamshell id="models" title="Model library" aside={registry}>
              {modelsBody}
            </Clamshell>
          </section>
        </div>
      ) : (
        <section className="bench-panel" aria-label="Model library" data-wb-chrome="true">
          <header className="bench-panel-head">
            <h2>Model library</h2>
            {registry}
          </header>
          {modelsBody}
        </section>
      )}

      <p className="bench-foot" data-wb-chrome="true"><BookMarked size={15} aria-hidden="true" /> Every figure on this page is read from the route named in its tooltip. None is illustrative.</p>
    </div>
  );
}
