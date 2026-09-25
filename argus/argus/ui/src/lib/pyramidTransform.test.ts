import { describe, expect, it } from "vitest";
import { levelCovers, levelPixelToPhysical, physicalToLevelPixel, type PyramidLevel } from "./pyramidTransform";


const OVERVIEW: PyramidLevel = { decimate: 5, origin_xy: [0, 0], size: [5000, 8000] };
const DETAIL: PyramidLevel = { decimate: 1, origin_xy: [2000, 0], size: [2400, 2400] };

describe("physicalToLevelPixel / levelPixelToPhysical round-trip", () => {
  it("recovers the original real-world point through the forward+inverse transform (overview)", () => {
    const rasterW = 1000;
    const rasterH = 1600;
    const [px, py] = [3200, 4100];
    const [x, y] = physicalToLevelPixel(px, py, OVERVIEW, rasterW, rasterH);
    const [px2, py2] = levelPixelToPhysical(x, y, OVERVIEW, rasterW, rasterH);
    expect(px2).toBeCloseTo(px, 6);
    expect(py2).toBeCloseTo(py, 6);
  });

  it("recovers the original real-world point through the forward+inverse transform (detail crop)", () => {
    const rasterW = 2400;
    const rasterH = 2400;
    const [px, py] = [3500, 900];
    const [x, y] = physicalToLevelPixel(px, py, DETAIL, rasterW, rasterH);
    const [px2, py2] = levelPixelToPhysical(x, y, DETAIL, rasterW, rasterH);
    expect(px2).toBeCloseTo(px, 6);
    expect(py2).toBeCloseTo(py, 6);
  });

  it("places a landmark at the same FRACTION of a level's own declared field regardless of the raster's actual export size", () => {
    const [px, py] = [3000, 600];
    const [xFull, yFull] = physicalToLevelPixel(px, py, DETAIL, 2400, 2400);
    const [xThumb, yThumb] = physicalToLevelPixel(px, py, DETAIL, 600, 600);
    expect(xThumb / 600).toBeCloseTo(xFull / 2400, 6);
    expect(yThumb / 600).toBeCloseTo(yFull / 2400, 6);
  });
});

describe("levelCovers", () => {
  it("says a landmark outside the level's declared window is not covered -- absence, not a wrong position", () => {
    expect(levelCovers(100, 100, DETAIL)).toBe(false);
    expect(levelCovers(3500, 900, DETAIL)).toBe(true);
    expect(levelCovers(3500, 900, OVERVIEW)).toBe(true);
  });
});
