(() => {
  const page = document.querySelector('[data-return-details]');
  if (!page) return;
  const tabs = [...page.querySelectorAll('[data-return-tab]')];
  const panels = [...page.querySelectorAll('[data-return-panel]')];
  const storageKey = `return-details:${page.dataset.returnRole || 'sales-supervisor'}:${page.dataset.returnId}:tab`;
  const topbar = document.querySelector('.portal-topbar');
  const offsetHeader = () => page.style.setProperty('--rd-top-offset',
    matchMedia('(max-width:860px)').matches && topbar ? `${Math.ceil(topbar.getBoundingClientRect().height)}px` : '0px');
  window.addEventListener('resize', offsetHeader);
  if (topbar) new ResizeObserver(offsetHeader).observe(topbar);
  offsetHeader();
  const remember = value => { try { sessionStorage.setItem(storageKey, value); } catch { /* Tabs work without storage. */ } };
  const remembered = () => { try { return sessionStorage.getItem(storageKey); } catch { return null; } };
  const showTab = (key, focus = false) => {
    const chosen = tabs.find(tab => tab.dataset.returnTab === key) || tabs[0];
    key = chosen.dataset.returnTab;
    tabs.forEach(tab => {
      const selected = tab === chosen;
      tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
    });
    panels.forEach(panel => { panel.hidden = panel.dataset.returnPanel !== key; });
    const url = new URL(location.href); url.searchParams.set('tab', key);
    history.replaceState(null, '', url); remember(key);
    if (focus) chosen.focus();
  };
  page.classList.add('is-enhanced');
  page.dataset.decisionMode = page.dataset.initialDecision;
  page.querySelector('[data-enable-review]')?.addEventListener('click', () => {
    page.dataset.decisionMode = 'review';
    page.querySelector('.sales-return-review')?.scrollIntoView({block:'center'});
  });
  page.querySelector('.rd-tabs').setAttribute('role', 'tablist');
  tabs.forEach(tab => {
    tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', `return-panel-${tab.dataset.returnTab}`);
    tab.addEventListener('click', event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); showTab(tab.dataset.returnTab);
    });
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let index = tabs.indexOf(tab);
      index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      showTab(tabs[index].dataset.returnTab, true);
    });
  });
  panels.forEach(panel => panel.setAttribute('role', 'tabpanel'));
  page.querySelectorAll('[data-return-switch]').forEach(button => button.addEventListener('click', () => showTab(button.dataset.returnSwitch, true)));
  const initialQuery = new URL(location.href).searchParams;
  showTab(initialQuery.get('tab') || (initialQuery.has('message') || initialQuery.has('error') || page.querySelector('.rd-feedback') ? remembered() : null) || 'overview');

  const confirm = page.querySelector('[data-return-confirm]');
  const confirmButton = confirm.querySelector('[data-confirm-submit]');
  const feedback = page.querySelector('[data-return-action-feedback]');
  const rejection = page.querySelector('.sales-return-rejection');
  if (rejection) rejection.hidden = true;
  let pending = null, sending = false, returnFocus = null;
  const accepted = new WeakSet();
  const labels = {
    approve: ['Approve return request?', 'Confirm approval', 'Record this approval using the reviewed products and notes.'],
    reject: ['Reject return request?', 'Confirm rejection', 'The reseller will receive the required rejection reason.'],
    delivery: ['Confirm replacement delivered?', 'Confirm delivery', 'Record delivery of the released replacement products.'],
    proceed: ['Confirm approved resolution?', 'Confirm next step', 'Clear the existing approved resolution for the next permitted step.'],
    payment: ['Confirm funds received?', 'Confirm funds received', 'Confirm that the submitted funds arrived in the payment provider.'],
    issue: ['Resolve reported issue?', 'Confirm resolution', 'Save the reseller-facing explanation and close this follow-up.'],
    accept: ['Accept replacement offer?', 'Confirm acceptance', 'Accept the displayed products, quantities, and price difference for the current offer.'],
    acknowledge: ['Confirm replacement received?', 'Confirm receipt', 'Record your acknowledgment. Any open issue remains with your Sales contact.'],
  };
  const reveal = form => {
    const panel = form.closest('[data-return-panel]');
    if (panel) showTab(panel.dataset.returnPanel);
    if (form.closest('.sales-return-rejection')) rejection.hidden = false;
    for (let ancestor = form.parentElement; ancestor && ancestor !== page; ancestor = ancestor.parentElement) {
      if (ancestor instanceof HTMLDetailsElement) ancestor.open = true;
    }
  };
  const forms = [...page.querySelectorAll('form[method="post"]')];
  forms.forEach(form => {
    const path = new URL(form.action).pathname;
    form.dataset.returnIntent ||= path.endsWith('/verify-payment') ? 'payment' : path.endsWith('/resolve-issue') ? 'issue' : '';
    if (!labels[form.dataset.returnIntent]) return;
    // Native constraints are checked after revealing the form's tab.
    form.noValidate = true;
    form.querySelectorAll('button[type="submit"]').forEach(button => { button.dataset.keepLabel = 'true'; });
    form.addEventListener('submit', event => {
      if (sending) { event.preventDefault(); return; }
      if (accepted.has(form)) {
        accepted.delete(form); sending = true; remember(form.closest('[data-return-panel]')?.dataset.returnPanel || 'overview');
        page.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
        feedback.textContent = 'Saving return action…'; return;
      }
      event.preventDefault(); reveal(form);
      if (!form.reportValidity()) {
        const invalid = form.querySelector(':invalid');
        feedback.textContent = 'Complete the required fields before continuing.';
        invalid?.scrollIntoView({block:'center'}); invalid?.focus(); return;
      }
      pending = form; returnFocus = event.submitter || document.activeElement;
      const [title, button, description] = form.dataset.returnIntent === 'approve' && page.dataset.returnStatus !== 'submitted'
        ? ['Save revised request?', 'Confirm revised request', 'Record the revised products and review notes.'] : labels[form.dataset.returnIntent];
      confirm.querySelector('#return-confirm-title').textContent = title;
      confirm.querySelector('[data-confirm-description]').textContent = description;
      confirmButton.textContent = button; confirmButton.disabled = false;
      const changed = form.dataset.returnIntent === 'approve' && (
        [...form.querySelectorAll('select[name="product_id"]')].some(input => input.value) ||
        [...form.querySelectorAll('[name="approved_quantity"]')].some(input => input.value !== input.defaultValue));
      confirm.querySelector('[data-confirm-current-offer]').hidden = changed || !['approve','accept'].includes(form.dataset.returnIntent);
      confirm.querySelector('[data-confirm-revised-offer]').hidden = !changed;
      confirm.showModal();
    });
  });
  page.querySelectorAll('.rd-action-bar button[form]').forEach(button => button.addEventListener('click', event => {
    const form = document.getElementById(button.getAttribute('form'));
    if (!form || sending) { event.preventDefault(); return; }
    reveal(form);
  }));
  page.querySelectorAll('[data-receipt-checkout]').forEach(form => {
    form.addEventListener('submit', () => remember('payment'));
  });
  confirm.querySelectorAll('[data-confirm-cancel]').forEach(button => button.addEventListener('click', () => confirm.close()));
  confirm.addEventListener('close', () => { pending = null; returnFocus?.focus(); });
  confirmButton.addEventListener('click', () => {
    if (!pending || sending) return;
    const form = pending; confirmButton.disabled = true;
    accepted.add(form); confirm.close(); form.requestSubmit();
  });
  const photoDialog = page.querySelector('[data-return-photo-dialog]');
  let photoOpener = null;
  page.querySelectorAll('[data-return-photo]').forEach(link => link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); photoOpener = link;
    const img = photoDialog.querySelector('[data-photo-full]'); img.src = link.href; img.alt = link.dataset.photoTitle;
    photoDialog.querySelector('[data-photo-original]').href = link.href;
    photoDialog.showModal();
  }));
  photoDialog.querySelector('[data-photo-close]').addEventListener('click', () => photoDialog.close());
  photoDialog.addEventListener('close', () => photoOpener?.focus());
  page.querySelector('.rd-feedback')?.focus({preventScroll:true});
})();
