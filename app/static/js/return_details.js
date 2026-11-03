(() => {
  function bindConfirmation(root, {reveal = () => {}, remember = () => {}} = {}) {
    const confirm = root.querySelector('[data-return-confirm]') || document.querySelector('[data-return-confirm]');
    if (!confirm) return;
    const confirmButton = confirm.querySelector('[data-confirm-submit]');
    const feedback = root.querySelector('[data-return-action-feedback]');
    const accepted = new WeakSet();
    let pending = null, sending = false, returnFocus = null;
    const labels = {
      approve: ['Approve return request?', 'Confirm approval', 'Record this approval using the reviewed products and notes.'],
      reject: ['Reject return request?', 'Confirm rejection', 'The reseller will receive the required rejection reason.'],
      delivery: ['Confirm replacement delivered?', 'Confirm delivery', 'Record delivery of the released replacement products.'],
      proceed: ['Confirm approved resolution?', 'Confirm next step', 'Clear the existing approved resolution for the next permitted step.'],
      payment: ['Confirm funds received?', 'Confirm funds received', 'Confirm that the submitted funds arrived in the payment provider.'],
      issue: ['Resolve reported issue?', 'Confirm resolution', 'Save the reseller-facing explanation and close this follow-up.'],
      accept: ['Accept replacement offer?', 'Confirm acceptance', 'Accept the displayed products, quantities, and price difference for the current offer.'],
      acknowledge: ['Confirm replacement received?', 'Confirm receipt', 'Record your acknowledgment. Any open issue remains with your Sales contact.'],
      'convert-to-refund': ['Convert replacement to refund?', 'Confirm refund conversion', 'Stop replacement preparation and release.'],
    };
    const postAction = async form => {
      const status = form.querySelector('[data-conversion-feedback], [data-return-form-feedback]');
      status.textContent = 'Recording return action…';
      status.classList.remove('is-error'); status.setAttribute('role', 'status');
      form.setAttribute('aria-busy', 'true');
      try {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content || form.elements.csrf_token.value;
        const response = await fetch(form.action, {method: 'POST', headers: {'Accept': 'application/json', 'X-CSRF-Token': csrf},
          body: new FormData(form), signal: AbortSignal.timeout(20000)});
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.redirect) throw new Error(result.error || 'The action could not be confirmed. Reload or retry with the same reference.');
        window.PortalForms.allowNavigation(); location.assign(result.redirect);
      } catch (error) {
        status.textContent = error.name === 'TimeoutError' || error instanceof TypeError
          ? 'The outcome is not confirmed. Check your connection and retry; this reference prevents duplicate actions.' : error.message;
        status.classList.add('is-error'); status.setAttribute('role', 'alert');
        sending = false; form.removeAttribute('aria-busy');
        root.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = false; });
      }
    };
    root.querySelectorAll('form[method="post"]').forEach(form => {
      const path = new URL(form.action).pathname;
      form.dataset.returnIntent ||= path.endsWith('/verify-payment') ? 'payment' : path.endsWith('/resolve-issue') ? 'issue' : '';
      if (!labels[form.dataset.returnIntent]) return;
      form.dataset.returnConfirmationBound = 'true';
      form.noValidate = true;
      const explanation = form.elements.customer_message;
      if (explanation && form.hasAttribute('data-alternative-explanation')) {
        const alwaysRequired = explanation.required;
        const updateRequirement = () => {
          explanation.required = alwaysRequired || [...form.querySelectorAll('select[name="product_id"]')].some(input => input.value);
        };
        form.querySelectorAll('select[name="product_id"]').forEach(input => input.addEventListener('change', updateRequirement));
        updateRequirement();
      }
      form.querySelectorAll('button[type="submit"]').forEach(button => { button.dataset.keepLabel = 'true'; });
      form.addEventListener('submit', event => {
        if (sending) { event.preventDefault(); return; }
        if (accepted.has(form)) {
          accepted.delete(form); sending = true;
          remember(form.closest('[data-return-panel]')?.dataset.returnPanel || 'overview');
          root.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
          if (feedback) feedback.textContent = 'Saving return action…';
          if (form.dataset.returnIntent === 'convert-to-refund' || form.hasAttribute('data-preserve-return-action')) { event.preventDefault(); postAction(form); }
          return;
        }
        event.preventDefault(); reveal(form);
        if (!window.PortalForms.validate(form)) {
          if (feedback) feedback.textContent = 'Complete the required fields before continuing.';
          return;
        }
        pending = form; returnFocus = event.submitter || document.activeElement;
        let [title, label, description] = form.dataset.returnIntent === 'approve' && root.dataset.returnStatus !== 'submitted'
          ? ['Save revised request?', 'Confirm revised request', 'Record the revised products and review notes.'] : labels[form.dataset.returnIntent];
        if (form.dataset.returnIntent === 'convert-to-refund') {
          description = `${description} Total refund: ${form.dataset.refundLabel}. Returned goods must be verified before settlement.`;
        }
        description = `Return #${form.elements.return_id.value}: ${description}`;
        confirm.querySelector('#return-confirm-title').textContent = title;
        confirm.querySelector('[data-confirm-description]').textContent = description;
        confirmButton.textContent = label; confirmButton.disabled = false;
        const changed = form.dataset.returnIntent === 'approve' && (
          [...form.querySelectorAll('select[name="product_id"]')].some(input => input.value) ||
          [...form.querySelectorAll('[name="approved_quantity"]')].some(input => input.value !== input.defaultValue));
        confirm.querySelector('[data-confirm-current-offer]').hidden = changed || !['approve','accept'].includes(form.dataset.returnIntent);
        confirm.querySelector('[data-confirm-revised-offer]').hidden = !changed;
        confirm.showModal(); confirm.querySelector('[data-confirm-safe]').focus();
      });
    });
    root.querySelectorAll('.rd-action-bar button[form]').forEach(button => button.addEventListener('click', event => {
      const form = document.getElementById(button.getAttribute('form'));
      if (!form || sending) { event.preventDefault(); return; }
      reveal(form);
    }));
    confirm.querySelectorAll('[data-confirm-cancel]').forEach(button => button.addEventListener('click', () => confirm.close()));
    confirm.addEventListener('close', () => { pending = null; returnFocus?.focus(); });
    confirmButton.addEventListener('click', () => {
      if (!pending || sending) return;
      const form = pending; confirmButton.disabled = true;
      accepted.add(form); confirm.close(); form.requestSubmit();
    });
  }
  const page = document.querySelector('[data-return-details]');
  if (!page) {
    const surface = document.querySelector('[data-return-confirmation-surface]');
    if (surface) bindConfirmation(surface);
    return;
  }
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

  bindConfirmation(page, {
    remember,
    reveal: form => {
      const panel = form.closest('[data-return-panel]');
      if (panel) showTab(panel.dataset.returnPanel);
      const rejection = form.closest('.sales-return-rejection');
      if (rejection) rejection.hidden = false;
      for (let ancestor = form.parentElement; ancestor && ancestor !== page; ancestor = ancestor.parentElement) {
        if (ancestor instanceof HTMLDetailsElement) ancestor.open = true;
      }
    },
  });
  page.querySelectorAll('[data-receipt-checkout]').forEach(form => {
    form.addEventListener('submit', () => remember('payment'));
  });
  const rejection = page.querySelector('.sales-return-rejection');
  if (rejection) rejection.hidden = true;
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
