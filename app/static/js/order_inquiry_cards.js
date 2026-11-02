/* Read-only order context shared by both reseller composers. Never creates a chat. */
(() => {
  function create(stage, select) {
    const card = stage.querySelector('[data-order-inquiry-card]');
    let revision = 0;
    async function show(orderId) {
      const version = ++revision;
      card.replaceChildren(); card.hidden = !orderId;
      if (!orderId) return;
      const loading = document.createElement('p'); loading.textContent = 'Loading order details...'; card.append(loading);
      try {
        const response = await fetch(`/api/portal/reseller/inbox/orders/${Number(orderId)}`, {cache:'no-store'});
        if (!response.ok) throw new Error('Order details are unavailable. Clear the selection or try again.');
        const order = await response.json();
        if (version !== revision) return;
        card.replaceChildren();
        const head = document.createElement('header');
        const title = document.createElement('strong'); title.textContent = `Order #${order.order_id}`;
        const status = document.createElement('span'); status.className = 'inbox-order-status'; status.textContent = order.status.replaceAll('_',' ');
        const clear = document.createElement('button'); clear.type = 'button'; clear.className = 'inbox-order-clear'; clear.setAttribute('aria-label','Clear order selection'); clear.textContent = '×';
        clear.addEventListener('click', () => { select.value = ''; select.dispatchEvent(new Event('change')); show(''); });
        head.append(title,status,clear); card.append(head);
        const products = document.createElement('p'); products.className = 'inbox-order-products';
        products.textContent = order.items.slice(0,2).map(item => `${item.name} × ${item.quantity}`).join(' · ') || 'No product lines recorded';
        if (order.items.length > 2) products.textContent += ` · +${order.items.length-2} more`;
        card.append(products);
        const foot = document.createElement('footer');
        const total = document.createElement('strong'); total.textContent = new Intl.NumberFormat('en-PH',{style:'currency',currency:'PHP'}).format(Number(order.total_amount));
        const link = document.createElement('a'); link.href = order.details_url; link.target = '_blank'; link.rel = 'noopener'; link.textContent = 'View order details ↗';
        foot.append(total,link); card.append(foot);
        const more = document.createElement('details'); more.className = 'inbox-order-more';
        const toggle = document.createElement('summary'); toggle.textContent = 'More order details'; more.append(toggle);
        const date = document.createElement('small'); date.textContent = `Ordered ${new Date(order.order_date).toLocaleDateString('en-PH',{timeZone:'Asia/Manila',month:'short',day:'numeric',year:'numeric'})}`; more.append(date);
        const list = document.createElement('ul');
        order.items.forEach(item => { const line = document.createElement('li'); line.textContent = `${item.name} × ${item.quantity}`; list.append(line); });
        more.append(list);
        if (order.received_at) { const delivery = document.createElement('small'); delivery.textContent = 'Receipt acknowledged by reseller'; more.append(delivery); }
        else if (order.fulfilled_at) { const delivery = document.createElement('small'); delivery.textContent = 'Order fulfilled'; more.append(delivery); }
        card.append(more);
      } catch (error) { if (version === revision) { card.replaceChildren(); card.textContent = error.message; } }
    }
    select.addEventListener('change', () => show(select.value.replace('order:','')));
    stage.addEventListener('click', event => {
      const reference = event.target.closest('[data-inbox-order-reference]');
      if (!reference || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); show(reference.dataset.inboxOrderReference);
    });
    return {show};
  }
  function badge(orderId) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = 'inbox-order-badge'; button.dataset.inboxOrderReference = String(orderId);
    button.textContent = `Order #${orderId}`; button.setAttribute('aria-label',`Preview Order #${orderId}`);
    return button;
  }
  function reference(orderId, kind = 'order') {
    const row = document.createElement('div'); row.className = 'inbox-order-event'; row.setAttribute('role','note');
    const button = badge(orderId); button.textContent = `Regarding Order #${orderId}`;
    if (kind === 'replacement') {
      delete button.dataset.inboxOrderReference;
      button.dataset.inboxReplacementReference = String(orderId);
      button.textContent = `Regarding Replacement #${orderId}`;
      button.setAttribute('aria-label',`Preview Replacement #${orderId}`);
    }
    row.append(button);
    return row;
  }
  function group(feed) {
    let previous = null;
    feed.querySelectorAll('.reseller-chat-message').forEach(row => {
      row.classList.remove('is-grouped','has-next-in-group');
      const elapsed = previous ? Date.parse(row.dataset.createdAt)-Date.parse(previous.dataset.createdAt) : Infinity;
      const sameDay = previous && new Date(row.dataset.createdAt).toLocaleDateString('en-PH',{timeZone:'Asia/Manila'}) === new Date(previous.dataset.createdAt).toLocaleDateString('en-PH',{timeZone:'Asia/Manila'});
      if (previous && previous.nextElementSibling === row && sameDay && previous.dataset.groupSender === row.dataset.groupSender && elapsed >= 0 && elapsed <= 300000) {
        row.classList.add('is-grouped'); previous.classList.add('has-next-in-group');
      }
      previous = row;
    });
  }
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.inbox-conversation-actions[open]').forEach(menu => { menu.open = false; menu.querySelector('summary').focus(); });
  });
  document.addEventListener('click', event => {
    document.querySelectorAll('.inbox-conversation-actions[open]').forEach(menu => {
      if (!menu.contains(event.target) || event.target.closest('button')) menu.open = false;
    });
  });
  window.OrderInquiryCards = {create,badge,reference,group};
})();
