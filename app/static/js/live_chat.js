(function () {
    const root = document.querySelector("[data-live-chat-workspace]");
    if (!root) return;

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const queue = root.querySelector("[data-live-queue]");
    const assigned = root.querySelector("[data-live-assigned]");
    const queueCount = root.querySelector("[data-live-queue-count]");
    const refreshButton = root.querySelector("[data-live-refresh]");
    const stageEmpty = root.querySelector("[data-live-stage-empty]");
    const thread = root.querySelector("[data-live-thread]");
    const messages = root.querySelector("[data-live-messages]");
    const messageForm = root.querySelector("[data-live-message-form]");
    const messageInput = messageForm.querySelector("textarea");
    const closeButton = root.querySelector("[data-live-close-conversation]");
    const transferButton = root.querySelector("[data-live-transfer-conversation]");
    const inquiryFormButton = root.querySelector("[data-live-send-inquiry-form]");
    const inquiryFormLabel = root.querySelector("[data-live-inquiry-form-label]");
    const statusDot = root.querySelector("[data-live-status-dot]");
    const presenceLabel = root.querySelector("[data-live-presence-label]");
    const threadName = root.querySelector("[data-live-thread-name]");
    const threadStatus = root.querySelector("[data-live-thread-status]");
    const detailStatus = root.querySelector("[data-live-detail-status]");
    const detailReason = root.querySelector("[data-live-detail-reason]");
    const detailContact = root.querySelector("[data-live-detail-contact]");
    const threadAvatar = root.querySelector("[data-live-thread-avatar]");
    const visitorTyping = root.querySelector("[data-live-visitor-typing]");
    const typingAvatar = root.querySelector("[data-live-typing-avatar]");
    const typingName = root.querySelector("[data-live-typing-name]");
    const threadPresence = root.querySelector("[data-live-thread-presence]");
    const actionModal = root.querySelector("[data-live-action-modal]");
    const actionModalTitle = root.querySelector("[data-live-action-title]");
    const actionModalCopy = root.querySelector("[data-live-action-copy]");
    const actionModalConfirm = root.querySelector("[data-live-action-confirm]");
    const actionModalCancelButtons = root.querySelectorAll("[data-live-action-cancel]");
    const loadingOverlay = root.querySelector("[data-live-loading]");
    let activeConversation = null;
    let lastMessageId = 0;
    let realtime = null;
    let activeChannel = null;
    let notificationRealtime = null;
    let refreshTimer = null;
    let visitorTypingTimer = null;
    let leaderTypingTimer = null;
    let leaderTypingSent = false;
    let leaderTypingLastSentAt = 0;
    let actionModalResolver = null;
    let actionModalTrigger = null;

    async function api(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: {
                ...(options.body ? { "Content-Type": "application/json" } : {}),
                ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
                "X-Live-Chat-Actor": "team_leader",
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

    function closeActionModal(confirmed) {
        if (!actionModal || actionModal.hidden) return;
        actionModal.classList.remove("is-open");
        const resolve = actionModalResolver;
        actionModalResolver = null;
        window.setTimeout(() => {
            actionModal.hidden = true;
            actionModalTrigger?.focus?.();
            actionModalTrigger = null;
        }, 160);
        resolve?.(confirmed);
    }

    function confirmLiveAction({ title, copy, confirmLabel, danger = false }) {
        if (!actionModal || !actionModalTitle || !actionModalCopy || !actionModalConfirm) {
            return Promise.resolve(false);
        }
        if (actionModalResolver) closeActionModal(false);
        actionModalTitle.textContent = title;
        actionModalCopy.textContent = copy;
        actionModalConfirm.textContent = confirmLabel;
        actionModal.classList.toggle("is-danger", danger);
        actionModalTrigger = document.activeElement;
        actionModal.hidden = false;
        window.requestAnimationFrame(() => actionModal.classList.add("is-open"));
        window.setTimeout(() => actionModalConfirm.focus(), 40);
        return new Promise((resolve) => {
            actionModalResolver = resolve;
        });
    }

    function setManualLoading(visible) {
        if (!loadingOverlay) return;
        if (visible) {
            loadingOverlay.hidden = false;
            root.setAttribute("aria-busy", "true");
            window.requestAnimationFrame(() => loadingOverlay.classList.add("is-open"));
            return;
        }
        loadingOverlay.classList.remove("is-open");
        root.removeAttribute("aria-busy");
        window.setTimeout(() => {
            loadingOverlay.hidden = true;
        }, 160);
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
        label.textContent = action === "claim" ? "Accept" : conversation.status === "active" ? "Open" : "View";
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
            empty.textContent = "Queue is clear.";
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
        const hasActiveConversation = Boolean(workspace.presence?.active_conversation_id);
        root.querySelectorAll("[data-live-availability]").forEach((button) => {
            const isActive = button.dataset.liveAvailability === availability;
            button.classList.toggle("is-active", isActive);
            button.setAttribute("aria-pressed", String(isActive));
            const lockedAvailable = hasActiveConversation && button.dataset.liveAvailability === "available";
            button.disabled = lockedAvailable;
            button.title = lockedAvailable
                ? "End or return the active conversation before becoming available."
                : `Set status to ${button.dataset.liveAvailability}`;
        });
        if (presenceLabel) presenceLabel.textContent = hasActiveConversation ? "Busy during active chat" : "Your status";
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
                if (updated) {
                    updateConversationDetails({ ...activeConversation, ...updated });
                } else {
                    clearSelectedConversation();
                }
            }
        } catch (error) {
            showError(error);
        }
    }

    async function refreshWorkspaceManually() {
        if (!refreshButton || refreshButton.classList.contains("is-loading")) return;
        const startedAt = performance.now();
        refreshButton.classList.add("is-loading");
        refreshButton.disabled = true;
        refreshButton.setAttribute("aria-busy", "true");
        setManualLoading(true);
        try {
            await refreshWorkspace();
            const remaining = Math.max(0, 420 - (performance.now() - startedAt));
            if (remaining) await new Promise((resolve) => window.setTimeout(resolve, remaining));
        } finally {
            refreshButton.classList.remove("is-loading");
            refreshButton.disabled = false;
            refreshButton.removeAttribute("aria-busy");
            setManualLoading(false);
        }
    }

    function appendMessage(item) {
        if (messages.querySelector(`[data-message-id="${item.chat_message_id}"]`)) return;
        const bubble = document.createElement("div");
        bubble.className = "live-chat-message";
        if (item.sender_type === "team_leader") bubble.classList.add("is-leader");
        if (item.sender_type === "system") bubble.classList.add("is-system");
        bubble.dataset.messageId = String(item.chat_message_id);
        if (item.sender_type === "system") {
            bubble.textContent = item.content || item.text || "";
        } else {
            const content = document.createElement("span");
            content.className = "live-message-copy";
            content.textContent = item.content || item.text || "";
            const meta = document.createElement("small");
            meta.className = "live-message-meta";
            const sender = item.sender_type === "team_leader"
                ? "You"
                : (activeConversation?.display_name || "Visitor");
            const created = item.created_at ? new Date(item.created_at) : null;
            const time = created && !Number.isNaN(created.getTime())
                ? created.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
                : "";
            meta.textContent = time ? `${sender} · ${time}` : sender;
            bubble.append(content, meta);
        }
        if (item.sender_type === "visitor") setVisitorTyping(false);
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
        const presenceLabels = {
            active: "Connected now",
            follow_up: "Follow-up requested",
            closed: "Conversation ended",
            cancelled: "Conversation cancelled",
        };
        threadPresence.lastChild.textContent = ` ${presenceLabels[conversation.status] || "Waiting"}`;
        threadPresence.classList.toggle("is-active", conversation.status === "active");
        const initial = String(conversation.display_name || "V").slice(0, 1).toUpperCase();
        threadAvatar.textContent = initial;
        typingAvatar.textContent = initial;
        typingName.textContent = conversation.display_name || "Visitor";
        setVisitorTyping(Boolean(conversation.visitor_is_typing), conversation.display_name);
        const writable = conversation.status === "active";
        messageInput.disabled = !writable;
        messageForm.querySelector("button").disabled = !writable;
        closeButton.hidden = !["active", "follow_up"].includes(conversation.status);
        transferButton.hidden = !writable;
        inquiryFormButton.hidden = !writable;
        inquiryFormButton.disabled = !writable || Boolean(conversation.inquiry_form_requested_at);
        inquiryFormLabel.textContent = conversation.has_reseller_inquiry
            ? "Inquiry submitted"
            : conversation.inquiry_form_requested_at ? "Form sent" : "Send inquiry form";
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
        activeChannel.subscribe((event) => {
            let data = event.data || {};
            if (typeof data === "string") {
                try { data = JSON.parse(data); } catch (error) { data = {}; }
            }
            if (event.name === "typing.updated" && data.sender_type === "visitor") {
                setVisitorTyping(Boolean(data.is_typing), data.sender_name);
                return;
            }
            loadMessages().catch(showError);
            refreshWorkspace();
        });
    }

    function setVisitorTyping(isTyping, name = "") {
        if (visitorTypingTimer) window.clearTimeout(visitorTypingTimer);
        visitorTypingTimer = null;
        if (!isTyping || !activeConversation) {
            visitorTyping.hidden = true;
            return;
        }
        const displayName = name || activeConversation.display_name || "Visitor";
        typingName.textContent = displayName;
        typingAvatar.textContent = displayName.slice(0, 1).toUpperCase();
        visitorTyping.hidden = false;
        visitorTypingTimer = window.setTimeout(() => setVisitorTyping(false), 2600);
    }

    function sendLeaderTyping(isTyping) {
        if (!activeConversation || activeConversation.status !== "active") return;
        const now = Date.now();
        if (isTyping && leaderTypingSent && now - leaderTypingLastSentAt < 1200) return;
        if (!isTyping && !leaderTypingSent) return;
        leaderTypingSent = isTyping;
        leaderTypingLastSentAt = now;
        api(`/api/live-chat/${encodeURIComponent(activeConversation.conversation_id)}/typing`, {
            method: "POST",
            body: JSON.stringify({ is_typing: isTyping }),
        }).catch(() => {});
    }

    function stopLeaderTyping() {
        if (leaderTypingTimer) window.clearTimeout(leaderTypingTimer);
        leaderTypingTimer = null;
        sendLeaderTyping(false);
    }

    function clearSelectedConversation() {
        stopLeaderTyping();
        setVisitorTyping(false);
        activeConversation = null;
        lastMessageId = 0;
        messages.replaceChildren();
        thread.hidden = true;
        stageEmpty.hidden = false;
        if (activeChannel) {
            try {
                activeChannel.unsubscribe();
            } catch (error) {
                // Channel cleanup is best-effort; the realtime client is closed below.
            }
        }
        activeChannel = null;
        if (realtime) realtime.close();
        realtime = null;
    }

    async function openConversation(conversation) {
        stopLeaderTyping();
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
        stopLeaderTyping();
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

    messageInput.addEventListener("input", () => {
        if (!activeConversation || activeConversation.status !== "active") return;
        if (leaderTypingTimer) window.clearTimeout(leaderTypingTimer);
        const isTyping = Boolean(messageInput.value.trim());
        sendLeaderTyping(isTyping);
        if (isTyping) {
            leaderTypingTimer = window.setTimeout(stopLeaderTyping, 1400);
        }
    });
    messageInput.addEventListener("keydown", (event) => {
        if (event.key.length === 1 && activeConversation?.status === "active") sendLeaderTyping(true);
    });
    messageInput.addEventListener("blur", stopLeaderTyping);

    closeButton.addEventListener("click", async () => {
        if (!activeConversation) return;
        const confirmed = await confirmLiveAction({
            title: "End this conversation?",
            copy: "The visitor will be notified that the live chat has ended and can leave a service rating.",
            confirmLabel: "End chat",
            danger: true,
        });
        if (!confirmed) return;
        try {
            await api(`/api/portal/live-chat/${encodeURIComponent(activeConversation.conversation_id)}/close`, {
                method: "POST",
                body: JSON.stringify({}),
            });
            clearSelectedConversation();
            await refreshWorkspace();
        } catch (error) {
            showError(error);
        }
    });

    transferButton.addEventListener("click", async () => {
        if (!activeConversation) return;
        const confirmed = await confirmLiveAction({
            title: "Return to the queue?",
            copy: "This releases the conversation so another available sales team leader can accept it.",
            confirmLabel: "Return to queue",
        });
        if (!confirmed) return;
        try {
            await api(`/api/portal/live-chat/${encodeURIComponent(activeConversation.conversation_id)}/transfer`, {
                method: "POST",
                body: JSON.stringify({}),
            });
            clearSelectedConversation();
            await refreshWorkspace();
        } catch (error) {
            showError(error);
        }
    });

    inquiryFormButton.addEventListener("click", async () => {
        if (!activeConversation) return;
        const confirmed = await confirmLiveAction({
            title: "Send the inquiry form?",
            copy: "Only continue after the visitor confirms they want to apply as a reseller.",
            confirmLabel: "Send form",
        });
        if (!confirmed) return;
        inquiryFormButton.disabled = true;
        try {
            const result = await api(`/api/portal/live-chat/${encodeURIComponent(activeConversation.conversation_id)}/inquiry-form`, {
                method: "POST",
                body: JSON.stringify({}),
            });
            updateConversationDetails({ ...activeConversation, ...result.conversation });
            await loadMessages();
            clearError();
        } catch (error) {
            inquiryFormButton.disabled = false;
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

    actionModalCancelButtons.forEach((button) => button.addEventListener("click", () => closeActionModal(false)));
    actionModalConfirm?.addEventListener("click", () => closeActionModal(true));
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && actionModal && !actionModal.hidden) closeActionModal(false);
    });
    refreshButton?.addEventListener("click", refreshWorkspaceManually);
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
