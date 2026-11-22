export const LIMIT = 10;

export interface Shape {
  area(): number;
}

export class Circle implements Shape {
  constructor(public r: number) {}

  area(): number {
    return 3.14 * this.r * this.r;
  }
}

export function helper(x: number): number {
  return x + LIMIT;
}

export function total(shapes: Shape[]): number {
  let t = 0;
  for (const s of shapes) {
    t += s.area();
  }
  return t;
}
