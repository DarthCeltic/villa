
export interface PyramidLevel {
  decimate: number;
  origin_xy?: number[];
  size: number[];
}

export function physicalToLevelPixel(
  px: number,
  py: number,
  level: PyramidLevel,
  rasterW: number,
  rasterH: number,
): [number, number] {
  const dec = level.decimate || 1;
  const org = level.origin_xy ?? [0, 0];
  const sx = rasterW / (level.size[0] ?? rasterW);
  const sy = rasterH / (level.size[1] ?? rasterH);
  const x = ((px - (org[0] ?? 0)) / dec) * sx;
  const y = ((py - (org[1] ?? 0)) / dec) * sy;
  return [x, y];
}

export function levelPixelToPhysical(
  x: number,
  y: number,
  level: PyramidLevel,
  rasterW: number,
  rasterH: number,
): [number, number] {
  const dec = level.decimate || 1;
  const org = level.origin_xy ?? [0, 0];
  const sx = rasterW / (level.size[0] ?? rasterW);
  const sy = rasterH / (level.size[1] ?? rasterH);
  const px = (x / sx) * dec + (org[0] ?? 0);
  const py = (y / sy) * dec + (org[1] ?? 0);
  return [px, py];
}

export function levelCovers(px: number, py: number, level: PyramidLevel): boolean {
  const org = level.origin_xy ?? [0, 0];
  const [w, h] = level.size;
  if (w === undefined || h === undefined) return true;
  return px >= (org[0] ?? 0) && px <= (org[0] ?? 0) + w && py >= (org[1] ?? 0) && py <= (org[1] ?? 0) + h;
}
