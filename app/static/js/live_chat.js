(function () {
    const root = document.querySelector("[data-live-chat-workspace]");
    if (!root) return;

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const queue = root.querySelector("[data-live-queue]");
    const assigned = root.querySelector("[data-live-assigned]");
    const queueCount = root.querySelector("[data-live-queue-count]");
    const stageEmpty = root.querySelector("[data-live-stage-empty]");
    const thread = root.querySelector("[data-live-thread]");
    const messages = root.querySelector("[data-live-messages]");
    const messageForm = root.querySelector("[data-live-message-form]");
    const messageInput = messageForm.querySelector("textarea");
    const closeButton = root.querySelector("[data-live-close-conversation]");
    const transferButton = root.querySelector("[data-live-transfer-conversation]");
    const statusDot = root.querySelector("[data-live-status-dot]");
    const threadName = root.querySelector("[data-live-thread-name]");
    const threadStatus = root.querySelector("[data-live-thread-status]");
    const detailStatus = root.querySelector("[data-live-detail-status]");
    const detailReason = root.querySelector("[data-live-detail-reason]");
    const detailContact = root.querySelector("[data-live-detail-contact]");
    let activeConversation = null;
    let lastMessageId = 0;
    let realtime = null;
    let activeChannel = null;
    let notificationRealtime = null;
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
        const result = contentType.includes("application/json") ? await response.json() : await response.text();
        if (!response.ok) {
            throw new Error(result.message || result.detail || result.reply || "The request could not be completed.");
        }
        return result;
    }

    function showError(error) {
        const region = document.querySelector("[data-toast-region]");
        if (!region) return;
        const toast = document.createElement("article");
        toast.className = "toast error";
        toast.dataset.liveChatError = "true";
        const copy = document.createElement("div");
        copy.className = "toast-copy";
        const title = document.createElement("strong");
        title.textContent = "Live chat update failed";
        const message = document.createElement("p");
        message.textContent = error.message || String(error);
        copy.append(title, message);
        toast.append(copy);
        region.replaceChildren(toast);
    }

    function clearError() {
        document.querySelector("[data-toast-region] [data-live-chat-error]")?.remove();
    }

    function conversationRow(conversation, action) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "live-conversation-row";
        if (activeConversation?.conversation_id === conversation.conversation_id) button.classList.add("is-active");
        const avatar = document.createElement("span");
        avatar.className = "live-avatar";
        avatar.textContent = String(conversation.display_name || "V").slice(0, 1).toUpperCase();
        const copy = document.createElement("span");
        const name = document.createElement("strong");
        name.textContent = conversation.display_name || "Visitor";
        const status = document.createElement("small");
        status.textContent = String(conversation.status || conversation.escalation_reason || "waiting").replaceAll("_", " ");
        copy.append(name, status);
        const label = document.createElement("em");
        label.textContent = action === "claim" ? "Accept" : "Open";
        button.append(avatar, copy, label);
        button.addEventListener("click", () => action === "claim" ? claimConversation(conversation.conversation_id) : openConversation(conversation));
        return button;
    }

    function renderWorkspace(workspace) {
        const queueItems = workspace.queue || [];
        const assignedItems = workspace.assigned || [];
        queueCount.textContent = String(queueItems.length);
        queue.replaceChildren();
        if (!queueItems.length) {
            const empty = document.createElement("p");
            empty.className = "live-empty";
            empty.textContent = "No visitors are waiting.";
            queue.append(empty);
        } else {
            queueItems.forEach((item) => queue.append(conversationRow(item, "claim")));
        }
        assigned.replaceChildren();
        if (!assignedItems.length) {
            const empty = document.createElement("p");
            empty.className = "live-empty";
            empty.textContent = "No assigned conversations.";
            assigned.append(empty);
        } else {
            assignedItems.forEach((item) => assigned.append(conversationRow(item, "open")));
        }
        const availability = workspace.presence?.availability || "offline";
        root.querySelectorAll("[data-live-availability]").forEach((button) => {
            const isActive = button.dataset.liveAvailability === availability;
            button.classList.toggle("is-active", isActive);
            button.setAttribute("aria-pressed", String(isActive));
        });
        statusDot.classList.toggle("is-online", availability === "available" || availability === "busy");
        attachNotificationChannel(workspace.account_id);
    }

    function attachNotificationChannel(accountId) {
        if (notificationRealtime || !window.Ably || !accountId || root.dataset.liveChatEnabled !== "true") return;
        notificationRealtime = new window.Ably.Realtime({
            authCallback: async (_params, callback) => {
                try {
                    const token = await api("/api/ably/token", {
                        method: "POST",
                        body: JSON.stringify({ scope: "notifications" }),
                    });
                    callback(null, token);
                } catch (error) {
                    callback(error, null);
                }
            },
        });
        notificationRealtime.channels.get(`leader:${accountId}`).subscribe(() => refreshWorkspace());
    }

    async function refreshWorkspace() {
        try {
            const workspace = await api("/api/portal/live-chat/workspace");
            renderWorkspace(workspace);
            clearError();
            if (activeConversation) {
                const updated = (workspace.assigned || []).find((item) => item.conversation_id === activeConversation.conversation_id);
                if (updated) updateConversationDetails({ ...activeConversation, ...updated });
            }
        } catch (error) {
            showError(error);
        }
    }

    function appendMessage(item) {
        if (messages.querySelector(`[data-message-id="${item.chat_message_id}"]`)) return;
        const bubble = document.createElement("div");
        bubble.className = "live-chat-message";
        if (item.sender_type === "team_leader") bubble.classList.add("is-leader");
        if (item.sender_type === "system") bubble.classList.add("is-system");
        bubble.dataset.messageId = String(item.chat_message_id);
        bubble.textContent = item.content || item.text || "";
        messages.append(bubble);
        lastMessageId = Math.max(lastMessageId, Number(item.chat_message_id || item.message_id || 0));
        messages.scrollTop = messages.scrollHeight;
    }

    async function loadMessages(reset = false) {
        if (!activeConversation) return;
        if (reset) {
            messages.replaceChildren();
            lastMessageId = 0;
        }
        const result = await api(`/api/live-chat/${encodeURIComponent(activeConversation.conversation_id)}/messages?after_id=${lastMessageId}`);
        (result.messages || []).forEach(appendMessage);
    }

    function updateConversationDetails(conversation) {
        activeConversation = conversation;
        threadName.textContent = conversation.display_name || "Visitor";
        threadStatus.textContent = String(conversation.status || "conversation").replaceAll("_", " ");
        detailStatus.textContent = String(conversation.status || "—").replaceAll("_", " ");
        detailReason.textContent = String(conversation.escalation_reason || "requested").replaceAll("_", " ");
        detailContact.textContent = conversation.fallback_contact || "Not provided";
        const writable = conversation.status === "active";
        messageInput.disabled = !writable;
        messageForm.querySelector("button").disabled = !writable;
        closeButton.hidden = !["active", "follow_up"].includes(conversation.status);
        transferButton.hidden = !writable;
    }

    async function attachConversationChannel() {
        if (!window.Ably || !activeConversation || root.dataset.liveChatEnabled !== "true") return;
        if (activeChannel) {
            try { await activeChannel.unsubscribe(); } catch (error) { /* channel will be replaced */ }
        }
        if (realtime) {
            try { realtime.close(); } catch (error) { /* reconnect below */ }
        }
        realtime = new window.Ably.Realtime({
            authCallback: async (_params, callback) => {
                try {
                    const token = await api("/api/ably/token", {
                        method: "POST",
                        body: JSON.stringify({ scope: "conversation", conversation_id: activeConversation.conversation_id }),
                    });
                    callback(null, token);
                } catch (error) {
                    callback(error, null);
                }
            },
        });
        activeChannel = realtime.channels.get(`support:${activeConversation.conversation_id}`);
        activeChannel.subscribe(() => {
            loadMessages().catch(showError);
            refreshWorkspace();
        });
    }

    async function openConversation(conversation) {
        updateConversationDetails(conversation);
        stageEmpty.hidden = true;
        thread.hidden = false;
        await loadMessages(true);
        await attachConversationChannel();
        renderWorkspace(await api("/api/portal/live-chat/workspace"));
        messageInput.focus();
    }

    async function claimConversation(conversationId) {
        try {
            const result = await api(`/api/portal/live-chat/${encodeURIComponent(conversationId)}/claim`, {
                method: "POST",
                body: JSON.stringify({}),
            });
            await openConversation(result.conversation);
        } catch (error) {
            showError(error);
            await refreshWorkspace();
        }
    }

    messageForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!activeConversation || !messageInput.value.trim()) return;
        const text = messageInput.value.trim();
        const button = messageForm.querySelector("button");
        button.disabled = true;
        try {
            const result = await api(`/api/live-chat/${encodeURIComponent(activeConversation.conversation_id)}/messages`, {
                method: "POST",
                body: JSON.stringify({ message: text, client_message_id: crypto.randomUUID() }),
            });
            messageInput.value = "";
            appendMessage(result.message);
        } catch (error) {
            showError(error);
        } finally {
            button.disabled = false;
            messageInput.focus();
        }
    });

    closeButton.addEventListener("click", async () => {
        if (!activeConversation || !window.confirm("Close this live conversation?")) return;
        try {
            await api(`/api/portal/live-chat/${encodeURIComponent(activeConversation.conversation_id)}/close`, {
                method: "POST",
                body: JSON.stringify({}),
            });
            activeConversation = null;
            messages.replaceChildren();
            thread.hidden = true;
            stageEmpty.hidden = false;
            await refreshWorkspace();
        } catch (error) {
            showError(error);
        }
    });

    transferButton.addEventListener("click", async () => {
        if (!activeConversation || !window.confirm("Return this visitor to the shared queue?")) return;
        try {
            await api(`/api/portal/live-chat/${encodeURIComponent(activeConversation.conversation_id)}/transfer`, {
                method: "POST",
                body: JSON.stringify({}),
            });
            activeConversation = null;
            messages.replaceChildren();
            thread.hidden = true;
            stageEmpty.hidden = false;
            await refreshWorkspace();
        } catch (error) {
            showError(error);
        }
    });

    root.querySelectorAll("[data-live-availability]").forEach((button) => {
        button.addEventListener("click", async () => {
            try {
                await api("/api/portal/live-chat/presence", {
                    method: "POST",
                    body: JSON.stringify({ availability: button.dataset.liveAvailability }),
                });
                await refreshWorkspace();
            } catch (error) {
                showError(error);
            }
        });
    });

    root.querySelector("[data-live-refresh]")?.addEventListener("click", refreshWorkspace);
    root.querySelectorAll("[data-live-claim]").forEach((button) => button.addEventListener("click", () => claimConversation(button.dataset.liveClaim)));
    root.querySelectorAll("[data-live-open]").forEach((button) => {
        button.addEventListener("click", () => openConversation({
            conversation_id: button.dataset.liveOpen,
            display_name: button.querySelector("strong")?.textContent || "Visitor",
            status: button.dataset.liveState,
        }));
    });

    window.setInterval(() => {
        api("/api/portal/live-chat/heartbeat", { method: "POST", body: JSON.stringify({}) })
            .then(refreshWorkspace)
            .catch(() => statusDot.classList.remove("is-online"));
    }, 15000);
    refreshTimer = window.setInterval(() => {
        refreshWorkspace();
        if (activeConversation) loadMessages().catch(() => {});
    }, 5000);
    window.addEventListener("beforeunload", () => {
        if (refreshTimer) window.clearInterval(refreshTimer);
        if (realtime) realtime.close();
        if (notificationRealtime) notificationRealtime.close();
    });
    refreshWorkspace();
})();
