// Prototype renderer shared by all three styles. Vanilla JS, no build step.
(() => {
  const P = window.PROTO;
  const STYLE = document.documentElement.dataset.style;

  // ------------------------------------------------------------------ graph
  const nodes = new Map();
  const out = new Map();
  const inc = new Map();
  const edges = [];
  for (const f of P.changed) for (const e of f.entities) nodes.set(e.id, { ...e, kind: "changed", file: f, group: f.group });
  for (const i of P.impacted) {
    nodes.set(i.id, { ...i, kind: "impacted" });
    for (const src of i.from) {
      const e = { from: src, to: i.id, via: i.via, error: !!i.error, possible: !!i.possible };
      edges.push(e);
      (out.get(src) ?? out.set(src, []).get(src)).push(e);
      (inc.get(i.id) ?? inc.set(i.id, []).get(i.id)).push(e);
    }
  }
  const walk = (start, adj, key) => {
    const seen = new Set();
    const q = [...start];
    while (q.length) {
      const id = q.shift();
      for (const e of adj.get(id) ?? []) if (!seen.has(e[key])) { seen.add(e[key]); q.push(e[key]); }
    }
    return seen;
  };
  const down = (ids) => walk(ids, out, "to");
  const up = (ids) => walk(ids, inc, "from");
  const scopeOf = (ids) => new Set([...ids, ...down(ids), ...up(ids)]);
  const impactCount = (id) => down([id]).size;

  const CONTRACT = new Set(["signature", "removed", "renamed", "value", "type"]);
  const TAG = { signature: "签名", removed: "删除", renamed: "重命名", value: "值", type: "类型", body: "实现", added: "新增", cosmetic: "格式", module: "模板" };
  const VIA = { call: "调用", ref: "引用", implements: "实现", dispatch: "可能分发" };

  // ------------------------------------------------------------------ state
  const S = { state: "default", hover: null, focus: null, drawer: null, tab: "detail", hidden: new Set(), reviewed: new Set(["OrderValidator", "OrderDto", "Currency"]) };
  const $ = (s, r = document) => r.querySelector(s);
  const h = (tag, cls, html) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // ------------------------------------------------------------------ chrome
  const LOGO = `<svg class="logo" viewBox="0 0 32 32" aria-hidden="true">
    <path d="M5.2 26.2V6.4h6.4a5.2 5.2 0 0 1 0 10.4H5.2"/>
    <path d="M10.6 16.8 17.2 26.2"/>
    <circle class="logo-dot" cx="11.2" cy="11.6" r="1.45"/>
    <path d="M20.4 6.4v19.8H29"/></svg>`;

  function topbar() {
    const errors = P.impacted.filter((i) => i.error).length;
    const fileCount = P.changed.reduce((a, f) => a + (f.parts?.length ?? 1), 0);
    const changedEntities = P.changed.reduce((a, f) => a + f.entities.length, 0);
    const large = S.state === "large";
    const bar = h("header", "topbar");
    bar.innerHTML = `
      <div class="brand">${LOGO}<span class="brand-name">RippleLens</span></div>
      <div class="range"><span class="ref">${P.range.base}</span><span class="range-arrow">→</span><span class="ref ref-head">${P.range.head}</span></div>
      <div class="metrics">
        <span class="metric metric-changed"><b>${large ? P.large.files : fileCount}</b> 个文件改动<i>${large ? P.large.entities : changedEntities} 处</i></span>
        <span class="metric metric-impact"><b>${large ? P.large.impacted : P.impacted.length}</b> 个函数受波及</span>
        <span class="metric metric-error"><b>${large ? P.large.errors : errors}</b> 个编译错误</span>
      </div>
      <div class="focus-slot"></div>
      <div class="spacer"></div>
      <div class="progress" title="已阅进度"><span class="progress-bar"><i style="width:${(S.reviewed.size / P.changed.length) * 100}%"></i></span>${S.reviewed.size}/${P.changed.length} 已阅</div>
      <label class="search"><svg viewBox="0 0 16 16"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 14 14"/></svg><input placeholder="搜索函数或文件" /><kbd>/</kbd></label>
      <button class="icon-btn" title="更多">⋯</button>`;
    if (S.focus) {
      const chip = h("button", "focus-chip", `<span class="focus-label">聚焦</span>${esc(S.focus.label)}<span class="x">×</span>`);
      chip.onclick = () => { S.focus = null; S.drawer = null; render(); };
      $(".focus-slot", bar).append(chip);
    }
    return bar;
  }

  // ------------------------------------------------------------------ board
  function groupHead(key, extra = "") {
    const g = P.groups[key];
    return `<div class="group-head"><span class="group-module">${esc(g.module)}</span><span class="group-sep">/</span><span class="group-pkg">${esc(g.pkg)}</span>${extra}</div>`;
  }

  function changedCard(f) {
    const card = h("article", "card card-changed");
    card.dataset.ids = f.entities.map((e) => e.id).join(",");
    card.dataset.file = f.id;
    const total = new Set(f.entities.flatMap((e) => [...down([e.id])])).size;
    const parts = f.parts ? `<span class="parts">${f.parts.map((p) => `<i>${p}</i>`).join("")}</span>` : "";
    const ai = P.ai.scope === f.id && S.state === "ai" ? `<span class="ai-mark">AI 2</span>` : "";
    const done = S.reviewed.has(f.id) ? `<span class="reviewed" title="已阅">✓</span>` : "";
    card.innerHTML = `
      <div class="card-head"><span class="lang lang-${P.groups[f.group].lang}"></span><span class="file">${esc(f.name)}</span>${parts}${ai}${done}
        <span class="card-meta">${total ? `→ ${total}` : ""}</span>
        <span class="card-actions"><button title="聚焦">⌖</button><button title="隐藏">⊘</button></span></div>
      <div class="rows">${f.entities.map((e) => {
        const n = impactCount(e.id);
        const kind = CONTRACT.has(e.change) ? "contract" : e.change === "body" ? "body" : "minor";
        return `<div class="row row-changed" data-id="${e.id}"><span class="tag tag-${kind}">${TAG[e.change]}</span><span class="sym">${esc(e.name)}</span><span class="count">${n || ""}</span></div>`;
      }).join("")}</div>`;
    return card;
  }

  function impactedCard(file, members) {
    const card = h("article", "card card-impacted");
    card.dataset.ids = members.map((m) => m.id).join(",");
    card.innerHTML = `
      <div class="card-head"><span class="lang lang-${P.groups[members[0].group].lang}"></span><span class="file">${esc(file)}</span>
        <span class="card-meta">${members.length > 1 ? members.length : ""}</span>
        <span class="card-actions"><button title="聚焦">⌖</button><button title="隐藏">⊘</button></span></div>
      <div class="rows">${members.map((m) => `
        <div class="row row-impacted depth-${Math.min(m.depth, 2)} ${m.error ? "has-error" : ""} ${m.possible || m.degraded ? "uncertain" : ""}" data-id="${m.id}">
          <span class="dot"></span><span class="sym">${esc(m.name)}</span>
          <span class="via">${m.error ? `<b class="err-badge">编译错误</b>` : m.possible ? "可能" : m.degraded ? "降级" : VIA[m.via]}</span>
          ${m.error ? `<div class="err-msg">${esc(m.error)}</div>` : ""}
        </div>`).join("")}</div>`;
    return card;
  }

  function riskOfGroup(key) {
    const files = P.changed.filter((f) => f.group === key);
    let r = 0;
    for (const f of files) for (const e of f.entities) {
      r += (CONTRACT.has(e.change) ? 3 : 1) + impactCount(e.id) * 0.3;
      for (const d of down([e.id])) if (nodes.get(d).error) r += 10;
    }
    return r;
  }

  function column(title, cls, count) {
    const col = h("section", `col ${cls}`);
    col.innerHTML = `<div class="col-head"><span class="col-dot"></span><span class="col-title">${title}</span><span class="col-count">${count}</span></div>`;
    return col;
  }

  function renderBoard(board) {
    const cols = h("div", "cols");
    const changedGroups = [...new Set(P.changed.map((f) => f.group))].sort((a, b) => riskOfGroup(b) - riskOfGroup(a));
    const c0 = column("本次改动", "col-changed", P.changed.reduce((a, f) => a + (f.parts?.length ?? 1), 0) + " 个文件");
    for (const g of changedGroups) {
      const sec = h("div", "group");
      sec.innerHTML = groupHead(g);
      for (const f of P.changed.filter((x) => x.group === g)) sec.append(changedCard(f));
      c0.append(sec);
    }
    cols.append(c0);
    for (const depth of [1, 2]) {
      const items = P.impacted.filter((i) => Math.min(i.depth, 2) === depth);
      const col = column(depth === 1 ? "直接波及" : "间接波及", `col-impact col-depth-${depth}`, items.length + " 个函数");
      const byGroup = new Map();
      for (const i of items) (byGroup.get(i.group) ?? byGroup.set(i.group, new Map()).get(i.group));
      for (const i of items) {
        const files = byGroup.get(i.group);
        (files.get(i.file) ?? files.set(i.file, []).get(i.file)).push(i);
      }
      const order = [...byGroup.keys()].sort((a, b) => {
        const err = (k) => [...byGroup.get(k).values()].flat().some((m) => m.error) ? 1 : 0;
        return err(b) - err(a) || [...byGroup.get(b).values()].flat().length - [...byGroup.get(a).values()].flat().length;
      });
      for (const g of order) {
        const sec = h("div", "group");
        sec.innerHTML = groupHead(g);
        const files = [...byGroup.get(g).entries()].sort((a, b) => (b[1].some((m) => m.error) ? 1 : 0) - (a[1].some((m) => m.error) ? 1 : 0));
        for (const [file, members] of files) sec.append(impactedCard(file, members));
        col.append(sec);
      }
      cols.append(col);
    }
    board.append(cols);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("wires");
    board.append(svg);
    wire(board);
  }

  function renderLarge(board) {
    const L = P.large;
    const banner = h("div", "large-banner", `<b>大型改动</b>：${L.files} 个文件分布在 ${L.groups.length} 个包里，已按风险折叠为分组。点开分组再看文件，或用搜索直接定位。`);
    board.append(banner);
    const cols = h("div", "cols cols-large");
    const c0 = column("本次改动", "col-changed", `${L.groups.length} 个分组`);
    const c1 = column("波及范围", "col-impact col-depth-1", `${L.impacted} 个函数`);
    const max = Math.max(...L.groups.map((g) => g[4]));
    L.groups.forEach(([mod, pkg, files, ents, callers, errs, contract], idx) => {
      const row = h("article", `card card-changed grouprow ${idx === 0 ? "open" : ""}`);
      row.innerHTML = `
        <div class="card-head"><span class="chev">${idx === 0 ? "▾" : "▸"}</span>
          <span class="group-module">${mod}</span><span class="group-sep">/</span><span class="file">${pkg}</span>
          ${errs ? `<b class="err-badge">${errs} 错误</b>` : ""}<span class="card-meta">${files} 个文件</span></div>
        <div class="grouprow-stats"><span class="stack"><i class="stack-contract" style="flex:${contract}"></i><i class="stack-body" style="flex:${ents - contract}"></i></span>
          <span>${contract} 处契约变更 · ${ents - contract} 处实现变更</span></div>
        ${idx === 0 ? `<div class="rows">${["PaymentGateway.java", "RefundPolicy.java", "PaymentClient.java", "CardVault.java"].map((f, k) => `
          <div class="row row-changed"><span class="tag tag-${k < 2 ? "contract" : "body"}">${k === 0 ? "签名" : k === 1 ? "值" : "实现"}</span><span class="sym">${f}</span><span class="count">${[9, 4, 3, 2][k]}</span></div>`).join("")}
          <div class="row more">还有 10 个文件</div></div>` : ""}`;
      c0.append(row);
      const imp = h("article", "card card-impacted grouprow");
      imp.innerHTML = `
        <div class="card-head"><span class="chev">▸</span><span class="group-module">${mod}</span><span class="group-sep">/</span><span class="file">${pkg}</span>
          ${errs ? `<b class="err-badge">${errs}</b>` : ""}<span class="card-meta">${callers} 个调用方</span></div>
        <div class="heat"><i style="width:${(callers / max) * 100}%"></i></div>`;
      c1.append(imp);
    });
    cols.append(c0, c1);
    board.append(cols);
  }

  // ------------------------------------------------------------------ interaction
  function lit(board) {
    const active = S.hover ?? S.focus?.ids ?? null;
    board.classList.toggle("peeking", !!S.hover);
    const scope = active ? scopeOf(active) : null;
    board.querySelectorAll(".row[data-id]").forEach((r) => r.classList.toggle("lit", !!scope && scope.has(r.dataset.id)));
    board.querySelectorAll(".card[data-ids]").forEach((c) => {
      const ids = c.dataset.ids.split(",");
      c.classList.toggle("lit", !!scope && ids.some((i) => scope.has(i)));
      c.classList.toggle("source", !!active && ids.some((i) => active.includes(i)));
    });
    drawWires(board, scope);
  }

  function drawWires(board, scope) {
    const svg = $(".wires", board);
    if (!svg) return;
    svg.innerHTML = "";
    if (!scope) return;
    const box = board.getBoundingClientRect();
    const z = board.currentCSSZoom || 1;
    svg.setAttribute("width", board.scrollWidth);
    svg.setAttribute("height", board.scrollHeight);
    const pos = (id) => {
      const r = board.querySelector(`.row[data-id="${id}"]`);
      if (!r || !r.offsetParent) return null;
      const b = r.getBoundingClientRect();
      return {
        l: (b.left - box.left) / z + board.scrollLeft,
        r: (b.right - box.left) / z + board.scrollLeft,
        y: (b.top - box.top + b.height / 2) / z + board.scrollTop,
      };
    };
    for (const e of edges) {
      if (!scope.has(e.from) || !scope.has(e.to)) continue;
      const a = pos(e.from);
      const b = pos(e.to);
      if (!a || !b) continue;
      const x1 = a.r + 2, x2 = b.l - 2, dx = Math.max(40, (x2 - x1) * 0.5);
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", `M${x1},${a.y} C${x1 + dx},${a.y} ${x2 - dx},${b.y} ${x2},${b.y}`);
      p.setAttribute("class", `wire ${e.error ? "wire-error" : ""} ${e.possible || e.via === "implements" ? "wire-soft" : ""}`);
      svg.append(p);
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", x2); dot.setAttribute("cy", b.y); dot.setAttribute("r", 2.6);
      dot.setAttribute("class", `wire-end ${e.error ? "wire-error" : ""}`);
      svg.append(dot);
    }
  }

  function wire(board) {
    board.querySelectorAll(".row[data-id]").forEach((r) => {
      r.onmouseenter = () => { if (!S.focus) { S.hover = [r.dataset.id]; lit(board); } };
      r.onmouseleave = () => { if (!S.focus) { S.hover = null; lit(board); } };
      r.onclick = (ev) => { ev.stopPropagation(); focusOn([r.dataset.id], nodes.get(r.dataset.id).name); };
    });
    board.querySelectorAll(".card[data-ids] .card-head").forEach((hd) => {
      const card = hd.parentElement;
      const ids = card.dataset.ids.split(",");
      hd.onmouseenter = () => { if (!S.focus) { S.hover = ids; lit(board); } };
      hd.onmouseleave = () => { if (!S.focus) { S.hover = null; lit(board); } };
      hd.onclick = () => focusOn(ids, $(".file", hd).textContent);
    });
  }

  function focusOn(ids, label) {
    S.focus = { ids, label };
    S.hover = null;
    S.drawer = ids[0];
    render();
  }

  function applyFocus(board) {
    if (!S.focus) return;
    const scope = scopeOf(S.focus.ids);
    board.classList.add("focused");
    board.querySelectorAll(".row[data-id]").forEach((r) => r.classList.toggle("out", !scope.has(r.dataset.id)));
    board.querySelectorAll(".card[data-ids]").forEach((c) => c.classList.toggle("out", !c.dataset.ids.split(",").some((i) => scope.has(i))));
    board.querySelectorAll(".group").forEach((g) => g.classList.toggle("out", !g.querySelector(".card:not(.out)")));
  }

  // ------------------------------------------------------------------ drawer
  function drawer() {
    if (!S.drawer) return null;
    const n = nodes.get(S.drawer);
    const d = h("aside", "drawer");
    const isAi = S.tab === "ai";
    d.innerHTML = `
      <div class="drawer-tabs"><button class="${isAi ? "" : "on"}" data-tab="detail">详情</button><button class="${isAi ? "on" : ""}" data-tab="ai">AI 评审<span class="beta">已开启</span></button><span class="spacer"></span><button class="x" title="关闭">×</button></div>
      <div class="drawer-body">${isAi ? aiBody() : detailBody(n)}</div>`;
    d.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { S.tab = b.dataset.tab; render(); }));
    $(".x", d).onclick = () => { S.drawer = null; render(); };
    return d;
  }

  function detailBody(n) {
    if (n.kind === "changed") {
      const callers = [...down([n.id])].map((id) => nodes.get(id)).filter((x) => x.from.includes(n.id));
      const kind = CONTRACT.has(n.change) ? "contract" : n.change === "body" ? "body" : "minor";
      return `
        <div class="d-kicker">${esc(n.file.name)} · ${esc(P.groups[n.group].pkg)}</div>
        <h2 class="d-title"><span class="tag tag-${kind}">${TAG[n.change]}</span>${esc(n.name)}</h2>
        ${n.before ? `<div class="sigdiff"><div class="del">− ${esc(n.before)}</div><div class="add">+ ${esc(n.after)}</div></div>` : ""}
        <p class="d-hint">签名变化由编译器给出，所有调用方都需要检查。</p>
        <h3 class="d-sub">直接调用方 <span>${callers.length}</span></h3>
        ${callers.map((c) => `
          <div class="caller ${c.error ? "has-error" : ""}">
            <div class="caller-head"><span class="sym">${esc(c.name)}</span><span class="caller-file">${esc(c.file)}${P.code[c.id] ? ":" + P.code[c.id][1] : ""}</span></div>
            ${P.code[c.id] ? `<pre class="snippet">${esc(P.code[c.id][0].trim())}</pre>` : ""}
            ${c.error ? `<div class="err-msg">${esc(c.error)}</div>` : ""}
          </div>`).join("")}
        <div class="d-actions"><button class="btn btn-primary">标记为已阅</button><button class="btn">复制给 AI</button></div>`;
    }
    return `<div class="d-kicker">${esc(n.file)}</div><h2 class="d-title">${esc(n.name)}</h2>`;
  }

  function aiBody() {
    const A = P.ai;
    return `
      <div class="d-kicker">PaymentGateway.java · 签名变更 · 3 个直接调用方</div>
      <div class="ai-meta"><span class="ai-model">claude-sonnet · 结构化上下文 3.1k tokens</span><span class="ai-live"><i></i>正在评审</span></div>
      ${A.findings.map((f, i) => `
        <article class="finding sev-${f.sev}">
          <div class="finding-head"><span class="sev">${f.sev === "high" ? "高" : f.sev === "medium" ? "中" : "低"}</span><span class="finding-title">${esc(f.title)}</span></div>
          <div class="finding-at">${esc(f.at)}</div>
          <p><b>依据</b>${esc(f.evidence)}</p>
          <p><b>建议</b>${esc(i === 1 ? f.fix.slice(0, 18) : f.fix)}${i === 1 ? '<span class="caret"></span>' : ""}</p>
        </article>`).join("")}
      <div class="clean">${A.clean.map((c) => `<span>${c}</span>`).join("")}<em>未发现问题</em></div>`;
  }

  // ------------------------------------------------------------------ states (prototype only)
  const STATES = [
    ["default", "默认"], ["hover", "悬浮"], ["focus", "点击聚焦"], ["large", "大量文件"], ["ai", "AI 评审"],
  ];

  function switcher() {
    const s = h("nav", "switcher");
    s.innerHTML = `<span class="switcher-label">${STYLE}</span>` + STATES.map(([k, t]) => `<button data-s="${k}" class="${S.state === k ? "on" : ""}">${t}</button>`).join("");
    s.querySelectorAll("button").forEach((b) => (b.onclick = () => setState(b.dataset.s)));
    return s;
  }

  function setState(k) {
    S.state = k;
    S.hover = null; S.focus = null; S.drawer = null; S.tab = "detail";
    if (k === "focus") { S.focus = { ids: ["placeOrder", "computeTotal"], label: "OrderService.java" }; S.drawer = "placeOrder"; }
    if (k === "ai") { S.focus = { ids: ["charge"], label: "PaymentGateway.java" }; S.drawer = "charge"; S.tab = "ai"; }
    history.replaceState(null, "", "#" + k);
    render();
    if (k === "hover") requestAnimationFrame(() => { S.hover = ["placeOrder", "computeTotal"]; lit($(".board")); });
  }

  function render() {
    const root = $("#app");
    root.innerHTML = "";
    root.append(topbar());
    const main = h("main", "main");
    const board = h("div", "board");
    main.append(board);
    if (S.state === "large") renderLarge(board);
    else renderBoard(board);
    applyFocus(board);
    const d = drawer();
    if (d) { main.append(d); main.classList.add("with-drawer"); }
    root.append(main, switcher());
    if (S.state !== "large") requestAnimationFrame(() => lit(board));
    board.onclick = (e) => { if (e.target === board || e.target.classList.contains("cols")) { if (S.focus) { S.focus = null; S.drawer = null; render(); } } };
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && S.focus) { S.focus = null; S.drawer = null; render(); }
  });
  window.addEventListener("resize", () => lit($(".board")));
  const zoom = new URLSearchParams(location.search).get("zoom");
  if (zoom) document.documentElement.style.zoom = zoom;
  setState((location.hash.slice(1) || "default").replace(/[^a-z]/g, "") || "default");
})();
