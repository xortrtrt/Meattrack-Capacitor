(function () {
    if (window.lucide) {
        window.lucide.createIcons();
    }

    const today = new Date().toISOString().slice(0, 10);
    document.querySelectorAll('input[type="date"]').forEach((input) => {
        if (!input.value) {
            input.value = today;
        }
    });

    document.querySelectorAll("form").forEach((form) => {
        form.addEventListener("submit", () => {
            if (form.hasAttribute("data-chatbot-form")) {
                return;
            }
            const button = form.querySelector('button[type="submit"]');
            if (!button || button.dataset.keepLabel === "true") {
                return;
            }
            button.dataset.originalText = button.textContent.trim();
            button.classList.add("is-loading");
        });
    });

    const widget = document.querySelector("[data-chatbot]");
    if (!widget) {
        return;
    }

    const toggle = widget.querySelector(".chatbot-toggle");
    const panel = widget.querySelector(".chatbot-panel");
    const closeButton = widget.querySelector("[data-chatbot-close]");
    const form = widget.querySelector("[data-chatbot-form]");
    const input = form.querySelector("input[name='message']");
    const messages = widget.querySelector("[data-chatbot-messages]");

    function setOpen(open) {
        panel.hidden = !open;
        toggle.setAttribute("aria-expanded", String(open));
        if (open) {
            input.focus();
        }
    }

    function addMessage(text, type) {
        const bubble = document.createElement("div");
        bubble.className = `chatbot-message ${type}`;
        bubble.textContent = text;
        messages.appendChild(bubble);
        messages.scrollTop = messages.scrollHeight;
        return bubble;
    }

    toggle.addEventListener("click", () => setOpen(panel.hidden));
    closeButton.addEventListener("click", () => setOpen(false));

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const question = input.value.trim();
        if (!question) {
            return;
        }
        input.value = "";
        addMessage(question, "user");
        const loading = addMessage("Checking approved Batangas Premium information...", "bot");

        try {
            const response = await fetch("/api/chatbot", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: question }),
            });
            const result = await response.json();
            loading.textContent = result.reply || "Please contact Batangas Premium directly for complete details.";
        } catch (error) {
            loading.textContent = "Please contact Batangas Premium directly for complete details.";
        }
    });
})();
