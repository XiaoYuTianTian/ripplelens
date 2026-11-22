import { Cart } from "../../../../../packages/core/src/cart/cart";
import { formatMoney } from "../../../../../packages/core/src/pricing/money";

export class CartBadgeComponent {
  constructor(private cart: Cart) {}

  get label(): string {
    return `${this.cart.count()} items · ${formatMoney(this.cart.subtotal())}`;
  }
}
