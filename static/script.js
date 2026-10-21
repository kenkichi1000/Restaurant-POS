/* ==========================================================
   GourmetPOS — client script (shared across pages)
   ========================================================== */

const TAX_RATE = 0.08;
const DEFAULT_STAFF_ID = 1; // TODO: replace with the logged-in staff member

const money = (n) => `$${Number(n).toFixed(2)}`;

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

/* ---------- Toasts (replace alert()) ---------- */
function showToast(message, type = 'info') {
  let stack = document.querySelector('.toast-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.className = 'toast-stack';
    stack.setAttribute('role', 'status');
    stack.setAttribute('aria-live', 'polite');
    document.body.appendChild(stack);
  }
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  stack.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

/* ==========================================================
   POS TERMINAL
   ========================================================== */
let cartState = [];
let orderType = 'Dine-In';

function addToCart(name, price, itemId) {
  const existing = cartState.find((item) => item.itemId === itemId);
  if (existing) {
    existing.qty += 1;
  } else {
    cartState.push({ itemId, name, price, qty: 1 });
  }
  updateCartUI();
}

function adjustQty(index, change) {
  if (!cartState[index]) return;
  cartState[index].qty += change;
  if (cartState[index].qty <= 0) cartState.splice(index, 1);
  updateCartUI();
}

function updateCartUI() {
  const container = document.getElementById('cartContainer');
  if (!container) return;

  container.innerHTML = '';
  let subtotal = 0;

  if (cartState.length === 0) {
    container.innerHTML = '<div class="cart-empty">No items yet.<br>Tap a menu item to add it.</div>';
  }

  cartState.forEach((item, index) => {
    subtotal += item.price * item.qty;

    const row = document.createElement('div');
    row.className = 'cart-item';
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(item.name)}</strong>
        <div class="cart-item-meta">${money(item.price)} each</div>
      </div>
      <div class="qty-controls">
        <button type="button" class="qty-btn" aria-label="Remove one ${escapeHtml(item.name)}" onclick="adjustQty(${index}, -1)">&minus;</button>
        <span>${item.qty}</span>
        <button type="button" class="qty-btn" aria-label="Add one ${escapeHtml(item.name)}" onclick="adjustQty(${index}, 1)">+</button>
      </div>`;
    container.appendChild(row);
  });

  const tax = subtotal * TAX_RATE;
  document.getElementById('subtotalVal').textContent = money(subtotal);
  document.getElementById('taxVal').textContent = money(tax);
  document.getElementById('totalVal').textContent = money(subtotal + tax);

  const submit = document.getElementById('submitOrderBtn');
  if (submit) submit.disabled = cartState.length === 0;
}

function setOrderType(button, type) {
  orderType = type;
  document.querySelectorAll('.toggle-btn').forEach((btn) => {
    const active = btn === button;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', active);
  });
}

function filterCategory(category, button) {
  document.querySelectorAll('.category-pill').forEach((pill) => {
    pill.classList.toggle('active', pill === button);
  });

  const cards = document.querySelectorAll('#menuGrid .menu-card');
  let visible = 0;
  cards.forEach((card) => {
    const show = category === 'all' || card.dataset.category === category;
    card.hidden = !show;
    if (show) visible += 1;
  });

  const empty = document.getElementById('menuEmpty');
  if (empty) empty.hidden = visible > 0;
}

function submitOrder() {
  if (cartState.length === 0) {
    showToast('Add at least one item before submitting.', 'error');
    return;
  }

  const submit = document.getElementById('submitOrderBtn');
  if (submit) submit.disabled = true;

  fetch('/api/orders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      order_type: orderType,
      staff_id: DEFAULT_STAFF_ID,
      items: cartState.map((i) => ({ item_id: i.itemId, quantity: i.qty, notes: '' })),
    }),
  })
    .then((res) => res.json().then((data) => ({ ok: res.ok, data })))
    .then(({ ok, data }) => {
      if (!ok) throw new Error(data.error || 'Server rejected the order.');
      showToast(`Order #${data.order_id} sent to the kitchen.`, 'success');
      cartState = [];
      const heading = document.getElementById('orderNumber');
      if (heading && data.order_id) heading.textContent = `Order #${data.order_id + 1}`;
      updateCartUI();
    })
    .catch((err) => {
      showToast(`Order not sent: ${err.message}`, 'error');
      if (submit) submit.disabled = cartState.length === 0;
    });
}

