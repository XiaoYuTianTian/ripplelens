import { applyDiscount, bestDiscount } from "./discount";

export function testApplyDiscount(): boolean {
  return applyDiscount(100, 10) === 90;
}

export function testBestDiscount(): boolean {
  return bestDiscount([{ code: "A", pct: 5 }, { code: "B", pct: 15 }])?.code === "B";
}
