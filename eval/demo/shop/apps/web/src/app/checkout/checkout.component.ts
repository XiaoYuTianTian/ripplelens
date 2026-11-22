import { Cart } from "../../../../../packages/core/src/cart/cart";
import { formatMoney } from "../../../../../packages/core/src/pricing/money";

export class CheckoutComponent {
  cart = new Cart();

  get totalLabel(): string {
    return formatMoney(this.cart.total());
  }

  submit(): number {
    return this.cart.total();
  }
}
