import { handleCheckout } from "../../../../../packages/api/src/orders/orderController";
import { renderInvoice } from "../../../../../packages/api/src/billing/invoice";

export class OrderSummaryComponent {
  invoice = "";

  confirm(items: { sku: string; price: number; qty: number }[]): void {
    const order = handleCheckout({ items });
    this.invoice = renderInvoice(order.id, order.total);
  }
}
