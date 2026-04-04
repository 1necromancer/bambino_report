const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

const initData = tg?.initData || "";
let isOwner = false;
let stockData = [];
let currentSaleProduct = null;
let currentSaleSize = null;

const headers = {
  "Content-Type": "application/json",
  "X-Telegram-Init-Data": initData,
};

async function api(path, opts = {}) {
  const res = await fetch(path, { headers, ...opts });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2200);
}

function $(id) { return document.getElementById(id); }

// ── Tabs ──────────────────────────────────────────────────────────

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    loadTab(tab.dataset.tab);
  });
});

function loadTab(name) {
  const content = $("content");
  content.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  switch (name) {
    case "stock": loadStock(); break;
    case "sales": loadSales(); break;
    case "reports": loadReports(); break;
    case "admin": loadAdmin(); break;
  }
}

// ── Stock ─────────────────────────────────────────────────────────

async function loadStock() {
  try {
    const data = await api("/api/stock");
    stockData = data.products;
    isOwner = data.is_owner;
    if (isOwner) $("tab-admin").style.display = "";

    if (!stockData.length) {
      $("content").innerHTML = `
        <div class="empty">
          <div class="empty-icon">🍦</div>
          <div class="empty-text">Нет сортов. Добавьте через настройки.</div>
        </div>`;
      return;
    }

    const maxWeight = Math.max(...stockData.map(p => p.weight), 5000);

    $("content").innerHTML = stockData.map(p => {
      const pct = Math.min((p.weight / maxWeight) * 100, 100);
      const pricesHtml = p.prices.map(pr =>
        `<span class="price-tag">${pr.size} г — ${pr.price} ₸</span>`
      ).join("");
      return `
        <div class="card">
          <div class="card-title">${esc(p.name)}</div>
          <div class="card-row">
            <span class="label">Остаток</span>
            <span class="value">${p.weight.toFixed(0)} г (${(p.weight/1000).toFixed(3)} кг)</span>
          </div>
          <div class="weight-bar"><div class="weight-bar-fill" style="width:${pct}%"></div></div>
          <div class="card-prices">${pricesHtml}</div>
          <div class="card-actions">
            <button class="btn btn-primary btn-sm" onclick="openSale(${p.id})">Продажа</button>
          </div>
        </div>`;
    }).join("");
  } catch (e) {
    $("content").innerHTML = `<div class="empty"><div class="empty-text">Ошибка: ${esc(e.message)}</div></div>`;
  }
}

// ── Sales ─────────────────────────────────────────────────────────

async function loadSales() {
  try {
    const data = await api("/api/sales");
    let html = `
      <div class="summary">
        <div class="summary-value">${data.total_revenue.toLocaleString()} ₸</div>
        <div class="summary-label">Выручка за сегодня</div>
        <div class="summary-row">
          <div class="summary-item">
            <div class="value">${data.entries.length}</div>
            <div class="label">продаж</div>
          </div>
          <div class="summary-item">
            <div class="value">${data.entries.reduce((s, e) => s + e.qty, 0)}</div>
            <div class="label">порций</div>
          </div>
        </div>
      </div>`;

    if (data.entries.length) {
      html += '<div class="card">';
      data.entries.forEach(e => {
        html += `
          <div class="sale-item">
            <div class="sale-info">
              <div class="sale-product">${esc(e.product)}</div>
              <div class="sale-detail">${e.size} г × ${e.qty}</div>
            </div>
            <div class="sale-total">${e.total.toLocaleString()} ₸</div>
          </div>`;
      });
      html += "</div>";
    } else {
      html += `
        <div class="empty">
          <div class="empty-icon">🛒</div>
          <div class="empty-text">Сегодня продаж нет</div>
        </div>`;
    }

    // Quick sale buttons
    if (stockData.length) {
      html += '<div style="margin-top:16px">';
      stockData.forEach(p => {
        html += `<button class="btn btn-primary btn-full" style="margin-top:8px" onclick="openSale(${p.id})">+ ${esc(p.name)}</button>`;
      });
      html += "</div>";
    }

    $("content").innerHTML = html;
  } catch (e) {
    $("content").innerHTML = `<div class="empty"><div class="empty-text">Ошибка: ${esc(e.message)}</div></div>`;
  }
}

// ── Sale modal ────────────────────────────────────────────────────

function openSale(productId) {
  const p = stockData.find(x => x.id === productId);
  if (!p) return;
  currentSaleProduct = p;
  currentSaleSize = null;

  $("sale-modal-title").textContent = p.name;
  $("sale-qty").value = 1;
  $("sale-qty-row").style.display = "none";

  $("sale-sizes").innerHTML = p.prices.map(pr => `
    <button class="size-btn" data-size="${pr.size}" onclick="selectSize(this, ${pr.size})">
      <span class="size-name">${pr.size} г</span>
      <span class="size-price">${pr.price} ₸</span>
    </button>
  `).join("");

  $("sale-modal").style.display = "";
}

function selectSize(btn, size) {
  document.querySelectorAll(".size-btn").forEach(b => b.classList.remove("selected"));
  btn.classList.add("selected");
  currentSaleSize = size;
  $("sale-qty-row").style.display = "";
}

function changeQty(delta) {
  const input = $("sale-qty");
  const val = Math.max(1, Math.min(99, parseInt(input.value || 1) + delta));
  input.value = val;
}

function closeModal() {
  $("sale-modal").style.display = "none";
}

