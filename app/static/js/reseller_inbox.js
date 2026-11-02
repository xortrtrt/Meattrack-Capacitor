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
    const returnId = Number(root.dataset.returnContactId ?? root.dataset.returnId) || null;
    let reportIssue = root.dataset.reportIssue === "1";
    let pendingSend = null;
    let sending=false;
    const media=window.InternalConversationMedia.controls(form);
    if (returnId) input.value = reportIssue ? `I have a problem with my replacement for Return #${returnId}: ` : `I need help with Return #${returnId}: `;
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
    let lastMessageDay = "";
    let directScroll = null;
    let canMessage = false;
    let refreshTimer = null;
    let openConversationType = null;
    let activeSystemConversationButton = null;
    let hasUnreadMessages = false;
    let activeCategory = "all";
    const replacementStage = root.querySelector("[data-inbox-replacement-stage]");
    const search = root.querySelector("[data-inbox-search]");
    const rows = Array.from(root.querySelectorAll("[data-inbox-row]"));
    rows.forEach(row => root.querySelector(".reseller-conversation-browser").append(row));
    root.querySelectorAll("[data-inbox-time]").forEach(time => {
        const date = new Date(time.dateTime);
        if (!Number.isNaN(date.valueOf())) time.textContent = date.toLocaleString("en-PH", {
            timeZone: "Asia/Manila", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
        });
    });
    document.addEventListener("conversations:unread", event => {
        const summary = event.detail;
        const counts = {
            "data-inbox-unread-count": Math.max(0, summary.unread_conversations - (summary.orders?.length || 0)),
            "data-direct-unread-count": summary.sales_unread_count,
            "data-replacement-unread-count": (summary.replacement_threads || []).reduce((sum, row) => sum + row.unread_count, 0),
            "data-system-unread-count": summary.orders?.reduce((sum, row) => sum + row.unread_count, 0),
        };
        Object.entries(counts).forEach(([attribute, count]) => {
            const badge = root.querySelector(`[${attribute}]`);
            if (!badge || count == null) return;
            badge.textContent = String(count);
            badge.hidden = !count;
            badge.setAttribute("aria-label", `${count} unread ${attribute === "data-inbox-unread-count" ? "conversations" : "messages"}`);
        });
    });

    function filterConversations() {
        const query = (search?.value || "").trim().toLocaleLowerCase();
        rows.forEach(row => {
            const searchable = `${row.textContent} ${row.dataset.inboxSearchText || ""}`.toLocaleLowerCase();
            row.hidden = (row.dataset.inboxRow === "system" && activeCategory !== "system") || row.dataset.inboxDuplicate === "1" || (activeCategory !== "all" && row.dataset.inboxRow !== activeCategory && !(activeCategory === "messages" && row.dataset.hasDirect === "1"))
                || !searchable.includes(query);
        });
        const empty = root.querySelector("[data-inbox-search-empty]");
        if (empty) empty.hidden = rows.some(row => !row.hidden);
        const caption = root.querySelector("[data-inbox-list-caption]");
        if (caption) caption.textContent = {all:"All conversations",messages:"Direct messages",replacements:"Replacement conversations",system:"Order updates"}[activeCategory];
    }
    search?.addEventListener("input", filterConversations);
    root.addEventListener("replacement:list", event => {
        if (event.detail.open) closeConversation({focus:true});
        else openReplacementConversation();
    });
    function openReplacementConversation() {
        if (!replacementStage) return;
        closeConversation();
        replacementStage.hidden = false;
        chatPlaceholder.hidden = true;
        root.classList.add("is-chat-open");
        root.classList.remove("is-list-open");
        openConversationType = "replacement";
        root.querySelector("[data-replacement-thread-id][aria-current='page']")?.classList.add("is-active");
    }

    root.addEventListener("inbox:contact-open", () => {
        closeConversation(); openConversationType = "contact";
        chatPlaceholder.hidden = true; root.classList.add("is-chat-open");
        root.querySelector("[data-contact-stage]").hidden = false;
    });
    root.addEventListener("inbox:list-change", filterConversations);
    root.addEventListener("inbox:close", () => closeConversation());
    const orderSelect = root.querySelector("[data-direct-order]");
    const orderCard = orderSelect ? window.OrderInquiryCards?.create(chatThread, orderSelect) : null;
    fetch("/api/portal/reseller/inbox/orders").then(response => response.ok ? response.json() : Promise.reject())
        .then(data => {
            data.orders.forEach(order => orderSelect?.add(new Option(`Order #${order.order_id}`, String(order.order_id))));
            const explicitOrder = new URLSearchParams(location.search).get("inquire_order");
            if (explicitOrder && [...orderSelect.options].some(option => option.value === explicitOrder)) {
                orderSelect.value = explicitOrder; orderCard?.show(explicitOrder);
                const openInquiry = () => {
                    if (root.querySelector('[data-contact-id][data-has-direct="1"]')) {
                        root.dispatchEvent(new CustomEvent("inbox:inquire-order", {detail:{orderId:explicitOrder}}));
                    } else if (canMessage) openConversation();
                };
                if (canMessage) openInquiry();
                else root.addEventListener("inbox:direct", openInquiry, {once:true});
            }
        }).catch(() => {});
    function messageUuid() {
        if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 15) | 64;
        bytes[8] = (bytes[8] & 63) | 128;
        const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
        return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
    }

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
        const previousType = openConversationType;
        if (previousType === "sales") directScroll = {top:messages.scrollTop, atBottom:messages.scrollHeight-messages.scrollTop-messages.clientHeight < 65};
        openConversationType = null;
        if (replacementStage) replacementStage.hidden = true;
        root.querySelectorAll(".inbox-replacement-row").forEach(row => row.classList.remove("is-active"));
        root.classList.remove("is-chat-open");
        root.classList.remove("is-list-open");
        chatThread.hidden = true;
        systemConversationThreads.forEach((thread) => { thread.hidden = true; });
        chatPlaceholder.hidden = false;
        conversationButton.classList.remove("is-active");
        conversationButton.setAttribute("aria-expanded", "false");
        systemConversationButtons.forEach((button) => {
            button.classList.remove("is-active");
            button.setAttribute("aria-expanded", "false");
        });
        root.querySelector("[data-contact-stage]")?.setAttribute("hidden", "");
        input.disabled = true;
        sendButton.disabled = true;
        if (focus) {
            const target = activeSystemConversationButton || (previousType === "replacement"
                ? root.querySelector("[data-replacement-thread-id][aria-current='page']") : null) || conversationButton;
            target.focus();
        }
        activeSystemConversationButton = null;
    }

    function setPlaceholder(folder) {
        const title = chatPlaceholder.querySelector("h3");
        const copy = chatPlaceholder.querySelector("p");
        if (folder === "system") {
            title.textContent = "System messages";
            copy.textContent = "Choose an order to see its updates and refund conversation.";
        } else {
            title.textContent = "Select a conversation";
            copy.textContent = folder === "replacements"
                ? "Choose a replacement to coordinate your return and delivery."
                : "Choose a conversation to view messages and keep things moving.";
        }
    }

    function activateInboxTab(name, { focus = false } = {}) {
        activeCategory = name;
        if ((openConversationType === "system" && name !== "system") || (openConversationType === "contact" && name === "system")) closeConversation();
        if (name !== "all" && openConversationType &&
            openConversationType !== "contact" && name !== {sales:"messages",system:"system",replacement:"replacements"}[openConversationType]) closeConversation();
        inboxTabs.forEach((tab) => {
            const selected = tab.dataset.resellerInboxTab === name;
            tab.classList.toggle("is-active", selected);
            tab.setAttribute("aria-pressed", String(selected));
            if (selected && focus) tab.focus();
        });
        inboxPanels.forEach((panel) => {
            const selected = true;
            panel.classList.toggle("is-active", selected);
            panel.hidden = !selected;
        });
        setPlaceholder(name);
        filterConversations();
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
    activateInboxTab("all");

    async function api(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: {
                ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
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
        badge.textContent = String(remaining);
        badge.hidden = !remaining;
    }

    async function openSystemConversation(button) {
        if (!button) return;
        if (activeCategory !== "all") activateInboxTab("system");
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
        if (!document.hidden && unreadIds.length) {
            const results = await Promise.allSettled(
                unreadIds.map((messageId) => api(`/portal/reseller/system-messages/${messageId}/read`, { method: "POST" })),
            );
            window.refreshConversationBadges?.();
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
        const stickToBottom = !messages.querySelector("[data-message-id]") || messages.scrollHeight - messages.scrollTop - messages.clientHeight < 65;
        const day = new Date(item.created_at).toLocaleDateString("en-PH", {timeZone:"Asia/Manila",month:"short",day:"numeric",year:"numeric"});
        if (day !== lastMessageDay) {
            const divider = document.createElement("p"); divider.className = "inbox-date-divider"; divider.textContent = day;
            messages.append(divider); lastMessageDay = day;
        }
        messages.querySelector("[data-reseller-message-empty]")?.remove();
        const row = document.createElement("article");
        row.className = "reseller-chat-message";
        row.dataset.messageId = String(item.reseller_message_id);
        row.dataset.orderId = String(item.order_id || "");
        row.dataset.groupSender = String(item.sender_account_id); row.dataset.createdAt = item.created_at;
        const mine = item.sender_account_type === "reseller";
        row.classList.toggle("is-mine", mine);
        const meta = document.createElement("small");
        meta.className = "inbox-message-author";
        meta.textContent = mine ? "You" : (item.sender_name || "Sales leader");
        const bubble = document.createElement("p");
        bubble.textContent = item.content || "";
        const time = document.createElement("time");
        time.textContent = messageTime(item.created_at);
        row.append(meta);
        if (item.content?.trim()) row.append(bubble);
        window.InternalConversationMedia.renderAttachments(row,item.attachments);
        row.append(time);
        if (item.return_id) {
            const link = document.createElement("a");
            link.className = "return-chat-reference";
            link.href = `/portal/reseller/history?view=returns&case=${Number(item.return_id)}`;
            link.textContent = `Return #${item.return_id}`;
            row.prepend(link);
        }
        if (item.order_id && window.OrderInquiryCards) messages.append(window.OrderInquiryCards.reference(item.order_id));
        messages.append(row);
        window.OrderInquiryCards?.group(messages);
        lastMessageId = Math.max(lastMessageId, Number(item.reseller_message_id) || 0);
        conversationPreview.textContent = item.content || (item.attachments?.length ? "Attachment" : "Open conversation");
        conversationTime.textContent = messageTime(item.created_at);
        conversationButton.dataset.activity = item.created_at;
        root.dispatchEvent(new CustomEvent("inbox:sort"));
        if (stickToBottom && !chatThread.hidden) messages.scrollTop = messages.scrollHeight;
        filterConversations();
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
        root.querySelectorAll("[data-reseller-chat-avatar]").forEach(target => window.InternalConversationMedia.renderAvatar(target,participant,"team_leader_"));
        detail.textContent = canMessage
            ? "Your permanent conversation"
            : "Contact the owner to assign a sales leader";
        input.disabled = sending || !(canMessage || reportIssue) || openConversationType !== "sales";
        sendButton.disabled = sending || !(canMessage || reportIssue) || openConversationType !== "sales";
        media.disable(sendButton.disabled);
        conversationButton.disabled = !(canMessage || reportIssue);
        if (!canMessage) conversationPreview.textContent = "Waiting for a sales leader assignment";
        filterConversations();
        const conversation = result.conversation;
        const deletedAt = conversation?.reseller_deleted_at ? new Date(conversation.reseller_deleted_at).valueOf() : 0;
        const lastMessageAt = conversation?.last_message_at ? new Date(conversation.last_message_at).valueOf() : 0;
        deleteButton.hidden = !conversation || (deletedAt > 0 && lastMessageAt <= deletedAt);
        setStatus(
            canMessage ? "Direct messages ready" : "Waiting for a sales leader assignment",
            canMessage ? "live" : "waiting",
        );
    }

    async function refreshMessages({ reset = false } = {}) {
        if (reset) {
            lastMessageId = 0;
            lastMessageDay = "";
            messages.querySelectorAll("[data-message-id]").forEach((node) => node.remove());
        }
        const result = await api(`/api/portal/reseller/inbox?after_id=${lastMessageId}`);
        renderParticipant(result);
        root.dispatchEvent(new CustomEvent("inbox:direct", {detail:result}));
        result.messages.forEach(appendMessage);
        if (reset && !result.messages.length && canMessage) {
            conversationPreview.textContent = "No messages yet";
            conversationTime.textContent = "";
        }
        if (result.messages.some((item) => item.sender_account_type === "team_leader" && !item.read_at)) {
            hasUnreadMessages = true;
        }
        if (!document.hidden && openConversationType === "sales" && hasUnreadMessages) {
            await api("/api/portal/reseller/inbox/read", { method: "POST", body: JSON.stringify({through_id:lastMessageId}) });
            hasUnreadMessages = false;
            window.refreshConversationBadges?.();
        }
    }

    async function openConversation() {
        if (!canMessage && !reportIssue) return;
        if (activeCategory !== "all") activateInboxTab("messages");
        closeConversation();
        openConversationType = "sales";
        root.classList.add("is-chat-open");
        chatPlaceholder.hidden = true;
        chatThread.hidden = false;
        conversationButton.classList.add("is-active");
        messages.scrollTop = !directScroll || directScroll.atBottom ? messages.scrollHeight : directScroll.top;
        conversationButton.setAttribute("aria-expanded", "true");
        input.disabled = false;
        sendButton.disabled = false;
        media.disable(false);
        if (!document.hidden && hasUnreadMessages) {
            try {
                await api("/api/portal/reseller/inbox/read", { method: "POST", body: JSON.stringify({through_id:lastMessageId}) });
                hasUnreadMessages = false;
                window.refreshConversationBadges?.();
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
        if (sending || (!content && !media.hasFiles()) || (!canMessage && !reportIssue)) return;
        sending=true;
        if (!pendingSend || pendingSend.message !== content || pendingSend.signature !== media.signature() || pendingSend.order_id !== (Number(orderSelect?.value) || null)) pendingSend = { order_id: Number(orderSelect?.value) || null, message: content, signature:media.signature(), client_message_id: messageUuid(), ...(returnId ? {return_id: returnId, report_issue: reportIssue} : {}) };
        input.disabled = true;
        sendButton.disabled = true;
        media.disable(true);
        setStatus(media.hasFiles()?"Uploading attachments…":"Sending message…");
        try {
            const result = await api("/api/portal/reseller/inbox/messages", {
                method: "POST",
                body: media.body(pendingSend),
            });
            if (!result.attachments_not_sent) {
                input.value = "";
                if (orderSelect && (Number(orderSelect.value) || null) === pendingSend.order_id) {
                    orderSelect.value = ""; orderCard?.show("");
                }
                media.clear();
            }
            if (result.message) appendMessage(result.message);
            const note = root.querySelector("[data-return-chat-notice]");
            if (note) note.textContent = result.warning || (reportIssue ? "Your issue is saved for Supervisor/Owner review. Inventory and settlement are unchanged." : "Message sent about this return.");
            if (!result.attachments_not_sent) pendingSend = null;
            deleteButton.hidden = false;
            setStatus(`Messaging ${agentLabels[0].textContent}`, "live");
        } catch (error) {
            setStatus(error.message, "error");
            const note = root.querySelector("[data-return-chat-notice]");
            if (note) note.textContent = error.message;
        } finally {
            sending=false;
            sendButton.classList.remove("is-loading");
            input.disabled = sending || !(canMessage || reportIssue) || openConversationType !== "sales";
            sendButton.disabled = sending || !(canMessage || reportIssue) || openConversationType !== "sales";
            media.disable(sending || !(canMessage || reportIssue) || openConversationType !== "sales");
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
            setStatus(`Messaging ${agentLabels[0].textContent}`, "live");
        } catch (error) {
            setStatus(error.message, "error");
        } finally {
            deleteButton.disabled = false;
        }
    });

    if (replacementStage) openReplacementConversation();

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

    refreshMessages({ reset: true }).then(() => { if (returnId) return openConversation(); }).catch((error) => setStatus(error.message, "error"));
    refreshTimer = window.setInterval(() => {if(!document.hidden)refreshMessages().catch(() => {});}, 3000);
    window.addEventListener("pagehide", () => window.clearInterval(refreshTimer), { once: true });
}());
