/* Combine authorized histories in the view; retain each source's write/read boundary. */
(() => {
  const root = document.querySelector('[data-reseller-inbox]');
  if (!root) return;
  const stage = root.querySelector('[data-contact-stage]');
  const browser = root.querySelector('.reseller-conversation-browser');
  const directRow = root.querySelector('[data-reseller-conversation-open]');
  const groups = new Map();
  root.querySelectorAll('[data-contact-id]').forEach(row => {
    const key = row.dataset.contactId;
    if (!groups.has(key)) groups.set(key, {row, cases: []});
    const group = groups.get(key);
    group.searchText = `${group.searchText || ''} ${row.textContent} ${row.dataset.inboxSearchText || ''}`;
    group.row.dataset.inboxSearchText = group.searchText;
    group.cases.push(row.dataset.replacementThreadId);
    row.dataset.inboxDuplicate = row === group.row ? '' : '1';
    row.dataset.activity = row.querySelector('time')?.dateTime || '';
    row.addEventListener('click', event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); open(group, row.dataset.replacementThreadId);
    });
    row.querySelector('.replacement-row-reference').textContent = 'Replacement history';
  });
  function sort() {
    [...browser.querySelectorAll('[data-inbox-row]')].sort((a,b) =>
      (Date.parse(b.dataset.activity) || 0) - (Date.parse(a.dataset.activity) || 0)
    ).forEach(row => browser.append(row));
  }
  root.querySelectorAll('[data-inbox-row="system"]').forEach(row => {
    row.dataset.activity = row.querySelector('time')?.dateTime || '';
  });
  root.addEventListener('inbox:sort', sort);
  root.addEventListener('inbox:direct', event => {
    const id = String(event.detail.participant?.team_leader_account_id || '');
    const group = groups.get(id);
    directRow.dataset.inboxDuplicate = group ? '1' : '';
    groups.forEach(item => { item.row.dataset.hasDirect = item === group ? '1' : ''; });
    if (group && requestedOrder) { const orderId = requestedOrder; requestedOrder = null; open(group,group.cases[0]); active.pendingOrder = orderId; }
    root.dispatchEvent(new Event('inbox:list-change')); sort();
  });
  document.addEventListener('conversations:unread', event => {
    const incoming = event.detail.replacement_threads || [];
    groups.forEach(group => {
      const threads = incoming.filter(row => group.cases.includes(String(row.return_id)));
      const count = threads.reduce((sum,row) => sum + row.unread_count,0);
      let badge = group.row.querySelector('[data-replacement-thread-unread]');
      if (!badge && count) { badge = document.createElement('b'); badge.dataset.replacementThreadUnread = ''; group.row.append(badge); }
      if (badge) { badge.hidden = !count; badge.textContent = String(count); }
      const latest = threads.sort((a,b) => Date.parse(b.last_message_at)-Date.parse(a.last_message_at))[0];
      if (latest) {
        group.row.dataset.activity = latest.last_message_at;
        group.row.querySelector('[data-replacement-thread-preview]').textContent = latest.last_message;
      }
    });
    sort();
  });
  const log = stage.querySelector('[data-contact-log]');
  const form = stage.querySelector('form');
  const input = form.querySelector('textarea');
  const media = window.InternalConversationMedia.controls(form);
  const regarding = stage.querySelector('[data-contact-regarding]');
  const caseSelect = stage.querySelector('[data-contact-case]');
  const orderCard = window.OrderInquiryCards.create(stage, regarding);
  let requestedOrder = new URLSearchParams(location.search).get('inquire_order');
  const status = stage.querySelector('[data-contact-status]');
  const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const uuid = () => {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(byte => byte.toString(16).padStart(2,'0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  };
  const states = new Map();
  let active = null, generation = 0, busy = false, sending = false, pending = null;
  root.addEventListener('inbox:inquire-order', event => {
    const group = [...groups.values()].find(item => item.row.dataset.hasDirect === '1');
    if (!group) return;
    if (active?.group !== group || stage.hidden) open(group,group.cases[0]);
    active.pendingOrder = event.detail.orderId;
    if (active.data?.direct) {
      regarding.value = `order:${event.detail.orderId}`; caseSelect.value = '';
      active.pendingOrder = null; orderCard.show(event.detail.orderId); updateComposer();
    }
  });
  const money = value => new Intl.NumberFormat('en-PH',{style:'currency',currency:'PHP'}).format(Number(value));
  async function api(url, options = {}) {
    const response = await fetch(url, {...options, headers:{'X-CSRF-Token':token,...options.headers}});
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.message || data.detail || data.error || 'Could not load messages. Try again.'); error.status = response.status; throw error; }
    return data;
  }
  function notice(text) { status.textContent = text; status.hidden = !text; }
  function options(data) {
    const previous = regarding.value, previousCase = caseSelect.value;
    regarding.replaceChildren(new Option('No order selected',''));
    (data.orders || []).forEach(row => regarding.add(new Option(`Order #${row.order_id}`,`order:${row.order_id}`)));
    if ([...regarding.options].some(option => option.value === previous)) regarding.value = previous;
    regarding.disabled = !data.direct;
    caseSelect.replaceChildren();
    if (data.direct) caseSelect.add(new Option('General inquiry',''));
    data.threads.forEach(row => caseSelect.add(new Option(`Replacement #${row.return_id}${row.status === 'rejected' ? ' (read-only)' : ''}`,String(row.return_id))));
    if ([...caseSelect.options].some(option => option.value === previousCase)) caseSelect.value = previousCase;
    else caseSelect.value = data.direct ? '' : String(active.caseId);
    if (active.pendingOrder && data.orders.some(row => String(row.order_id) === active.pendingOrder)) {
      regarding.value = `order:${active.pendingOrder}`; caseSelect.value = ''; orderCard.show(active.pendingOrder); active.pendingOrder = null;
    }
    updateComposer();
  }
  function updateComposer() {
    const closed = active?.data?.threads.find(row => String(row.return_id) === caseSelect.value)?.status === 'rejected';
    const direct = !caseSelect.value && Boolean(active?.data?.direct);
    input.maxLength = direct ? 500 : 1000;
    stage.querySelector('[data-contact-summary]').hidden = direct && !active?.previewCaseId;
    input.disabled = Boolean(closed || sending);
    form.querySelector('button[type="submit"]').disabled = Boolean(closed || sending);
    // Case APIs support text; direct APIs retain existing private attachments.
    form.querySelector('.internal-media-picker')?.toggleAttribute('hidden', !direct);
    media.disable(!direct || Boolean(closed || sending));
    if (closed) notice('This declined replacement is read-only. Choose another context to reply.');
  }
  regarding.addEventListener('change', () => { if (regarding.value) caseSelect.value = ''; media.clear(); pending = null; updateComposer(); });
  caseSelect.addEventListener('change', () => { active.previewCaseId = null; regarding.value = ''; orderCard.show(''); media.clear(); pending = null; updateComposer(); refresh(true); });
  stage.addEventListener('click', event => {
    const reference = event.target.closest('[data-inbox-replacement-reference]');
    if (!reference || !active) return;
    active.previewCaseId = reference.dataset.inboxReplacementReference;
    stage.querySelector('[data-contact-summary]').hidden = false;
    stage.querySelector('[data-contact-summary]').open = true;
    refresh(true);
  });
  function render() {
    if (!active) return;
    const nearBottom = log.scrollHeight-log.scrollTop-log.clientHeight < 65;
    const previousTop = log.scrollTop;
    log.replaceChildren();
    let day = '';
    [...active.messages.values()].sort((a,b) => Date.parse(a.created_at)-Date.parse(b.created_at) || a.id-b.id).forEach(message => {
      const date = new Date(message.created_at);
      const dateLabel = date.toLocaleDateString('en-PH',{timeZone:'Asia/Manila',month:'short',day:'numeric',year:'numeric'});
      if (day !== dateLabel) { const divider = document.createElement('p'); divider.className = 'inbox-date-divider'; divider.textContent = dateLabel; log.append(divider); day = dateLabel; }
      const row = document.createElement('article'); row.className = 'reseller-chat-message';
      row.classList.toggle('is-mine', message.mine); row.dataset.source = message.source;
      row.dataset.id = message.id; row.dataset.caseId = message.return_id || '';
      row.dataset.groupSender = String(message.sender_account_id); row.dataset.createdAt = message.created_at;
      const name = document.createElement('small'); name.className = 'inbox-message-author'; name.textContent = message.mine ? 'You' : message.sender_name;
      const context = document.createElement('small'); context.textContent = message.return_id ? `Replacement #${message.return_id}` : '';
      const text = document.createElement('p'); text.textContent = message.content;
      const time = document.createElement('time'); time.dateTime = message.created_at; time.textContent = date.toLocaleTimeString('en-PH',{timeZone:'Asia/Manila',hour:'numeric',minute:'2-digit'});
      row.append(name); if (context.textContent) row.append(context);
      if (message.source === 'case' && message.return_id) log.append(window.OrderInquiryCards.reference(message.return_id,'replacement'));
      else if (message.order_id) log.append(window.OrderInquiryCards.reference(message.order_id));
      if (message.content) row.append(text);
      window.InternalConversationMedia.renderAttachments(row,message.attachments || []); row.append(time); log.append(row);
    });
    if (!log.children.length) { const empty = document.createElement('p'); empty.textContent = 'No messages in this view.'; log.append(empty); }
    window.OrderInquiryCards.group(log);
    log.scrollTop = nearBottom ? log.scrollHeight : previousTop;
  }
  async function markVisible() {
    if (!active || stage.hidden || document.hidden || !log.getClientRects().length) return;
    const rect = log.getBoundingClientRect(), cursors = new Map();
    const state = active;
    state.readThrough ||= new Map();
    log.querySelectorAll('[data-id]').forEach(row => {
      const box = row.getBoundingClientRect();
      if (box.bottom <= rect.top || box.top >= Math.min(rect.bottom,innerHeight)) return;
      const key = row.dataset.source === 'direct' ? 'direct' : row.dataset.caseId;
      cursors.set(key, Math.max(cursors.get(key) || 0, Number(row.dataset.id)));
    });
    await Promise.all([...cursors].filter(([key,id]) => id > (state.readThrough.get(key) || 0)).map(async ([key,id]) => {
      await api(key === 'direct' ? '/api/portal/reseller/inbox/read' : `/api/portal/reseller/returns/${key}/read`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({through_id:id})});
      state.readThrough.set(key,id); window.refreshConversationBadges?.();
    }));
  }
  async function refresh(force = false) {
    if (!active || busy || document.hidden || (!force && stage.hidden)) return;
    const state = active, revision = generation; busy = true;
    try {
      const selected = state.previewCaseId || caseSelect.value || state.caseId;
      const data = await api(`/api/portal/reseller/inbox/contacts/${selected}?after_id=${state.last}&direct_after_id=${state.directLast}`);
      if (revision !== generation) return;
      const previousCases = state.data?.threads.map(row => row.return_id).sort().join(',');
      const currentCases = data.threads.map(row => row.return_id).sort().join(',');
      const changed = previousCases != null && previousCases !== currentCases;
      state.data = data;
      state.group.cases = data.threads.map(row => String(row.return_id));
      if (changed) {
        state.last = 0;
        for (const [key,message] of state.messages) if (message.source === 'case') state.messages.delete(key);
      }
      stage.querySelector('[data-contact-delete]').hidden = !data.direct?.conversation;
      const allowed = new Set(data.threads.map(row => row.return_id));
      for (const [key,message] of state.messages) {
        if ((message.source === 'direct' && !data.direct) || (message.source === 'case' && !allowed.has(message.return_id))) state.messages.delete(key);
      }
      if (!sending) notice(state.sendError || '');
      if (!changed) data.messages.forEach(message => { state.messages.set(`case:${message.message_id}`,{...message,source:'case',id:message.message_id,mine:message.sender_account_id === data.direct?.participant?.reseller_account_id || message.sender_account_id === state.actor}); state.last = Math.max(state.last,message.message_id); });
      (data.direct?.messages || []).forEach(message => { state.messages.set(`direct:${message.reseller_message_id}`,{...message,source:'direct',id:message.reseller_message_id,mine:message.sender_account_type === 'reseller'}); state.directLast = Math.max(state.directLast,message.reseller_message_id); });
      options(data); render();
      const context = stage.querySelector('[data-contact-context]'); context.replaceChildren();
      const link = document.createElement('a'); link.href = `/portal/reseller/history?view=returns&case=${selected}`; link.textContent = `Replacement #${selected} · Order #${data.context.order_id} · Open return details`; context.append(link);
      stage.querySelector('[data-contact-summary] summary').textContent = `Replacement #${selected} - ${data.context.status.replaceAll('_',' ')}`;
      const totals = document.createElement('dl');
      [['Return credit','credit'],['Replacement value','replacement_total'],['Additional payment','additional_due'],['Difference refund','unused_credit']].forEach(([label,key]) => { const row = document.createElement('div'); const dt = document.createElement('dt'); dt.textContent = label; const dd = document.createElement('dd'); dd.textContent = money(data.context[key]); row.append(dt,dd); totals.append(row); }); context.append(totals);
      const latest = [...state.messages.values()].sort((a,b) => Date.parse(b.created_at)-Date.parse(a.created_at))[0];
      if (latest) {
        state.group.row.dataset.activity = latest.created_at;
        state.group.row.querySelector('[data-replacement-thread-preview]').textContent = latest.content || 'Attachment';
        const time = state.group.row.querySelector('time');
        if (time) { time.dateTime = latest.created_at; time.textContent = new Date(latest.created_at).toLocaleString('en-PH',{timeZone:'Asia/Manila',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}); }
        sort();
      }
      await markVisible();
    } catch (error) {
      if (revision === generation) {
        if ([401,403,404].includes(error.status)) { state.messages.clear(); render(); input.disabled = true; form.querySelector('[type="submit"]').disabled = true; }
        notice(`${error.message} Refresh conversations to retry.`);
      }
    }
    finally { busy = false; }
  }
  function open(group, caseId) {
    if (active) { active.draft = input.value; media.clear(); }
    generation += 1;
    const key = group.row.dataset.contactId;
    if (!states.has(key)) states.set(key,{group,caseId,last:0,directLast:0,messages:new Map(),actor:Number(document.querySelector('[data-contact-actor]')?.dataset.contactActor),draft:''});
    active = states.get(key); active.caseId = caseId; active.previewCaseId = null;
    root.dispatchEvent(new Event('inbox:contact-open'));
    group.row.classList.add('is-active');
    stage.querySelector('[data-contact-name]').textContent = group.row.querySelector('strong').textContent;
    stage.querySelector('[data-contact-avatar]').replaceChildren(group.row.querySelector('.internal-person-avatar').cloneNode(true));
    regarding.replaceChildren(new Option('No order selected','')); orderCard.show('');
    caseSelect.replaceChildren(new Option('General inquiry','')); input.value = active.draft;
    render(); notice('Loading messages...'); refresh(true);
  }

  log.addEventListener('scroll', () => markVisible().catch(() => {}),{passive:true});
  stage.querySelector('[data-contact-back]').addEventListener('click', () => { root.dispatchEvent(new Event('inbox:close')); active?.group.row.focus(); });
  stage.querySelector('[data-contact-delete]').addEventListener('click', async () => {
    if (!active?.data?.direct || !confirm('Delete your direct-message copy? Replacement case histories and the other participant\'s copy remain available.')) return;
    try {
      await api('/api/portal/reseller/inbox',{method:'DELETE'});
      for (const [key,message] of active.messages) if (message.source === 'direct') active.messages.delete(key);
      active.directLast = 0; render(); await refresh(true);
    } catch (error) { notice(error.message); }
  });
  root.querySelector('[data-reseller-chat-refresh]').addEventListener('click', () => refresh(true));
  form.addEventListener('submit', async event => {
    event.preventDefault(); const content = input.value.trim(); if (sending || (!content && !media.hasFiles()) || !active) return;
    const target = caseSelect.value || regarding.value || 'direct', state = active;
    const originalSelection = regarding.value;
    if (!pending || pending.content !== content || pending.target !== target || pending.signature !== media.signature()) pending = {content,target,signature:media.signature(),id:uuid()};
    sending = true; updateComposer(); notice('Sending...');
    try {
      const direct = target === 'direct' || target.startsWith('order:');
      const body = new FormData(); body.append('content',content); body.append('client_message_id',pending.id);
      await api(direct ? '/api/portal/reseller/inbox/messages' : `/api/portal/reseller/returns/${target}/messages`, {method:'POST',headers:direct && !media.hasFiles() ? {'Content-Type':'application/json'} : {},body:direct ? media.body({message:content,client_message_id:pending.id,order_id:target.startsWith('order:') ? Number(target.slice(6)) : null}) : body});
      if (active === state && input.value.trim() === content) {
        input.value = '';
        if (regarding.value === originalSelection) { regarding.value = ''; orderCard.show(''); }
      }
      state.draft = ''; state.sendError = ''; pending = null; media.clear(); notice(''); await refresh(true); window.refreshConversationBadges?.();
    } catch (error) { state.sendError = error.message; notice(error.message); }
    finally { sending = false; updateComposer(); }
  });
  input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); } });
  sort(); root.dispatchEvent(new Event('inbox:list-change'));
  const linked = root.dataset.returnId;
  if (linked) { const group = [...groups.values()].find(item => item.cases.includes(linked)); if (group) open(group,linked); }
  const timer = setInterval(refresh,3000); window.addEventListener('pagehide',() => clearInterval(timer),{once:true});
})();
