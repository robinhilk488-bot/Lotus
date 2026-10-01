"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const money = (v, cur = "₽") => `${nf.format(v || 0)} ${cur}`;
const ACC_COLORS = ["#A855F7", "#6D8BFF", "#C77DFF", "#4FD1A1", "#8B6DFF", "#E07DFF"];
const STATUS = { paid: "Оплачен", closed: "Закрыт", refunded: "Возврат" };

const state = { demo: false, server: "", page: "dashboard", lastEvent: 0, settings: null, timer: null };

// ---------- связь с воркером ----------
async function api(method, path, body) {
  const res = state.demo ? await Demo.request(method, path, body)
                         : await window.pywebview.api.request(method, path, body ?? null);
  if (res && res.error) throw new Error(res.error);
  return res && "data" in res && Object.keys(res).length === 1 ? res.data : res;
}

function toast(text, bad = false) {
  const t = $("#toast");
  t.textContent = text;
  t.className = "toast" + (bad ? " bad" : "");
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.hidden = true), 4200);
}

function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "только что";
  if (s < 3600) return `${Math.floor(s / 60)} мин назад`;
  if (s < 86400) return `${Math.floor(s / 3600)} ч назад`;
  return new Date(ts * 1000).toLocaleDateString("ru-RU");
}
const hhmm = ts => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
const dateTime = ts => new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

// ---------- подключение ----------
const INSTALL_FALLBACK = "KASSA_REPO=https://github.com/robinhilk488-bot/Lotus.git bash <(curl -s https://raw.githubusercontent.com/robinhilk488-bot/Lotus/main/install.sh)";

async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const t = document.createElement("textarea");
    t.value = text; document.body.append(t); t.select(); document.execCommand("copy"); t.remove();
  }
  const old = btn.textContent;
  btn.textContent = "Скопировано";
  setTimeout(() => (btn.textContent = old), 1600);
}

async function boot() {
  if (state.booted) return;
  state.booted = true;
  $("#demo-btn").onclick = () => enterApp(true, "Демо-режим");
  $("#connect-btn").onclick = connect;
  const cmd = window.pywebview ? await window.pywebview.api.install_command() : INSTALL_FALLBACK;
  $("#install-cmd").textContent = cmd;
  $("#copy-cmd").onclick = e => copyText(cmd, e.target);
  if (!window.pywebview) { showConnect(); return; }
  const saved = await window.pywebview.api.load_saved();
  if (saved?.link) {
    $("#link").value = saved.link;
    const r = await window.pywebview.api.connect(saved.link);
    if (r.ok) return enterApp(false, `${r.host}:${r.port}`);
    showConnect(r.error);
  } else showConnect();
}

function showConnect(error) {
  $("#app").hidden = true;
  $("#connect").hidden = false;
  const e = $("#connect-error");
  e.hidden = !error;
  e.textContent = error || "";
}

async function connect() {
  const link = $("#link").value.trim();
  if (!window.pywebview) return showConnect("Вход на сервер работает в приложении Lotus. В браузере доступен только просмотр без сервера.");
  if (!link) return showConnect("Вставьте ключ подключения lotus_… с сервера.");
  const btn = $("#connect-btn");
  btn.disabled = true; btn.textContent = "Подключаюсь…";
  const r = await window.pywebview.api.connect(link);
  btn.disabled = false; btn.textContent = "Войти";
  if (r.ok) enterApp(false, `${r.host}:${r.port}`); else showConnect(r.error);
}

async function enterApp(demo, server) {
  state.demo = demo;
  state.server = server;
  state.lastEvent = 0;
  $("#connect").hidden = true;
  $("#app").hidden = false;
  $("#server-name").textContent = server;
  document.querySelectorAll("#nav a").forEach(a => (a.onclick = () => go(a.dataset.page)));
  try { state.settings = await api("GET", "/api/settings"); } catch { state.settings = {}; }
  await poll(true);
  clearInterval(state.timer);
  state.timer = setInterval(poll, 20000);
  go("dashboard");
}

async function poll(silent = false) {
  const dot = $("#server .dot");
  try {
    const st = await api("GET", "/api/status");
    applyStatus(st);
    dot.className = "dot ok";
    $("#server-sub").textContent = state.demo ? "данные для примера" : `работает · v${st.version}`;
    const evs = await api("GET", `/api/events?since=${state.lastEvent}`);
    if (evs.length) {
      if (!silent) {
        const bad = evs.filter(e => e.level === "error" || e.level === "warn");
        if (bad.length) toast(bad[0].text + (bad.length > 1 ? ` (и ещё ${bad.length - 1})` : ""), true);
        else if (state.settings?.notify_new_orders) evs.filter(e => e.level === "order").slice(0, 1).forEach(e => toast(e.text));
      }
      state.lastEvent = Math.max(state.lastEvent, ...evs.map(e => e.id));
    }
  } catch (e) {
    dot.className = "dot bad";
    $("#server-sub").textContent = "нет связи";
  }
}

// ---------- счётчики в меню ----------
function setBadge(page, n, title = "") {
  if (page === "plugins") state.attention = n || 0;
  const a = $(`#nav a[data-page="${page}"]`);
  let b = $(".badge", a);
  if (!n) { b?.remove(); return; }
  if (!b) { b = document.createElement("span"); a.append(b); }
  b.className = "badge" + (page === "chats" ? " info" : "");
  b.textContent = n;
  b.title = title;
}
function applyStatus(st) {
  setBadge("plugins", st.attention, "Заказы, которые нужно проверить вручную");
  setBadge("chats", st.unread, "Непрочитанные чаты");
  state.sub = st.subscription;
  const sabug = st.subscription && st.subscription.configured && !st.subscription.active;
  const a = $('#nav a[data-page="subscription"]');
  if (a) { let b = $(".badge", a); if (sabug) { if (!b) { b = document.createElement("span"); b.className = "badge"; a.append(b); } b.textContent = "!"; } else b?.remove(); }
}
async function refreshBadge() { try { applyStatus(await api("GET", "/api/status")); } catch {} }

// ---------- навигация ----------
const pages = {};
async function go(page) {
  clearInterval(state.chatTimer);
  state.page = page;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.page === page));
  const root = $("#page");
  root.innerHTML = "";
  try { await pages[page](root); }
  catch (e) { root.innerHTML = `<div class="panel empty"><h2>Не удалось загрузить раздел</h2><p class="muted">${esc(e.message)}</p><button class="btn" onclick="go('${page}')">Повторить</button></div>`; }
}

// ---------- график ----------
function drawChart(el, daily) {
  const W = el.clientWidth || 700, H = el.clientHeight || 240, padB = 24, padL = 46;
  const max = Math.max(1, ...daily.map(d => d.revenue));
  const raw = max / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), nrm = raw / mag;
  const tick = (nrm <= 1 ? 1 : nrm <= 2 ? 2 : nrm <= 2.5 ? 2.5 : nrm <= 5 ? 5 : 10) * mag;
  const ticks = Math.ceil(max / tick), top = ticks * tick;
  const n = daily.length, step = (W - padL) / n, bw = Math.max(2, step * 0.62);
  const y = v => (H - padB) * (1 - v / top);
  const labelEvery = Math.ceil(n / 8);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Выручка по дням">`;
  for (let i = 0; i <= ticks; i++) {
    const v = tick * i, yy = y(v);
    svg += `<line class="gridline" x1="${padL}" x2="${W}" y1="${yy}" y2="${yy}"/>`;
    svg += `<text class="axis" x="${padL - 8}" y="${yy + 4}" text-anchor="end">${v >= 1000 ? nf.format(v / 1000) + "к" : nf.format(v)}</text>`;
  }
  daily.forEach((d, i) => {
    const x = padL + i * step + (step - bw) / 2;
    const h = Math.max(2, (H - padB) - y(d.revenue));
    svg += `<g data-i="${i}"><rect class="hit" x="${padL + i * step}" y="0" width="${step}" height="${H - padB}"/>`;
    svg += `<rect class="bar${d.revenue ? "" : " empty"}" x="${x}" y="${H - padB - h}" width="${bw}" height="${h}" rx="${Math.min(3, bw / 2)}"/></g>`;
    if ((i % labelEvery === 0 && n - 1 - i >= labelEvery / 2) || i === n - 1) {
      const dt = new Date(d.date + "T00:00");
      svg += `<text class="axis" x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${dt.getDate()}.${String(dt.getMonth() + 1).padStart(2, "0")}</text>`;
    }
  });
  el.innerHTML = svg + "</svg><div class='tip' hidden></div>";
  const tip = $(".tip", el);
  el.querySelectorAll("g[data-i]").forEach(g => {
    g.onmouseenter = () => {
      const d = daily[+g.dataset.i], r = g.querySelector(".bar");
      const dt = new Date(d.date + "T00:00").toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
      tip.innerHTML = `${dt}<br><b>${money(d.revenue)}</b> · ${d.orders} зак.`;
      tip.style.left = (+r.getAttribute("x") + +r.getAttribute("width") / 2) / W * el.clientWidth + "px";
      tip.style.top = Math.max(40, +r.getAttribute("y") - 6) + "px";
      tip.hidden = false;
    };
    g.onmouseleave = () => (tip.hidden = true);
  });
}

function pct(cur, prev) {
  if (!prev) return "";
  const p = Math.round((cur - prev) / prev * 100);
  return `<span class="delta ${p >= 0 ? "up" : "down"}">${p >= 0 ? "+" : ""}${p}%</span>`;
}

function statusPill(s) { return `<span class="pill ${s}">${STATUS[s] || s}</span>`; }

