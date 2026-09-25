
const GENERATED_SLUG = [
  { re: /^gate-[0-9a-f]{6}$/i, why: "a gate-suite fixture workspace" },
  { re: /^theme-[0-9a-f]{6}$/i, why: "a theme-suite fixture workspace" },
  { re: /^gc-[0-9a-f]{6}$/i, why: "a garbage-collection fixture workspace" },
  { re: /^rpc-[0-9a-f]{5}$/i, why: "an rpc-suite fixture workspace" },
];

const FIXTURE_NAME = [
  { re: /PHercTest/i, why: "PHercTest is a test scroll, not a physical scroll" },
  { re: /\bAtlantis\b/i, why: "Atlantis is a fixture collection, not a holding" },
  { re: /\bobjective_x\b/i, why: "objective_x is a fixture objective" },
];

export interface FixtureVerdict {
  fixture: boolean;
  why: string | null;
  basis: "DECLARED" | "INFERRED" | "NONE";
}

export function classifyFixture(record: unknown): FixtureVerdict {
  if (record === null || record === undefined) return { fixture: false, why: null, basis: "NONE" };

  if (typeof record === "string") {
    return fromText(record);
  }

  const r = record as Record<string, unknown>;

  if (r.fixture_only === true) {
    return {
      fixture: true,
      why: typeof r.fixture_reason === "string" ? r.fixture_reason : "the record declares fixture_only",
      basis: "DECLARED",
    };
  }
  if (r.fixture_only === false) {
    return { fixture: false, why: null, basis: "DECLARED" };
  }

  const fields = ["slug", "id", "key", "name", "target", "collection", "scroll", "run_id", "label"];
  for (const f of fields) {
    const v = r[f];
    if (typeof v !== "string") continue;
    const verdict = fromText(v);
    if (verdict.fixture) return verdict;
  }
  return { fixture: false, why: null, basis: "NONE" };
}

function fromText(s: string): FixtureVerdict {
  const t = s.trim();
  for (const g of GENERATED_SLUG) {
    if (g.re.test(t)) return { fixture: true, why: g.why, basis: "INFERRED" };
  }
  for (const n of FIXTURE_NAME) {
    if (n.re.test(t)) return { fixture: true, why: n.why, basis: "INFERRED" };
  }
  return { fixture: false, why: null, basis: "NONE" };
}

export interface Partitioned<T> {
  real: T[];
  fixtures: { item: T; verdict: FixtureVerdict }[];
}

export function partitionFixtures<T>(items: readonly T[]): Partitioned<T> {
  const real: T[] = [];
  const fixtures: { item: T; verdict: FixtureVerdict }[] = [];
  for (const item of items) {
    const verdict = classifyFixture(item);
    if (verdict.fixture) fixtures.push({ item, verdict });
    else real.push(item);
  }
  return { real, fixtures };
}

export function hiddenNote(n: number): string | null {
  if (n <= 0) return null;
  return `${n} test fixture${n === 1 ? "" : "s"} hidden. They are developer data and are listed under System → Developer data.`;
}
