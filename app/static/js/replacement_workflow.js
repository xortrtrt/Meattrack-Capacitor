(() => {
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const headers = () => ({'X-CSRF-Token':csrf(),Accept:'application/json'});
  const uuid = () => {
    if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(value => value.toString(16).padStart(2,'0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  };
  const money = value => new Intl.NumberFormat('en-PH',{style:'currency',currency:'PHP'}).format(Number(value));
  const request = document.querySelector('.return-request-form[action$="submit"]');
  if (request) {
    const selection = request.querySelector('[data-replacement-selection]');
    const token = request.elements.comparison_token;
    const output = request.querySelector('[data-replacement-comparison]');
    const button = request.querySelector('[data-return-request-submit]');
    const buttonLabel = button.querySelector('[data-return-submit-label]');
    const feedback = request.querySelector('[data-return-request-feedback]');
    const catalog = selection.querySelector('[data-replacement-catalog]');
    const search = selection.querySelector('[data-replacement-search]');
    const rows = [...selection.querySelectorAll('[data-replacement-product]')];
    const quantities = [...request.querySelectorAll('[name="quantity"]')];
    const replacements = rows.map(row => row.querySelector('[name="replacement_quantity"]'));
    let revision = 0, calculating = false;
    // Validate explicitly so comparison review can happen before required photos/details.
    request.noValidate = true;
    const message = (text, error = false) => {
      feedback.textContent = text;
      feedback.classList.toggle('is-error', error);
    };
    const updateButton = () => {
      button.disabled = calculating;
      buttonLabel.textContent = calculating ? 'Calculating…' : !selection.hidden && !token.value ? 'Review price comparison' : 'Request return';
    };
    const updateSelected = () => {
      const list = selection.querySelector('[data-replacement-selected]');
      list.replaceChildren();
      let packs = 0, products = 0;
      rows.forEach((row, index) => {
        const quantity = Number(replacements[index].value);
        if (quantity <= 0) return;
        products += 1; packs += quantity;
        const item = document.createElement('li');
        const name = document.createElement('span'); name.textContent = row.dataset.replacementProduct;
        const count = document.createElement('strong'); count.textContent = `${quantity} pack${quantity === 1 ? '' : 's'}`;
        item.append(name, count); list.append(item);
      });
      selection.querySelector('[data-replacement-count]').textContent = products ? `${products} product${products === 1 ? '' : 's'} · ${packs} pack${packs === 1 ? '' : 's'} selected` : 'No products selected';
    };
    const reset = () => {
      revision += 1;
      token.value = '';
      selection.hidden = request.elements.resolution.value !== 'replace';
      output.replaceChildren();
      quantities.concat(replacements).forEach(input => input.setCustomValidity(''));
      message(selection.hidden ? 'Add returned packs, a reason, and supporting photos.' : 'Choose replacement products, then review the price comparison.');
      updateSelected(); updateButton();
    };
    request.addEventListener('input', event => {
      if (['resolution','quantity','replacement_quantity'].includes(event.target.name)) reset();
      else if (event.target !== search) {
        event.target.setCustomValidity?.('');
        message(token.value ? 'Comparison ready. Add your details, then request the return.' : 'Complete the required details before requesting the return.');
      }
    });
    search.addEventListener('input', () => {
      const query = search.value.trim().toLocaleLowerCase();
      let matches = 0;
      rows.forEach(row => {
        row.hidden = !row.dataset.replacementProduct.toLocaleLowerCase().includes(query);
        if (!row.hidden) matches += 1;
      });
      selection.querySelector('[data-replacement-empty]').hidden = matches > 0;
    });
    search.addEventListener('keydown', event => {
      if (event.key === 'Enter') event.preventDefault();
    });
    const reveal = input => {
      if (selection.contains(input)) {
        catalog.open = true;
        // A selected product must remain reachable even after a different search.
        rows.forEach(row => { row.hidden = false; }); search.value = '';
        selection.querySelector('[data-replacement-empty]').hidden = true;
      }
      input.scrollIntoView({block:'center'});
      input.focus(); input.reportValidity();
    };
    const validatePacks = (inputs, text) => {
      inputs.forEach(input => input.setCustomValidity(''));
      const invalid = inputs.find(input => !input.validity.valid);
      if (invalid) { message(invalid.validationMessage, true); reveal(invalid); return false; }
      if (!inputs.some(input => Number(input.value) > 0)) {
        message(text, true);
        if (inputs[0]) { inputs[0].setCustomValidity(text); reveal(inputs[0]); }
        return false;
      }
      return true;
    };
    const compare = async () => {
      const version = revision;
      const data = new FormData();
      for (const [key,value] of new FormData(request)) {
        if (['order_id','allocation_id','quantity','replacement_product_id','replacement_quantity'].includes(key)) data.append(key,value);
      }
      calculating = true; updateButton(); message('Calculating the current comparison…');
      try {
        const response = await fetch('/portal/reseller/returns/replacement-preview',{method:'POST',headers:headers(),body:data,signal:AbortSignal.timeout(20000)});
        const result = await response.json().catch(() => ({}));
        if (version !== revision) return;
        if (!response.ok) throw new Error(result.error || 'The comparison could not be loaded. Try again.');
        output.replaceChildren();
        const heading = document.createElement('h4'); heading.textContent = 'Price comparison'; output.append(heading);
        const totals = document.createElement('dl'); totals.className = 'return-comparison-totals';
        for (const [label, value] of [['Return credit',result.credit],['Replacement value',result.total],['Extra payment',result.additional_due],['Difference refund',result.refund_due]]) {
          const row = document.createElement('div');
          const name = document.createElement('dt'); name.textContent = label;
          const amount = document.createElement('dd'); amount.textContent = money(value);
          row.append(name,amount); totals.append(row);
        }
        output.append(totals);
        const details = document.createElement('details'); details.className = 'return-pricing-help';
        const summary = document.createElement('summary'); summary.textContent = 'Price details'; details.append(summary);
        const list = document.createElement('ul');
        for (const item of result.originals) {
          const row = document.createElement('li');
          row.textContent = `Returned: ${item.name} · ${item.quantity} packs × ${money(item.unit_price)} = ${money(item.line_total)}`;
          list.append(row);
        }
        for (const item of result.items) {
          const row = document.createElement('li');
          row.textContent = `Replacement: ${item.name} · ${item.quantity} packs = ${money(item.line_total)}`;
          for (const part of item.pricing_components) {
            const detail = document.createElement('small');
            detail.textContent = `${part.quantity} × ${money(part.unit_price)} (${part.basis === 'original' ? 'original paid price' : 'current catalog price'})`;
            row.append(detail);
          }
          list.append(row);
        }
        details.append(list); output.append(details);
        token.value = result.token; catalog.open = false;
        message('Comparison ready. Check the totals, add details, then request the return.');
        output.scrollIntoView({block:'center'}); output.focus({preventScroll:true});
      } catch (error) {
        if (version === revision) message(error.name === 'TimeoutError' || error instanceof TypeError ? 'Could not load the comparison. Check your connection and try again.' : error.message, true);
      } finally { calculating = false; updateButton(); }
    };
    request.addEventListener('submit', event => {
      if (calculating) { event.preventDefault(); return; }
      if (!validatePacks(quantities, 'Enter at least one pack to return.')) { event.preventDefault(); return; }
      if (!selection.hidden) {
        if (!validatePacks(replacements, 'Choose at least one replacement pack.')) { event.preventDefault(); return; }
        if (!token.value) { event.preventDefault(); compare(); return; }
      }
      const photos = request.elements.evidence;
      photos.setCustomValidity('');
      if (photos.files.length > 3) photos.setCustomValidity('Choose up to 3 supporting photos.');
      else if ([...photos.files].some(file => file.size > 5 * 1024 * 1024)) photos.setCustomValidity('Each supporting photo must be 5 MB or smaller.');
      const invalid = [...request.elements].find(input => input.willValidate && !input.validity.valid);
      if (invalid) {
        event.preventDefault();
        const label = invalid === photos ? 'Supporting photos' : invalid.name === 'remarks' ? 'Reason and remarks' : 'Quantity';
        message(`${label}: ${invalid.validationMessage}`, true); reveal(invalid); return;
      }
      button.disabled = true; buttonLabel.textContent = 'Sending request…';
      message('Sending your return request…');
    });
    reset();
  }
  document.querySelectorAll('[data-replacement-payment]').forEach(form => {
    const file = form.elements.payment_proofs;
    const status = form.querySelector('[data-replacement-scan-status]');
    let scanRevision = 0;
    file.addEventListener('change', async () => {
      const revision = ++scanRevision;
      form.elements.receipt_scan_token.value = '';
      form.elements.payment_details_confirmed.checked = false;
      if (!file.files.length) return;
      status.textContent = 'Recognizing the receipt. You can check and correct its details afterwards…';
      try {
        const data = new FormData();
        data.append('payment_proofs',file.files[0]);
        const response = await fetch(`/portal/reseller/returns/${form.dataset.returnId}/receipt-scan`,{method:'POST',headers:headers(),body:data});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Receipt recognition failed. Choose the file again to retry.');
        if (revision !== scanRevision) return;
        form.elements.receipt_scan_token.value = result.scan_token;
        for (const field of ['transaction_number','payment_date','payment_time','payment_amount']) {
          if (result[field]) form.elements[field].value = result[field];
        }
        status.textContent = 'Check or correct the reference, date, time, and amount, then confirm they match the receipt.';
      } catch (error) { if (revision === scanRevision) status.textContent = error.message; }
    });
    form.addEventListener('submit', event => {
      if (!form.elements.receipt_scan_token.value) {
        event.preventDefault(); status.textContent = 'Wait for receipt recognition, or choose the file again to retry.';
      }
    });
  });
  const chat = document.querySelector('[data-replacement-conversation]');
  if (!chat) return;
  const base = `/api/portal/${chat.dataset.role}/returns/${chat.dataset.returnId}`;
  const log = chat.querySelector('[data-replacement-message-log]');
  const error = chat.querySelector('[data-replacement-message-error]');
  const feedback = error.parentElement;
  const retry = chat.querySelector('[data-replacement-retry]');
  const form = chat.querySelector('[data-replacement-message-form]');
  const input = form.elements.content;
  const listToggle = chat.querySelector('[data-replacement-list-toggle]');
  const selectedRow = chat.querySelector('[data-replacement-thread-id][aria-current="page"]');
  let last = 0, refreshing = false, sending = false, closed = false;
  let clientId = null, pendingContent = null, readThrough = 0, loadError = '', sendError = '';
  function showUnread(count) {
    if (!selectedRow) return;
    let badge = selectedRow.querySelector('[data-replacement-thread-unread]');
    if (!count) { badge?.remove(); return; }
    if (!badge) { badge = document.createElement('b'); badge.dataset.replacementThreadUnread = ''; selectedRow.append(badge); }
    badge.textContent = count > 99 ? '99+' : String(count);
    badge.setAttribute('aria-label',`${count} unread messages`);
  }
  function showFeedback() {
    error.textContent = loadError || sendError || (closed ? 'This declined request is read-only.' : '');
    retry.hidden = !loadError;
    feedback.hidden = !error.textContent;
  }
  function showList(open) {
    chat.classList.toggle('is-list-open',open);
    chat.dispatchEvent(new CustomEvent('replacement:list',{detail:{open}}));
    listToggle.setAttribute('aria-expanded',String(open));
    if (open) selectedRow?.focus();
    else { listToggle.focus(); markVisible().catch(() => {}); }
  }
  listToggle.addEventListener('click',() => showList(true));
  selectedRow?.addEventListener('click',event => {
    if ((!chat.matches('[data-reseller-inbox]') && !matchMedia('(max-width:760px)').matches) || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); showList(false);
  });
  chat.addEventListener('keydown',event => {
    if (event.key === 'Escape' && chat.classList.contains('is-list-open')) { event.preventDefault(); showList(false); }
  });
  async function markVisible() {
    if (document.hidden || !log.getClientRects().length) return;
    const viewport = log.getBoundingClientRect();
    const top = Math.max(0,viewport.top), bottom = Math.min(innerHeight,viewport.bottom);
    if (bottom <= top) return;
    const visible = [...log.querySelectorAll('[data-message-id]')].filter(node => {
      const rect = node.getBoundingClientRect();
      return rect.top < bottom && rect.bottom > top;
    });
    const through = Math.max(0,...visible.map(node => Number(node.dataset.messageId)));
    if (through <= readThrough) return;
    const response = await fetch(`${base}/read`,{method:'POST',headers:{...headers(),'Content-Type':'application/json'},body:JSON.stringify({through_id:through})});
    if (response.ok) {
      readThrough = Math.max(readThrough,through);
      showUnread((await response.json()).unread_count);
    }
  }
  function appendMessage(message) {
    const article = document.createElement('article');
    article.dataset.messageId = message.message_id;
    const mine = Number(message.sender_account_id) === Number(chat.dataset.actor);
    article.className = `reseller-chat-message${mine ? ' is-mine' : ''}${message.sender_account_id == null ? ' is-system' : ''}`;
    const name = document.createElement('small'); name.textContent = mine ? 'You' : message.sender_name;
    const text = document.createElement('p'); text.textContent = message.content;
    const date = document.createElement('time');
    date.dateTime = message.created_at;
    date.textContent = new Date(message.created_at).toLocaleString('en-PH',{timeZone:'Asia/Manila',month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'});
    article.append(name,text,date); log.append(article); last = message.message_id;
    if (selectedRow) selectedRow.querySelector('[data-replacement-thread-preview]').textContent = message.content;
  }
  async function refresh() {
    if (refreshing || document.hidden) return;
    refreshing = true;
    try {
      const response = await fetch(`${base}/messages?after_id=${last}`,{headers:headers()});
      if (!response.ok) throw new Error('The conversation is unavailable. Retry loading or refresh to check your access.');
      const data = await response.json();
      showUnread(data.unread_count);
      const atBottom = log.scrollHeight-log.scrollTop-log.clientHeight < 60;
      log.querySelector('[data-replacement-message-loading]')?.remove();
      for (const message of data.messages) {
        if (message.message_id <= last) continue;
        log.querySelector('[data-replacement-message-empty]')?.remove();
        appendMessage(message);
      }
      if (!last && !log.children.length) {
        const empty = document.createElement('p');
        empty.className = 'replacement-message-empty'; empty.dataset.replacementMessageEmpty = '';
        empty.textContent = 'Start the conversation. Discuss your return, replacement, or delivery here.';
        log.append(empty);
      }
      if (atBottom) log.scrollTop = log.scrollHeight;
      await markVisible();
      closed = data.closed;
      form.hidden = closed;
      chat.querySelector('.replacement-composer-help').hidden = closed;
      loadError = '';
    } catch (problem) { loadError = problem.message; }
    finally {
      refreshing = false; log.setAttribute('aria-busy','false');
      log.querySelector('[data-replacement-message-loading]')?.remove(); showFeedback();
    }
  }
  retry.addEventListener('click',refresh);
  function resizeInput() {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight,96)}px`;
  }
  input.addEventListener('input',resizeInput);
  form.addEventListener('submit',async event => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || sending || closed) return;
    if (pendingContent !== text) { clientId = uuid(); pendingContent = text; }
    const data = new FormData(); data.append('content',text); data.append('client_message_id',clientId);
    const button = form.querySelector('button'); button.disabled = true; sending = true;
    try {
      const response = await fetch(`${base}/messages`,{method:'POST',headers:headers(),body:data});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The message could not be sent. Retry to send the same message safely.');
      // Keep any text entered while the previous message was being sent.
      if (input.value.trim() === text) { input.value = ''; resizeInput(); }
      clientId = null; pendingContent = null; sendError = ''; await refresh();
    } catch (problem) { sendError = problem.message; }
    finally { button.disabled = false; sending = false; showFeedback(); }
  });
  input.addEventListener('keydown',event => {
    if (event.key==='Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
  });
  document.addEventListener('visibilitychange',refresh);
  log.addEventListener('scroll',() => { markVisible().catch(() => {}); },{passive:true});
  window.addEventListener('scroll',() => { markVisible().catch(() => {}); },{passive:true});
  window.addEventListener('focus',refresh);
  setInterval(refresh,3000); refresh();
})();