// ---------- Сводка ----------
pages.dashboard = async root => {
  const [accs, st, evs] = await Promise.all([api("GET", "/api/accounts"), api("GET", "/api/stats?days=60"), api("GET", "/api/events")]);
  const cur = st.daily.slice(30), prev = st.daily.slice(0, 30);
  const sum = (a, k) => a.reduce((s, d) => s + d[k], 0);
  const rev = sum(cur, "revenue"), revP = sum(prev, "revenue"), ord = sum(cur, "orders"), ordP = sum(prev, "orders");
  const prof = sum(cur, "profit"), profP = sum(prev, "profit");
  const today = st.daily[st.daily.length - 1];
  const total = accs.reduce((s, a) => s + a.balance, 0);

  const split = accs.map((a, i) => `<span style="flex:${Math.max(a.balance, total * .02)};background:${ACC_COLORS[i % 6]}"></span>`).join("");
  const legend = accs.map((a, i) => `<span><i style="background:${ACC_COLORS[i % 6]}"></i>${esc(a.name)} <b>${money(a.balance, a.currency)}</b></span>`).join("");

  root.innerHTML = `
    <div class="page-head"><h1>Сводка</h1><button class="btn" id="sync">Обновить данные</button></div>
    ${state.attention ? `<a class="banner" onclick="go('plugins')"><b>Заказов требуют проверки: ${state.attention}.</b> Плагин не довёл их до конца. Откройте, чтобы решить, что с ними делать.</a>` : ""}
    <div class="hero">
      <section class="panel">
        <div class="balance-label">Баланс на ${accs.length} ${accs.length === 1 ? "аккаунте" : "аккаунтах"}</div>
        <div class="balance">${nf.format(Math.round(total))}<small>₽</small></div>
        ${accs.length ? `<div class="split">${split}</div><div class="legend">${legend}</div>`
                      : `<p class="muted" style="margin-top:16px">Добавьте аккаунт FunPay, чтобы здесь появился баланс.</p>`}
      </section>
      <section class="panel figures">
        <div class="figure"><span class="muted">Выручка за 30 дней</span><span><span class="v">${money(rev)}</span>${pct(rev, revP)}</span></div>
        <div class="figure"><span class="muted">Чистая прибыль за 30 дней</span><span><span class="v profit">${money(Math.round(prof))}</span>${pct(prof, profP)}</span></div>
        <div class="figure"><span class="muted">Заказов за 30 дней</span><span><span class="v">${ord}</span>${pct(ord, ordP)}</span></div>
        <div class="figure"><span class="muted">Сегодня</span><span class="v">${money(today?.revenue)} <span class="muted small">${today?.orders || 0} зак.</span></span></div>
      </section>
    </div>
    <div class="grid-2">
      <section class="panel"><div class="panel-head"><h2>Выручка по дням</h2><span class="muted small">последние 30 дней</span></div><div class="chart" id="chart"></div></section>
      <section class="panel"><div class="panel-head"><h2>Журнал</h2></div>
        <ul class="log">${evs.slice(0, 30).map(e => `<li class="${e.level}"><time>${hhmm(e.ts)}</time><span class="${e.level}">${esc(e.text)}</span></li>`).join("") || `<li><span class="muted">Событий пока нет</span></li>`}</ul>
      </section>
    </div>`;
  drawChart($("#chart"), cur);
  $("#sync").onclick = async () => { await api("POST", "/api/sync"); toast("Синхронизация запущена. Данные обновятся через несколько секунд."); };
};

// ---------- Аккаунты ----------
pages.accounts = async root => {
  const accs = await api("GET", "/api/accounts");
  root.innerHTML = `
    <div class="page-head"><h1>Аккаунты</h1><button class="btn primary" id="add">Добавить аккаунт</button></div>
    ${accs.length ? `<div class="accounts">${accs.map((a, i) => `
      <section class="panel acc">
        <div class="acc-top">
          <div class="avatar" style="background:${ACC_COLORS[i % 6]}">${esc((a.username || a.name)[0].toUpperCase())}</div>
          <div><div class="acc-name">${esc(a.name)}</div><div class="muted small">${esc(a.username || "")}</div></div>
          <span class="pill ${a.status}" style="margin-left:auto">${{ ok: "Работает", error: "Ошибка", new: "Проверка" }[a.status] || a.status}</span>
        </div>
        <div class="acc-balance">${money(a.balance, a.currency)}</div>
        ${a.error ? `<div class="err">${esc(a.error)}</div>` : ""}
        <div class="acc-meta muted"><span>Заказов: ${a.orders_total}</span><span>Синхр.: ${a.last_sync ? ago(a.last_sync) : "—"}</span></div>
        <div class="acc-actions">
          <button class="btn" data-rename="${a.id}">Переименовать</button>
          <button class="btn ghost danger" data-del="${a.id}">Удалить</button>
        </div>
      </section>`).join("")}</div>`
    : `<div class="panel empty"><h2>Аккаунтов пока нет</h2><p class="muted">Добавьте аккаунт FunPay по golden_key, и сервер начнёт собирать баланс и продажи.</p><button class="btn primary" id="add2">Добавить аккаунт</button></div>`}`;

  const openAdd = () => modal(`
      <h2>Новый аккаунт</h2>
      <p class="muted">golden_key хранится на сервере в зашифрованном виде и в приложение не возвращается.</p>
      <label class="field"><span>Название (необязательно)</span><input name="name" placeholder="Например, Основной"></label>
      <label class="field"><span>golden_key</span><input name="golden_key" placeholder="32 символа из cookie funpay.com" autocomplete="off"></label>
      <details class="howto"><summary>Как получить golden_key</summary><ol>
        <li>Войдите на funpay.com в браузере.</li><li>Нажмите F12 → Application → Cookies → funpay.com.</li>
        <li>Скопируйте значение golden_key.</li></ol></details>`,
    "Добавить", async f => {
      await api("POST", "/api/accounts", { name: f.name.value, golden_key: f.golden_key.value });
      toast("Аккаунт добавлен. Продажи подтянутся в течение минуты.");
      go("accounts");
    });
  $("#add").onclick = openAdd;
  if ($("#add2")) $("#add2").onclick = openAdd;

  root.querySelectorAll("[data-rename]").forEach(b => (b.onclick = () => {
    const a = accs.find(x => x.id === +b.dataset.rename);
    modal(`<h2>Переименовать</h2><p class="muted">Название видно только вам.</p>
      <label class="field"><span>Название</span><input name="name" value="${esc(a.name)}"></label>`,
      "Сохранить", async f => { await api("PATCH", `/api/accounts/${a.id}`, { name: f.name.value }); go("accounts"); });
  }));
  root.querySelectorAll("[data-del]").forEach(b => (b.onclick = () => {
    const a = accs.find(x => x.id === +b.dataset.del);
    modal(`<h2>Удалить «${esc(a.name)}»?</h2><p class="muted">Сервер перестанет работать с этим аккаунтом, история его продаж будет удалена.</p>`,
      "Удалить", async () => { await api("DELETE", `/api/accounts/${a.id}`); toast("Аккаунт удалён"); go("accounts"); });
  }));
};

