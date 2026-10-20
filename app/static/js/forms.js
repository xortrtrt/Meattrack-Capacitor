(function () {
    document.querySelectorAll("form").forEach((form) => {
        form.addEventListener("submit", () => {
            if (form.hasAttribute("data-chatbot-form") || form.hasAttribute("data-async-form")) {
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
    function bindOtpModal() {
        const modal = document.querySelector("[data-otp-modal]");
        if (!modal) {
            return;
        }
        const input = modal.querySelector("[data-otp-modal-input]");

        function close() {
            modal.hidden = true;
            document.body.classList.remove("modal-is-open");
        }

        document.body.classList.add("modal-is-open");
        input?.focus();

        modal.querySelectorAll("[data-otp-modal-close]").forEach((button) => {
            button.addEventListener("click", close);
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !modal.hidden) {
                close();
            }
        });
    }
    function bindPasswordRuleTracking() {
        document.querySelectorAll("[data-password-rules-input]").forEach((input) => {
            const ruleList = input.closest("label")?.querySelector("[data-password-rule-list]");
            if (!ruleList) {
                return;
            }
            const rules = {
                length: (value) => value.length >= 8,
                uppercase: (value) => /[A-Z]/.test(value),
                lowercase: (value) => /[a-z]/.test(value),
                number: (value) => /[0-9]/.test(value),
                special: (value) => /[^A-Za-z0-9]/.test(value),
            };

            function updateRules() {
                const value = input.value || "";
                Object.entries(rules).forEach(([rule, validator]) => {
                    const item = ruleList.querySelector(`[data-password-rule="${rule}"]`);
                    if (!item) {
                        return;
                    }
                    const passed = validator(value);
                    item.classList.toggle("is-met", passed);
                    item.classList.toggle("is-missing", !passed);
                    item.setAttribute("aria-label", `${item.textContent.trim()}: ${passed ? "met" : "missing"}`);
                });
            }

            input.addEventListener("input", updateRules);
            input.addEventListener("blur", updateRules);
            updateRules();
        });
    }
    bindOtpModal();
    bindPasswordRuleTracking();
})();
