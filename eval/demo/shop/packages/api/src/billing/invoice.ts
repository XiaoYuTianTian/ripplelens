import { TAX_RATE } from "../../../core/src/pricing/discount";
import { formatMoney, round } from "../../../core/src/pricing/money";

export function taxOf(amount: number): number {
  return round(amount * TAX_RATE);
}

export function renderInvoice(id: string, amount: number): string {
  return `Invoice ${id}: ${formatMoney(amount)} (tax ${formatMoney(taxOf(amount))})`;
}
