// Shared mock data for the style prototypes: a mid-size change in a Java + Angular shop.
window.PROTO = (() => {
  const groups = {
    order: { module: "order-service", pkg: "com.shop.order", lang: "java" },
    api: { module: "order-service", pkg: "com.shop.order.api", lang: "java" },
    pay: { module: "payment-service", pkg: "com.shop.payment", lang: "java" },
    money: { module: "shared-lib", pkg: "com.shop.common.money", lang: "java" },
    checkout: { module: "web", pkg: "app/checkout", lang: "ts" },
    core: { module: "web", pkg: "app/core", lang: "ts" },
    notify: { module: "notification-service", pkg: "com.shop.notify", lang: "java" },
    report: { module: "reporting", pkg: "com.shop.report", lang: "java" },
    history: { module: "web", pkg: "app/order-history", lang: "ts" },
    admin: { module: "web", pkg: "app/admin", lang: "ts" },
  };

  const changed = [
    { id: "OrderService", group: "order", name: "OrderService.java", entities: [
      { id: "placeOrder", name: "placeOrder(Cart, Coupon)", change: "signature",
        before: "Order placeOrder(Cart cart)", after: "Order placeOrder(Cart cart, Coupon coupon)" },
      { id: "computeTotal", name: "computeTotal(Cart)", change: "body" } ] },
    { id: "OrderValidator", group: "order", name: "OrderValidator.java", entities: [
      { id: "validate", name: "validate(Order)", change: "body" } ] },
    { id: "OrderRepository", group: "order", name: "OrderRepository.java", entities: [
      { id: "findOpenOrders", name: "findOpenOrders(CustomerId)", change: "body" } ] },
    { id: "OrderStatus", group: "order", name: "OrderStatus.java", entities: [
      { id: "orderStatus", name: "PENDING_PAYMENT", change: "value", before: "PENDING", after: "PENDING_PAYMENT" } ] },
    { id: "OrderController", group: "api", name: "OrderController.java", entities: [
      { id: "create", name: "create(CheckoutRequest)", change: "body" } ] },
    { id: "OrderDto", group: "api", name: "OrderDto.java", entities: [
      { id: "couponCode", name: "couponCode", change: "added" } ] },
    { id: "CheckoutRequest", group: "api", name: "CheckoutRequest.java", entities: [
      { id: "checkoutReq", name: "CheckoutRequest", change: "type" } ] },
    { id: "PaymentGateway", group: "pay", name: "PaymentGateway.java", entities: [
      { id: "charge", name: "charge(Money, Card, String)", change: "signature",
        before: "Receipt charge(Money amount, Card card)", after: "Receipt charge(Money amount, Card card, String idempotencyKey)" } ] },
    { id: "RefundPolicy", group: "pay", name: "RefundPolicy.java", entities: [
      { id: "refundDays", name: "MAX_REFUND_DAYS", change: "value", before: "30", after: "14" } ] },
    { id: "PaymentClient", group: "pay", name: "PaymentClient.java", entities: [
      { id: "retry", name: "withRetry(Supplier<T>)", change: "body" } ] },
    { id: "Money", group: "money", name: "Money.java", entities: [
      { id: "plus", name: "plus(Money)", change: "body" },
      { id: "round", name: "round()", change: "removed" } ] },
    { id: "Currency", group: "money", name: "Currency.java", entities: [
      { id: "currencyDefault", name: "DEFAULT", change: "value", before: "USD", after: "EUR" } ] },
    { id: "CheckoutComponent", group: "checkout", name: "checkout.component", parts: ["ts", "html", "scss"], entities: [
      { id: "submit", name: "submit()", change: "body" },
      { id: "checkoutTpl", name: "template", change: "module" },
      { id: "checkoutStyle", name: "styles", change: "cosmetic" } ] },
    { id: "CartService", group: "core", name: "cart.service.ts", entities: [
      { id: "applyCoupon", name: "applyCoupon(code, cart)", change: "signature",
        before: "applyCoupon(code: string): Cart", after: "applyCoupon(code: string, cart: Cart): Cart" } ] },
    { id: "PricePipe", group: "core", name: "price.pipe.ts", entities: [
      { id: "transform", name: "transform(value)", change: "body" } ] },
    { id: "ApiClient", group: "core", name: "api.client.ts", entities: [
      { id: "post", name: "post<T>(url, body)", change: "body" } ] },
  ];

  // group | file | name | depth | from | via | flag (E:message, P = possible dispatch, D:reason)
  const impactedSpec = `
i1|api|CheckoutController.java|checkout(CheckoutRequest)|1|placeOrder|call|E:placeOrder(Cart) is not applicable: argument Coupon is required
i2|api|OrderController.java|reorder(OrderId)|1|computeTotal|call|
i3|order|OrderService.java|quote(Cart)|1|computeTotal|call|
i5|order|OrderServiceProxy.java|placeOrder(Cart, Coupon)|1|placeOrder|implements|
i6|order|OrderAuditor.java|audit(Order)|1|validate|call|
i7|api|OrderController.java|submit(OrderDto)|1|validate|call|
i8|order|OrderQueryService.java|openOrdersFor(Customer)|1|findOpenOrders|call|
i9|report|DailyOrderReport.java|build(LocalDate)|1|findOpenOrders|call|
i10|notify|PaymentReminderJob.java|run()|1|orderStatus|ref|
i11|report|OrderStatusMetrics.java|count()|1|orderStatus|ref|
i12|api|OrderMapper.java|toEntity(OrderDto)|1|couponCode|ref|
i13|api|OrderControllerAdvice.java|handleInvalid(Exception)|1|checkoutReq|ref|
i14|pay|PaymentService.java|capture(Order)|1|charge|call|E:charge(Money, Card) is undefined; expected (Money, Card, String)
i15|pay|StripeGateway.java|charge(Money, Card, String)|1|charge|implements|
i16|pay|RefundService.java|isRefundable(Order)|1|refundDays|ref|
i17|pay|PaymentService.java|refund(Order)|1|retry|call|
i18|money|MoneyTotals.java|sum(List<Money>)|1|plus|call|
i19|report|InvoiceFormatter.java|format(Money)|1|round|call|D:caller found on the base revision
i20|pay|FxConverter.java|convert(Money, Currency)|1|currencyDefault|ref|
i21|history|reorder.button.ts|onClick()|1|submit|call|P
i22|checkout|checkout-summary.component.ts|apply()|1|applyCoupon|call|
i23|admin|promo-preview.component.ts|preview()|1|applyCoupon|call|
i24|core|cart.store.ts|redeem(code)|1|applyCoupon|call|
i25|history|order-row.component.html|price pipe|1|transform|ref|
i26|core|order.api.ts|create(dto)|1|post|call|
i27|core|payment.api.ts|confirm(id)|1|post|call|
i28|api|CheckoutApi.java|postCheckout()|2|i1|call|
i29|report|DailyOrderReport.java|export(LocalDate)|2|i9|call|
i30|notify|ReminderScheduler.java|schedule()|2|i10|call|
i31|pay|PaymentListener.java|onOrderPlaced(Event)|2|i14|call|
i32|pay|RefundController.java|refund(OrderId)|2|i16,i17|call|
i33|money|CartTotals.java|grandTotal(Cart)|2|i18|call|
i34|report|RevenueReport.java|byCurrency()|2|i20|call|
i35|checkout|checkout.page.ts|confirm()|2|i22|call|
i36|admin|promo.page.ts|load()|2|i23|call|
i37|core|checkout.effects.ts|placeOrder$|2|i24,i26|call|
i38|history|order-history.page.ts|refresh()|2|i25|call|
i39|api|OrderQueryController.java|open(CustomerId)|2|i8|call|
i40|api|QuoteController.java|quote(CartDto)|2|i3|call|
i41|pay|PaymentController.java|capture(OrderId)|2|i14|call|`;

  const impacted = impactedSpec.trim().split("\n").map((line) => {
    const [id, group, file, name, depth, from, via, flag] = line.split("|");
    const n = { id, group, file, name, depth: +depth, from: from.split(","), via };
    if (flag?.startsWith("E:")) n.error = flag.slice(2);
    else if (flag === "P") n.possible = true;
    else if (flag?.startsWith("D:")) n.degraded = flag.slice(2);
    return n;
  });

  const code = {
    i1: ["    Order order = orderService.placeOrder(cart);", 48],
    i14: ["    Receipt r = gateway.charge(order.total(), order.card());", 71],
    i3: ["    return new Quote(computeTotal(cart), cart.currency());", 112],
    i5: ["  public Order placeOrder(Cart cart, Coupon coupon) {", 23],
  };

  const ai = {
    scope: "PaymentGateway",
    findings: [
      { sev: "high", title: "重试会生成新的幂等键，幂等保护失效",
        at: "payment-service/.../PaymentClient.java:58",
        evidence: "withRetry 每次重试都重新调用 supplier；PaymentService.capture 在 supplier 内部生成 UUID 作为 idempotencyKey，第二次重试会带着新键重复扣款。",
        fix: "在进入 withRetry 之前生成 idempotencyKey，并在每次重试中复用。" },
      { sev: "medium", title: "StripeGateway 未把 idempotencyKey 传给下游",
        at: "payment-service/.../StripeGateway.java:41",
        evidence: "新增参数只出现在方法签名里，请求头 Idempotency-Key 仍未设置，网关接口层面没有幂等。",
        fix: "在 createCharge 请求上设置 Idempotency-Key 请求头。" },
    ],
    clean: ["RefundPolicy.java", "Currency.java"],
  };

  const large = {
    files: 126, entities: 214, impacted: 388, errors: 5,
    groups: [
      ["payment-service", "com.shop.payment", 14, 22, 41, 2, 6],
      ["order-service", "com.shop.order", 12, 19, 37, 1, 5],
      ["web", "app/checkout", 9, 15, 28, 1, 3],
      ["shared-lib", "com.shop.common.money", 6, 9, 64, 1, 4],
      ["order-service", "com.shop.order.api", 11, 16, 22, 0, 2],
      ["web", "app/core", 10, 18, 35, 0, 3],
      ["inventory-service", "com.shop.inventory", 13, 21, 19, 0, 1],
      ["web", "app/admin", 15, 24, 12, 0, 0],
      ["notification-service", "com.shop.notify", 8, 11, 9, 0, 1],
      ["reporting", "com.shop.report", 12, 23, 30, 0, 0],
      ["search-service", "com.shop.search", 9, 20, 14, 0, 0],
      ["web", "app/order-history", 7, 16, 8, 0, 0],
    ],
  };

  return { range: { base: "main", head: "feature/coupon-checkout" }, groups, changed, impacted, code, ai, large };
})();
