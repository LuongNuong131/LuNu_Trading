(() => {
  const select = (selector, root = document) => root.querySelector(selector);
  const all = (selector, root = document) => [...root.querySelectorAll(selector)];
  const format = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 5 });
  const charts = new WeakMap();
  let chartResize;
  let refreshing = false;
  let composing = false;

  function draw(canvas) {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    const values = JSON.parse(canvas.dataset.points).filter(point => Number.isFinite(point.value));
    if (!values.length) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    context.scale(dpr, dpr);
    const left = 64, right = width - 11, top = 18, bottom = height - 27;
    const amounts = values.map(point => point.value);
    const min = Math.min(...amounts), max = Math.max(...amounts);
    const target = Math.max((max - min) / 4, Math.abs(max) * 0.00005, .00001);
    const unit = 10 ** Math.floor(Math.log10(target));
    const step = [1, 2, 2.5, 5, 10].map(scale => scale * unit).find(value => value >= target) || 10 * unit;
    const low = Math.floor(min / step) * step - step;
    const high = Math.ceil(max / step) * step + step;
    const y = value => bottom - (value - low) / (high - low) * (bottom - top);
    const points = values.map((point, index) => ({
      ...point,
      x: values.length === 1 ? left : left + index * (right - left) / (values.length - 1),
      y: y(point.value),
    }));
    charts.set(canvas, { points, index: -1 });
    context.font = "11px system-ui";
    context.textBaseline = "middle";
    const ticks = min === max ? [min] : Array.from(
      { length: Math.round((high - low) / step) + 1 }, (_, index) => low + step * index);
    for (const price of ticks) {
      const line = y(price);
      context.strokeStyle = "#28334a";
      context.beginPath();
      context.moveTo(left, line);
      context.lineTo(right, line);
      context.stroke();
      context.fillStyle = "#a0acc4";
      context.textAlign = "right";
      context.fillText(format.format(price), left - 8, line, left - 10);
    }
    context.fillStyle = "#a0acc4";
    context.textAlign = "left";
    if (points.length > 1) context.fillText("Đầu phạm vi", left, bottom + 16);
    context.textAlign = "right";
    context.fillText("Ảnh chụp hiện tại", right, bottom + 16);
    context.beginPath();
    points.forEach(({ x, y }, i) => i === 0 ? context.moveTo(x, y) : context.lineTo(x, y));
    context.strokeStyle = "#9b9eff";
    context.lineWidth = 2.1;
    context.stroke();
    const last = points.at(-1);
    if (points.length > 1) {
      const shade = context.createLinearGradient(0, top, 0, bottom);
      shade.addColorStop(0, "rgba(139,145,255,.32)");
      shade.addColorStop(1, "rgba(139,145,255,.02)");
      context.lineTo(last.x, bottom);
      context.lineTo(points[0].x, bottom);
      context.closePath();
      context.fillStyle = shade;
      context.fill();
    }
    context.fillStyle = "#c0c3ff";
    context.beginPath();
    context.arc(last.x, last.y, 3.6, 0, Math.PI * 2);
    context.fill();
  }

  function chartTooltip(canvas, index) {
    const chart = charts.get(canvas);
    if (!chart) return;
    const tooltip = select(".chart-tooltip", canvas.parentElement);
    chart.index = index;
    if (index === -1 || chart.points.length < 2) {
      tooltip.hidden = true;
      return;
    }
    const point = chart.points[index];
    tooltip.textContent = format.format(point.value) + " · " + point.label;
    tooltip.hidden = false;
    tooltip.style.left = Math.max(0, Math.min(point.x - tooltip.offsetWidth / 2,
      canvas.clientWidth - tooltip.offsetWidth)) + "px";
  }

  function showWallet(currency) {
    const wallets = all("[data-wallet]");
    if (!wallets.some(wallet => wallet.dataset.wallet === currency)) currency = wallets[0]?.dataset.wallet;
    for (const wallet of wallets) wallet.hidden = wallet.dataset.wallet !== currency;
    for (const button of all("[data-select-wallet]")) {
      button.setAttribute("aria-pressed", button.dataset.selectWallet === currency);
    }
    initCharts();
  }

  function initCharts() {
    chartResize?.disconnect();
    chartResize = "ResizeObserver" in window ? new ResizeObserver(entries => {
      for (const entry of entries) draw(entry.target);
    }) : null;
    for (const canvas of all("canvas[data-points]")) {
      draw(canvas);
      chartResize?.observe(canvas);
    }
  }

  function filter(section) {
    const query = select('[data-search="' + section + '"]')?.value.trim().toLocaleLowerCase("vi") || "";
    const kind = select('[data-filter="' + section + '"]')?.value || "";
    const rows = all('[data-row="' + section + '"]');
    for (const row of rows) {
      const summary = select("summary", row);
      row.hidden = (!!kind && row.dataset.kind !== kind) || !summary.textContent.toLocaleLowerCase("vi").includes(query);
    }
    const empty = select('[data-empty="' + section + '"]');
    if (empty) empty.hidden = !rows.length || rows.some(row => !row.hidden);
  }

  function bindings(wallet) {
    for (const tools of all("[data-tools]")) tools.hidden = false;
    for (const section of ["watch", "audit"]) filter(section);
    showWallet(wallet);
  }

  function updateNavigation() {
    const active = window.location.hash || "#top";
    for (const a of all(".sidebar nav a")) {
      if (a.getAttribute("href") === active) a.setAttribute("aria-current", "location");
      else a.removeAttribute("aria-current");
    }
  }

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    const button = select("#refresh");
    const status = select("#refresh-status");
    button.disabled = true;
    try {
      const response = await fetch("/dashboard", { cache: "no-store", signal: AbortSignal.timeout(10_000) });
      if (!response.ok) throw new Error("Dashboard HTTP " + response.status);
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const incoming = select(".dashboard-view", page);
      if (!incoming) throw new Error("Dashboard content missing");
      const current = select(".dashboard-view");
      // Keep controls and details where the reader left them on each refresh.
      const open = new Set(all("details[open][data-disclosure]", current).map(el => el.dataset.disclosure));
      const controls = all("[data-search], [data-filter]", current);
      const wallet = select('[data-select-wallet][aria-pressed="true"]', current)?.dataset.selectWallet;
      const scrolls = all(".scroll", current).map(el => el.scrollLeft);
      for (const detail of all("details[data-disclosure]", incoming)) detail.open = open.has(detail.dataset.disclosure);
      for (const control of all("[data-search], [data-filter]", incoming)) {
        const old = controls.find(el => el.dataset.search === control.dataset.search && el.dataset.filter === control.dataset.filter);
        if (old) control.value = old.value;
      }
      const focus = document.activeElement;
      const search = focus?.dataset.search;
      const filterName = focus?.dataset.filter;
      const disclosure = focus?.closest("details")?.dataset.disclosure;
      const start = search ? focus.selectionStart : null;
      const end = search ? focus.selectionEnd : null;
      current.replaceWith(incoming);
      bindings(wallet);
      all(".scroll", incoming).forEach((el, index) => { el.scrollLeft = scrolls[index] || 0; });
      if (search) {
        const input = all("[data-search]").find(el => el.dataset.search === search);
        input?.focus({ preventScroll: true });
        if (start !== null) input?.setSelectionRange(start, end);
      } else if (filterName) {
        all("[data-filter]").find(el => el.dataset.filter === filterName)?.focus({ preventScroll: true });
      } else if (disclosure) {
        all("details[data-disclosure]").find(el => el.dataset.disclosure === disclosure)?.querySelector("summary")
          ?.focus({ preventScroll: true });
      }
      status.textContent = "Cập nhật " + new Date().toLocaleTimeString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" });
      status.classList.remove("error");
    } catch {
      status.textContent = "Mất kết nối · đang hiển thị dữ liệu cũ";
      status.classList.add("error");
    } finally {
      button.disabled = false;
      refreshing = false;
    }
  }

  document.addEventListener("click", event => {
    const tab = event.target.closest("[data-select-wallet]");
    if (tab) showWallet(tab.dataset.selectWallet);
  });
  document.addEventListener("input", event => {
    if (event.target.dataset.search) filter(event.target.dataset.search);
  });
  document.addEventListener("change", event => {
    if (event.target.dataset.filter) filter(event.target.dataset.filter);
  });
  document.addEventListener("pointermove", event => {
    const canvas = event.target;
    if (!canvas.matches("canvas[data-points]")) return;
    const chart = charts.get(canvas);
    if (!chart) return;
    const x = event.clientX - canvas.getBoundingClientRect().left;
    const closest = chart.points.reduce((best, point, i) =>
      Math.abs(point.x - x) < Math.abs(chart.points[best].x - x) ? i : best, 0);
    chartTooltip(canvas, closest);
  });
  document.addEventListener("pointerout", event => {
    if (event.target.matches("canvas[data-points]")) chartTooltip(event.target, -1);
  });
  document.addEventListener("focusout", event => {
    if (event.target.matches("canvas[data-points]")) chartTooltip(event.target, -1);
  });
  document.addEventListener("keydown", event => {
    if (!event.target.matches("canvas[data-points]")) return;
    const chart = charts.get(event.target);
    if (!chart || !["ArrowLeft", "ArrowRight", "Escape"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Escape") chartTooltip(event.target, -1);
    else chartTooltip(event.target, Math.max(0, Math.min(chart.points.length - 1,
      chart.index + (event.key === "ArrowRight" ? 1 : -1))));
  });
  document.addEventListener("compositionstart", () => { composing = true; });
  document.addEventListener("compositionend", () => { composing = false; });
  if (document.body.dataset.live) {
    const button = select("#refresh");
    button.hidden = false;
    button.addEventListener("click", refresh);
    window.setInterval(() => {
      if (!document.hidden && !composing && !document.activeElement?.matches("canvas, select")) refresh();
    }, 15_000);
  }
  if (!("ResizeObserver" in window)) window.addEventListener("resize", initCharts);
  window.addEventListener("hashchange", updateNavigation);
  bindings();
  updateNavigation();
})();
