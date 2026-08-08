(() => {
  if (document.body.dataset.authenticated !== 'true') return;

  const ENDPOINT = '/api/events/batch';
  const STORAGE_KEY = 'lumalearn:event-queue:v1';
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const productId = document.body.dataset.productId || null;
  let queue = [];
  let activeStarted = performance.now();
  let activeMs = 0;
  let sending = false;

  try { queue = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]').slice(-50); } catch (_) { queue = []; }

  const id = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const persist = () => {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(queue.slice(-50))); } catch (_) {}
  };

  function track(eventType, details = {}) {
    queue.push({
      client_event_id: id(),
      event_type: eventType,
      product_id: details.product_id || null,
      path: `${location.pathname}${location.search}`.slice(0, 500),
      query: details.query || null,
      duration_ms: details.duration_ms || null,
      metadata: details.metadata || {},
      created_at: new Date().toISOString(),
    });
    queue = queue.slice(-50);
    persist();
    if (queue.length >= 10) flush();
  }

  async function flush(useBeacon = false) {
    if (!queue.length || sending) return;
    const batch = queue.splice(0, 50);
    persist();
    const payload = JSON.stringify({events: batch, csrf_token: csrf});
    if (useBeacon && navigator.sendBeacon) {
      const accepted = navigator.sendBeacon(ENDPOINT, new Blob([payload], {type: 'application/json'}));
      if (!accepted) { queue.unshift(...batch); persist(); }
      return;
    }
    sending = true;
    try {
      const response = await fetch(ENDPOINT, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: payload,
        keepalive: true,
        credentials: 'same-origin',
      });
      if (!response.ok) throw new Error(`event batch ${response.status}`);
    } catch (_) {
      queue.unshift(...batch);
      queue = queue.slice(-50);
      persist();
    } finally {
      sending = false;
    }
  }

  const searchQuery = document.body.dataset.searchQuery?.trim();
  if (productId) track('product_view', {product_id: productId});
  else track('catalog_view', {metadata: {route: document.body.dataset.page}});
  if (searchQuery) track('search', {query: searchQuery});

  document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-track-product]');
    if (!target) return;
    const source = target.dataset.trackSource || 'catalog';
    track(source === 'recommendation' ? 'recommendation_click' : 'product_click', {
      product_id: target.dataset.trackProduct,
      metadata: {source},
    });
  }, {capture: true});

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      activeMs += performance.now() - activeStarted;
    } else {
      activeStarted = performance.now();
    }
  });

  function closeSession() {
    if (!document.hidden) activeMs += performance.now() - activeStarted;
    if (activeMs >= 5000) track('dwell', {product_id: productId, duration_ms: Math.round(activeMs)});
    flush(true);
  }

  window.addEventListener('pagehide', closeSession);
  window.setInterval(() => flush(false), 5000);
})();
