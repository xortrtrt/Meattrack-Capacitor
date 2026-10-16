(function () {
    const root = document.querySelector("[data-reseller-inbox]");
    if (!root) return;

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const status = root.querySelector("[data-reseller-chat-status]");
    const refreshButton = root.querySelector("[data-reseller-chat-refresh]");
    const messages = root.querySelector("[data-reseller-chat-messages]");
    const agent = root.querySelector("[data-reseller-chat-agent]");
    const detail = root.querySelector("[data-reseller-chat-detail]");
    const deleteButton = root.querySelector("[data-reseller-message-delete]");
    const form = root.querySelector("[data-reseller-chat-form]");
    const input = form.querySelector("textarea");
    const sendButton = root.querySelector("[data-reseller-chat-send]");
    const inboxTabs = Array.from(root.querySelectorAll("[data-reseller-inbox-tab]"));
    const inboxPanels = Array.from(root.querySelectorAll("[data-reseller-inbox-panel]"));
    const systemMessageButtons = Array.from(root.querySelectorAll("[data-system-message-open]"));
    const olderMessagesButton = root.querySelector("[data-system-messages-older]");
    const systemModal = root.querySelector("[data-system-message-modal]");
    const systemDialog = systemModal?.querySelector('[role="dialog"]');
    const systemDialogMark = systemModal?.querySelector("[data-system-message-dialog-mark]");
    const systemDialogEyebrow = systemModal?.querySelector("[data-system-message-dialog-eyebrow]");
    const systemDialogTitle = systemModal?.querySelector("[data-system-message-dialog-title]");
    const systemDialogOrder = systemModal?.querySelector("[data-system-message-dialog-order]");
    const systemDialogTime = systemModal?.querySelector("[data-system-message-dialog-time]");
    const systemDialogBody = systemModal?.querySelector("[data-system-message-dialog-body]");
    const systemDialogReasonWrap = systemModal?.querySelector("[data-system-message-dialog-reason-wrap]");
    const systemDialogReason = systemModal?.querySelector("[data-system-message-dialog-reason]");
    let lastMessageId = 0;
    let canMessage = false;
    let refreshTimer = null;
    let lastSystemMessageTrigger = null;
    let previousBodyOverflow = "";

    function activateInboxTab(name, { focus = false } = {}) {
        inboxTabs.forEach((tab) => {
            const selected = tab.dataset.resellerInboxTab === name;
            tab.classList.toggle("is-active", selected);
            tab.setAttribute("aria-selected", String(selected));
            tab.tabIndex = selected ? 0 : -1;
            if (selected && focus) tab.focus();
        });
        inboxPanels.forEach((panel) => {
            panel.classList.toggle("is-mobile-active", panel.dataset.resellerInboxPanel === name);
        });
    }

    inboxTabs.forEach((tab, index) => {
        tab.addEventListener("click", () => activateInboxTab(tab.dataset.resellerInboxTab));
        tab.addEventListener("keydown", (event) => {
            if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
            event.preventDefault();
            const direction = event.key === "ArrowRight" ? 1 : -1;
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

    function updateSystemUnreadState(button) {
        if (!button.classList.contains("is-unread")) return;
        button.classList.remove("is-unread");
        const unreadDot = button.querySelector(".reseller-system-unread");
        if (unreadDot) {
            const arrow = document.createElement("i");
            arrow.className = "reseller-system-arrow";
            arrow.dataset.lucide = "chevron-right";
            arrow.setAttribute("aria-hidden", "true");
            unreadDot.replaceWith(arrow);
            window.lucide?.createIcons();
        }
        const badge = root.querySelector('[data-reseller-inbox-tab="system"] b');
        if (!badge) return;
        const remaining = Math.max(0, (Number.parseInt(badge.textContent, 10) || 0) - 1);
        if (remaining) badge.textContent = String(remaining);
        else badge.remove();
    }

    function closeSystemMessage() {
        if (!systemModal || systemModal.hidden) return;
        systemModal.hidden = true;
        document.body.style.overflow = previousBodyOverflow;
        lastSystemMessageTrigger?.focus();
    }

    function openSystemMessage(button) {
        if (!systemModal || !systemDialog || !button) return;
        const eventType = button.dataset.systemMessageEvent || "approved";
        const eventDetails = {
            approved: { eyebrow: "Approval confirmed", icon: "badge-check" },
            rejected: { eyebrow: "Action required", icon: "circle-x" },
            fulfilled: { eyebrow: "Order completed", icon: "package-check" },
        }[eventType] || { eyebrow: "Order update", icon: "package-check" };
        const sourceTime = button.querySelector("time");
        const reason = button.querySelector("[data-system-message-reason]")?.textContent.trim() || "";

        lastSystemMessageTrigger = button;
        systemModal.dataset.event = eventType;
        systemDialogEyebrow.textContent = eventDetails.eyebrow;
        systemDialogTitle.textContent = button.querySelector("strong")?.textContent.trim() || "System message";
        systemDialogOrder.textContent = button.dataset.systemMessageOrder
            ? `Order #${button.dataset.systemMessageOrder}`
            : "Order update";
        systemDialogTime.textContent = sourceTime?.textContent.trim() || "";
        systemDialogTime.dateTime = sourceTime?.dateTime || "";
        systemDialogBody.textContent = button.querySelector("[data-system-message-body]")?.textContent.trim() || "";
        systemDialogReason.textContent = reason;
        systemDialogReasonWrap.hidden = eventType !== "rejected";
        const modalIcon = systemDialogMark.querySelector("i, svg");
        if (modalIcon) {
            const replacement = document.createElement("i");
            replacement.dataset.lucide = eventDetails.icon;
            modalIcon.replaceWith(replacement);
        }
        previousBodyOverflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        systemModal.hidden = false;
        window.lucide?.createIcons();
        systemDialog.focus();

        if (button.classList.contains("is-unread")) {
            api(`/portal/reseller/system-messages/${button.dataset.systemMessageId}/read`, { method: "POST" })
                .then(() => updateSystemUnreadState(button))
                .catch(() => {});
        }
    }

    olderMessagesButton?.addEventListener("click", revealOlderMessages);
    systemMessageButtons.forEach((button) => {
        button.addEventListener("click", () => openSystemMessage(button));
    });
    systemModal?.querySelectorAll("[data-system-message-close]").forEach((button) => {
        button.addEventListener("click", closeSystemMessage);
    });
    systemModal?.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            event.preventDefault();
            closeSystemMessage();
            return;
        }
        if (event.key !== "Tab") return;
        const focusable = Array.from(systemModal.querySelectorAll('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'));
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === systemDialog)) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
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
        window.lucide?.createIcons();
    }

    function renderParticipant(result) {
        const participant = result.participant || {};
        canMessage = Boolean(result.can_message);
        agent.textContent = participant.team_leader_name || "Sales leader not assigned";
        detail.textContent = canMessage
            ? "Your permanent conversation"
            : "Contact the owner to assign a sales leader";
        input.disabled = !canMessage;
        sendButton.disabled = !canMessage;
        const conversation = result.conversation;
        const deletedAt = conversation?.reseller_deleted_at ? new Date(conversation.reseller_deleted_at).valueOf() : 0;
        const lastMessageAt = conversation?.last_message_at ? new Date(conversation.last_message_at).valueOf() : 0;
        deleteButton.hidden = !conversation || (deletedAt > 0 && lastMessageAt <= deletedAt);
        setStatus(
            canMessage ? `Messaging ${participant.team_leader_name}` : "Waiting for a sales leader assignment",
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
        if (result.messages.some((item) => item.sender_account_type === "team_leader" && !item.read_at)) {
            await api("/api/portal/reseller/inbox/read", { method: "POST", body: JSON.stringify({}) });
        }
    }

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
            setStatus(`Messaging ${agent.textContent}`, "live");
        } catch (error) {
            setStatus(error.message, "error");
        } finally {
            input.disabled = !canMessage;
            sendButton.disabled = !canMessage;
            input.focus();
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
            setStatus(`Messaging ${agent.textContent}`, "live");
        } catch (error) {
            setStatus(error.message, "error");
        } finally {
            deleteButton.disabled = false;
        }
    });

    const linkedSystemMessageId = root.dataset.openSystemMessage;
    if (linkedSystemMessageId) {
        const linkedButton = root.querySelector(`[data-system-message-id="${CSS.escape(linkedSystemMessageId)}"]`);
        if (linkedButton) {
            revealOlderMessages();
            activateInboxTab("system");
            openSystemMessage(linkedButton);
        }
    }

    refreshMessages({ reset: true }).catch((error) => setStatus(error.message, "error"));
    refreshTimer = window.setInterval(() => refreshMessages().catch(() => {}), 3000);
    window.addEventListener("pagehide", () => window.clearInterval(refreshTimer), { once: true });
}());