// ---------- Продажи ----------
const salesView = { days: 30, account: 0 };
pages.sales = async root => {
  const [accs, st] = await Promise.all([api("GET", "/api/accounts"), api("GET", `/api/stats?days=${salesView.days}&account=${salesView.account}`)]);
  root.innerHTML = `
    <div class="page-head"><h1>Продажи</h1>
      <select class="compact" id="acc"><option value="0">Все аккаунты</option>${accs.map(a => `<option value="${a.id}" ${a.id === salesView.account ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
      <div class="seg">${[7, 30, 90].map(d => `<button data-d="${d}" class="${d === salesView.days ? "on" : ""}">${d} дней</button>`).join("")}</div>
    </div>
    <div class="kpis">
      <section class="panel kpi money"><div class="k">Выручка</div><div class="v">${money(st.revenue)}</div></section>
      <section class="panel kpi"><div class="k">Заказов</div><div class="v">${st.orders}</div></section>
      <section class="panel kpi"><div class="k">Чистая прибыль</div><div class="v profit">${money(st.profit)}</div>
        <div class="muted small">комиссия ${money(st.commission)}, себестоимость ${money(st.costs)}</div></section>
      <section class="panel kpi"><div class="k">Средний чек</div><div class="v">${money(st.avg_check)}</div><div class="muted small">возвраты ${money(st.refunds)}</div></section>
    </div>
    <section class="panel" style="margin-bottom:16px"><div class="panel-head"><h2>Выручка по дням</h2></div><div class="chart" id="chart"></div></section>
    ${st.top_lots.length ? `<section class="panel" style="margin-bottom:16px"><div class="panel-head"><h2>Самые прибыльные лоты</h2><span class="muted small">за период</span></div>
      <div class="table-wrap"><table><thead><tr><th>Лот</th><th class="num">Заказов</th><th class="num">Выручка</th><th class="num">Прибыль</th></tr></thead>
      <tbody>${st.top_lots.map(l => `<tr><td class="lot" title="${esc(l.lot)}">${esc(l.lot)}</td><td class="num">${l.orders}</td><td class="num nowrap">${money(l.revenue)}</td><td class="num nowrap"><b class="profit">${money(Math.round(l.profit))}</b></td></tr>`).join("")}</tbody></table></div>
    </section>` : ""}
    <section class="panel"><div class="panel-head"><h2>Заказы</h2><span class="muted small">${st.recent.length} за период</span></div>
      ${st.recent.length ? `<div class="table-wrap"><table>
        <thead><tr><th>Дата</th><th>Заказ</th><th>Товар</th><th>Покупатель</th><th>Аккаунт</th><th>Статус</th><th class="num">Сумма</th><th class="num">Прибыль</th></tr></thead>
        <tbody>${st.recent.map(o => `<tr>
          <td class="muted nowrap">${dateTime(o.ts)}</td><td>#${esc(o.id)}</td><td class="lot" title="${esc(o.description)}">${esc(o.description)}</td>
          <td>${esc(o.buyer)}</td><td class="muted">${esc(o.account)}</td><td>${statusPill(o.status)}</td>
          <td class="num nowrap"><b>${money(o.amount, o.currency)}</b></td><td class="num nowrap muted">${o.profit != null ? money(Math.round(o.profit)) : "—"}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">За этот период заказов нет.</p>`}
    </section>`;
  drawChart($("#chart"), st.daily);
  root.querySelectorAll("[data-d]").forEach(b => (b.onclick = () => { salesView.days = +b.dataset.d; go("sales"); }));
  $("#acc").onchange = e => { salesView.account = +e.target.value; go("sales"); };
};

// ---------- Плагины ----------
let pluginFilter = "all";

// ---------- полноэкранное окно настройки плагина ----------
let _rentReopen = null;
function reopenRent() { if (_rentReopen) _rentReopen(); }


let _offlineReopen = null;
async function renderOfflineInto(body, host) {
  const [accs, list] = await Promise.all([api("GET", "/api/offline/accounts"), api("GET", "/api/plugins")]);
  const p = list.find(x => x.id === "offline_activite");
  const fieldsHtml = p.settings.map(st => {
    const v = p.config[st.key] ?? st.default ?? "";
    const hint = st.hint ? `<small class="hint">${esc(st.hint)}</small>` : "";
    if (st.type === "bool") return `<div class="row"><div>${esc(st.label)}${hint}</div><label class="switch"><input type="checkbox" data-k="${st.key}" ${v ? "checked" : ""}><i></i></label></div>`;
    return `<label class="field"><span>${esc(st.label)}</span><input data-k="${st.key}" value="${esc(v)}">${hint}</label>`;
  }).join("");
  body.innerHTML = `
    <div class="page-head" style="margin-bottom:16px"><h1 style="font-size:22px">Offline Activite</h1><button class="btn primary" id="oa-add">Добавить аккаунт</button></div>
    <div class="cfg-cols"><div>
      ${accs.length ? `<section class="panel" style="margin-bottom:16px"><div class="table-wrap"><table>
        <thead><tr><th>Логин</th><th>maFile</th><th></th></tr></thead>
        <tbody>${accs.map(a => `<tr><td><b>${esc(a.login)}</b></td><td>${a.has_mafile ? "есть" : `<span class="warn-text">нет</span>`}</td>
          <td class="num"><button class="btn ghost danger" data-del="${esc(a.login)}">Удалить</button></td></tr>`).join("")}</tbody></table></div></section>`
        : `<div class="panel empty" style="margin-bottom:16px"><h2>Аккаунтов нет</h2><p class="muted">Добавьте аккаунт: логин и maFile. По команде ${esc(p.config.command || "!guard")} бот выдаст покупателю код Steam Guard.</p></div>`}
      <section class="panel"><form id="oa-form"><h2 style="margin-bottom:14px">Настройки</h2>${fieldsHtml}
        <button class="btn primary">Сохранить</button></form></section>
    </div>
    <aside class="cfg-aside"><section class="panel"><h3 style="margin-bottom:6px">${esc(p.name)}</h3><p class="muted">${esc(p.description)}</p></section></aside></div>`;

  $("#oa-add", body).onclick = () => modal(`<h2>Новый аккаунт</h2>
    <label class="field"><span>Логин Steam</span><input name="login" autocomplete="off"></label>
    <label class="field"><span>maFile (содержимое файла из Steam Desktop Authenticator)</span><textarea name="mafile" rows="4" placeholder='{"shared_secret":"…"}'></textarea>
      <small class="hint">Нужен для генерации кода. Хранится на сервере в зашифрованном виде.</small></label>`,
    "Добавить", async f => { await api("POST", "/api/offline/accounts", { login: f.login.value, mafile: f.mafile.value }); toast("Аккаунт добавлен"); _offlineReopen(); });
  body.querySelectorAll("[data-del]").forEach(b => (b.onclick = () => {
    modal(`<h2>Удалить ${esc(b.dataset.del)}?</h2><p class="muted">Код по этому аккаунту больше выдаваться не будет.</p>`,
      "Удалить", async () => { await api("DELETE", `/api/offline/accounts/${encodeURIComponent(b.dataset.del)}`); toast("Удалён"); _offlineReopen(); });
  }));
  const form = $("#oa-form", body);
  form.onsubmit = async e => {
    e.preventDefault();
    const cfg = {};
    p.settings.forEach(st => { const el = form.querySelector(`[data-k="${st.key}"]`); cfg[st.key] = st.type === "bool" ? el.checked : el.value; });
    try { await api("PUT", `/api/plugins/offline_activite/config`, cfg); toast("Настройки сохранены"); } catch (err) { toast(err.message, true); }
  };
}

async function openPluginConfig(id) {
  const host = document.createElement("div");
  host.className = "cfg-overlay";
  host.innerHTML = `<div class="cfg-screen"><div class="cfg-top">
      <button class="btn ghost cfg-back">← Назад к плагинам</button>
      <div class="cfg-title" id="cfg-title"></div></div>
      <div class="cfg-body" id="cfg-body"></div></div>`;
  document.body.append(host);
  const close = () => { host.remove(); document.onkeydown = null; go("plugins"); };
  host.querySelector(".cfg-back").onclick = close;
  document.onkeydown = e => { if (e.key === "Escape") close(); };
  host.onmousedown = e => { if (e.target === host) close(); };
  const body = host.querySelector("#cfg-body");
  const titleEl = host.querySelector("#cfg-title");

  if (id === "rent_steam") {
    _rentReopen = () => renderRentInto(body);
    titleEl.textContent = "Аренда Steam";
    await renderRentInto(body);
    return;
  }

  if (id === "offline_activite") {
    titleEl.textContent = "Offline Activite";
    _offlineReopen = () => renderOfflineInto(body, host);
    await renderOfflineInto(body, host);
    return;
  }

  const list = await api("GET", "/api/plugins");
  const p = list.find(x => x.id === id);
  if (!p) { close(); return; }
  titleEl.textContent = p.name;
  const label = s => `${esc(s.label)}${s.required ? ' <span class="req">обязательно</span>' : ""}`;
  const hint = s => (s.hint ? `<small class="hint">${esc(s.hint)}</small>` : "");
  const fields = p.settings.map(s => {
    const v = p.config[s.key] ?? s.default ?? "";
    if (s.type === "bool") return `<div class="row"><div>${label(s)}${hint(s)}</div><label class="switch"><input type="checkbox" name="${s.key}" ${v ? "checked" : ""}><i></i></label></div>`;
    if (s.type === "secret") { const set = p.secrets_set[s.key];
      return `<label class="field"><span>${label(s)}</span><div class="secret"><input name="${s.key}" type="password" autocomplete="off" placeholder="${set ? "Сохранён — пусто = не менять" : "Вставьте ключ"}">${set ? `<button type="button" class="btn ghost" data-clear="${s.key}">Стереть</button>` : ""}</div>${hint(s)}</label>`; }
    if (s.type === "textarea") return `<label class="field"><span>${label(s)}</span><textarea name="${s.key}" rows="4">${esc(v)}</textarea>${hint(s)}</label>`;
    if (s.type === "select") return `<label class="field"><span>${label(s)}</span><select name="${s.key}">${(s.options || []).map(o => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>${hint(s)}</label>`;
    return `<label class="field"><span>${label(s)}</span><input name="${s.key}" type="${s.type === "number" ? "number" : "text"}" step="any" value="${esc(v)}">${hint(s)}</label>`;
  }).join("");
  body.innerHTML = `<div class="cfg-cols">
    <section class="panel"><form id="cfg-form">
      ${p.settings.length ? `<h2 style="margin-bottom:14px">Настройки</h2><p class="muted" style="margin-bottom:16px">Ключи и пароли хранятся на сервере в зашифрованном виде и не показываются повторно.</p>${fields}
      <button class="btn primary">Сохранить</button>` : `<h2>У плагина нет настроек</h2><p class="muted" style="margin-top:8px">Он работает сразу после включения.</p>`}
    </form></section>
    <aside class="cfg-aside">
      <section class="panel"><h3 style="margin-bottom:6px">${esc(p.name)}</h3><p class="muted">${esc(p.description)}</p>
        <div class="plugin-meta" style="margin-top:12px"><span>Выполнено: ${p.tasks.done}</span>${p.tasks.attention ? `<span class="warn-text">Проверка: ${p.tasks.attention}</span>` : ""}</div></section>
      ${p.can_test || p.can_dry_run ? `<section class="panel"><h3 style="margin-bottom:12px">Проверка</h3>
        ${p.can_test ? `<button class="btn wide" id="cfg-test" style="margin-bottom:10px">Проверить подключение</button>` : ""}
        ${p.can_dry_run ? `<button class="btn wide" id="cfg-dry">Пробный запуск</button>` : ""}
        <div id="cfg-check"></div></section>` : ""}
    </aside></div>`;

  const clear = new Set();
  body.querySelectorAll("[data-clear]").forEach(x => (x.onclick = () => {
    clear.add(x.dataset.clear); const inp = x.previousElementSibling; inp.placeholder = "Будет стёрт при сохранении"; inp.value = ""; x.remove();
  }));
  const form = body.querySelector("#cfg-form");
  if (p.settings.length) form.onsubmit = async e => {
    e.preventDefault();
    const cfg = { __clear__: [...clear] };
    p.settings.forEach(s => { const el = form.elements[s.key]; cfg[s.key] = s.type === "bool" ? el.checked : s.type === "number" ? (el.value === "" ? "" : Number(el.value)) : el.value; });
    try { await api("PUT", `/api/plugins/${p.id}/config`, cfg); toast("Настройки сохранены"); } catch (err) { toast(err.message, true); }
  };
  const test = body.querySelector("#cfg-test");
  if (test) test.onclick = async () => {
    test.disabled = true; test.textContent = "Проверяю…";
    try { const r = await api("POST", `/api/plugins/${p.id}/test`); body.querySelector("#cfg-check").innerHTML = `<div class="result ${r.ok ? "ok" : "bad"}"><b>${esc(r.message)}</b></div>`; }
    catch (e) { toast(e.message, true); } finally { test.disabled = false; test.textContent = "Проверить подключение"; }
  };
  const dry = body.querySelector("#cfg-dry");
  if (dry) dry.onclick = () => modal(`<h2>Пробный запуск</h2>
    <p class="muted">Плагин обработает выдуманный заказ: ничего не покупает и не отправляет.</p>
    <label class="field"><span>Название лота, как на FunPay</span><input name="description" value="Пробный заказ"></label>
    <label class="field"><span>Сумма, ₽</span><input name="amount" type="number" step="any" value="100"></label><div id="dry-out"></div>`,
    "Запустить", async f => {
      const r = await api("POST", `/api/plugins/${p.id}/dry-run`, { description: f.description.value, amount: Number(f.amount.value) });
      $("#dry-out").innerHTML = `<div class="result ${r.ok ? "ok" : "bad"}"><b>${esc(r.message)}</b>${r.log.length ? `<pre>${esc(r.log.join("\n"))}</pre>` : ""}</div>`;
      return "keep";
    });
}

pages.plugins = async root => {
  const [list, tasks] = await Promise.all([api("GET", "/api/plugins"), api("GET", "/api/tasks?limit=60")]);
  const shown = list.filter(p => pluginFilter === "all" || (pluginFilter === "on" ? p.enabled : p.ready));
  const groups = [];
  shown.forEach(p => { let g = groups.find(x => x[0] === p.category); if (!g) groups.push(g = [p.category, []]); g[1].push(p); });
  const attention = tasks.filter(t => t.status === "attention");
  const noSub = state.sub && state.sub.configured && !state.sub.active;
  const TASK = { done: "Выполнен", skipped: "Не его лот", pending: "В очереди", running: "Обрабатывается", attention: "Нужна проверка", cancelled: "Отменён" };
  const taskPill = s => `<span class="pill ${{ done: "closed", attention: "refunded", pending: "paid", running: "paid", cancelled: "new", skipped: "new" }[s] || ""}">${TASK[s] || s}</span>`;

  root.innerHTML = `
    <div class="page-head"><h1>Плагины</h1>
      <div class="seg">${[["all", "Все"], ["on", "Включены"], ["ready", "Готовы к работе"]].map(([k, t]) => `<button data-f="${k}" class="${pluginFilter === k ? "on" : ""}">${t}</button>`).join("")}</div></div>

    ${noSub ? `<a class="banner" onclick="go('subscription')"><b>Подписка неактивна.</b> Плагины не принимают новые заказы. Откройте раздел «Подписка», чтобы активировать код.</a>` : ""}
    ${attention.length ? `<section class="panel alert" style="margin-bottom:16px">
      <div class="panel-head"><h2>Нужна ваша проверка: ${attention.length}</h2></div>
      <p class="muted" style="margin-bottom:12px">Эти заказы плагин не довёл до конца. Сервер не повторяет их сам, чтобы не купить и не выдать товар дважды. Проверьте, получил ли покупатель товар, и выберите действие.</p>
      ${attention.map(t => `<div class="task">
        <div><b>#${esc(t.order_id)}</b> · ${esc(t.plugin_name)} · ${money(t.amount, t.currency)} · ${esc(t.buyer)}
          <div class="lot muted">${esc(t.description)}</div><div class="err">${esc(t.error)}</div></div>
        <div class="task-actions"><button class="btn" data-retry="${t.id}">Повторить</button><button class="btn ghost" data-resolve="${t.id}">Выполнено вручную</button></div>
      </div>`).join("")}
    </section>` : ""}

    ${groups.map(([cat, items]) => `<h2 class="group">${esc(cat)}</h2><div class="plugins">${items.map(p => p.ready ? `
      <section class="panel plugin">
        <div>
          <h3>${esc(p.name)}<span class="ver">v${esc(p.version)}</span></h3>
          <p>${esc(p.description)}</p>
          <div class="plugin-meta">
            ${p.missing.length ? `<span class="warn-text">Заполните: ${esc(p.missing.join(", "))}</span>` : ""}
            <span>Выполнено: ${p.tasks.done}</span>
            ${p.tasks.pending ? `<span>В очереди: ${p.tasks.pending}</span>` : ""}
            ${p.tasks.attention ? `<span class="warn-text">Нужна проверка: ${p.tasks.attention}</span>` : ""}
          </div>
        </div>
        <div class="plugin-actions">
          <button class="btn" data-cfg="${p.id}">Настроить</button>
        </div>
        <label class="switch" title="${p.enabled ? "Выключить" : "Включить"}"><input type="checkbox" data-on="${p.id}" ${p.enabled ? "checked" : ""}><i></i></label>
      </section>` : `
      <section class="panel plugin soon">
        <div>
          <h3>${esc(p.name)}<span class="pill new">В разработке</span></h3>
          <p>${esc(p.description)}</p>
          <div class="plugin-meta"><span>Для запуска нужно: ${esc(p.needs.join("; "))}</span></div>
        </div>
        <div></div>
        <label class="switch" title="Плагин ещё в разработке"><input type="checkbox" disabled><i></i></label>
      </section>`).join("")}</div>`).join("") || `<div class="panel empty"><h2>${pluginFilter === "on" ? "Ни один плагин не включён" : "Готовых плагинов нет"}</h2><p class="muted">Переключите фильтр на «Все», чтобы увидеть весь каталог.</p></div>`}

    <section class="panel" style="margin-top:16px">
      <div class="panel-head"><h2>Заказы в работе у плагинов</h2><span class="muted small">последние ${tasks.length}</span></div>
      ${tasks.length ? `<div class="table-wrap"><table>
        <thead><tr><th>Время</th><th>Заказ</th><th>Плагин</th><th>Товар</th><th>Статус</th><th>Результат</th></tr></thead>
        <tbody>${tasks.map(t => `<tr><td class="muted nowrap">${dateTime(t.updated)}</td><td>#${esc(t.order_id)}</td><td class="nowrap">${esc(t.plugin_name)}</td>
          <td class="lot">${esc(t.description)}</td><td>${taskPill(t.status)}</td>
          <td class="muted lot" title="${esc(t.error || t.result || "")}">${esc(t.error || t.result || "")}${t.attempts > 1 ? ` (попыток: ${t.attempts})` : ""}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">Когда придёт оплаченный заказ, здесь будет видно, как его обработал каждый плагин.</p>`}
    </section>`;

  root.querySelectorAll("[data-on]").forEach(c => (c.onchange = async () => {
    try { await api("POST", `/api/plugins/${c.dataset.on}/enabled`, { enabled: c.checked }); toast(c.checked ? "Плагин включён" : "Плагин выключен"); refreshBadge(); }
    catch (e) { c.checked = !c.checked; toast(e.message, true); }
  }));

  root.querySelectorAll("[data-cfg]").forEach(b => (b.onclick = () => openPluginConfig(b.dataset.cfg)));

  root.querySelectorAll("[data-retry]").forEach(b => (b.onclick = () => {
    const t = attention.find(x => x.id === +b.dataset.retry);
    modal(`<h2>Повторить заказ #${esc(t.order_id)}?</h2><p class="muted">Плагин обработает заказ заново. Нажимайте, только если убедились, что покупатель товар НЕ получил, иначе товар может быть выдан дважды.</p>`,
      "Повторить", async () => { await api("POST", `/api/tasks/${t.id}/retry`); toast("Заказ поставлен на повтор"); go("plugins"); refreshBadge(); });
  }));
  root.querySelectorAll("[data-resolve]").forEach(b => (b.onclick = async () => {
    try { await api("POST", `/api/tasks/${b.dataset.resolve}/resolve`); toast("Отмечено как выполненное"); go("plugins"); refreshBadge(); }
    catch (e) { toast(e.message, true); }
  }));
  root.querySelectorAll("[data-f]").forEach(b => (b.onclick = () => { pluginFilter = b.dataset.f; go("plugins"); }));
};

// ---------- общие помощники для форм настроек ----------
function collect(el) {
  const out = {};
  el.querySelectorAll("[data-k]").forEach(i => {
    const k = i.dataset.k;
    if (i.type === "checkbox") out[k] = i.checked;
    else if (i.dataset.list !== undefined) out[k] = i.value.split("\n");
    else if (i.type === "number") out[k] = Number(i.value);
    else if (i.dataset.secret !== undefined) { if (i.value.trim()) out[k] = i.value.trim(); }
    else out[k] = i.value;
  });
  return out;
}
async function saveSection(el, extra = {}) {
  const btn = $(".save", el);
  btn.disabled = true;
  try { state.settings = await api("PUT", "/api/settings", { ...collect(el), ...extra }); toast("Сохранено"); return true; }
  catch (e) { toast(e.message, true); return false; }
  finally { btn.disabled = false; }
}
const sw = (k, on) => `<label class="switch"><input type="checkbox" data-k="${k}" ${on ? "checked" : ""}><i></i></label>`;
const whenTs = ts => {
  const d = new Date(ts * 1000), now = new Date();
  return d.toDateString() === now.toDateString() ? hhmm(ts) : d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
};

// ---------- Чаты ----------
const chatView = { acc: null, id: null, name: "" };
pages.chats = async root => {
  let list = await api("GET", "/api/chats");
  root.innerHTML = `
    <div class="page-head"><h1>Чаты</h1><input id="chat-search" class="compact search" placeholder="Поиск по имени покупателя"></div>
    ${list.length ? `<div class="chat-layout">
      <aside class="panel chat-list" id="chat-list"></aside>
      <section class="panel chat-box" id="chat-box"><div class="chat-empty muted">Выберите чат слева</div></section>
    </div>` : `<div class="panel empty"><h2>Чатов пока нет</h2><p class="muted">Когда покупатели напишут вам на FunPay, переписка появится здесь. Сервер проверяет новые сообщения каждые несколько секунд.</p></div>`}`;
  if (!list.length) return;
  const many = new Set(list.map(c => c.account_id)).size > 1;

  const renderList = () => {
    const q = $("#chat-search").value.trim().toLowerCase();
    $("#chat-list").innerHTML = list.filter(c => !q || (c.name || "").toLowerCase().includes(q)).map(c => `
      <a class="chat-item${c.unread ? " unread" : ""}${chatView.acc === c.account_id && chatView.id === c.chat_id ? " on" : ""}" data-acc="${c.account_id}" data-id="${esc(c.chat_id)}" data-name="${esc(c.name)}">
        <div class="chat-item-top"><b>${esc(c.name)}</b><time>${c.last_ts ? whenTs(c.last_ts) : ""}</time></div>
        <div class="chat-item-text">${esc(c.last_text || "")}</div>
        ${many ? `<div class="muted small">${esc(c.account)}</div>` : ""}
      </a>`).join("") || `<p class="muted" style="padding:12px">Никого не найдено</p>`;
    $("#chat-list").querySelectorAll(".chat-item").forEach(a => (a.onclick = () => openChat(+a.dataset.acc, a.dataset.id, a.dataset.name)));
  };

  const renderMessages = (msgs, warning) => {
    const box = $("#chat-msgs");
    if (!box) return;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    box.innerHTML = (warning ? `<div class="msg-sys">${esc(warning)}</div>` : "") + msgs.map(m =>
      m.author_id === 0 ? `<div class="msg-sys">${esc(m.text)}</div>`
        : `<div class="msg ${m.mine ? "mine" : ""}"><div class="msg-text">${esc(m.text)}</div><time>${whenTs(m.ts)}</time></div>`).join("");
    if (atBottom || box.dataset.first !== "0") { box.scrollTop = box.scrollHeight; box.dataset.first = "0"; }
  };

  async function openChat(acc, id, name) {
    Object.assign(chatView, { acc, id, name });
    renderList();
    $("#chat-box").innerHTML = `
      <div class="chat-head"><h2>${esc(name)}</h2><span class="muted small">Enter — отправить, Shift+Enter — новая строка</span></div>
      <div class="chat-msgs" id="chat-msgs" data-first="1"><div class="msg-sys">Загружаю переписку…</div></div>
      <form class="chat-input" id="chat-form"><textarea id="chat-text" rows="2" placeholder="Сообщение покупателю"></textarea><button class="btn primary">Отправить</button></form>`;
    const load = async () => {
      if (chatView.acc !== acc || chatView.id !== id) return;
      try { const r = await api("GET", `/api/chats/${acc}/${encodeURIComponent(id)}`); renderMessages(r.messages, r.warning); }
      catch (e) { renderMessages([], e.message); }
    };
    await load();
    const c = list.find(x => x.account_id === acc && x.chat_id === id);
    if (c) c.unread = 0;
    renderList(); refreshBadge();
    const form = $("#chat-form"), ta = $("#chat-text");
    ta.onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } };
    form.onsubmit = async e => {
      e.preventDefault();
      const text = ta.value.trim();
      if (!text) return;
      const btn = $("button", form);
      btn.disabled = true;
      try { const r = await api("POST", `/api/chats/${acc}/${encodeURIComponent(id)}`, { text }); ta.value = ""; $("#chat-msgs").dataset.first = "1"; renderMessages(r.messages); }
      catch (err) { toast(err.message, true); }
      finally { btn.disabled = false; ta.focus(); }
    };
    ta.focus();
    clearInterval(state.chatTimer);
    state.chatTimer = setInterval(async () => {
      try { list = await api("GET", "/api/chats"); renderList(); } catch {}
      load();
    }, 8000);
  }

  $("#chat-search").oninput = renderList;
  renderList();
  if (chatView.acc && list.some(c => c.account_id === chatView.acc && c.chat_id === chatView.id)) openChat(chatView.acc, chatView.id, chatView.name);
  else {
    clearInterval(state.chatTimer);
    state.chatTimer = setInterval(async () => { try { list = await api("GET", "/api/chats"); renderList(); } catch {} }, 8000);
  }
};

