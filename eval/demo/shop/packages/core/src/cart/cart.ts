import { applyDiscount, type Discount, TAX_RATE } from "../pricing/discount";
import { round } from "../pricing/money";

export interface Line {
  sku: string;
  price: number;
  qty: number;
}

export class Cart {
  private lines: Line[] = [];
  private discount?: Discount;

  add(line: Line): void {
    this.lines.push(line);
  }

  count(): number {
    return this.lines.reduce((n, l) => n + l.qty, 0);
  }

  setDiscount(d: Discount): void {
    this.discount = d;
  }

  subtotal(): number {
    return round(this.lines.reduce((s, l) => s + l.price * l.qty, 0));
  }

  total(): number {
    const sub = this.subtotal();
    const net = this.discount ? applyDiscount(sub, this.discount.pct) : sub;
    return round(net * (1 + TAX_RATE));
  }
}
