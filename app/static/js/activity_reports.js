(() => {
  window.lucide?.createIcons();
  document.querySelectorAll('[data-period-start]').forEach(button => {
    button.addEventListener('click', () => {
      document.getElementById('ar-start').value = button.dataset.periodStart;
      document.getElementById('ar-end').value = button.dataset.periodEnd;
    });
  });
  const tablist = document.querySelector('[data-report-tabs]');
  if (tablist) {
    const tabs = [...tablist.querySelectorAll('[data-report-tab]')];
    const panels = [...document.querySelectorAll('[data-report-panel]')];
    tablist.setAttribute('role', 'tablist');
    function activate(index, focus = false) {
      tabs.forEach((tab, i) => {
        tab.setAttribute('role', 'tab');
        tab.setAttribute('aria-controls', `ar-panel-${tab.dataset.reportTab}`);
        tab.setAttribute('aria-selected', String(i === index));
        tab.tabIndex = i === index ? 0 : -1;
      });
      panels.forEach(panel => {
        panel.setAttribute('role', 'tabpanel');
        panel.hidden = panel.dataset.reportPanel !== tabs[index].dataset.reportTab;
      });
      if (focus) tabs[index].focus();
    }
    tabs.forEach((tab, i) => {
      tab.addEventListener('click', event => { event.preventDefault(); activate(i); });
      tab.addEventListener('keydown', event => {
        const destinations = { ArrowRight: (i + 1) % tabs.length, ArrowLeft: (i + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 };
        if (event.key in destinations) { event.preventDefault(); activate(destinations[event.key], true); }
      });
    });
    activate(0);
  }
  document.querySelectorAll('[data-print-report]').forEach(button => button.addEventListener('click', () => window.print()));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') document.querySelectorAll('.ar-csv-menu[open]').forEach(menu => { menu.open = false; menu.querySelector('summary').focus(); });
  });
  const form = document.querySelector('[data-activity-report-form]');
  form?.addEventListener('submit', event => {
    if (!event.submitter?.matches('[data-submit-report]')) return;
    if (form.dataset.submitting) { event.preventDefault(); return; }
    form.dataset.submitting = 'true';
    event.submitter.setAttribute('aria-disabled', 'true');
    event.submitter.textContent = 'Sending report…';
  });
})();