// ---------- Автовыдача ----------
pages.delivery = async root => {
  const [lots, plist] = await Promise.all([api("GET", "/api/delivery"), api("GET", "/api/plugins")]);
  const pl = plist.find(p => p.id === "autodelivery");
  const MODE = { text: "Один текст всем", fifo: "Уникальный товар из списка" };
  root.innerHTML = `
    <div class="page-head"><h1>Автовыдача</h1><button class="btn primary" id="add-lot">Добавить лот</button></div>
    <section class="panel row-panel ${pl?.enabled ? "" : "off"}">
      <div><h2>${pl?.enabled ? "Автовыдача включена" : "Автовыдача выключена"}</h2>
        <p class="muted">Когда заказ оплачен, сервер ищет в названии лота вашу фразу и сразу отправляет покупателю товар в чат FunPay. Если подходят несколько фраз, берётся самая длинная. Один заказ — одна выдача.</p></div>
      <label class="switch"><input type="checkbox" id="ad-on" ${pl?.enabled ? "checked" : ""}><i></i></label>
    </section>
    ${lots.length ? `<section class="panel" style="margin-top:16px"><div class="table-wrap"><table>
      <thead><tr><th>Фраза в названии лота</th><th>Что выдаётся</th><th class="num">В наличии</th><th class="num">Выдано</th><th class="num">Себестоимость</th><th></th><th></th></tr></thead>
      <tbody>${lots.map(l => `<tr>
        <td><b>${esc(l.phrase)}</b></td><td class="muted nowrap">${l.mode === "fifo" ? "Из списка" : "Текст всем"}</td>
        <td class="num">${l.mode === "fifo" ? `<span class="${l.stock ? "" : "warn-text"}">${l.stock}</span>` : "∞"}</td>
        <td class="num">${l.mode === "fifo" ? l.sold : "—"}</td>
        <td class="num nowrap">${l.cost ? money(l.cost) : "—"}</td>
        <td><div class="row-actions">${l.mode === "fifo" ? `<button class="btn" data-stock="${l.id}">Товар</button>` : ""}<button class="btn" data-edit="${l.id}">Изменить</button><button class="btn ghost danger" data-del="${l.id}">Удалить</button></div></td>
        <td class="toggle-cell"><label class="switch" title="Выдавать по этому лоту"><input type="checkbox" data-lot-on="${l.id}" ${l.enabled ? "checked" : ""}><i></i></label></td>
      </tr>`).join("")}</tbody></table></div></section>`
    : `<div class="panel empty" style="margin-top:16px"><h2>Лотов для автовыдачи нет</h2><p class="muted">Добавьте лот: фразу из его названия на FunPay и что получит покупатель.</p></div>`}`;

  $("#ad-on").onchange = async e => {
    try { await api("POST", "/api/plugins/autodelivery/enabled", { enabled: e.target.checked }); toast(e.target.checked ? "Автовыдача включена" : "Автовыдача выключена"); go("delivery"); }
    catch (err) { e.target.checked = !e.target.checked; toast(err.message, true); }
  };
  const lotForm = (l = {}) => `
    <label class="field"><span>Фраза из названия лота на FunPay</span><input name="phrase" value="${esc(l.phrase || "")}" placeholder="Например: Steam ключ Elden Ring">
      <small class="hint">Достаточно части названия, регистр не важен. Пришлите точную фразу, чтобы она не совпадала с другими лотами.</small></label>
    <label class="field"><span>Что выдаётся</span><select name="mode">
      <option value="fifo" ${l.mode === "fifo" ? "selected" : ""}>Уникальный товар из списка — ключи, аккаунты</option>
      <option value="text" ${l.mode === "text" ? "selected" : ""}>Один текст всем — инструкция, ссылка</option></select></label>
    <label class="field"><span>Сообщение покупателю</span><textarea name="text" rows="4" placeholder="Спасибо за покупку, {buyer}! Ваш товар:&#10;{item}">${esc(l.text || "")}</textarea>
      <small class="hint">Подстановки: {buyer} — имя покупателя, {order} — номер заказа, {lot} — название лота, {item} — товар из списка. Для списка пустое поле = стандартный текст.</small></label>
    <label class="field"><span>Себестоимость одной выдачи, ₽</span><input name="cost" type="number" step="any" min="0" value="${l.cost || 0}">
      <small class="hint">Сколько вам стоит товар — для расчёта чистой прибыли. Можно оставить 0.</small></label>`;
  const read = f => ({ phrase: f.phrase.value, mode: f.mode.value, text: f.text.value, cost: Number(f.cost.value) || 0 });

  $("#add-lot").onclick = () => modal(`<h2>Новый лот для автовыдачи</h2><p class="muted">После сохранения загрузите товар, если выбран список.</p>${lotForm({ mode: "fifo" })}`,
    "Сохранить", async f => { await api("POST", "/api/delivery", { ...read(f), enabled: true }); toast("Лот добавлен"); go("delivery"); });
  root.querySelectorAll("[data-edit]").forEach(b => (b.onclick = () => {
    const l = lots.find(x => x.id === +b.dataset.edit);
    modal(`<h2>Лот «${esc(l.phrase)}»</h2>${lotForm(l)}`, "Сохранить", async f => {
      await api("PUT", `/api/delivery/${l.id}`, { ...read(f), enabled: !!l.enabled }); toast("Сохранено"); go("delivery");
    });
  }));
  root.querySelectorAll("[data-lot-on]").forEach(c => (c.onchange = async () => {
    const l = lots.find(x => x.id === +c.dataset.lotOn);
    try { await api("PUT", `/api/delivery/${l.id}`, { ...l, enabled: c.checked }); toast(c.checked ? "Лот включён" : "Лот выключен"); }
    catch (e) { c.checked = !c.checked; toast(e.message, true); }
  }));
  root.querySelectorAll("[data-stock]").forEach(b => (b.onclick = () => {
    const l = lots.find(x => x.id === +b.dataset.stock);
    modal(`<h2>Товар: ${esc(l.phrase)}</h2>
      <p class="muted">Сейчас в наличии: <b>${l.stock}</b>. Каждый покупатель получит одну строку, выданные строки больше не используются.</p>
      <label class="field"><span>Добавить товар — по одному на строку</span><textarea name="lines" rows="8" placeholder="XXXXX-XXXXX-XXXXX&#10;login:password"></textarea></label>
      ${l.stock ? `<button type="button" class="btn ghost danger" id="clear-stock">Удалить весь невыданный товар (${l.stock})</button>` : ""}`,
      "Добавить", async f => { const r = await api("POST", `/api/delivery/${l.id}/stock`, { lines: f.lines.value }); toast(`Добавлено: ${r.added} шт.`); go("delivery"); });
    const clr = $("#clear-stock");
    if (clr) clr.onclick = async () => {
      if (clr.dataset.sure !== "1") { clr.dataset.sure = "1"; clr.textContent = "Точно удалить? Нажмите ещё раз"; return; }
      await api("DELETE", `/api/delivery/${l.id}/stock`); $("#modal-root").innerHTML = ""; toast("Невыданный товар удалён"); go("delivery");
    };
  }));
  root.querySelectorAll("[data-del]").forEach(b => (b.onclick = () => {
    const l = lots.find(x => x.id === +b.dataset.del);
    modal(`<h2>Удалить лот «${esc(l.phrase)}»?</h2><p class="muted">Автовыдача по нему прекратится${l.stock ? `, невыданный товар (${l.stock} шт.) будет удалён` : ""}.</p>`,
      "Удалить", async () => { await api("DELETE", `/api/delivery/${l.id}`); toast("Лот удалён"); go("delivery"); });
  }));
};

