import { Cart } from "../../../core/src/cart/cart";
import { applyDiscount } from "../../../core/src/pricing/discount";

export interface Order {
  id: string;
  total: number;
}

export class OrderService {
  private seq = 0;

  place(cart: Cart): Order {
    return { id: `o-${++this.seq}`, total: cart.total() };
  }

  quote(price: number, pct: number): number {
    return applyDiscount(price, pct);
  }
}