async function submitSale() {
  if (!currentSaleProduct || !currentSaleSize) return;
  const qty = parseInt($("sale-qty").value) || 1;
  const btn = $("sale-submit");
  btn.disabled = true;
  btn.textContent = "...";

  try {
    await api("/api/sales", {
      method: "POST",
      body: JSON.stringify({
        product_id: currentSaleProduct.id,
        size_grams: currentSaleSize,
        quantity: qty,
      }),
    });
    closeModal();
    toast(`${currentSaleProduct.name} ${currentSaleSize}г × ${qty} добавлено`);
    loadTab(document.querySelector(".tab.active")?.dataset.tab || "stock");
    // Refresh stock data
    api("/api/stock").then(d => { stockData = d.products; });
  } catch (e) {
    toast("Ошибка: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Добавить";
  }
}

// ── Reports ───────────────────────────────────────────────────────

async function loadReports() {
  try {
    const data = await api("/api/reports");
    let html = `
      <div class="summary">
        <div class="summary-row">
          <div class="summary-item">
            <div class="value">${data.total_revenue.toLocaleString()} ₸</div>
            <div class="label">Выручка</div>
          </div>
          <div class="summary-item">
            <div class="value ${data.total_penalty > 0 ? 'negative' : ''}">${data.total_penalty.toLocaleString()} ₸</div>
            <div class="label">Штрафы</div>
          </div>
        </div>
      </div>`;

    if (data.items.length) {
      html += '<div class="card">';
      data.items.forEach(item => {
        const discClass = item.discrepancy < 0 ? "negative" : (item.discrepancy > 0 ? "positive" : "");
        html += `
          <div class="report-item">
            <div class="report-product">${esc(item.product)}</div>
            <div class="report-grid">
              <span class="label">Ожидаемый</span><span>${item.expected.toFixed(0)} г</span>
              <span class="label">Фактический</span><span>${item.actual.toFixed(0)} г</span>
              <span class="label">Разница</span><span class="${discClass}">${item.discrepancy >= 0 ? "+" : ""}${item.discrepancy.toFixed(0)} г</span>
              <span class="label">Штраф</span><span class="${item.penalty > 0 ? 'negative' : ''}">${item.penalty.toFixed(0)} ₸</span>
            </div>
          </div>`;
      });
      html += "</div>";
    } else {
      html += `
        <div class="empty">
          <div class="empty-icon">📊</div>
          <div class="empty-text">Инвентаризация ещё не проводилась сегодня</div>
        </div>`;
    }

    $("content").innerHTML = html;
  } catch (e) {
    $("content").innerHTML = `<div class="empty"><div class="empty-text">Ошибка: ${esc(e.message)}</div></div>`;
  }
}

// ── Admin ─────────────────────────────────────────────────────────

async function loadAdmin() {
  try {
    const data = await api("/api/stock");
    const products = data.products;

    let html = '<button class="btn btn-primary btn-full" onclick="openAddProduct()">+ Добавить сорт</button>';
    html += '<div style="margin-top:16px">';

    products.forEach(p => {
      const pricesHtml = p.prices.map(pr =>
        `<span class="price-tag">${pr.size} г — ${pr.price} ₸</span>`
      ).join("");
      html += `
        <div class="card">
          <div class="card-title">${esc(p.name)}</div>
          <div class="card-row">
            <span class="label">Тара</span>
            <span class="value">${p.tare} г</span>
          </div>
          <div class="card-row">
            <span class="label">Себестоимость</span>
            <span class="value">${p.cost_per_gram} ₸/г</span>
          </div>
          <div class="card-prices">${pricesHtml}</div>
          <div class="card-actions">
            <button class="btn btn-danger btn-sm" onclick="deleteProduct(${p.id}, '${esc(p.name)}')">Удалить</button>
          </div>
        </div>`;
    });
    html += "</div>";

    $("content").innerHTML = html;
  } catch (e) {
    $("content").innerHTML = `<div class="empty"><div class="empty-text">Ошибка: ${esc(e.message)}</div></div>`;
  }
}

function openAddProduct() {
  $("new-product-name").value = "";
  $("new-product-tare").value = "50";
  $("new-product-cost").value = "0.06";
  $("add-product-modal").style.display = "";
}

function closeAddProduct() {
  $("add-product-modal").style.display = "none";
}

async function submitProduct() {
  const name = $("new-product-name").value.trim();
  if (!name) { toast("Введите название"); return; }

  try {
    await api("/api/products", {
      method: "POST",
      body: JSON.stringify({
        name,
        tare: parseFloat($("new-product-tare").value) || 50,
        cost_per_gram: $("new-product-cost").value || "0.06",
      }),
    });
    closeAddProduct();
    toast(`Сорт «${name}» создан`);
    // Refresh stock data
    const d = await api("/api/stock");
    stockData = d.products;
    loadAdmin();
  } catch (e) {
    toast("Ошибка: " + e.message);
  }
}

async function deleteProduct(id, name) {
  if (!confirm(`Удалить сорт «${name}»?`)) return;
  try {
    await api(`/api/products/${id}`, { method: "DELETE" });
    toast(`«${name}» удалён`);
    const d = await api("/api/stock");
    stockData = d.products;
    loadAdmin();
  } catch (e) {
    toast("Ошибка: " + e.message);
  }
}

// ── Utils ─────────────────────────────────────────────────────────

function esc(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

// ── Init ──────────────────────────────────────────────────────────

loadTab("stock");
