import { applyDiscount } from "../../../core/src/pricing/discount";

export function flashSale(prices: number[]): number[] {
  return prices.map((p) => applyDiscount(p, 30));
}