/* ==========================================================
   KITCHEN DISPLAY
   ========================================================== */
function advanceTicket(button) {
  const card = button.closest('.kds-card');
  const state = card.dataset.state;
  const badge = card.querySelector('.kds-status');

  if (state === 'queued') {
    card.dataset.state = 'preparing';
    card.classList.add('preparing');
    button.textContent = 'Mark as ready';
    if (badge) { badge.textContent = 'Preparing'; badge.className = 'badge badge-muted kds-status'; }
  } else if (state === 'preparing') {
    card.dataset.state = 'ready';
    card.classList.remove('preparing');
    card.classList.add('ready');
    button.textContent = 'Ready for pickup';
    button.disabled = true;
    if (badge) { badge.textContent = 'Ready'; badge.className = 'badge badge-success kds-status'; }
    const timer = card.querySelector('.order-timer');
    if (timer) timer.dataset.stopped = 'true';
  }
}

function formatElapsed(totalSeconds) {
  const m = String(Math.floor(totalSeconds / 60)).padStart(2, '0');
  const s = String(totalSeconds % 60).padStart(2, '0');
  return `${m}:${s}`;
}

function startTimers() {
  const timers = document.querySelectorAll('.order-timer[data-elapsed]');
  if (!timers.length) return;

  const tick = () => {
    timers.forEach((el) => {
      if (el.dataset.stopped) return;
      const seconds = parseInt(el.dataset.elapsed, 10);
      el.textContent = formatElapsed(seconds);
      el.classList.toggle('late', seconds >= 600); // 10+ minutes
      el.dataset.elapsed = seconds + 1;
    });
  };
  tick();
  setInterval(tick, 1000);
}

/* ==========================================================
   BILLING
   ========================================================== */
function filterOrders(query) {
  const q = query.trim().toLowerCase();
  const rows = document.querySelectorAll('#ordersBody tr[data-order]');
  let visible = 0;

  rows.forEach((row) => {
    const match = row.textContent.toLowerCase().includes(q);
    row.hidden = !match;
    if (match) visible += 1;
  });

  const empty = document.getElementById('ordersEmpty');
  if (empty) empty.hidden = visible > 0;
}

function printReceipt(orderId) {
  const id = document.getElementById('receiptId');
  if (id) id.textContent = `#${orderId}`;
  window.print();
}

async function processPayment(orderId) {
  const row = document.querySelector(`#ordersBody tr[data-order="${orderId}"]`);
  if (!row) return;
  const button = row.querySelector('button');
  button.disabled = true;

  try {
    const billRes = await fetch(`/api/orders/${orderId}/bill`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ staff_id: DEFAULT_STAFF_ID }),
    });
    const bill = await billRes.json();
    // "Bill already exists" still returns a bill_id we can pay against.
    if (!billRes.ok && !bill.bill_id) throw new Error(bill.error || 'Could not create bill.');

    const payRes = await fetch('/api/payments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bill_id: bill.bill_id,
        amount: row.dataset.total,
        payment_method: 'Cash',
        staff_id: DEFAULT_STAFF_ID,
      }),
    });
    const pay = await payRes.json();
    if (!payRes.ok) throw new Error(pay.error || 'Payment failed.');

    row.querySelector('.badge').className = 'badge badge-success';
    row.querySelector('.badge').textContent = 'Paid';
    button.className = 'btn btn-secondary btn-sm';
    button.textContent = 'Print receipt';
    button.setAttribute('onclick', `printReceipt('${orderId}')`);
    button.disabled = false;
    showToast(`Order #${orderId} paid.`, 'success');
  } catch (err) {
    button.disabled = false;
    showToast(err.message, 'error');
  }
}

/* ---------- Init ---------- */
document.addEventListener('DOMContentLoaded', () => {
  updateCartUI();
  startTimers();
});