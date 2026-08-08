(() => {
  const button = document.querySelector('[data-menu-button]');
  const menu = document.querySelector('[data-mobile-menu]');
  if (button && menu) {
    button.addEventListener('click', () => {
      const open = menu.hasAttribute('hidden');
      menu.toggleAttribute('hidden', !open);
      button.setAttribute('aria-expanded', String(open));
    });
  }

  document.querySelectorAll('[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  window.setTimeout(() => {
    document.querySelectorAll('.flash').forEach((flash) => flash.classList.add('flash-out'));
  }, 4500);

  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  const refresh = document.querySelector('[data-refresh-recommendations]');
  if (refresh) {
    refresh.addEventListener('click', async () => {
      refresh.disabled = true;
      refresh.innerHTML = 'Reading your signal… <span class="spin">↻</span>';
      try {
        const response = await fetch('/api/recommendations/refresh', {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
          body: JSON.stringify({csrf_token: csrf}),
        });
        if (!response.ok) throw new Error('Refresh failed');
        refresh.innerHTML = 'Agent is working… <span class="spin">↻</span>';
        window.setTimeout(() => window.location.reload(), 5000);
      } catch (_error) {
        refresh.disabled = false;
        refresh.textContent = 'Could not refresh — try again';
      }
    });
  }

  const digestToggle = document.querySelector('[data-digest-toggle]');
  if (digestToggle) {
    digestToggle.addEventListener('change', async () => {
      digestToggle.disabled = true;
      try {
        const response = await fetch('/api/profile/digest', {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
          body: JSON.stringify({enabled: digestToggle.checked, csrf_token: csrf}),
        });
        if (!response.ok) throw new Error('Preference failed');
      } catch (_error) {
        digestToggle.checked = !digestToggle.checked;
      } finally {
        digestToggle.disabled = false;
      }
    });
  }
})();
