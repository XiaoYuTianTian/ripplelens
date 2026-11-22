import { round } from "./money";

export const TAX_RATE = 0.08;

export interface Discount {
  code: string;
  pct: number;
}

export function applyDiscount(price: number, pct: number): number {
  return round(price * (1 - pct / 100));
}

export function bestDiscount(list: Discount[]): Discount | undefined {
  return list.reduce<Discount | undefined>((best, d) => (!best || d.pct > best.pct ? d : best), undefined);
}
