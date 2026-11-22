import { Cart, type Line } from "../../../core/src/cart/cart";
import { OrderService } from "./orderService";

const service = new OrderService();

export function handleCheckout(body: { items: Line[] }) {
  const cart = new Cart();
  for (const it of body.items) cart.add(it);
  return service.place(cart);
}

export function handleQuote(q: { price: number; pct: number }) {
  return service.quote(q.price, q.pct);
}
