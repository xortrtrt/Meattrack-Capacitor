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
    const scrollTarget = resellerView ? conversation : list;
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
    list.append(row);
    if (stickToBottom) scrollTarget.scrollTop = scrollTarget.scrollHeight;
  }

  function isVisible(conversation) {
    return !conversation.hidden && !conversation.parentElement?.closest("[hidden]");
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

  refreshVisibleConversations();
  const refreshTimer = window.setInterval(refreshVisibleConversations, 3000);
  window.addEventListener("pagehide", () => window.clearInterval(refreshTimer), { once: true });
})();
