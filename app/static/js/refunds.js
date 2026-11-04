(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  async function api(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
        ...(options.headers || {}),
      },
    });
    const contentType = response.headers.get("content-type") || "";
    const result = contentType.includes("application/json") ? await response.json() : {};
    if (!response.ok) {
      throw new Error(result.message || result.detail || "The refund conversation could not be updated.");
    }
    return result;
  }

  function roleLabel(role) {
    if (role === "reseller") return "Reseller";
    if (role === "owner") return "Owner";
    return "Sales supervisor";
  }

  function lastMessageId(conversation) {
    return Array.from(conversation.querySelectorAll("[data-refund-message-id]")).reduce(
      (latest, item) => Math.max(latest, Number(item.dataset.refundMessageId) || 0),
      0,
    );
  }

  function messageTime(value) {
    const timestamp = new Date(value);
    return Number.isNaN(timestamp.valueOf())
      ? ""
      : timestamp.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
  }

  function appendMessage(conversation, item) {
    const messageId = Number(item.order_refund_message_id) || 0;
    if (!messageId || conversation.querySelector(`[data-refund-message-id="${messageId}"]`)) return;
    const list = conversation.querySelector("[data-refund-message-list]");
    if (!list) return;
    const role = conversation.dataset.refundRole;
    const mine = item.sender_role === role;
    const resellerView = role === "reseller";
    const scrollTarget = list;
    const stickToBottom = mine || (
      scrollTarget.scrollHeight - scrollTarget.scrollTop - scrollTarget.clientHeight < 80
    );
    list.querySelector(".refund-message-empty, .reseller-refund-empty")?.remove();

    const row = document.createElement("article");
    row.dataset.refundMessageId = String(messageId);
    if (resellerView) {
      row.className = mine ? "is-mine" : "is-team";
    } else {
      row.className = `refund-message ${item.sender_role === "reseller" ? "from-reseller" : "from-team"}`;
    }
    const meta = document.createElement("small");
    meta.textContent = mine && resellerView
      ? "You"
      : `${item.sender_name || (mine ? "You" : roleLabel(item.sender_role))} · ${roleLabel(item.sender_role)}`;
    const body = document.createElement("p");
    body.textContent = item.message || "";
    const time = document.createElement("time");
    time.dateTime = item.created_at || "";
    time.textContent = messageTime(item.created_at);
    row.append(meta, body, time);
    row.classList.toggle("is-system",item.message_kind==="system");
    list.append(row);
    if (stickToBottom) scrollTarget.scrollTop = scrollTarget.scrollHeight;
  }

  function isVisible(conversation) {
    return !document.hidden && !conversation.hidden && !conversation.parentElement?.closest("[hidden]") && conversation.getClientRects().length>0;
  }

  async function refreshConversation(conversation) {
    if (!isVisible(conversation) || conversation.dataset.refundRefreshing === "true") return;
    const refundId = conversation.dataset.refundId;
    const role = conversation.dataset.refundRole;
    if (!refundId || !role) return;
    conversation.dataset.refundRefreshing = "true";
    try {
      const result = await api(
        `/api/portal/${encodeURIComponent(role)}/refunds/${encodeURIComponent(refundId)}/messages?after_id=${lastMessageId(conversation)}`,
      );
      (result.messages || []).forEach((item) => appendMessage(conversation, item));
      conversation.dataset.refundStatus = result.status || "";
      const through=lastMessageId(conversation);
      if (through && isVisible(conversation) && Number(conversation.dataset.readThrough||0)<through) {
        await api(`/api/portal/${role}/refunds/${refundId}/read`,{method:"POST",body:JSON.stringify({through_id:through})});
        conversation.dataset.readThrough=String(through);
        window.refreshConversationBadges?.();
      }
    } finally {
      delete conversation.dataset.refundRefreshing;
    }
  }

  function refreshVisibleConversations() {
    document.querySelectorAll("[data-refund-conversation]").forEach((conversation) => {
      refreshConversation(conversation).catch(() => {});
    });
  }

  if (document.documentElement.dataset.refundInteractionsBound !== "true") {
    document.documentElement.dataset.refundInteractionsBound = "true";

    document.addEventListener("submit", async (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || !form.matches("[data-refund-message-form]")) return;
      event.preventDefault();
      const conversation = form.closest("[data-refund-conversation]");
      const input = form.querySelector("textarea[name='message']");
      const button = form.querySelector("button[type='submit']");
      const status = form.querySelector("[data-refund-message-status]");
      const content = input?.value.trim() || "";
      if (!conversation || !input || !button || !content) return;
      if (conversation.dataset.refundStatus === "completed") {
        if (status) status.textContent = "This refund is complete. You can still send a message about it.";
        return;
      }
      const refundId = conversation.dataset.refundId;
      const role = conversation.dataset.refundRole;
      button.disabled = true;
      input.disabled = true;
      if (status) status.textContent = "";
      try {
        const result = await api(
          `/api/portal/${encodeURIComponent(role)}/refunds/${encodeURIComponent(refundId)}/messages`,
          { method: "POST", body: JSON.stringify({ message: content }) },
        );
        input.value = "";
        appendMessage(conversation, result.message);
      } catch (error) {
        if (status) status.textContent = error.message;
      } finally {
        input.disabled = false;
        button.disabled = false;
        input.focus();
      }
    });

    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || !form.matches("[data-refund-confirm]")) return;
      if (form.dataset.receiptScanning === "true") {
        event.preventDefault();
        return;
      }
      if (!window.confirm(form.dataset.refundConfirm || "Confirm this refund action?")) {
        event.preventDefault();
        return;
      }
      const button = form.querySelector("button[type='submit']");
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
      }
    });

    document.addEventListener("refund:conversation-visible", refreshVisibleConversations);
  }

  const receiptScans = new WeakMap();
  async function scanReceipt(form) {
    const fileInput=form.querySelector('[name="proof"]');
    const file=fileInput?.files[0];
    const status=form.querySelector('[data-refund-scan-status]');
    const token=form.querySelector('[name="receipt_scan_token"]');
    const confirmed=form.querySelector('[name="payment_details_confirmed"]');
    receiptScans.get(form)?.abort();
    const controller=new AbortController();
    receiptScans.set(form,controller);
    token.value="";confirmed.checked=false;
    if (!file) {
      delete form.dataset.receiptScanning;
      form.querySelector('[type="submit"]').disabled=false;
      status.textContent="Choose a refund screenshot first.";
      return;
    }
    const submit=form.querySelector('[type="submit"]');
    const fields=["transaction_reference","transferred_at","transfer_amount"].map(name=>form.elements.namedItem(name));
    const originalValues=fields.map(field=>field.value);
    form.dataset.receiptScanning="true";
    submit.disabled=true;
    status.textContent="Reading transfer receipt…";
    const body=new FormData();body.append("proof",file);
    try {
      const response=await fetch(form.dataset.scanUrl,{method:"POST",headers:{"X-CSRF-Token":csrfToken},body,signal:controller.signal});
      const result=await response.json();
      if (!response.ok) throw new Error(result.message||result.detail||"The receipt could not be read.");
      if (controller.signal.aborted || fileInput.files[0]!==file) return;
      token.value=result.scan_token;
      const values=[result.transaction_number||"", result.payment_date&&result.payment_time?`${result.payment_date}T${result.payment_time}`:"", result.payment_amount||""];
      fields.forEach((field,index)=>{if(field.value===originalValues[index])field.value=values[index];});
      confirmed.checked=false;
      status.textContent=(result.warnings||[]).join(" ")||"Receipt read. Check the extracted details and correct any mistakes before confirming.";
    } catch(error) {
      if (!controller.signal.aborted) status.textContent=error.message+" Enter the receipt details manually, then check them against the screenshot.";
    } finally {
      if (receiptScans.get(form)===controller) {
        delete form.dataset.receiptScanning;
        submit.disabled=false;
      }
    }
  }
  document.addEventListener("change",event=>{
    const form=event.target.closest('[data-refund-receipt-form]');
    if (!form) return;
    if (event.target.name==="proof") scanReceipt(form);
    if (event.target.matches('[data-refund-extracted]')) form.querySelector('[name="payment_details_confirmed"]').checked=false;
  });
  document.addEventListener("input",event=>{
    const form=event.target.closest('[data-refund-receipt-form]');
    if(form && event.target.matches('[data-refund-extracted]')) form.querySelector('[name="payment_details_confirmed"]').checked=false;
  });
  refreshVisibleConversations();
  const refreshTimer = window.setInterval(refreshVisibleConversations, 3000);
  window.addEventListener("pagehide", () => window.clearInterval(refreshTimer), { once: true });
})();
