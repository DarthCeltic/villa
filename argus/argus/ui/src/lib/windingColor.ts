export function hueForWinding(id: number): number {
  return (id * 137.508) % 360;
}
