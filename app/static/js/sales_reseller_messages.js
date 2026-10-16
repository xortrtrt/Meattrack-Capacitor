(function () {
    const root = document.querySelector("[data-sales-reseller-messages]");
    if (!root) return;

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const list = root.querySelector("[data-sales-message-list]");
    const refreshButton = root.querySelector("[data-sales-messages-refresh]");
    const status = root.querySelector("[data-sales-message-status]");
    const placeholder = root.querySelector("[data-sales-message-placeholder]");
    const thread = root.querySelector("[data-sales-message-thread]");
    const business = root.querySelector("[data-sales-thread-business]");
    const person = root.querySelector("[data-sales-thread-person]");
    const avatar = root.querySelector("[data-sales-thread-avatar]");
    const assignment = root.querySelector("[data-sales-thread-assignment]");
    const deleteButton = root.querySelector("[data-sales-message-delete]");
    const feed = root.querySelector("[data-sales-message-feed]");
    const form = root.querySelector("[data-sales-message-form]");
    const input = form.querySelector("textarea");
    const sendButton = form.querySelector("button[type='submit']");
    let conversations = [];
    let activeResellerId = null;
    let canMessage = false;
    let lastMessageId = 0;
    let refreshTimer = null;

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
        if (!response.ok) throw new Error(result.message || result.detail || "Messages could not be loaded.");
        return result;
    }

    function setStatus(copy, tone = "ready") {
        status.querySelector("span:last-child").textContent = copy;
        status.dataset.tone = tone;
    }

    function messageTime(value) {
        const timestamp = new Date(value);
        return Number.isNaN(timestamp.valueOf())
            ? ""
            : timestamp.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }

    function appendMessage(item) {
        if (feed.querySelector(`[data-message-id="${item.reseller_message_id}"]`)) return;
        feed.querySelector("[data-sales-message-empty]")?.remove();
        const row = document.createElement("article");
        row.className = "reseller-chat-message";
        row.dataset.messageId = String(item.reseller_message_id);
        const mine = item.sender_account_type === "team_leader";
        row.classList.toggle("is-mine", mine);
        const meta = document.createElement("small");
        meta.textContent = mine ? "You" : (item.sender_name || "Reseller");
        const bubble = document.createElement("p");
        bubble.textContent = item.content || "";
        const time = document.createElement("time");
        time.textContent = messageTime(item.created_at);
        row.append(meta, bubble, time);
        feed.append(row);
        lastMessageId = Math.max(lastMessageId, Number(item.reseller_message_id) || 0);
        feed.scrollTop = feed.scrollHeight;
    }

    function conversationRow(item) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "sales-message-row";
        button.dataset.resellerId = String(item.reseller_account_id);
        button.classList.toggle("is-active", Number(item.reseller_account_id) === activeResellerId);
        const icon = document.createElement("span");
        icon.className = "sales-message-avatar";
        icon.textContent = String(item.business_name || item.reseller_name || "R").slice(0, 1).toUpperCase();
        const copy = document.createElement("span");
        copy.className = "sales-message-row-copy";
        const name = document.createElement("strong");
        name.textContent = item.business_name || item.reseller_name || "Reseller";
        const preview = document.createElement("small");
        preview.textContent = item.last_message || "Start a conversation";
        copy.append(name, preview);
        button.append(icon, copy);
        if (Number(item.unread_count) > 0) {
            const count = document.createElement("b");
            count.textContent = Number(item.unread_count) > 99 ? "99+" : String(item.unread_count);
            button.append(count);
        }
        button.addEventListener("click", () => openThread(Number(item.reseller_account_id), true));
        return button;
    }

    function renderConversations(items) {
        conversations = items;
        list.replaceChildren();
        if (!items.length) {
            const empty = document.createElement("div");
            empty.className = "sales-message-list-empty";
            const title = document.createElement("strong");
            title.textContent = "No assigned resellers";
            const copy = document.createElement("p");
            copy.textContent = "Assigned reseller accounts will appear here.";
            empty.append(title, copy);
            list.append(empty);
            return;
        }
        items.forEach((item) => list.append(conversationRow(item)));
        if (activeResellerId && !items.some((item) => Number(item.reseller_account_id) === activeResellerId)) {
            clearThread();
        }
    }

    function clearThread() {
        activeResellerId = null;
        canMessage = false;
        lastMessageId = 0;
        feed.querySelectorAll("[data-message-id]").forEach((node) => node.remove());
        thread.hidden = true;
        placeholder.hidden = false;
        deleteButton.hidden = true;
    }

    async function refreshConversations() {
        const result = await api("/api/portal/live-chat/resellers");
        renderConversations(result.conversations || []);
        setStatus(`${result.conversations.length} reseller${result.conversations.length === 1 ? "" : "s"}`);
    }

    async function openThread(resellerId, reset = false) {
        if (reset || activeResellerId !== resellerId) {
            activeResellerId = resellerId;
            lastMessageId = 0;
            feed.querySelectorAll("[data-message-id]").forEach((node) => node.remove());
        }
        const result = await api(`/api/portal/live-chat/resellers/${resellerId}?after_id=${lastMessageId}`);
        const participant = result.participant || {};
        business.textContent = participant.business_name || participant.reseller_name || "Reseller";
        person.textContent = participant.reseller_name || "Registered reseller";
        avatar.textContent = String(business.textContent).slice(0, 1).toUpperCase();
        canMessage = Boolean(result.can_message);
        assignment.textContent = canMessage ? "Assigned to you" : "Previous reseller";
        assignment.classList.toggle("is-previous", !canMessage);
        input.disabled = !canMessage;
        sendButton.disabled = !canMessage;
        input.placeholder = canMessage ? "Message this reseller…" : "This conversation is read-only";
        const conversation = result.conversation;
        const deletedAt = conversation?.team_leader_deleted_at ? new Date(conversation.team_leader_deleted_at).valueOf() : 0;
        const lastMessageAt = conversation?.last_message_at ? new Date(conversation.last_message_at).valueOf() : 0;
        deleteButton.hidden = !conversation || (deletedAt > 0 && lastMessageAt <= deletedAt);
        placeholder.hidden = true;
        thread.hidden = false;
        result.messages.forEach(appendMessage);
        if (result.messages.some((item) => item.sender_account_type === "reseller" && !item.read_at)) {
            await api(`/api/portal/live-chat/resellers/${resellerId}/read`, {
                method: "POST",
                body: JSON.stringify({}),
            });
        }
        list.querySelectorAll("[data-reseller-id]").forEach((row) => {
            row.classList.toggle("is-active", Number(row.dataset.resellerId) === activeResellerId);
        });
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const content = input.value.trim();
        if (!content || !activeResellerId || !canMessage) return;
        input.disabled = true;
        sendButton.disabled = true;
        try {
            const result = await api(`/api/portal/live-chat/resellers/${activeResellerId}/messages`, {
                method: "POST",
                body: JSON.stringify({ message: content, client_message_id: crypto.randomUUID() }),
            });
            input.value = "";
            appendMessage(result.message);
            await refreshConversations();
        } catch (error) {
            setStatus(error.message, "error");
        } finally {
            input.disabled = !canMessage;
            sendButton.disabled = !canMessage;
            input.focus();
        }
    });

    deleteButton.addEventListener("click", async () => {
        if (!activeResellerId || !window.confirm("Delete this conversation from your inbox? The reseller will keep their copy.")) return;
        deleteButton.disabled = true;
        try {
            await api(`/api/portal/live-chat/resellers/${activeResellerId}`, { method: "DELETE" });
            clearThread();
            await refreshConversations();
        } catch (error) {
            setStatus(error.message, "error");
        } finally {
            deleteButton.disabled = false;
        }
    });

    refreshButton.addEventListener("click", () => {
        refreshButton.disabled = true;
        refreshConversations().then(() => activeResellerId && openThread(activeResellerId)).catch((error) => {
            setStatus(error.message, "error");
        }).finally(() => {
            refreshButton.disabled = false;
        });
    });

    refreshConversations().then(() => {
        const first = conversations[0];
        if (first) openThread(Number(first.reseller_account_id), true);
    }).catch((error) => setStatus(error.message, "error"));
    refreshTimer = window.setInterval(() => {
        refreshConversations().then(() => activeResellerId && openThread(activeResellerId)).catch(() => {});
    }, 3000);
    window.addEventListener("pagehide", () => window.clearInterval(refreshTimer), { once: true });
}());