// ---------- Аренда Steam ----------
async function renderRentInto(root) {
  const [accs, plist, onlypc] = await Promise.all([api("GET", "/api/rent/accounts"), api("GET", "/api/plugins"), api("GET", "/api/rent/onlypc").catch(() => [])]);
  const pl = plist.find(p => p.id === "rent_steam");
  const cfg = pl?.config || {};
  const busy = a => !!a.rented_until && a.rented_by;
  const stateLabel = a => busy(a) ? `В аренде до ${whenTs(a.rented_until)}` : a.state === "needs_reset" ? "Нужен сброс" : a.enabled ? "Свободен" : "Выключен";
  const statePill = a => busy(a) ? "paid" : a.state === "needs_reset" ? "refunded" : a.enabled ? "ok" : "new";

  root.innerHTML = `
    <div class="page-head"><h1>Аренда Steam</h1><button class="btn primary" id="add-acc">Добавить аккаунт</button></div>
    <section class="panel row-panel ${pl?.enabled ? "" : "off"}">
      <div><h2>${pl?.enabled ? "Аренда включена" : "Аренда выключена"}</h2>
        <p class="muted">1 купленная штука = 1 час, время идёт с момента оплаты. Команды покупателя: !code, !time, ${esc(cfg.extend_command || "!продлить")}. После аренды пароль меняется автоматически (если загружен maFile), иначе аккаунт ждёт ручного сброса.</p>
        ${pl?.missing?.length ? `<p class="warn-text small" style="margin-top:8px">Заполните в настройках плагина: ${esc(pl.missing.join(", "))}</p>` : ""}</div>
      <label class="switch"><input type="checkbox" id="rent-on" ${pl?.enabled ? "checked" : ""}><i></i></label>
    </section>
    ${onlypc.length ? `<section class="panel alert" style="margin-top:16px">
      <div class="panel-head"><h2>Проверка OnlyPC: ${onlypc.length}</h2></div>
      <p class="muted" style="margin-bottom:12px">Посмотрите фото покупателя в разделе «Чаты» и решите: выдать аккаунт или отклонить.</p>
      ${onlypc.map(j => `<div class="task">
        <div><b>#${esc(j.order_id)}</b> · ${esc(j.buyer)} · ${j.got_photo ? "прислал фото/сообщение" : `<span class="warn-text">ещё не прислал</span>`}</div>
        <div class="task-actions">
          <button class="btn primary" data-pc-ok="${esc(j.order_id)}" ${j.got_photo ? "" : "disabled"}>Выдать</button>
          <button class="btn ghost danger" data-pc-no="${esc(j.order_id)}">Отклонить</button>
        </div></div>`).join("")}
    </section>` : ""}

    <section class="panel" style="margin-top:16px">
      <div class="panel-head"><h2>Тексты и лот продления</h2><button class="btn" id="cfg-btn">Настроить</button></div>
      <p class="muted small">Лот продления: создайте на FunPay отдельный выключенный лот «Продление аренды» и впишите его ID. Плагин включает его на 10 минут, когда покупатель пишет ${esc(cfg.extend_command || "!продлить")}, и выключает после оплаты или по таймауту.</p>
    </section>

    ${accs.length ? `<section class="panel" style="margin-top:16px"><div class="table-wrap"><table>
      <thead><tr><th>Логин</th><th>Пароль</th><th>maFile</th><th>Статус</th><th>Арендатор</th><th></th><th></th></tr></thead>
      <tbody>${accs.map(a => `<tr>
        <td><b>${esc(a.login)}</b></td>
        <td><span class="pw" data-pw="${esc(a.password)}">••••••••</span> <button class="linkbtn" data-show="${esc(a.login)}">показать</button></td>
        <td>${a.has_mafile ? "есть" : `<span class="warn-text">нет</span>`}</td>
        <td><span class="pill ${statePill(a)}">${stateLabel(a)}</span></td>
        <td class="muted">${a.rented_by ? esc(a.rented_by) : "—"}</td>
        <td><div class="row-actions">
          ${a.state === "needs_reset" ? `<button class="btn" data-reset="${esc(a.login)}">Сброс выполнен</button>` : ""}
          ${a.has_mafile ? `<button class="btn" data-logout="${esc(a.login)}" title="Выйти на всех устройствах, пароль не меняется">Завершить сессии</button>` : ""}
          ${a.has_mafile ? `<button class="btn" data-test="${esc(a.login)}">Проверить вход</button>` : ""}
          <button class="btn" data-edit="${esc(a.login)}">Изменить</button>
          <button class="btn ghost danger" data-del="${esc(a.login)}">Удалить</button>
        </div></td>
        <td class="toggle-cell"><label class="switch" title="Сдавать этот аккаунт"><input type="checkbox" data-acc-on="${esc(a.login)}" ${a.enabled ? "checked" : ""} ${busy(a) ? "disabled" : ""}><i></i></label></td>
      </tr>`).join("")}</tbody></table></div></section>`
    : `<div class="panel empty" style="margin-top:16px"><h2>Аккаунтов для аренды нет</h2><p class="muted">Добавьте Steam-аккаунт: логин, пароль и maFile (файл из Steam Desktop Authenticator). Без maFile не будет кодов Guard и автоматической смены пароля.</p></div>`}`;

  root.querySelectorAll("[data-pc-ok]").forEach(b => (b.onclick = () => {
    const id = b.dataset.pcOk;
    modal(`<h2>Выдать аккаунт по заказу #${esc(id)}?</h2><p class="muted">Нажимайте, только если проверили фото и покупатель действительно в компьютерном клубе. Бот сразу выдаст ему свободный аккаунт.</p>`,
      "Выдать", async () => { const r = await api("POST", `/api/rent/onlypc/${encodeURIComponent(id)}/approve`); toast(r.message || "Аккаунт выдан"); reopenRent(); });
  }));
  root.querySelectorAll("[data-pc-no]").forEach(b => (b.onclick = () => {
    const id = b.dataset.pcNo;
    modal(`<h2>Отклонить заказ #${esc(id)}?</h2><p class="muted">Покупателю уйдёт сообщение, что проверка не пройдена. Деньги вернёте вручную на FunPay при необходимости.</p>`,
      "Отклонить", async () => { await api("POST", `/api/rent/onlypc/${encodeURIComponent(id)}/reject`); toast("Отклонено"); reopenRent(); });
  }));
  $("#rent-on").onchange = async e => {
    try { await api("POST", "/api/plugins/rent_steam/enabled", { enabled: e.target.checked }); toast(e.target.checked ? "Аренда включена" : "Аренда выключена"); reopenRent(); }
    catch (err) { e.target.checked = !e.target.checked; toast(err.message, true); }
  };
  root.querySelectorAll("[data-show]").forEach(b => (b.onclick = () => {
    const span = b.previousElementSibling;
    const shown = span.textContent !== "••••••••";
    span.textContent = shown ? "••••••••" : span.dataset.pw;
    b.textContent = shown ? "показать" : "скрыть";
  }));

  const accForm = (a = {}) => `
    <label class="field"><span>Логин Steam</span><input name="login" value="${esc(a.login || "")}" ${a.login ? "readonly" : ""} autocomplete="off"></label>
    <label class="field"><span>Пароль</span><input name="password" type="text" autocomplete="off" placeholder="${a.login ? "Оставьте пустым, чтобы не менять" : ""}"></label>
    <label class="field"><span>maFile (содержимое файла из Steam Desktop Authenticator)</span><textarea name="mafile" rows="4" placeholder='${a.has_mafile ? "maFile загружен. Вставьте новый, чтобы заменить" : '{"shared_secret":"…", "account_name":"…"}'}'></textarea>
      <small class="hint">Нужен для кодов Guard и смены пароля. Хранится на сервере в зашифрованном виде.</small></label>
    <label class="field"><span>ID лота этого аккаунта на FunPay (необязательно)</span><input name="offer_id" value="${esc(a.offer_id || "")}" placeholder="Число из ссылки offer?id=...">
      <small class="hint">Если указать — лот будет скрываться на время аренды (можно отключить в настройках плагина).</small></label>`;

  $("#add-acc").onclick = () => modal(`<h2>Новый аккаунт для аренды</h2>${accForm()}`, "Добавить", async f => {
    await api("POST", "/api/rent/accounts", { login: f.login.value, password: f.password.value, mafile: f.mafile.value, offer_id: f.offer_id.value });
    toast("Аккаунт добавлен"); reopenRent();
  });
  root.querySelectorAll("[data-edit]").forEach(b => (b.onclick = () => {
    const a = accs.find(x => x.login === b.dataset.edit);
    modal(`<h2>Аккаунт ${esc(a.login)}</h2>${accForm(a)}`, "Сохранить", async f => {
      const body = {};
      if (f.password.value.trim()) body.password = f.password.value.trim();
      if (f.mafile.value.trim()) body.mafile = f.mafile.value.trim();
      body.offer_id = f.offer_id.value;
      await api("PUT", `/api/rent/accounts/${encodeURIComponent(a.login)}`, body); toast("Сохранено"); reopenRent();
    });
  }));
  root.querySelectorAll("[data-acc-on]").forEach(c => (c.onchange = async () => {
    try { await api("PUT", `/api/rent/accounts/${encodeURIComponent(c.dataset.accOn)}`, { enabled: c.checked }); toast(c.checked ? "Аккаунт включён" : "Аккаунт выключен"); }
    catch (e) { c.checked = !c.checked; toast(e.message, true); }
  }));
  root.querySelectorAll("[data-logout]").forEach(b => (b.onclick = () => {
    const login = b.dataset.logout;
    modal(`<h2>Завершить сессии: ${esc(login)}?</h2><p class="muted">Аккаунт выйдет на всех устройствах, пароль НЕ меняется. Полезно, если нужно выкинуть игрока вручную.</p>`,
      "Завершить", async () => {
        const r = await api("POST", `/api/rent/accounts/${encodeURIComponent(login)}/logout`);
        toast(r.message || "Сессии завершены");
      });
  }));
  root.querySelectorAll("[data-test]").forEach(b => (b.onclick = async () => {
    b.disabled = true; b.textContent = "Проверяю…";
    try { const r = await api("POST", `/api/rent/accounts/${encodeURIComponent(b.dataset.test)}/test`); toast((r.ok ? "" : "Не вошёл: ") + r.message, !r.ok); }
    catch (e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = "Проверить вход"; }
  }));
  root.querySelectorAll("[data-reset]").forEach(b => (b.onclick = () => {
    const login = b.dataset.reset;
    modal(`<h2>Сброс доступа: ${esc(login)}</h2>
      <p class="muted">Смена пароля не прошла автоматически. Зайдите в Steam, выйдите на всех устройствах, при желании смените пароль. Если сменили — впишите новый ниже, чтобы приложение его показывало.</p>
      <label class="field"><span>Новый пароль (если меняли)</span><input name="password" type="text" autocomplete="off" placeholder="Можно оставить пустым"></label>`,
      "Аккаунт свободен", async f => { await api("POST", `/api/rent/accounts/${encodeURIComponent(login)}/reset-done`, { password: f.password.value }); toast("Аккаунт освобождён"); reopenRent(); });
  }));
  root.querySelectorAll("[data-del]").forEach(b => (b.onclick = () => {
    const login = b.dataset.del;
    modal(`<h2>Удалить аккаунт ${esc(login)}?</h2><p class="muted">Он больше не будет сдаваться в аренду.</p>`,
      "Удалить", async () => { await api("DELETE", `/api/rent/accounts/${encodeURIComponent(login)}`); toast("Удалён"); reopenRent(); });
  }));

  $("#cfg-btn").onclick = () => {
    const p = pl;
    const fields = p.settings.map(st => {
      const v = p.config[st.key] ?? st.default ?? "";
      const lbl = `${esc(st.label)}${st.required ? ' <span class="req">обязательно</span>' : ""}`;
      const hint = st.hint ? `<small class="hint">${esc(st.hint)}</small>` : "";
      if (st.type === "number") return `<label class="field"><span>${lbl}</span><input name="${st.key}" type="number" value="${esc(v)}">${hint}</label>`;
      if (st.type === "textarea") return `<label class="field"><span>${lbl}</span><textarea name="${st.key}" rows="3">${esc(v)}</textarea>${hint}</label>`;
      return `<label class="field"><span>${lbl}</span><input name="${st.key}" value="${esc(v)}">${hint}</label>`;
    }).join("");
    modal(`<h2>Настройка аренды</h2><p class="muted">Подстановки: {buyer}, {login}, {password}, {until}, {hours}, {code}, {link}.</p>${fields}`, "Сохранить", async f => {
      const c = {};
      p.settings.forEach(st => { c[st.key] = st.type === "number" ? Number(f.elements[st.key].value) : f.elements[st.key].value; });
      await api("PUT", "/api/plugins/rent_steam/config", c); toast("Настройки сохранены"); reopenRent();
    });
  };
};

