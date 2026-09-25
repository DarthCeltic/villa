
export interface UnknownPart {
  part: "physical_scroll" | "argus_alias" | "upstream_segment_id" | "volume_id";
  reason: string;
}

export interface QualifiedSegment {
  label: string;
  publicName: string;
  physicalScroll: string | null;
  argusAlias: string;
  upstreamSegmentId: string | null;
  volumeId: string | null;
  missing: UnknownPart[];
  why: string;
  publishable: boolean;
}

const BARE_ALIAS = /(?<![A-Za-z0-9_-])w\d{2,3}(?![A-Za-z0-9_-])/gi;

export function hasBareAlias(text: string): boolean {
  BARE_ALIAS.lastIndex = 0;
  return BARE_ALIAS.test(text ?? "");
}

export function scopedAlias(scroll: string | null, alias: string): string {
  const a = (alias ?? "").trim();
  if (!scroll) return a;
  const s = scroll.trim();
  if (a.toLowerCase().startsWith(s.toLowerCase())) return a;
  return `${s.toLowerCase()}-${a}`;
}

const NO_UPSTREAM =
  "no upstream segment id: the store this row came from declares none, and deriving one " +
  "from a directory name is the inference scroll identity refuses to make";
const NO_VOLUME =
  "no volume identity: this record does not name the render the alias refers to. The " +
  "catalogue withholds absolute paths, and a volume is not guessed from a pitch";
const NO_SCROLL =
  "no physical scroll: nothing in this record declares which scroll the alias belongs to, " +
  "which is the part that makes an alias ambiguous in the first place";

export function qualify(input: {
  alias: string;
  scroll?: string | null;
  upstreamSegmentId?: string | null;
  volumeId?: string | null;
  pitchUm?: number | number[] | null;
  energyKev?: number | null;
}): QualifiedSegment {
  const scroll = input.scroll?.trim() || null;
  const alias = scopedAlias(scroll, input.alias);
  const upstream = input.upstreamSegmentId?.trim() || null;
  const volume = input.volumeId?.trim() || null;

  const missing: UnknownPart[] = [];
  if (!scroll) missing.push({ part: "physical_scroll", reason: NO_SCROLL });
  if (!upstream) missing.push({ part: "upstream_segment_id", reason: NO_UPSTREAM });
  if (!volume) missing.push({ part: "volume_id", reason: NO_VOLUME });

  const publicName = `${scroll ?? "scroll undeclared"} / ${alias} / upstream ${
    upstream ?? "undeclared"
  } / volume ${volume ?? "undeclared"}`;

  const label = scroll ? `${scroll} · ${alias}` : alias;

  return {
    label,
    publicName,
    physicalScroll: scroll,
    argusAlias: alias,
    upstreamSegmentId: upstream,
    volumeId: volume,
    missing,
    why: missing.length
      ? missing.map((m) => m.reason).join(". ")
      : "every part of the public name is declared by the record",
    publishable: missing.length === 0,
  };
}

export function acquisitionLine(pitchUm?: number | number[] | null, energyKev?: number | null) {
  const parts: string[] = [];
  if (Array.isArray(pitchUm) && pitchUm.length) {
    const uniq = [...new Set(pitchUm.map((n) => Number(n)))];
    parts.push(uniq.map((n) => `${n} µm`).join(" / "));
  } else if (typeof pitchUm === "number") {
    parts.push(`${pitchUm} µm`);
  }
  if (typeof energyKev === "number") parts.push(`${energyKev} keV`);
  return parts.length ? parts.join(" · ") : null;
}

export function qualifyInText(text: string, scroll: string | null): string {
  if (!scroll) return text;
  return (text ?? "").replace(BARE_ALIAS, (m) => scopedAlias(scroll, m));
}
