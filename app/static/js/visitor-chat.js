export function bindVisitorChat() {
    const widget = document.querySelector("[data-chatbot]");
    if (!widget) {
        return;
    }

    const toggle = widget.querySelector(".chatbot-toggle");
    const panel = widget.querySelector(".chatbot-panel");
    const closeButton = widget.querySelector("[data-chatbot-close]");
    const form = widget.querySelector("[data-chatbot-form]");
    const input = form.querySelector("input[name='message']");
    const sendButton = form.querySelector("button[type='submit']");
    const sendLabel = sendButton.querySelector("[data-chatbot-send-label]");
    const messages = widget.querySelector("[data-chatbot-messages]");
    const chatbotStorageKey = "meattrack_chatbot_messages_v1";
    const liveChatStorageKey = "meattrack_live_chat_v1";
    const liveChatRatingStorageKey = "meattrack_live_chat_rating_v1";
    const chatbotStorageTtlMs = 24 * 60 * 60 * 1000;
    const defaultSuggestions = ["View products", "Delivery details", "Become a reseller"];
    const newChatWelcome = "Welcome to Batangas Premium. How can I help you today?";
    let activeSuggestions = [...defaultSuggestions];
    let requestPending = false;
    let liveConversation = null;
    let liveLastMessageId = 0;
    let liveRealtime = null;
    let liveChannel = null;
    let livePollTimer = null;
    let lastLiveStatus = null;
    let liveRefreshPromise = null;
    let liveRefreshQueued = false;
    let pendingLiveChatRating = null;
    let leaderTypingBubble = null;
    let leaderTypingTimer = null;
    let visitorTypingTimer = null;
    let visitorTypingSent = false;
    let visitorTypingLastSentAt = 0;

    messages.setAttribute("role", "log");
    messages.setAttribute("aria-live", "polite");
    messages.setAttribute("aria-label", "Chat conversation");
    input.maxLength = 500;

    const suggestions = document.createElement("div");
    suggestions.className = "chatbot-suggestions";
    suggestions.setAttribute("aria-label", "Suggested replies");
    form.before(suggestions);

    const inquiryFormPanel = document.createElement("section");
    inquiryFormPanel.className = "chatbot-inquiry-form";
    inquiryFormPanel.hidden = true;
    inquiryFormPanel.setAttribute("aria-label", "Reseller inquiry form");
    inquiryFormPanel.innerHTML = `
        <div class="chatbot-inquiry-heading">
            <span>Reseller application</span>
            <strong>Tell us about your business</strong>
            <p>Your assigned sales leader will review these details. Submitting does not create a reseller account.</p>
        </div>
        <form data-chatbot-inquiry-form>
            <label>Full name<input name="name" maxlength="80" autocomplete="name" required></label>
            <label>Business name<input name="business_name" maxlength="160" autocomplete="organization" required></label>
            <div class="chatbot-inquiry-pair">
                <label>Email<input name="email" type="email" maxlength="254" autocomplete="email" required></label>
                <label>Contact number<input name="contact_number" type="tel" maxlength="30" autocomplete="tel" required></label>
            </div>
            <label>Business location<input name="location" maxlength="180" autocomplete="street-address" required></label>
            <label>Notes <span>(optional)</span><textarea name="notes" maxlength="500" rows="3" placeholder="Products, volume, or questions"></textarea></label>
            <label class="chatbot-inquiry-consent"><input name="consent_confirmed" type="checkbox" required><span>I confirm these details may be stored and reviewed for my reseller application.</span></label>
            <div class="chatbot-inquiry-actions">
                <button type="button" class="chatbot-inquiry-later" data-chatbot-inquiry-later>Complete later</button>
                <button type="submit" class="chatbot-inquiry-submit">Submit inquiry</button>
            </div>
            <p class="chatbot-inquiry-status" data-chatbot-inquiry-status role="status"></p>
        </form>`;
    messages.after(inquiryFormPanel);
    const inquiryForm = inquiryFormPanel.querySelector("[data-chatbot-inquiry-form]");
    const inquiryStatus = inquiryFormPanel.querySelector("[data-chatbot-inquiry-status]");

    const resetButton = document.createElement("button");
    resetButton.type = "button";
    resetButton.className = "chatbot-reset";
    resetButton.textContent = "New chat";
    resetButton.setAttribute("aria-label", "Start a new conversation");
    closeButton.before(resetButton);

    function setBusy(busy) {
        requestPending = busy;
        input.disabled = busy;
        sendButton.disabled = busy;
        suggestions.querySelectorAll("button").forEach((button) => {
            button.classList.toggle("is-disabled", busy);
            button.setAttribute("aria-disabled", String(busy));
        });
        sendLabel.textContent = busy ? "Sending" : "Send";
        sendButton.classList.toggle("is-sending", busy);
        form.setAttribute("aria-busy", String(busy));
    }

    function renderSuggestions(items = []) {
        const nextSuggestions = (Array.isArray(items) ? items : [])
            .map((label) => String(label).trim())
            .filter(Boolean)
            .slice(0, 5);
        if (nextSuggestions.length) {
            activeSuggestions = nextSuggestions;
        }

        const fragment = document.createDocumentFragment();
        activeSuggestions.forEach((label) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "chatbot-suggestion";
            button.textContent = label;
            button.classList.toggle("is-disabled", requestPending);
            button.setAttribute("aria-disabled", String(requestPending));
            button.addEventListener("click", () => {
                if (requestPending) {
                    return;
                }
                input.value = String(label);
                form.requestSubmit();
            });
            fragment.appendChild(button);
        });
        suggestions.replaceChildren(fragment);
    }

    async function typeBotReply(bubble, reply) {
        const text = String(reply || "Please contact Batangas Premium directly for complete details.");
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        bubble.removeAttribute("aria-label");

        if (reduceMotion || text.length < 2) {
            bubble.textContent = text;
            return;
        }

        const characters = Array.from(text);
        const chunkSize = characters.length > 180 ? 3 : characters.length > 90 ? 2 : 1;
        const delay = Math.max(14, Math.min(30, Math.round(1400 / Math.ceil(characters.length / chunkSize))));
        const previousLiveSetting = messages.getAttribute("aria-live") || "polite";

        messages.setAttribute("aria-live", "off");
        bubble.setAttribute("aria-label", text);
        bubble.classList.add("is-typing");
        bubble.textContent = "";

        for (let index = 0; index < characters.length; index += chunkSize) {
            bubble.textContent += characters.slice(index, index + chunkSize).join("");
            messages.scrollTop = messages.scrollHeight;
            await new Promise((resolve) => window.setTimeout(resolve, delay));
        }

        bubble.classList.remove("is-typing");
        bubble.removeAttribute("aria-label");
        messages.setAttribute("aria-live", previousLiveSetting);
    }

    function currentChatMessages() {
        return Array.from(messages.querySelectorAll(".message:not(.live-agent-typing)")).map((bubble) => ({
            text: bubble.textContent || "",
            type: bubble.classList.contains("user-message") ? "user" : "bot",
            liveMessageId: bubble.dataset.liveMessageId || null,
            liveSenderName: bubble.dataset.liveSenderName || null,
            liveSender: bubble.classList.contains("agent-message")
                ? "team_leader"
                : bubble.classList.contains("system-message") ? "system" : null,
        })).filter((item) => item.text.trim());
    }

    function saveChatMessages() {
        try {
            window.localStorage.setItem(chatbotStorageKey, JSON.stringify({
                savedAt: Date.now(),
                messages: currentChatMessages(),
                suggestions: activeSuggestions,
            }));
        } catch (error) {
            // Ignore storage failures in private browsing or restricted webviews.
        }
    }

    function restoreChatMessages() {
        try {
            const stored = JSON.parse(window.localStorage.getItem(chatbotStorageKey) || "null");
            if (!stored || !Array.isArray(stored.messages) || Date.now() - Number(stored.savedAt || 0) > chatbotStorageTtlMs) {
                window.localStorage.removeItem(chatbotStorageKey);
                saveChatMessages();
                return;
            }
            messages.innerHTML = "";
            stored.messages.forEach((item) => {
                let messageText = String(item.text || "");
                let senderName = String(item.liveSenderName || "");
                if (item.liveSender === "team_leader" && !senderName) {
                    const legacyPrefix = messageText.match(/^((?:Sales\s+)?(?:Team\s+)?Leader[^:]{0,60}):\s*(.*)$/is);
                    if (legacyPrefix) {
                        senderName = legacyPrefix[1].trim();
                        messageText = legacyPrefix[2];
                    }
                }
                const bubble = addMessage(messageText, item.type, false);
                const liveMessageId = Number(item.liveMessageId || 0);
                if (liveMessageId > 0) {
                    bubble.dataset.liveMessageId = String(liveMessageId);
                    liveLastMessageId = Math.max(liveLastMessageId, liveMessageId);
                }
                if (item.liveSender === "team_leader") {
                    bubble.classList.add("agent-message");
                    addLiveSenderLabel(bubble, senderName || "Sales team leader");
                }
                if (item.liveSender === "system") bubble.classList.add("system-message");
            });
            if (Array.isArray(stored.suggestions) && stored.suggestions.length) {
                activeSuggestions = stored.suggestions
                    .map((label) => String(label).trim())
                    .filter(Boolean)
                    .slice(0, 5);
            }
        } catch (error) {
            saveChatMessages();
        }
    }

    function setOpen(open) {
        panel.hidden = !open;
        widget.classList.toggle("is-open", open);
        toggle.setAttribute("aria-expanded", String(open));
        if (open) {
            messages.scrollTop = messages.scrollHeight;
            input.focus();
        }
    }

    function addMessage(text, type, shouldSave = true) {
        const bubble = document.createElement("div");
        bubble.className = `message ${type === 'user' ? 'user-message' : 'bot-message'}`;
        bubble.textContent = text;
        messages.appendChild(bubble);
        messages.scrollTop = messages.scrollHeight;
        if (shouldSave) {
            saveChatMessages();
        }
        return bubble;
    }

    function liveMessageExists(messageId) {
        return Boolean(messages.querySelector(`[data-live-message-id="${messageId}"]`));
    }

    function addLiveSenderLabel(bubble, senderName) {
        const cleanName = String(senderName || "Sales team leader").trim() || "Sales team leader";
        bubble.dataset.liveSenderName = cleanName;
        const label = document.createElement("span");
        label.className = "live-message-sender-label";
        label.textContent = cleanName;
        messages.insertBefore(label, bubble);
    }

    function addLiveMessage(item) {
        const messageId = Number(item.chat_message_id || item.message_id || 0);
        if (!messageId || liveMessageExists(messageId)) return;
        const sender = item.sender_type;
        setLeaderTyping(false);
        const bubble = addMessage(item.content || item.text || "", sender === "visitor" ? "user" : "bot", false);
        bubble.dataset.liveMessageId = String(messageId);
        if (sender === "team_leader") {
            bubble.classList.add("agent-message");
            addLiveSenderLabel(bubble, item.sender_name || liveConversation?.assigned_team_leader_name);
        }
        if (sender === "system") bubble.classList.add("system-message");
        liveLastMessageId = Math.max(liveLastMessageId, messageId);
        saveChatMessages();
    }

    async function liveApi(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: {
                ...(options.body ? { "Content-Type": "application/json" } : {}),
                "X-Live-Chat-Actor": "visitor",
                ...(options.headers || {}),
            },
        });
        const contentType = response.headers.get("content-type") || "";
        const result = contentType.includes("application/json") ? await response.json() : await response.text();
        if (!response.ok) {
            throw new Error(result.message || result.detail || result.reply || "Live chat request failed.");
        }
        return result;
    }

    function saveLiveConversation() {
        try {
            if (liveConversation) {
                window.localStorage.setItem(liveChatStorageKey, JSON.stringify({
                    conversation_id: liveConversation.conversation_id,
                    savedAt: Date.now(),
                }));
            } else {
                window.localStorage.removeItem(liveChatStorageKey);
            }
        } catch (error) {
            // The secure server session remains authoritative.
        }
    }

    async function loadLiveMessages(reset = false) {
        if (!liveConversation) return;
        if (reset) {
            liveLastMessageId = Math.max(
                0,
                ...Array.from(messages.querySelectorAll("[data-live-message-id]"))
                    .map((bubble) => Number(bubble.dataset.liveMessageId || 0)),
            );
        }
        const result = await liveApi(
            `/api/live-chat/${encodeURIComponent(liveConversation.conversation_id)}/messages?after_id=${liveLastMessageId}`,
        );
        (result.messages || []).forEach(addLiveMessage);
    }

    async function attachVisitorLiveChannel() {
        if (!window.Ably || !liveConversation) return;
        if (liveRealtime) {
            try { liveRealtime.close(); } catch (error) { /* reconnect below */ }
        }
        liveRealtime = new window.Ably.Realtime({
            authCallback: async (_params, callback) => {
                try {
                    const token = await liveApi("/api/ably/token", {
                        method: "POST",
                        body: JSON.stringify({
                            scope: "conversation",
                            conversation_id: liveConversation.conversation_id,
                        }),
                    });
                    callback(null, token);
                } catch (error) {
                    callback(error, null);
                }
            },
        });
        liveChannel = liveRealtime.channels.get(`support:${liveConversation.conversation_id}`);
        liveChannel.subscribe((event) => {
            let data = event.data || {};
            if (typeof data === "string") {
                try { data = JSON.parse(data); } catch (error) { data = {}; }
            }
            if (event.name === "typing.updated" && data.sender_type === "team_leader") {
                setLeaderTyping(Boolean(data.is_typing), data.sender_name);
                return;
            }
            refreshLiveConversation().catch(() => {});
        });
    }

    function setLeaderTyping(isTyping, name = "") {
        if (leaderTypingTimer) window.clearTimeout(leaderTypingTimer);
        leaderTypingTimer = null;
        if (!isTyping || !liveConversation) {
            leaderTypingBubble?.remove();
            leaderTypingBubble = null;
            return;
        }
        if (!leaderTypingBubble) {
            leaderTypingBubble = document.createElement("div");
            leaderTypingBubble.className = "message bot-message agent-message live-agent-typing";
            const label = document.createElement("span");
            label.className = "live-agent-typing-label";
            const dots = document.createElement("span");
            dots.className = "live-agent-typing-dots";
            dots.setAttribute("aria-hidden", "true");
            dots.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
            leaderTypingBubble.append(label, dots);
            messages.append(leaderTypingBubble);
        }
        leaderTypingBubble.querySelector(".live-agent-typing-label").textContent = `${name || "Sales team leader"} is typing`;
        messages.scrollTop = messages.scrollHeight;
        leaderTypingTimer = window.setTimeout(() => setLeaderTyping(false), 2600);
    }

    function sendVisitorTyping(isTyping) {
        if (!liveConversation || liveConversation.status !== "active") return;
        const now = Date.now();
        if (isTyping && visitorTypingSent && now - visitorTypingLastSentAt < 1200) return;
        if (!isTyping && !visitorTypingSent) return;
        visitorTypingSent = isTyping;
        visitorTypingLastSentAt = now;
        liveApi(`/api/live-chat/${encodeURIComponent(liveConversation.conversation_id)}/typing`, {
            method: "POST",
            body: JSON.stringify({ is_typing: isTyping }),
        }).catch(() => {});
    }

    function stopVisitorTyping() {
        if (visitorTypingTimer) window.clearTimeout(visitorTypingTimer);
        visitorTypingTimer = null;
        sendVisitorTyping(false);
    }

    async function activateLiveConversation(conversation, announce = true) {
        clearPendingLiveChatRating();
        liveConversation = conversation;
        lastLiveStatus = conversation.status;
        saveLiveConversation();
        if (announce) {
            if (conversation.status === "queued") {
                addMessage("You’re in the live-chat queue. I’ll keep this window updated while we look for an available sales team leader.", "bot");
            } else if (conversation.status === "active") {
                addMessage(`You’re connected with ${conversation.assigned_team_leader_name || "a sales team leader"}.`, "bot");
            }
        }
        if (conversation.status === "active") {
            renderSuggestions(["End chat"]);
        } else {
            renderSuggestions(["Cancel"]);
        }
        await loadLiveMessages(true);
        renderInquiryForm(conversation);
        await attachVisitorLiveChannel();
        if (!livePollTimer) {
            livePollTimer = window.setInterval(() => refreshLiveConversation().catch(() => {}), 3000);
        }
    }

    async function refreshLiveConversationOnce() {
        if (!liveConversation) return;
        const conversationId = liveConversation.conversation_id;
        const previousStatus = lastLiveStatus;
        const previousLeaderId = liveConversation.assigned_team_leader_account_id || null;
        const result = await liveApi(`/api/live-chat/${encodeURIComponent(conversationId)}`);
        if (!liveConversation || liveConversation.conversation_id !== conversationId) return;
        const next = result.conversation;
        const nextLeaderId = next.assigned_team_leader_account_id || null;
        const assignmentChanged = String(previousLeaderId || "") !== String(nextLeaderId || "");
        if (assignmentChanged) setLeaderTyping(false);
        liveConversation = next;
        lastLiveStatus = next.status;
        saveLiveConversation();
        setLeaderTyping(Boolean(next.leader_is_typing), next.assigned_team_leader_name);
        await loadLiveMessages();
        renderInquiryForm(next);
        if (next.status === "active" && (next.status !== previousStatus || assignmentChanged)) {
            const leaderName = next.assigned_team_leader_name || "a sales team leader";
            const copy = previousStatus === "active" && assignmentChanged
                ? `Your chat was transferred. You’re now connected with ${leaderName}.`
                : `You’re now connected with ${leaderName}.`;
            addMessage(copy, "bot");
            renderSuggestions(["End chat"]);
        } else if (next.status !== previousStatus && next.status === "queued" && next.claimed_at) {
            addMessage("Your conversation was returned to the sales queue. We’ll connect you with another team leader.", "bot");
            renderSuggestions(["End chat"]);
        } else if (next.status !== previousStatus && next.status === "awaiting_contact") {
            addMessage("No team leader accepted within two minutes. Enter an email address or phone number and the team will follow up.", "bot");
            input.placeholder = "Email address or contact number";
            renderSuggestions(["Cancel"]);
        } else if (next.status !== previousStatus && ["closed", "cancelled"].includes(next.status)) {
            addMessage(next.status === "closed" ? "The team leader closed the live conversation. How else can I help?" : "The live conversation was cancelled.", "bot");
            clearLiveConversation();
            offerLiveChatRating(next);
        }
    }

    async function refreshLiveConversation() {
        if (!liveConversation) return;
        if (liveRefreshPromise) {
            liveRefreshQueued = true;
            return liveRefreshPromise;
        }
        liveRefreshPromise = (async () => {
            do {
                liveRefreshQueued = false;
                await refreshLiveConversationOnce();
            } while (liveRefreshQueued && liveConversation);
        })();
        try {
            await liveRefreshPromise;
        } finally {
            liveRefreshPromise = null;
        }
    }

    function clearLiveConversation() {
        stopVisitorTyping();
        setLeaderTyping(false);
        liveConversation = null;
        lastLiveStatus = null;
        liveRefreshQueued = false;
        liveLastMessageId = 0;
        input.placeholder = "Type your message...";
        if (livePollTimer) window.clearInterval(livePollTimer);
        livePollTimer = null;
        if (liveRealtime) liveRealtime.close();
        liveRealtime = null;
        liveChannel = null;
        saveLiveConversation();
        activeSuggestions = [...defaultSuggestions];
        renderSuggestions(defaultSuggestions);
        inquiryFormPanel.hidden = true;
        inquiryStatus.textContent = "";
    }

    function renderInquiryForm(conversation) {
        const shouldShow = Boolean(conversation?.inquiry_form_requested_at)
            && !conversation?.has_reseller_inquiry
            && conversation.status === "active";
        inquiryFormPanel.hidden = !shouldShow;
        if (!shouldShow) return;
        const nameInput = inquiryForm.elements.namedItem("name");
        if (nameInput && !nameInput.value) nameInput.value = conversation.display_name || "";
        messages.scrollTop = messages.scrollHeight;
    }

    inquiryFormPanel.querySelector("[data-chatbot-inquiry-later]").addEventListener("click", () => {
        inquiryFormPanel.hidden = true;
        addMessage("You can complete the reseller inquiry form anytime while this live chat remains open.", "bot");
    });

    inquiryForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!liveConversation || liveConversation.status !== "active") return;
        const submitButton = inquiryForm.querySelector("button[type='submit']");
        const values = new FormData(inquiryForm);
        submitButton.disabled = true;
        inquiryStatus.textContent = "Submitting your inquiry…";
        try {
            const result = await liveApi(`/api/live-chat/${encodeURIComponent(liveConversation.conversation_id)}/inquiry`, {
                method: "POST",
                body: JSON.stringify({
                    name: values.get("name"),
                    business_name: values.get("business_name"),
                    email: values.get("email"),
                    contact_number: values.get("contact_number"),
                    location: values.get("location"),
                    notes: values.get("notes"),
                    consent_confirmed: values.get("consent_confirmed") === "on",
                }),
            });
            inquiryForm.reset();
            inquiryFormPanel.hidden = true;
            liveConversation = { ...liveConversation, ...(result.conversation || {}) };
            lastLiveStatus = liveConversation.status;
            saveLiveConversation();
            addLiveMessage(result.message);
            renderInquiryForm(liveConversation);
            renderSuggestions(["End chat"]);
            input.focus();
        } catch (error) {
            inquiryStatus.textContent = error.message;
        } finally {
            submitButton.disabled = false;
        }
    });

    function clearPendingLiveChatRating() {
        pendingLiveChatRating = null;
        try {
            window.localStorage.removeItem(liveChatRatingStorageKey);
        } catch (error) {
            // Rating recovery is optional when browser storage is unavailable.
        }
    }

    function offerLiveChatRating(conversation) {
        if (!conversation?.conversation_id || !conversation.claimed_at || !conversation.assigned_team_leader_account_id
            || conversation.customer_service_rating) return;
        pendingLiveChatRating = {
            conversation_id: conversation.conversation_id,
            team_leader_name: conversation.assigned_team_leader_name || "your sales team leader",
            savedAt: Date.now(),
        };
        try {
            window.localStorage.setItem(liveChatRatingStorageKey, JSON.stringify(pendingLiveChatRating));
        } catch (error) {
            // The rating remains available for the current page session.
        }
        addMessage(`How would you rate the customer service from ${pendingLiveChatRating.team_leader_name}? Choose 1 to 5 stars.`, "bot");
        renderSuggestions(["1 star", "2 stars", "3 stars", "4 stars", "5 stars"]);
    }

    function restorePendingLiveChatRating() {
        if (pendingLiveChatRating) return;
        try {
            const stored = JSON.parse(window.localStorage.getItem(liveChatRatingStorageKey) || "null");
            if (!stored?.conversation_id || Date.now() - Number(stored.savedAt || 0) > chatbotStorageTtlMs) {
                clearPendingLiveChatRating();
                return;
            }
            pendingLiveChatRating = stored;
            addMessage(`How would you rate the customer service from ${stored.team_leader_name || "your sales team leader"}? Choose 1 to 5 stars.`, "bot");
            renderSuggestions(["1 star", "2 stars", "3 stars", "4 stars", "5 stars"]);
        } catch (error) {
            clearPendingLiveChatRating();
        }
    }

    async function restoreLiveConversation() {
        try {
            const stored = JSON.parse(window.localStorage.getItem(liveChatStorageKey) || "null");
            if (!stored?.conversation_id || Date.now() - Number(stored.savedAt || 0) > chatbotStorageTtlMs) return;
            const result = await liveApi(`/api/live-chat/${encodeURIComponent(stored.conversation_id)}`);
            if (["queued", "active", "awaiting_contact", "follow_up"].includes(result.conversation.status)) {
                await activateLiveConversation(result.conversation, false);
            } else if (["closed", "cancelled"].includes(result.conversation.status)) {
                clearLiveConversation();
                offerLiveChatRating(result.conversation);
            } else {
                clearLiveConversation();
            }
        } catch (error) {
            clearLiveConversation();
        }
    }

    restoreChatMessages();
    renderSuggestions(activeSuggestions);
    restoreLiveConversation().then(restorePendingLiveChatRating);
    toggle.addEventListener("click", () => setOpen(panel.hidden));
    closeButton.addEventListener("click", () => {
        setOpen(false);
        toggle.focus();
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !panel.hidden) {
            setOpen(false);
            toggle.focus();
        }
    });
    resetButton.addEventListener("click", async () => {
        if (requestPending) {
            return;
        }
        setBusy(true);
        input.value = "";

        try {
            if (liveConversation && ["queued", "active", "awaiting_contact"].includes(liveConversation.status)) {
                if (!window.confirm("End the current live conversation and start a new chat?")) {
                    return;
                }
                await liveApi(`/api/live-chat/${encodeURIComponent(liveConversation.conversation_id)}/cancel`, {
                    method: "POST",
                    body: JSON.stringify({}),
                });
                clearLiveConversation();
            }
            const response = await fetch("/api/chatbot", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "reset" }),
            });
            if (!response.ok) {
                throw new Error("Unable to reset the server-side conversation.");
            }
            messages.innerHTML = "";
            activeSuggestions = [...defaultSuggestions];
            renderSuggestions(defaultSuggestions);
            clearPendingLiveChatRating();
            try {
                window.localStorage.removeItem(chatbotStorageKey);
            } catch (error) {
                // The confirmed server reset still starts a fresh in-memory transcript.
            }
            const resetMessage = addMessage("", "bot", false);
            await typeBotReply(resetMessage, newChatWelcome);
            saveChatMessages();
        } catch (error) {
            const errorMessage = addMessage("", "bot", false);
            await typeBotReply(
                errorMessage,
                "I couldn't start a new chat right now. Your current conversation is still active; please try again.",
            );
            saveChatMessages();
        } finally {
            setBusy(false);
            input.focus();
        }
    });
    document.querySelectorAll("[data-open-chatbot]").forEach((button) => {
        button.addEventListener("click", () => {
            setOpen(true);
            input.value = button.dataset.chatbotPrompt || "I want to be a reseller";
        });
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (requestPending) {
            return;
        }
        const question = input.value.trim();
        if (!question) {
            return;
        }
        if (pendingLiveChatRating) {
            const match = question.match(/^([1-5])(?:\s+stars?)?$/i);
            if (!match) {
                addMessage("Please choose a rating from 1 to 5 stars.", "bot");
                input.value = "";
                return;
            }
            const rating = Number(match[1]);
            const ratingConversation = pendingLiveChatRating;
            input.value = "";
            addMessage(`${rating} star${rating === 1 ? "" : "s"}`, "user");
            setBusy(true);
            try {
                await liveApi(`/api/live-chat/${encodeURIComponent(ratingConversation.conversation_id)}/rating`, {
                    method: "POST",
                    body: JSON.stringify({ rating }),
                });
                clearPendingLiveChatRating();
                addMessage("Thank you. Your customer-service rating was saved for the sales team leader.", "bot");
                activeSuggestions = [...defaultSuggestions];
                renderSuggestions(defaultSuggestions);
            } catch (error) {
                addMessage(error.message, "bot");
                renderSuggestions(["1 star", "2 stars", "3 stars", "4 stars", "5 stars"]);
            } finally {
                setBusy(false);
                input.focus();
            }
            return;
        }
        const liveAction = question.toLowerCase();
        if (liveConversation && ["cancel", "end chat"].includes(liveAction)) {
            const endingActiveChat = liveConversation.status === "active" || Boolean(liveConversation.claimed_at);
            if (endingActiveChat && !window.confirm("End this live conversation?")) {
                input.value = "";
                return;
            }
            input.value = "";
            setBusy(true);
            try {
                const result = await liveApi(`/api/live-chat/${encodeURIComponent(liveConversation.conversation_id)}/cancel`, {
                    method: "POST",
                    body: JSON.stringify({}),
                });
                const endedConversation = { ...liveConversation, ...(result.conversation || {}) };
                addMessage(
                    endingActiveChat
                        ? "The live chat has ended. How else can I help?"
                        : "The live-chat request was cancelled. How else can I help?",
                    "bot",
                );
                clearLiveConversation();
                offerLiveChatRating(endedConversation);
            } catch (error) {
                addMessage(error.message, "bot");
            } finally {
                setBusy(false);
            }
            return;
        }
        if (liveConversation?.status === "active") {
            input.value = "";
            setBusy(true);
            stopVisitorTyping();
            try {
                const result = await liveApi(`/api/live-chat/${encodeURIComponent(liveConversation.conversation_id)}/messages`, {
                    method: "POST",
                    body: JSON.stringify({ message: question, client_message_id: crypto.randomUUID() }),
                });
                addLiveMessage(result.message);
            } catch (error) {
                input.value = question;
                addMessage(error.message, "bot");
            } finally {
                setBusy(false);
                input.focus();
            }
            return;
        }
        if (liveConversation?.status === "awaiting_contact") {
            input.value = "";
            setBusy(true);
            try {
                const result = await liveApi(`/api/live-chat/${encodeURIComponent(liveConversation.conversation_id)}/fallback`, {
                    method: "POST",
                    body: JSON.stringify({ contact: question }),
                });
                addMessage("Your contact details were saved for follow-up. No reseller account or formal inquiry was created.", "bot");
                liveConversation = result.conversation;
                saveLiveConversation();
                clearLiveConversation();
            } catch (error) {
                input.value = question;
                addMessage(error.message, "bot");
            } finally {
                setBusy(false);
                input.focus();
            }
            return;
        }
        if (liveConversation?.status === "queued") {
            addMessage("We’re still looking for an available sales team leader. You can cancel or keep this window open.", "bot");
            input.value = "";
            return;
        }
        input.value = "";
        addMessage(question, "user");
        const loading = addMessage("Checking approved Batangas Premium information...", "bot");
        loading.setAttribute("aria-label", "Assistant is responding");
        setBusy(true);

        try {
            const response = await fetch("/api/chatbot", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: question }),
            });
            const result = await response.json();
            loading.removeAttribute("aria-label");
            await typeBotReply(
                loading,
                result.reply || "Please contact Batangas Premium directly for complete details.",
            );
            if (result.conversation && result.handoff?.started) {
                await activateLiveConversation(result.conversation, false);
            }
            renderSuggestions(result.suggestions || []);
            saveChatMessages();
        } catch (error) {
            await typeBotReply(loading, "I couldn't connect right now. Please check your connection and try again.");
            input.value = question;
            saveChatMessages();
        } finally {
            setBusy(false);
            input.focus();
        }
    });

    input.addEventListener("input", () => {
        if (!liveConversation || liveConversation.status !== "active") return;
        if (visitorTypingTimer) window.clearTimeout(visitorTypingTimer);
        const isTyping = Boolean(input.value.trim());
        sendVisitorTyping(isTyping);
        if (isTyping) visitorTypingTimer = window.setTimeout(stopVisitorTyping, 1400);
    });
    input.addEventListener("keydown", (event) => {
        if (event.key.length === 1 && liveConversation?.status === "active") sendVisitorTyping(true);
    });
    input.addEventListener("blur", stopVisitorTyping);
}

