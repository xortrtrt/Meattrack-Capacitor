(function () {
    const root = document.querySelector("[data-reseller-inbox]");
    if (!root) return;

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const status = root.querySelector("[data-reseller-chat-status]");
    const refreshButton = root.querySelector("[data-reseller-chat-refresh]");
    const messages = root.querySelector("[data-reseller-chat-messages]");
    const agentLabels = Array.from(root.querySelectorAll("[data-reseller-chat-agent]"));
    const detail = root.querySelector("[data-reseller-chat-detail]");
    const deleteButton = root.querySelector("[data-reseller-message-delete]");
    const form = root.querySelector("[data-reseller-chat-form]");
    const input = form.querySelector("textarea");
    const sendButton = root.querySelector("[data-reseller-chat-send]");
    const inboxTabs = Array.from(root.querySelectorAll("[data-reseller-inbox-tab]"));
    const inboxPanels = Array.from(root.querySelectorAll("[data-reseller-inbox-panel]"));
    const conversationButton = root.querySelector("[data-reseller-conversation-open]");
    const conversationPreview = root.querySelector("[data-reseller-conversation-preview]");
    const conversationTime = root.querySelector("[data-reseller-conversation-time]");
    const chatThread = root.querySelector("[data-reseller-chat-thread]");
    const chatPlaceholder = root.querySelector("[data-reseller-chat-placeholder]");
    const chatCloseButton = root.querySelector("[data-reseller-chat-close]");
    const systemConversationButtons = Array.from(root.querySelectorAll("[data-system-conversation-open]"));
    const systemConversationThreads = Array.from(root.querySelectorAll("[data-system-conversation-thread]"));
    const systemConversationCloseButtons = Array.from(root.querySelectorAll("[data-system-conversation-close]"));
    const refundOrderTabGroups = Array.from(root.querySelectorAll("[data-refund-order-tabs]"));
    const olderMessagesButton = root.querySelector("[data-system-messages-older]");
    let lastMessageId = 0;
    let canMessage = false;
    let refreshTimer = null;
    let openConversationType = null;
    let activeSystemConversationButton = null;
    let hasUnreadMessages = false;

    function activateRefundOrderTab(group, name, { focus = false } = {}) {
        const thread = group.closest("[data-system-conversation-thread]");
        if (!thread) return;
        const tabs = Array.from(group.querySelectorAll("[data-refund-order-tab]"));
        const panels = Array.from(thread.querySelectorAll("[data-refund-order-panel]"));
        tabs.forEach((tab) => {
            const selected = tab.dataset.refundOrderTab === name;
            tab.classList.toggle("is-active", selected);
            tab.setAttribute("aria-selected", String(selected));
            tab.tabIndex = selected ? 0 : -1;
            if (selected && focus) tab.focus();
        });
        panels.forEach((panel) => {
            panel.hidden = panel.dataset.refundOrderPanel !== name;
        });
        if (name === "conversation") {
            document.dispatchEvent(new CustomEvent("refund:conversation-visible"));
        }
    }

    refundOrderTabGroups.forEach((group) => {
        const tabs = Array.from(group.querySelectorAll("[data-refund-order-tab]"));
        tabs.forEach((tab, index) => {
            tab.addEventListener("click", () => activateRefundOrderTab(group, tab.dataset.refundOrderTab));
            tab.addEventListener("keydown", (event) => {
                if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
                event.preventDefault();
                const direction = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1;
                const nextTab = tabs[(index + direction + tabs.length) % tabs.length];
                activateRefundOrderTab(group, nextTab.dataset.refundOrderTab, { focus: true });
            });
        });
        activateRefundOrderTab(group, "activity");
    });

    function closeConversation({ focus = false } = {}) {
        openConversationType = null;
        root.classList.remove("is-chat-open");
        chatThread.hidden = true;
        systemConversationThreads.forEach((thread) => { thread.hidden = true; });
        chatPlaceholder.hidden = false;
        conversationButton.classList.remove("is-active");
        conversationButton.setAttribute("aria-expanded", "false");
        systemConversationButtons.forEach((button) => {
            button.classList.remove("is-active");
            button.setAttribute("aria-expanded", "false");
        });
        input.disabled = true;
        sendButton.disabled = true;
        if (focus) {
            const target = activeSystemConversationButton || conversationButton;
            target.focus();
        }
        activeSystemConversationButton = null;
    }

    function setPlaceholder(folder) {
        const title = chatPlaceholder.querySelector("h3");
        const copy = chatPlaceholder.querySelector("p");
        if (folder === "system") {
            title.textContent = "System messages";
            copy.textContent = "Order approvals, rejections, and fulfillment updates are kept in this separate folder.";
        } else {
            title.textContent = "Select a conversation";
            copy.textContent = "Choose your sales leader from the list to see your messages.";
        }
    }

    function activateInboxTab(name, { focus = false } = {}) {
        if ((name === "system" && openConversationType === "sales") || (name === "messages" && openConversationType === "system")) {
            closeConversation();
        }
        inboxTabs.forEach((tab) => {
            const selected = tab.dataset.resellerInboxTab === name;
            tab.classList.toggle("is-active", selected);
            tab.setAttribute("aria-pressed", String(selected));
            if (selected && focus) tab.focus();
        });
        inboxPanels.forEach((panel) => {
            const selected = panel.dataset.resellerInboxPanel === name;
            panel.classList.toggle("is-active", selected);
            panel.hidden = !selected;
        });
        setPlaceholder(name);
    }

    inboxTabs.forEach((tab, index) => {
        tab.addEventListener("click", () => activateInboxTab(tab.dataset.resellerInboxTab));
        tab.addEventListener("keydown", (event) => {
            if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
            event.preventDefault();
            const direction = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1;
            const nextTab = inboxTabs[(index + direction + inboxTabs.length) % inboxTabs.length];
            activateInboxTab(nextTab.dataset.resellerInboxTab, { focus: true });
        });
    });
    activateInboxTab("messages");

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
        if (!response.ok) throw new Error(result.message || result.detail || "We couldn't update your messages.");
        return result;
    }

    function revealOlderMessages() {
        root.querySelectorAll(".reseller-system-entry[hidden]").forEach((entry) => {
            entry.hidden = false;
        });
        olderMessagesButton?.remove();
    }

    function updateSystemUnreadState(button, readCount) {
        if (!button.classList.contains("is-unread")) return;
        button.classList.remove("is-unread");
        const unreadCount = button.querySelector(".reseller-system-count");
        if (unreadCount) {
            const arrow = document.createElement("i");
            arrow.className = "reseller-system-arrow";
            arrow.dataset.lucide = "chevron-right";
            arrow.setAttribute("aria-hidden", "true");
            unreadCount.replaceWith(arrow);
            window.lucide?.createIcons();
        }
        const orderKey = button.dataset.systemOrderKey;
        const thread = systemConversationThreads.find((item) => item.dataset.systemOrderKey === orderKey);
        thread?.querySelectorAll(".reseller-order-update.is-unread").forEach((item) => item.classList.remove("is-unread"));
        const badge = root.querySelector("[data-system-unread-count]");
        if (!badge) return;
        const remaining = Math.max(0, (Number.parseInt(badge.textContent, 10) || 0) - readCount);
        if (remaining) badge.textContent = String(remaining);
        else {
            const arrow = document.createElement("i");
            arrow.className = "reseller-folder-chevron";
            arrow.dataset.lucide = "chevron-right";
            arrow.setAttribute("aria-hidden", "true");
            badge.replaceWith(arrow);
            window.lucide?.createIcons();
        }
    }

    async function openSystemConversation(button) {
        if (!button) return;
        activateInboxTab("system");
        closeConversation();
        activeSystemConversationButton = button;
        openConversationType = "system";
        root.classList.add("is-chat-open");
        chatPlaceholder.hidden = true;
        button.classList.add("is-active");
        button.setAttribute("aria-expanded", "true");
        const orderKey = button.dataset.systemOrderKey;
        const thread = systemConversationThreads.find((item) => item.dataset.systemOrderKey === orderKey);
        if (thread) {
            thread.hidden = false;
            const refundTabs = thread.querySelector("[data-refund-order-tabs]");
            if (refundTabs) activateRefundOrderTab(refundTabs, "activity");
        }

        const unreadIds = (button.dataset.systemUnreadMessageIds || "").split(",").filter(Boolean);
        if (unreadIds.length) {
            const results = await Promise.allSettled(
                unreadIds.map((messageId) => api(`/portal/reseller/system-messages/${messageId}/read`, { method: "POST" })),
            );
            const readCount = results.filter((result) => result.status === "fulfilled").length;
            if (readCount === unreadIds.length) {
                button.dataset.systemUnreadMessageIds = "";
                updateSystemUnreadState(button, readCount);
            }
        }
    }

    olderMessagesButton?.addEventListener("click", revealOlderMessages);
    systemConversationButtons.forEach((button) => {
        button.addEventListener("click", () => {
            openSystemConversation(button).catch((error) => setStatus(error.message, "error"));
        });
    });
    systemConversationCloseButtons.forEach((button) => {
        button.addEventListener("click", () => closeConversation({ focus: true }));
    });

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
        if (messages.querySelector(`[data-message-id="${item.reseller_message_id}"]`)) return;
        messages.querySelector("[data-reseller-message-empty]")?.remove();
        const row = document.createElement("article");
        row.className = "reseller-chat-message";
        row.dataset.messageId = String(item.reseller_message_id);
        const mine = item.sender_account_type === "reseller";
        row.classList.toggle("is-mine", mine);
        const meta = document.createElement("small");
        meta.textContent = mine ? "You" : (item.sender_name || "Sales leader");
        const bubble = document.createElement("p");
        bubble.textContent = item.content || "";
        const time = document.createElement("time");
        time.textContent = messageTime(item.created_at);
        row.append(meta, bubble, time);
        messages.append(row);
        lastMessageId = Math.max(lastMessageId, Number(item.reseller_message_id) || 0);
        conversationPreview.textContent = item.content || "Open conversation";
        conversationTime.textContent = messageTime(item.created_at);
        messages.scrollTop = messages.scrollHeight;
    }

    function showEmptyState() {
        messages.replaceChildren();
        const empty = document.createElement("div");
        empty.className = "reseller-message-empty";
        empty.dataset.resellerMessageEmpty = "true";
        const icon = document.createElement("span");
        const iconGlyph = document.createElement("i");
        iconGlyph.dataset.lucide = "message-circle";
        icon.append(iconGlyph);
        const title = document.createElement("strong");
        const copy = document.createElement("p");
        title.textContent = "Your conversation starts here";
        copy.textContent = "Send a message to begin a new conversation with your assigned sales leader.";
        empty.append(icon, title, copy);
        messages.append(empty);
        conversationPreview.textContent = "No messages yet";
        conversationTime.textContent = "";
        window.lucide?.createIcons();
    }

    function renderParticipant(result) {
        const participant = result.participant || {};
        canMessage = Boolean(result.can_message);
        const participantName = participant.team_leader_name || "Sales leader not assigned";
        agentLabels.forEach((label) => { label.textContent = participantName; });
        detail.textContent = canMessage
            ? "Your permanent conversation"
            : "Contact the owner to assign a sales leader";
        input.disabled = !canMessage || openConversationType !== "sales";
        sendButton.disabled = !canMessage || openConversationType !== "sales";
        conversationButton.disabled = !canMessage;
        if (!canMessage) conversationPreview.textContent = "Waiting for a sales leader assignment";
        const conversation = result.conversation;
        const deletedAt = conversation?.reseller_deleted_at ? new Date(conversation.reseller_deleted_at).valueOf() : 0;
        const lastMessageAt = conversation?.last_message_at ? new Date(conversation.last_message_at).valueOf() : 0;
        deleteButton.hidden = !conversation || (deletedAt > 0 && lastMessageAt <= deletedAt);
        setStatus(
            canMessage ? `${participantName} is available` : "Waiting for a sales leader assignment",
            canMessage ? "live" : "waiting",
        );
    }

    async function refreshMessages({ reset = false } = {}) {
        if (reset) {
            lastMessageId = 0;
            messages.querySelectorAll("[data-message-id]").forEach((node) => node.remove());
        }
        const result = await api(`/api/portal/reseller/inbox?after_id=${lastMessageId}`);
        renderParticipant(result);
        result.messages.forEach(appendMessage);
        if (reset && !result.messages.length && canMessage) {
            conversationPreview.textContent = "No messages yet";
            conversationTime.textContent = "";
        }
        if (result.messages.some((item) => item.sender_account_type === "team_leader" && !item.read_at)) {
            hasUnreadMessages = true;
        }
        if (openConversationType === "sales" && hasUnreadMessages) {
            await api("/api/portal/reseller/inbox/read", { method: "POST", body: JSON.stringify({}) });
            hasUnreadMessages = false;
        }
    }

    async function openConversation() {
        if (!canMessage) return;
        activateInboxTab("messages");
        openConversationType = "sales";
        root.classList.add("is-chat-open");
        chatPlaceholder.hidden = true;
        chatThread.hidden = false;
        conversationButton.classList.add("is-active");
        conversationButton.setAttribute("aria-expanded", "true");
        input.disabled = false;
        sendButton.disabled = false;
        if (hasUnreadMessages) {
            try {
                await api("/api/portal/reseller/inbox/read", { method: "POST", body: JSON.stringify({}) });
                hasUnreadMessages = false;
            } catch (_) {
                // A later refresh retries read state without interrupting the conversation.
            }
        }
    }

    conversationButton.addEventListener("click", () => {
        openConversation().catch((error) => setStatus(error.message, "error"));
    });
    chatCloseButton.addEventListener("click", () => closeConversation({ focus: true }));

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const content = input.value.trim();
        if (!content || !canMessage) return;
        input.disabled = true;
        sendButton.disabled = true;
        try {
            const result = await api("/api/portal/reseller/inbox/messages", {
                method: "POST",
                body: JSON.stringify({ message: content, client_message_id: crypto.randomUUID() }),
            });
            input.value = "";
            appendMessage(result.message);
            deleteButton.hidden = false;
            setStatus(`${agentLabels[0].textContent} is available`, "live");
        } catch (error) {
            setStatus(error.message, "error");
        } finally {
            input.disabled = !canMessage || openConversationType !== "sales";
            sendButton.disabled = !canMessage || openConversationType !== "sales";
            if (openConversationType === "sales") input.focus();
        }
    });

    refreshButton.addEventListener("click", () => {
        refreshButton.disabled = true;
        refreshMessages().catch((error) => setStatus(error.message, "error")).finally(() => {
            refreshButton.disabled = false;
        });
    });

    deleteButton.addEventListener("click", async () => {
        if (!window.confirm("Delete this conversation from your inbox? Your sales leader will keep their copy.")) return;
        deleteButton.disabled = true;
        try {
            await api("/api/portal/reseller/inbox", { method: "DELETE" });
            lastMessageId = 0;
            showEmptyState();
            deleteButton.hidden = true;
            setStatus(`${agentLabels[0].textContent} is available`, "live");
        } catch (error) {
            setStatus(error.message, "error");
        } finally {
            deleteButton.disabled = false;
        }
    });

    const linkedSystemMessageId = root.dataset.openSystemMessage;
    const linkedRefundId = root.dataset.openRefund;
    if (linkedRefundId) {
        const linkedButton = systemConversationButtons.find((button) => button.dataset.refundId === linkedRefundId);
        if (linkedButton) {
            revealOlderMessages();
            activateInboxTab("system");
            openSystemConversation(linkedButton).then(() => {
                const conversationPanel = root.querySelector(`#refund-conversation-${CSS.escape(linkedRefundId)}`);
                const refundTabs = conversationPanel?.closest("[data-system-conversation-thread]")?.querySelector("[data-refund-order-tabs]");
                if (refundTabs) activateRefundOrderTab(refundTabs, "conversation");
                conversationPanel?.scrollIntoView({ block: "nearest" });
            }).catch((error) => setStatus(error.message, "error"));
        }
    } else if (linkedSystemMessageId) {
        const linkedMarker = root.querySelector(`#system-message-${CSS.escape(linkedSystemMessageId)}`);
        const linkedButton = linkedMarker?.closest(".reseller-system-entry")?.querySelector("[data-system-conversation-open]");
        if (linkedButton) {
            revealOlderMessages();
            activateInboxTab("system");
            openSystemConversation(linkedButton).catch((error) => setStatus(error.message, "error"));
        }
    }

    refreshMessages({ reset: true }).catch((error) => setStatus(error.message, "error"));
    refreshTimer = window.setInterval(() => refreshMessages().catch(() => {}), 3000);
    window.addEventListener("pagehide", () => window.clearInterval(refreshTimer), { once: true });
}());