// ---------- Автоответы ----------
pages.replies = async root => {
  const s = await api("GET", "/api/settings");
  const tgReady = s.tg_token_set && s.tg_chat_id;
  root.innerHTML = `
    <div class="page-head"><h1>Автоответы</h1></div>
    <div class="settings">
      <section class="panel" id="sec-greet">
        <div class="row"><div><h2>Приветствие</h2><p>Отправляется один раз — на первое сообщение нового покупателя. Покупатели, которые писали до подключения, его не получат.</p></div>${sw("greeting_enabled", s.greeting_enabled)}</div>
        <label class="field"><span>Текст приветствия</span><textarea data-k="greeting_text" rows="4">${esc(s.greeting_text)}</textarea><small class="hint">{buyer} — имя покупателя.</small></label>
        <button class="btn primary save">Сохранить</button>
      </section>
      <section class="panel" id="sec-help">
        <div class="row"><div><h2>Вызов продавца</h2><p>Покупатель пишет команду — вам приходит уведомление в Telegram, а покупателю — ответ.</p></div>${sw("help_enabled", s.help_enabled)}</div>
        ${tgReady ? "" : `<p class="warn-text small" style="margin-bottom:12px">Telegram не подключён — уведомления не придут. Подключите его в Настройках.</p>`}
        <label class="field"><span>Команда</span><input data-k="help_command" value="${esc(s.help_command)}" style="max-width:200px"></label>
        <label class="field"><span>Ответ покупателю</span><textarea data-k="help_reply" rows="2">${esc(s.help_reply)}</textarea><small class="hint">Оставьте пустым, чтобы не отвечать.</small></label>
        <button class="btn primary save">Сохранить</button>
      </section>
      <section class="panel" id="sec-review">
        <div class="row"><div><h2>Спасибо за отзыв</h2><p>Когда покупатель оставляет отзыв с нужной оценкой, сервер пишет ему в чат. Один раз на заказ.</p></div>${sw("review_thanks_enabled", s.review_thanks_enabled)}</div>
        <label class="field"><span>Благодарить за отзывы</span><select data-k="review_thanks_min" style="max-width:220px">
          ${[5, 4, 3, 2, 1].map(n => `<option value="${n}" ${s.review_thanks_min === n ? "selected" : ""}>${n === 5 ? "только 5 звёзд" : `от ${n} звёзд`}</option>`).join("")}</select></label>
        <label class="field"><span>Текст благодарности</span><textarea data-k="review_thanks_text" rows="2">${esc(s.review_thanks_text)}</textarea><small class="hint">{buyer} — имя покупателя, {order} — номер заказа.</small></label>
        <button class="btn primary save">Сохранить</button>
      </section>
    </div>`;
  root.querySelectorAll("section.panel").forEach(sec => {
    $(".save", sec).onclick = () => {
      const extra = {};
      const sel = $("select[data-k]", sec);
      if (sel) extra[sel.dataset.k] = Number(sel.value);
      saveSection(sec, extra);
    };
  });
};

