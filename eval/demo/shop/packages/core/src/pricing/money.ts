export function round(value: number): number {
  return Math.round(value * 100) / 100;
}

export function formatMoney(value: number, currency = "USD"): string {
  return `${currency} ${round(value).toFixed(2)}`;
}
