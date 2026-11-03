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
    const successfulResetCooldownSeconds = 5;
    const defaultSuggestions = ["View products", "Delivery details", "How to Become a Reseller?"];
    const newChatWelcome = "Welcome to Batangas Premium. How can I help you today?";
    let activeSuggestions = [...defaultSuggestions];
    let requestPending = false;
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
        return Array.from(messages.querySelectorAll(".message")).map((bubble) => ({
            text: bubble.textContent || "",
            type: bubble.classList.contains("user-message") ? "user" : "bot",
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
                const messageText = String(item.text || "");
                const bubble = addMessage(messageText, item.type, false);
                if (item.productPreview) {
                    try {
                        const preview = typeof item.productPreview === "string"
                            ? JSON.parse(item.productPreview)
                            : item.productPreview;
                        renderProductPreview(bubble, preview, preview?.reply || messageText);
                    } catch (error) {
                        // Keep the saved text when an older preview is invalid.
                    }
                }
            });
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

    restoreChatMessages();
    renderSuggestions(activeSuggestions);
    try {
        window.localStorage.removeItem(liveChatStorageKey);
        window.localStorage.removeItem(liveChatRatingStorageKey);
    } catch (error) {
        // Browser storage is optional.
    }
    renderSuggestions(defaultSuggestions);
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
        if (requestPending || resetCooldownUntil > Date.now()) {
            return;
        }
        setBusy(true);
        input.value = "";

        try {
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
                "I couldn't start a new chat right now. Please try again.",
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
            const prompt = button.dataset.chatbotPrompt;
            if (prompt) {
                input.value = prompt;
                form.requestSubmit();
            }
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

}