// ---------- Подписка ----------
pages.subscription = async root => {
  const sub = await api("GET", "/api/subscription");
  const fmt = ts => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }).replace(/ г\.$/, "");
  const LOTS = state.subLots || (state.demo ? { 1: "https://funpay.com/lots/offer?id=1", 3: "https://funpay.com/lots/offer?id=3", 6: "https://funpay.com/lots/offer?id=6", 12: "https://funpay.com/lots/offer?id=12" } : {});  // ссылки на лоты задаёшь ты, см. ниже
  const plans = [[1, "1 месяц", 349], [3, "3 месяца", 899], [6, "6 месяцев", 1690], [12, "12 месяцев", 2990]];

  if (!sub.configured) {
    root.innerHTML = `<div class="page-head"><h1>Подписка</h1></div>
      <section class="panel"><h2>Подписка не требуется</h2>
        <p class="muted" style="margin-top:8px">Этот сервер работает без подписки — все плагины доступны. Подписка включается, только если сервер настроен на проверку лицензий.</p></section>`;
    return;
  }

  let statusCard;
  if (sub.active && sub.source === "trial") {
    statusCard = `<div class="sub-hero trial"><div class="sub-badge">Пробный период</div>
      <div class="sub-days">${sub.days_left} ${plural(sub.days_left, "день", "дня", "дней")}</div>
      <p class="muted">Плагины доступны до ${fmt(sub.until)}. Чтобы продолжить пользоваться после, активируйте код подписки.</p></div>`;
  } else if (sub.active) {
    statusCard = `<div class="sub-hero active"><div class="sub-badge">Подписка активна</div>
      <div class="sub-days">${sub.days_left} ${plural(sub.days_left, "день", "дня", "дней")}</div>
      <p class="muted">Действует до ${fmt(sub.until)}. Продлить можно в любой момент — дни добавятся к остатку.</p></div>`;
  } else {
    statusCard = `<div class="sub-hero off"><div class="sub-badge">Подписка неактивна</div>
      <p class="muted" style="margin-top:8px">Плагины сейчас не принимают новые заказы (уже начатые дорабатываются). Купите код и активируйте его ниже.</p></div>`;
  }
  if (sub.offline) statusCard += `<p class="warn-text small" style="margin-top:10px">Сейчас нет связи с сервером лицензий, показан последний известный статус. ${esc(sub.offline_error || "")}</p>`;

  root.innerHTML = `
    <div class="page-head"><h1>Подписка</h1><button class="btn" id="sub-refresh">Обновить статус</button></div>
    ${statusCard}
    <section class="panel" style="margin-top:16px">
      <h2>Активировать код</h2>
      <p class="muted" style="margin:4px 0 14px">Купите подписку на FunPay, получите код и введите его здесь. Код активируется один раз и привязывается к этому серверу — при переезде на другой хост он не переносится.</p>
      <form id="act-form" class="sub-activate"><input id="sub-code" placeholder="KSA-XXXX-XXXX-XXXX" autocomplete="off" spellcheck="false"><button class="btn primary">Активировать</button></form>
      <p id="sub-msg" class="error" hidden></p>
    </section>
    <section class="panel" style="margin-top:16px">
      <h2>Купить подписку</h2>
      <p class="muted" style="margin:4px 0 14px">После оплаты код придёт вам в чат FunPay автоматически.</p>
      <div class="plan-grid">${plans.map(([m, name, price]) => `
        <div class="plan"><div class="plan-name">${name}</div><div class="plan-price">${price} ₽</div>
          ${LOTS[m] ? `<a class="btn primary" href="${esc(LOTS[m])}" target="_blank" rel="noopener">Купить на FunPay</a>`
                    : `<span class="muted small">Ссылка на лот не задана</span>`}</div>`).join("")}</div>
      ${state.demo ? "" : `<p class="muted small" style="margin-top:12px">Ссылки на ваши лоты задаются в файле сервера (см. README).</p>`}
    </section>`;

  $("#sub-refresh").onclick = async e => { e.target.disabled = true; try { await api("POST", "/api/subscription/refresh"); go("subscription"); } catch (err) { toast(err.message, true); e.target.disabled = false; } };
  $("#act-form").onsubmit = async e => {
    e.preventDefault();
    const code = $("#sub-code").value.trim();
    const msg = $("#sub-msg"), btn = $("button", e.target);
    msg.hidden = true; btn.disabled = true;
    try { const r = await api("POST", "/api/subscription/activate", { code }); toast(`Готово! Подписка активна ${r.days_left} дн.`); go("subscription"); }
    catch (err) { msg.textContent = err.message; msg.hidden = false; btn.disabled = false; }
  };
};
function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

