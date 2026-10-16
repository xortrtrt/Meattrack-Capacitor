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
    const identityName = widget.querySelector(".chatbot-identity strong");
    const identityStatus = widget.querySelector(".chatbot-identity > span");
    const chatbotStorageKey = "meattrack_chatbot_messages_v1";
    const liveChatStorageKey = "meattrack_live_chat_v1";
    const liveChatRatingStorageKey = "meattrack_live_chat_rating_v1";
    const chatbotStorageTtlMs = 24 * 60 * 60 * 1000;
    const successfulResetCooldownSeconds = 5;
    const defaultSuggestions = ["View products", "Delivery details", "Become a reseller"];
    const newChatWelcome = "Welcome to Batangas Premium. How can I help you today?";
    const inquiryDeferredNotice = "You can complete the reseller inquiry form anytime while this live chat remains open.";
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
    let resetCooldownUntil = 0;
    let resetCooldownTimer = null;

    messages.setAttribute("role", "log");
    messages.setAttribute("aria-live", "polite");
    messages.setAttribute("aria-label", "Chat conversation");
    input.maxLength = 500;

    const suggestions = document.createElement("div");
    suggestions.className = "chatbot-suggestions";
    suggestions.setAttribute("aria-label", "Suggested replies");
    form.before(suggestions);

    const inquiryPrompt = document.createElement("aside");
    inquiryPrompt.className = "chatbot-inquiry-prompt";
    inquiryPrompt.hidden = true;
    inquiryPrompt.setAttribute("aria-label", "Reseller inquiry form available");
    inquiryPrompt.innerHTML = `
        <div>
            <span>Reseller inquiry</span>
            <strong>Your application form is ready</strong>
            <p>Share your business details with your sales leader when you’re ready.</p>
        </div>
        <button type="button" data-chatbot-inquiry-open>Open form</button>`;
    messages.before(inquiryPrompt);

    const inquiryModal = document.createElement("div");
    inquiryModal.className = "chatbot-inquiry-modal";
    inquiryModal.hidden = true;
    inquiryModal.innerHTML = `
        <button type="button" class="chatbot-inquiry-backdrop" data-chatbot-inquiry-later aria-label="Complete inquiry later"></button>
        <section class="chatbot-inquiry-dialog" role="dialog" aria-modal="true" aria-labelledby="chatbot-inquiry-title">
            <div class="chatbot-inquiry-heading">
                <span>Reseller application</span>
                <strong id="chatbot-inquiry-title">Tell us about your business</strong>
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
            </form>
        </section>`;
    panel.append(inquiryModal);
    const inquiryOpenButton = inquiryPrompt.querySelector("[data-chatbot-inquiry-open]");
    const inquiryDialog = inquiryModal.querySelector(".chatbot-inquiry-dialog");
    const inquiryForm = inquiryModal.querySelector("[data-chatbot-inquiry-form]");
    const inquiryStatus = inquiryModal.querySelector("[data-chatbot-inquiry-status]");

    const resetButton = document.createElement("button");
    resetButton.type = "button";
    resetButton.className = "chatbot-reset";
    resetButton.textContent = "New chat";
    resetButton.setAttribute("aria-label", "Start a new conversation");
    closeButton.before(resetButton);

    function syncResetButton() {
        const remainingSeconds = Math.max(0, Math.ceil((resetCooldownUntil - Date.now()) / 1000));
        if (remainingSeconds > 0) {
            const minutes = Math.floor(remainingSeconds / 60);
            const seconds = remainingSeconds % 60;
            const countdown = `${minutes}:${String(seconds).padStart(2, "0")}`;
            resetButton.textContent = `Try again in ${countdown}`;
            resetButton.setAttribute("aria-label", `New chat available in ${countdown}`);
        } else {
            resetCooldownUntil = 0;
            resetButton.textContent = "New chat";
            resetButton.setAttribute("aria-label", "Start a new conversation");
            if (resetCooldownTimer) window.clearInterval(resetCooldownTimer);
            resetCooldownTimer = null;
        }
        resetButton.disabled = requestPending || remainingSeconds > 0;
    }

    function startResetCooldown(seconds) {
        const durationSeconds = Math.max(1, Math.ceil(Number(seconds) || 0));
        resetCooldownUntil = Date.now() + durationSeconds * 1000;
        if (resetCooldownTimer) window.clearInterval(resetCooldownTimer);
        syncResetButton();
        resetCooldownTimer = window.setInterval(syncResetButton, 1000);
    }

    function resetRetryAfterSeconds(response, result) {
        const header = String(response.headers.get("Retry-After") || "").trim();
        const headerSeconds = Number(header);
        if (header && Number.isFinite(headerSeconds) && headerSeconds > 0) {
            return Math.ceil(headerSeconds);
        }
        const retryAt = Date.parse(header);
        if (header && !Number.isNaN(retryAt) && retryAt > Date.now()) {
            return Math.max(1, Math.ceil((retryAt - Date.now()) / 1000));
        }
        const bodySeconds = Number(result?.retry_after);
        if (Number.isFinite(bodySeconds) && bodySeconds > 0) {
            return Math.ceil(bodySeconds);
        }
        return successfulResetCooldownSeconds;
    }

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
        syncResetButton();
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

    function normalizeProductPreview(preview, reply = "") {
        const products = (Array.isArray(preview?.products) ? preview.products : [])
            .map((product) => ({
                name: String(product?.name || "").trim().slice(0, 120),
                category: String(product?.category || "").trim().slice(0, 80),
                basePrice: Number(product?.base_price ?? product?.basePrice),
                packSize: String(product?.pack_size ?? product?.packSize ?? "").trim().slice(0, 30),
                packSizeUnit: String(product?.pack_size_unit ?? product?.packSizeUnit ?? "").trim().slice(0, 20),
            }))
            .filter((product) => product.name)
            .slice(0, 3);
        const requestedUrl = String(preview?.view_all_url || preview?.viewAllUrl || "/products").trim();
        const viewAllUrl = requestedUrl.startsWith("/") && !requestedUrl.startsWith("//")
            ? requestedUrl
            : "/products";
        return {
            reply: String(reply || "Browse our best-selling products.").trim(),
            title: String(preview?.title || "Top 3 best sellers").trim().slice(0, 80),
            products,
            viewAllUrl,
        };
    }

    function renderProductPreview(bubble, preview, reply = "") {
        const normalized = normalizeProductPreview(preview, reply);
        bubble.classList.add("product-preview-message");
        bubble.dataset.productPreview = JSON.stringify(normalized);
        bubble.replaceChildren();

        const intro = document.createElement("p");
        intro.className = "product-preview-intro";
        intro.textContent = normalized.reply;
        bubble.appendChild(intro);

        if (normalized.products.length) {
            const title = document.createElement("strong");
            title.className = "product-preview-title";
            title.textContent = normalized.title;
            bubble.appendChild(title);

            const list = document.createElement("ol");
            list.className = "product-preview-list";
            normalized.products.forEach((product, index) => {
                const item = document.createElement("li");
                item.className = "product-preview-item";

                const rank = document.createElement("span");
                rank.className = "product-preview-rank";
                rank.setAttribute("aria-hidden", "true");
                rank.textContent = String(index + 1).padStart(2, "0");

                const details = document.createElement("span");
                details.className = "product-preview-details";
                const name = document.createElement("strong");
                name.textContent = product.name;
                details.appendChild(name);

                const metaParts = [];
                if (product.category) metaParts.push(product.category);
                if (product.packSize && product.packSizeUnit) {
                    const numericPackSize = Number(product.packSize);
                    const displayPackSize = Number.isFinite(numericPackSize)
                        ? numericPackSize.toLocaleString("en-PH", { maximumFractionDigits: 3 })
                        : product.packSize;
                    metaParts.push(`${displayPackSize} ${product.packSizeUnit}`);
                }
                if (metaParts.length) {
                    const meta = document.createElement("span");
                    meta.textContent = metaParts.join(" · ");
                    details.appendChild(meta);
                }

                const price = document.createElement("span");
                price.className = "product-preview-price";
                price.textContent = Number.isFinite(product.basePrice) && product.basePrice > 0
                    ? `PHP ${product.basePrice.toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                    : "Ask for price";

                item.append(rank, details, price);
                list.appendChild(item);
            });
            bubble.appendChild(list);
        }

        const viewAll = document.createElement("a");
        viewAll.className = "product-preview-link";
        viewAll.href = normalized.viewAllUrl;
        viewAll.textContent = "View all products";
        bubble.appendChild(viewAll);
        messages.scrollTop = messages.scrollHeight;
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
            productPreview: bubble.dataset.productPreview || null,
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
                if (item.productPreview) {
                    try {
                        const preview = typeof item.productPreview === "string"
                            ? JSON.parse(item.productPreview)
                            : item.productPreview;
                        renderProductPreview(bubble, preview, preview?.reply || messageText);
                    } catch (error) {
                        // Keep the saved plain-text version when legacy preview data is invalid.
                    }
                }
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
        if (!open) closeInquiryModal(false);
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

    function addSystemNotice(text) {
        const alreadyShown = Array.from(messages.querySelectorAll(".system-message"))
            .some((message) => message.textContent?.trim() === text);
        if (alreadyShown) return;
        const notice = addMessage(text, "bot", false);
        notice.classList.add("system-message");
        saveChatMessages();
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

    function updateChatIdentity(conversation = null) {
        const hasLiveLeader = conversation?.status === "active"
            && Boolean(conversation?.assigned_team_leader_account_id);
        const name = hasLiveLeader
            ? (conversation.assigned_team_leader_name || "Sales team leader")
            : "BP ChatBot";
        identityName.textContent = name;
        identityStatus.textContent = hasLiveLeader
            ? "Live sales support"
            : "Products · Orders · Resellers";
        panel.setAttribute("aria-label", hasLiveLeader ? `${name} live support` : "Batangas Premium support");
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
        updateChatIdentity(conversation);
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
        updateChatIdentity(next);
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
        updateChatIdentity();
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
        inquiryPrompt.hidden = true;
        closeInquiryModal(false);
        inquiryStatus.textContent = "";
    }

    function inquiryRequestKey(conversation) {
        if (!conversation?.conversation_id || !conversation?.inquiry_form_requested_at) return "";
        return `${conversation.conversation_id}:${conversation.inquiry_form_requested_at}`;
    }

    function renderInquiryForm(conversation) {
        const requestKey = inquiryRequestKey(conversation);
        const shouldOffer = Boolean(requestKey)
            && !conversation?.has_reseller_inquiry
            && conversation.status === "active";
        inquiryPrompt.hidden = !shouldOffer;
        if (!shouldOffer) {
            closeInquiryModal(false);
            return;
        }
        const nameInput = inquiryForm.elements.namedItem("name");
        if (nameInput && !nameInput.value) nameInput.value = conversation.display_name || "";
    }

    function openInquiryModal() {
        if (inquiryPrompt.hidden) return;
        inquiryModal.hidden = false;
        panel.classList.add("has-inquiry-modal");
        window.requestAnimationFrame(() => {
            inquiryModal.classList.add("is-open");
            inquiryForm.elements.namedItem("name")?.focus();
        });
    }

    function closeInquiryModal(restoreFocus = true) {
        if (inquiryModal.hidden) return;
        inquiryModal.classList.remove("is-open");
        panel.classList.remove("has-inquiry-modal");
        inquiryModal.hidden = true;
        if (restoreFocus && !inquiryPrompt.hidden) inquiryOpenButton.focus();
    }

    inquiryOpenButton.addEventListener("click", openInquiryModal);
    inquiryModal.querySelectorAll("[data-chatbot-inquiry-later]").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.preventDefault();
            closeInquiryModal();
            addSystemNotice(inquiryDeferredNotice);
        });
    });
    inquiryDialog.addEventListener("keydown", (event) => {
        if (event.key !== "Tab") return;
        const focusable = Array.from(inquiryDialog.querySelectorAll("button, input, textarea"))
            .filter((element) => !element.disabled && element.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
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
            closeInquiryModal(false);
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
        if (event.key === "Escape" && !inquiryModal.hidden) {
            closeInquiryModal();
            return;
        }
        if (event.key === "Escape" && !panel.hidden) {
            setOpen(false);
            toggle.focus();
        }
    });
    resetButton.addEventListener("click", async () => {
        if (requestPending || resetCooldownUntil > Date.now()) {
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
            const contentType = response.headers.get("content-type") || "";
            const result = contentType.includes("application/json") ? await response.json() : {};
            if (response.status === 429) {
                startResetCooldown(resetRetryAfterSeconds(response, result));
                const errorMessage = addMessage("", "bot", false);
                await typeBotReply(
                    errorMessage,
                    result.reply || result.message || "Too many new-chat requests. Please wait before resetting again.",
                );
                saveChatMessages();
                return;
            }
            if (!response.ok) {
                throw new Error("Unable to reset the server-side conversation.");
            }
            startResetCooldown(successfulResetCooldownSeconds);
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
            if (result.product_preview) {
                renderProductPreview(
                    loading,
                    result.product_preview,
                    result.reply || "Browse our best-selling products.",
                );
            } else {
                await typeBotReply(
                    loading,
                    result.reply || "Please contact Batangas Premium directly for complete details.",
                );
            }
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