// ---------- Настройки ----------
pages.settings = async root => {
  const [s, st, raised] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/status"), api("GET", "/api/raise")]);
  const tgEvents = [["tg_on_attention", "Заказ требует проверки"], ["tg_on_help", "Покупатель вызвал продавца"], ["tg_on_account", "Аккаунт FunPay перестал работать"],
                    ["tg_on_stock", "Закончился товар автовыдачи"], ["tg_on_review", "Новый отзыв"], ["tg_on_blacklist", "Заказ от покупателя из чёрного списка"], ["tg_on_order", "Каждый новый заказ"]];
  root.innerHTML = `
    <div class="page-head"><h1>Настройки</h1></div>
    <div class="settings">
      <section class="panel" id="sec-tg">
        <h2>Уведомления в Telegram</h2>
        <p class="muted" style="margin:4px 0 14px">Сигналы на телефон, даже когда приложение закрыто. Управления через Telegram нет — только уведомления.</p>
        <details class="howto" style="margin:0 0 14px"><summary>Как подключить за 2 минуты</summary><ol>
          <li>В Telegram откройте <b>@BotFather</b>, отправьте <code>/newbot</code>, придумайте имя — он пришлёт токен.</li>
          <li>Откройте <b>@userinfobot</b> и отправьте ему любое сообщение — он пришлёт ваш ID (число).</li>
          <li>Найдите своего нового бота и нажмите «Запустить» (/start), иначе он не сможет вам писать.</li></ol></details>
        <label class="field"><span>Токен бота</span><input data-k="tg_token" data-secret type="password" autocomplete="off" placeholder="${s.tg_token_set ? "Сохранён — пусто = не менять" : "123456789:AA…"}"></label>
        <label class="field"><span>Ваш Telegram ID</span><input data-k="tg_chat_id" value="${esc(s.tg_chat_id)}" placeholder="Например: 123456789" style="max-width:260px"></label>
        <div class="checks">${tgEvents.map(([k, t]) => `<label class="check"><input type="checkbox" data-k="${k}" ${s[k] ? "checked" : ""}> ${t}</label>`).join("")}</div>
        <div class="btn-row"><button class="btn primary save">Сохранить</button><button class="btn" id="tg-test">Отправить тестовое</button></div>
      </section>

      <section class="panel" id="sec-raise">
        <div class="row"><div><h2>Автоподнятие лотов</h2><p>Поднимает все активные лоты на всех аккаунтах. FunPay разрешает поднимать раз в несколько часов, лишние попытки он просто отклоняет.</p></div>${sw("raise_enabled", s.raise_enabled)}</div>
        <div class="row"><div><div>Интервал</div><p>От 30 минут до 24 часов.</p></div><div class="inline"><input type="number" data-k="raise_interval_min" min="30" max="1440" value="${s.raise_interval_min}"><span class="muted">мин</span></div></div>
        ${raised.length ? `<div class="raise-log">${raised.map(r => `<div><b>${esc(r.account)}</b> <span class="muted">${ago(r.ts)}:</span> ${esc(r.report.join("; "))}</div>`).join("")}</div>` : ""}
        <div class="btn-row"><button class="btn primary save">Сохранить</button><button class="btn" id="raise-now">Поднять сейчас</button></div>
      </section>

      <section class="panel" id="sec-bl">
        <h2>Чёрный список</h2>
        <p class="muted" style="margin:4px 0 14px">Этим покупателям не выдаётся товар автоматически и не отправляются автоответы. Заказ остаётся вам на ручное решение, приходит уведомление.</p>
        <label class="field"><span>Имена покупателей на FunPay — по одному на строку</span><textarea data-k="blacklist" data-list rows="5">${esc(s.blacklist.join("\n"))}</textarea></label>
        <button class="btn primary save">Сохранить</button>
      </section>

      <section class="panel" id="sec-profit">
        <h2>Расчёт прибыли</h2>
        <div class="row" style="border-top:0"><div><div>Комиссия FunPay</div><p>Вычитается из выручки. Если в разделе «Продажи» видна сумма, которую получаете вы, оставьте 0.</p></div>
          <div class="inline"><input type="number" data-k="commission_pct" min="0" max="50" step="0.1" value="${s.commission_pct}"><span class="muted">%</span></div></div>
        <button class="btn primary save">Сохранить</button>
      </section>

      <section class="panel" id="sec-server">
        <h2 style="margin-bottom:14px">Работа сервера</h2>
        <div class="row"><div><div>Проверять заказы и баланс каждые</div><p>От 1 до 60 минут.</p></div><div class="inline"><input type="number" data-k="sync_interval_min" min="1" max="60" value="${s.sync_interval_min}"><span class="muted">мин</span></div></div>
        <div class="row"><div><div>Проверять новые сообщения каждые</div><p>От 5 до 120 секунд. Чем чаще, тем быстрее приветствие и автоответы.</p></div><div class="inline"><input type="number" data-k="chat_interval_sec" min="5" max="120" value="${s.chat_interval_sec}"><span class="muted">сек</span></div></div>
        <div class="row"><div><div>Всплывающие уведомления о заказах в приложении</div><p>Пока приложение открыто.</p></div>${sw("notify_new_orders", s.notify_new_orders)}</div>
        <button class="btn primary save">Сохранить</button>
      </section>

      <section class="panel"><h2 style="margin-bottom:14px">Подключение</h2>
        <div class="row" style="border-top:0"><div><div>${esc(state.server)}</div><p>Версия сервера ${esc(st.version)}, работает ${Math.floor(st.uptime / 3600)} ч ${Math.floor(st.uptime % 3600 / 60)} мин</p></div>
          <button class="btn" id="logout">${state.demo ? "Выйти из демо" : "Отключиться"}</button></div>
      </section>
    </div>`;

  root.querySelectorAll("section[id^=sec-]").forEach(sec => ($(".save", sec).onclick = () => saveSection(sec)));
  $("#tg-test").onclick = async e => {
    const sec = $("#sec-tg");
    if (!(await saveSection(sec))) return;
    e.target.disabled = true;
    try { const r = await api("POST", "/api/telegram/test"); toast(r.ok ? r.message : "Не отправлено: " + r.message, !r.ok); }
    catch (err) { toast(err.message, true); }
    finally { e.target.disabled = false; go("settings"); }
  };
  $("#raise-now").onclick = async e => {
    e.target.disabled = true; e.target.textContent = "Поднимаю…";
    try { const r = await api("POST", "/api/raise"); toast(Object.entries(r).map(([a, rep]) => `${a}: ${rep.join("; ")}`).join("\n") || "Нет работающих аккаунтов"); go("settings"); }
    catch (err) { toast(err.message, true); e.target.disabled = false; e.target.textContent = "Поднять сейчас"; }
  };
  $("#logout").onclick = async () => {
    clearInterval(state.timer);
    if (!state.demo && window.pywebview) await window.pywebview.api.forget();
    showConnect();
  };
};

// ---------- модальное окно ----------
function modal(html, okText, onOk) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="overlay"><form class="modal" novalidate>${html}
    <p class="error" hidden></p>
    <div class="modal-actions"><button type="button" class="btn ghost" data-close>Отмена</button><button class="btn primary">${okText}</button></div></form></div>`;
  const form = $("form", root), err = $(".error", form), ok = $(".btn.primary", form);
  const close = () => (root.innerHTML = "");
  $("[data-close]", form).onclick = close;
  $(".overlay", root).onmousedown = e => e.target.classList.contains("overlay") && close();
  document.onkeydown = e => e.key === "Escape" && close();
  form.onsubmit = async e => {
    e.preventDefault();
    ok.disabled = true; err.hidden = true;
    try { if ((await onOk(form)) !== "keep") close(); }
    catch (x) { err.textContent = x.message; err.hidden = false; }
    finally { ok.disabled = false; }
  };
  setTimeout(() => $("input, textarea", form)?.focus(), 30);
}

let resizeT;
window.addEventListener("resize", () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(() => { if (["dashboard", "sales"].includes(state.page) && !$("#app").hidden) go(state.page); }, 250);
});
if (window.pywebview) boot(); else window.addEventListener("pywebviewready", boot);
setTimeout(() => { if (!window.pywebview && $("#app").hidden && $("#connect").hidden) boot(); }, 400);
