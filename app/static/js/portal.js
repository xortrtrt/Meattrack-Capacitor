(function () {
    function bindPortalDrawer() {
        const toggle = document.querySelector("[data-portal-nav-toggle]");
        const drawer = document.querySelector("[data-portal-drawer]");
        const dismissButtons = document.querySelectorAll("[data-portal-nav-dismiss], [data-portal-nav-close]");
        const nav = document.querySelector("[data-portal-nav]");
        if (!toggle || !drawer) {
            return;
        }

        function setOpen(open) {
            toggle.setAttribute("aria-expanded", String(open));
            drawer.classList.toggle("is-open", open);
            document.body.classList.toggle("drawer-is-open", open);
        }

        toggle.addEventListener("click", () => {
            setOpen(toggle.getAttribute("aria-expanded") !== "true");
        });

        dismissButtons.forEach((button) => {
            button.addEventListener("click", () => setOpen(false));
        });

        if (nav) {
            nav.querySelectorAll("a").forEach((link) => {
                link.addEventListener("click", () => setOpen(false));
            });
        }

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                setOpen(false);
            }
        });
    }

    bindPortalDrawer();

    function bindNotificationMenu() {
        const menu = document.querySelector("[data-notification-menu]");
        const trigger = menu?.querySelector("[data-notification-trigger]");
        const panel = menu?.querySelector("[data-notification-panel]");
        const list = panel?.querySelector(".notification-list");
        if (!menu || !trigger || !panel) {
            return;
        }

        function positionPanel() {
            const viewportWidth = window.innerWidth;
            const viewportHeight = window.innerHeight;
            const edge = 12;
            const gap = 14;
            const panelWidth = Math.min(410, viewportWidth - (edge * 2));
            const triggerRect = trigger.getBoundingClientRect();
            const desktop = window.matchMedia("(min-width: 861px)").matches;

            panel.style.position = "fixed";
            panel.style.width = `${panelWidth}px`;
            panel.style.right = "auto";
            panel.style.left = `${edge}px`;
            panel.style.top = `${edge}px`;

            const panelHeight = panel.offsetHeight;
            const preferredLeft = desktop
                ? triggerRect.right + gap
                : triggerRect.right - panelWidth;
            const preferredTop = desktop
                ? triggerRect.top - 8
                : triggerRect.bottom + 10;
            const maxLeft = Math.max(edge, viewportWidth - panelWidth - edge);
            const maxTop = Math.max(edge, viewportHeight - panelHeight - edge);
            const left = Math.min(Math.max(edge, preferredLeft), maxLeft);
            const top = Math.min(Math.max(edge, preferredTop), maxTop);

            panel.style.left = `${Math.round(left)}px`;
            panel.style.top = `${Math.round(top)}px`;
        }

        function prepareOpenPanel() {
            menu.classList.add("is-positioning");
            window.requestAnimationFrame(() => {
                if (!menu.open) return;
                if (list) list.scrollTop = 0;
                positionPanel();
                menu.classList.remove("is-positioning");
            });
        }

        function setExpanded(open) {
            trigger.setAttribute("aria-expanded", String(open));
            menu.classList.toggle("is-open", open);
            if (open) prepareOpenPanel();
            else menu.classList.remove("is-positioning");
        }

        trigger.addEventListener("click", () => {
            if (!menu.open) menu.classList.add("is-positioning");
        });
        menu.addEventListener("toggle", () => setExpanded(menu.open));
        document.addEventListener("pointerdown", (event) => {
            if (menu.open && !menu.contains(event.target)) {
                menu.open = false;
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && menu.open) {
                menu.open = false;
                trigger.focus();
            }
        });
        window.addEventListener("resize", () => {
            if (menu.open) positionPanel();
        });
        window.visualViewport?.addEventListener("resize", () => {
            if (menu.open) positionPanel();
        });
        setExpanded(menu.open);
    }

    bindNotificationMenu();

    function bindSignOutConfirmation() {
        const form = document.querySelector("[data-logout-form]");
        const modal = document.querySelector("[data-logout-modal]");
        const dialog = modal?.querySelector("[role='dialog']");
        const confirmButton = modal?.querySelector("[data-logout-confirm]");
        const cancelButtons = modal?.querySelectorAll("[data-logout-close]") || [];
        if (!form || !modal || !dialog || !confirmButton) {
            return;
        }

        let returnFocus = null;
        let isSubmitting = false;

        function focusableElements() {
            return Array.from(
                dialog.querySelectorAll(
                    "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
                )
            ).filter((element) => !element.hidden);
        }

        function openModal() {
            returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            modal.hidden = false;
            document.body.classList.add("modal-is-open");
            window.requestAnimationFrame(() => confirmButton.focus());
        }

        function closeModal() {
            if (isSubmitting) return;
            modal.hidden = true;
            document.body.classList.remove("modal-is-open");
            returnFocus?.focus();
        }

        form.addEventListener("submit", (event) => {
            if (isSubmitting) return;
            event.preventDefault();
            openModal();
        });

        cancelButtons.forEach((button) => button.addEventListener("click", closeModal));

        confirmButton.addEventListener("click", () => {
            if (isSubmitting) return;
            isSubmitting = true;
            confirmButton.disabled = true;
            confirmButton.setAttribute("aria-busy", "true");
            const label = confirmButton.querySelector("span");
            if (label) label.textContent = "Signing out…";
            HTMLFormElement.prototype.submit.call(form);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !modal.hidden) {
                event.preventDefault();
                closeModal();
            }
        });

        modal.addEventListener("keydown", (event) => {
            if (event.key !== "Tab") return;

            const elements = focusableElements();
            if (!elements.length) {
                event.preventDefault();
                dialog.focus();
                return;
            }
            const first = elements[0];
            const last = elements[elements.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });
    }

    bindSignOutConfirmation();

    function bindAutoFilterForms() {
        const selector = [
            "form.filter-bar[method='get']",
            "form.stock-ledger__filters[method='get']",
            "form.reseller-product-search[method='get']",
            "form[data-auto-filter]",
        ].join(", ");
        const categorySelector = [
            ".movement-categories a",
            ".reseller-category-tabs a",
            ".order-history-tabs a",
            ".stock-clear-button",
            "form[data-auto-filter] a.button",
            "[data-async-filter-results] .pagination a",
            "[data-async-filter-results] .stock-pagination a",
        ].join(", ");
        const timers = new WeakMap();
        let activeRequest = null;

        function isFilterForm(form) {
            return form instanceof HTMLFormElement && form.matches(selector);
        }

        function formUrl(form) {
            const url = new URL(form.getAttribute("action") || window.location.href, window.location.origin);
            const values = new URLSearchParams();
            new FormData(form).forEach((value, key) => {
                if (typeof value === "string") values.append(key, value);
            });
            url.search = values.toString();
            return url;
        }

        function bindRefreshedContent() {
            bindReceiptCheckout();
            bindPaymentCountdowns();
        }

        function targetSelectorFor(source) {
            if (!(source instanceof Element)) return "";
            const directTarget = source.closest("[data-async-filter-target]")?.dataset.asyncFilterTarget;
            if (directTarget) return directTarget;
            return source.closest(".panel")?.querySelector("form[data-async-filter-target]")?.dataset.asyncFilterTarget || "";
        }

        async function refreshContent(url, { pushState = true, targetSelector = "" } = {}) {
            activeRequest?.abort();
            const controller = new AbortController();
            activeRequest = controller;
            const contentSelector = targetSelector || "[data-portal-content]";
            const currentContent = document.querySelector(contentSelector);
            currentContent?.setAttribute("aria-busy", "true");

            try {
                const response = await fetch(url, {
                    headers: {
                        Accept: "text/html",
                        "X-Requested-With": "fetch",
                    },
                    credentials: "same-origin",
                    signal: controller.signal,
                });
                if (!response.ok) throw new Error("Unable to update the filtered results.");

                const html = await response.text();
                const documentResponse = new DOMParser().parseFromString(html, "text/html");
                const replacement = documentResponse.querySelector(contentSelector);
                if (!replacement || !currentContent) throw new Error("Unable to update the filtered results.");

                currentContent.replaceWith(replacement);
                document.querySelectorAll("[data-async-filter-sync]").forEach((currentElement) => {
                    const syncKey = currentElement.dataset.asyncFilterSync;
                    if (!syncKey) return;
                    const nextElement = Array.from(documentResponse.querySelectorAll("[data-async-filter-sync]"))
                        .find((candidate) => candidate.dataset.asyncFilterSync === syncKey);
                    if (nextElement) currentElement.replaceWith(nextElement);
                });
                if (documentResponse.title) document.title = documentResponse.title;
                if (pushState) window.history.pushState({}, "", url);
                window.lucide?.createIcons();
                bindRefreshedContent();
            } catch (error) {
                if (error.name === "AbortError") return;
                window.location.assign(url);
            } finally {
                if (activeRequest === controller) activeRequest = null;
                currentContent?.removeAttribute("aria-busy");
            }
        }

        function queueFormRefresh(form) {
            window.clearTimeout(timers.get(form));
            timers.set(form, window.setTimeout(() => form.requestSubmit(), 300));
        }

        document.addEventListener("input", (event) => {
            const control = event.target;
            if (!(control instanceof HTMLInputElement) || !["search", "text"].includes(control.type) || event.isComposing) {
                return;
            }
            const form = control.closest("form");
            if (isFilterForm(form)) queueFormRefresh(form);
        });

        document.addEventListener("change", (event) => {
            const control = event.target;
            if (!(control instanceof HTMLSelectElement) && !(control instanceof HTMLInputElement)) return;
            if (control instanceof HTMLInputElement && !["checkbox", "radio"].includes(control.type)) return;
            const form = control.closest("form");
            if (isFilterForm(form)) {
                window.clearTimeout(timers.get(form));
                form.requestSubmit();
            }
        });

        document.addEventListener("submit", (event) => {
            const form = event.target;
            if (!isFilterForm(form)) return;
            event.preventDefault();
            refreshContent(formUrl(form), { targetSelector: targetSelectorFor(form) });
        });

        document.addEventListener("click", (event) => {
            const link = event.target.closest(categorySelector);
            if (!link || link.dataset.inventoryProductsLink !== undefined || event.defaultPrevented
                || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
                return;
            }
            event.preventDefault();
            refreshContent(new URL(link.href), { targetSelector: targetSelectorFor(link) });
        });

        window.addEventListener("popstate", () => {
            const targetForm = document.querySelector(`${selector}[data-async-filter-target]`);
            refreshContent(new URL(window.location.href), {
                pushState: false,
                targetSelector: targetForm?.dataset.asyncFilterTarget || "",
            });
        });
    }

    bindAutoFilterForms();

    function bindInquiryRejection() {
        if (document.documentElement.dataset.inquiryRejectionBound === "true") return;
        document.documentElement.dataset.inquiryRejectionBound = "true";
        let returnFocus = null;

        function closeModal(modal) {
            const form = modal?.querySelector("[data-inquiry-reject-form]");
            if (!modal || !form) return;
            modal.hidden = true;
            document.body.classList.remove("modal-is-open");
            form.reset();
            returnFocus?.focus();
            returnFocus = null;
        }

        document.addEventListener("click", (event) => {
            const button = event.target.closest("[data-inquiry-reject-open]");
            if (button) {
                const modal = document.querySelector("[data-inquiry-reject-modal]");
                const form = modal?.querySelector("[data-inquiry-reject-form]");
                const reason = modal?.querySelector("[data-inquiry-reject-reason]");
                const business = modal?.querySelector("[data-inquiry-reject-business]");
                if (!modal || !form || !reason || !business) return;
                returnFocus = button;
                form.action = button.dataset.inquiryRejectAction || "";
                business.textContent = button.dataset.inquiryRejectBusiness || "this applicant";
                modal.hidden = false;
                document.body.classList.add("modal-is-open");
                window.requestAnimationFrame(() => reason.focus());
                return;
            }
            const closeButton = event.target.closest("[data-inquiry-reject-close]");
            if (closeButton) closeModal(closeButton.closest("[data-inquiry-reject-modal]"));
        });
        document.addEventListener("keydown", (event) => {
            const modal = document.querySelector("[data-inquiry-reject-modal]");
            if (event.key === "Escape" && modal && !modal.hidden) closeModal(modal);
        });
    }

    bindInquiryRejection();

    function bindForecastHorizonPicker() {
        const form = document.querySelector("[data-forecast-horizon-form]");
        if (!form) {
            return;
        }
        const choices = Array.from(form.querySelectorAll("[data-forecast-horizon-choice]"));
        const hiddenValue = form.querySelector("[data-forecast-horizon-value]");
        const customWrap = form.querySelector("[data-forecast-custom-horizon]");
        const customInput = form.querySelector("[data-forecast-horizon-custom]");
        if (!choices.length || !hiddenValue || !customWrap || !customInput) {
            return;
        }

        function syncHorizon() {
            const selected = choices.find((choice) => choice.checked) || choices[0];
            const customSelected = selected.value === "custom";
            customWrap.hidden = !customSelected;
            customInput.required = customSelected;
            hiddenValue.value = customSelected ? customInput.value : selected.value;
        }

        choices.forEach((choice) => {
            choice.addEventListener("change", syncHorizon);
        });
        customInput.addEventListener("input", syncHorizon);
        form.addEventListener("submit", syncHorizon);
        syncHorizon();
    }

    bindForecastHorizonPicker();

    function bindForecastRunStatus() {
        const panel = document.querySelector("[data-forecast-run-status]");
        if (!panel) {
            return;
        }
        const runId = panel.dataset.forecastRunId;
        const progress = panel.querySelector("[data-forecast-progress]");
        if (!runId) {
            return;
        }
        const terminal = new Set(["completed", "completed_with_warnings", "failed"]);
        const poll = async () => {
            try {
                const response = await fetch(`/portal/owner/forecasts/runs/${encodeURIComponent(runId)}/status`, {
                    headers: { Accept: "application/json" },
                    credentials: "same-origin",
                });
                if (!response.ok) {
                    return;
                }
                const state = await response.json();
                if (progress) {
                    progress.textContent = `${state.message} ${state.processed_products} of ${state.total_products} products processed.`;
                }
                if (terminal.has(state.status)) {
                    window.location.reload();
                    return;
                }
            } catch (_error) {
                // A transient polling failure should not disrupt the page.
            }
            window.setTimeout(poll, 5000);
        };
        window.setTimeout(poll, 1000);
    }

    bindForecastRunStatus();

    const tabs = Array.from(document.querySelectorAll("[data-scroll-tab]"));
    if (tabs.length && "IntersectionObserver" in window) {
        const sections = tabs
            .map((tab) => document.querySelector(tab.getAttribute("href")))
            .filter(Boolean);
        const byId = new Map(tabs.map((tab) => [tab.getAttribute("href").slice(1), tab]));
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) {
                    return;
                }
                tabs.forEach((tab) => tab.classList.remove("is-active"));
                const tab = byId.get(entry.target.id);
                if (tab) {
                    tab.classList.add("is-active");
                }
            });
        }, { rootMargin: "-35% 0px -55% 0px", threshold: 0.01 });
        sections.forEach((section) => observer.observe(section));
    }

    function bindQuantitySteppers() {
        document.querySelectorAll("[data-quantity-step]").forEach((button) => {
            button.addEventListener("click", () => {
                const wrapper = button.closest(".reseller-quantity-stepper");
                const input = wrapper?.querySelector("[data-quantity-input], input[type='number']");
                if (!input) {
                    return;
                }
                const step = Number(button.dataset.quantityStep || 0);
                const min = Number(input.getAttribute("min") || 0);
                const maxValue = input.getAttribute("max");
                const max = maxValue === null || maxValue === "" ? Number.POSITIVE_INFINITY : Number(maxValue);
                const value = Number(input.value || min || 0);
                input.value = String(Math.min(max, Math.max(min, value + step)));
                input.dispatchEvent(new Event("input", { bubbles: true }));
            });
        });
    }

    function bindProductSearch() {
        const search = document.querySelector("[data-product-search]");
        const cards = Array.from(document.querySelectorAll("[data-product-card]"));
        const empty = document.querySelector("[data-product-empty]");
        if (!search || !cards.length) {
            return;
        }

        search.addEventListener("input", () => {
            const term = search.value.trim().toLowerCase();
            let visibleCount = 0;
            cards.forEach((card) => {
                const text = (card.dataset.productText || "").toLowerCase();
                const visible = !term || text.includes(term);
                card.hidden = !visible;
                if (visible) {
                    visibleCount += 1;
                }
            });
            if (empty) {
                empty.hidden = visibleCount !== 0;
            }
        });
    }

    function bindProductModal() {
        const modal = document.querySelector("[data-product-modal]");
        if (!modal) {
            return;
        }

        const image = modal.querySelector("[data-product-modal-image]");
        const productId = modal.querySelector("[data-product-modal-id]");
        const name = modal.querySelector("[data-product-modal-name]");
        const category = modal.querySelector("[data-product-modal-category]");
        const categoryLabel = modal.querySelector("[data-product-modal-category-label]");
        const price = modal.querySelector("[data-product-modal-price]");
        const stock = modal.querySelector("[data-product-modal-stock]");
        const description = modal.querySelector("[data-product-modal-description]");
        const quantity = modal.querySelector("[data-product-modal-quantity]");
        const addButton = modal.querySelector(".reseller-add-cart-button");
        let lastFocused = null;

        function openModal(card) {
            lastFocused = document.activeElement;
            const unit = card.dataset.productUnit || "pack";
            const productName = card.dataset.productName || "Product";
            const productCategory = card.dataset.productCategory || "Uncategorized";
            const available = Number(card.dataset.productStock || 0);

            if (image) {
                image.src = card.dataset.productImage || "";
                image.alt = productName;
            }
            if (productId) {
                productId.value = card.dataset.productId || "";
            }
            if (name) {
                name.textContent = productName;
            }
            if (category) {
                category.textContent = productCategory;
            }
            if (categoryLabel) {
                categoryLabel.textContent = productCategory;
            }
            if (price) {
                price.textContent = `${formatMoney(card.dataset.productPrice)}/${unit}`;
            }
            if (stock) {
                stock.textContent = `${formatQuantity(card.dataset.productStock)} ${unit}`;
            }
            if (description) {
                description.textContent = card.dataset.productDescription || "No description available yet.";
            }
            if (quantity) {
                quantity.value = "1";
                quantity.max = String(Math.max(0, Math.floor(available)));
                quantity.disabled = available < 1;
            }
            if (addButton) {
                addButton.setAttribute("aria-label", `Add ${productName} to cart`);
                addButton.disabled = available < 1;
            }

            modal.hidden = false;
            document.body.classList.add("modal-is-open");
            window.requestAnimationFrame(() => quantity?.focus());
        }

        function closeModal() {
            modal.hidden = true;
            document.body.classList.remove("modal-is-open");
            if (lastFocused && typeof lastFocused.focus === "function") {
                lastFocused.focus();
            }
        }

        document.addEventListener("click", (event) => {
            const button = event.target.closest("[data-product-modal-open]");
            const card = button?.closest("[data-product-card]");
            if (card) openModal(card);
        });

        modal.querySelectorAll("[data-product-modal-close]").forEach((button) => {
            button.addEventListener("click", closeModal);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !modal.hidden) {
                closeModal();
            }
        });
    }

    function bindProofModal() {
        const modal = document.querySelector("[data-proof-modal]");
        if (!modal) {
            return;
        }

        const title = modal.querySelector("[data-proof-modal-title]");
        const image = modal.querySelector("[data-proof-modal-image]");
        const externalLink = modal.querySelector("[data-proof-modal-link]");
        const fullscreenToggle = modal.querySelector("[data-proof-fullscreen-toggle]");
        const card = modal.querySelector(".proof-modal-card");
        let lastFocused = null;

        function updateProofOrientation() {
            if (!card || !image) return;
            card.classList.toggle(
                "is-portrait-proof",
                image.naturalHeight > image.naturalWidth,
            );
        }

        function setFullscreen(active) {
            modal.classList.toggle("is-fullscreen", active);
            if (fullscreenToggle) {
                fullscreenToggle.setAttribute(
                    "aria-label",
                    active ? "Exit fullscreen proof image" : "View proof image fullscreen",
                );
            }
        }

        function openModal(trigger) {
            const proofUrl = trigger.dataset.proofUrl || trigger.getAttribute("href");
            const proofTitle = trigger.dataset.proofTitle || trigger.textContent.trim() || "Payment screenshot";
            if (!proofUrl) {
                return;
            }

            lastFocused = document.activeElement;
            if (title) {
                title.textContent = proofTitle;
            }
            if (image) {
                image.src = proofUrl;
                image.alt = proofTitle;
                image.addEventListener("load", updateProofOrientation, { once: true });
                if (image.complete) updateProofOrientation();
            }
            if (externalLink) {
                externalLink.href = proofUrl;
            }
            setFullscreen(false);
            modal.hidden = false;
            document.body.classList.add("modal-is-open");
            modal.querySelector("[data-proof-modal-close]")?.focus();
        }

        function closeModal() {
            modal.hidden = true;
            setFullscreen(false);
            document.body.classList.remove("modal-is-open");
            if (image) {
                image.removeAttribute("src");
                image.alt = "";
            }
            card?.classList.remove("is-portrait-proof");
            if (lastFocused && typeof lastFocused.focus === "function") {
                lastFocused.focus();
            }
        }

        fullscreenToggle?.addEventListener("click", () => {
            setFullscreen(!modal.classList.contains("is-fullscreen"));
        });

        modal.querySelector(".proof-modal-backdrop")?.addEventListener("click", () => {
            if (modal.classList.contains("is-fullscreen")) {
                setFullscreen(false);
            }
        });

        document.addEventListener("click", (event) => {
            const trigger = event.target.closest("[data-proof-modal-open]");
            if (!trigger) return;
            event.preventDefault();
            openModal(trigger);
        });

        modal.querySelectorAll("[data-proof-modal-close]").forEach((button) => {
            button.addEventListener("click", closeModal);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !modal.hidden) {
                if (modal.classList.contains("is-fullscreen")) {
                    setFullscreen(false);
                    return;
                }
                closeModal();
            }
        });
    }

    function bindOrderActionConfirmation() {
        const modal = document.querySelector("[data-order-action-modal]");
        const dialog = modal?.querySelector("[role='dialog']");
        const icon = modal?.querySelector("[data-order-action-icon]");
        const eyebrow = modal?.querySelector("[data-order-action-eyebrow]");
        const title = modal?.querySelector("[data-order-action-title]");
        const description = modal?.querySelector("[data-order-action-description]");
        const confirmButton = modal?.querySelector("[data-order-action-confirm]");
        const cancelButton = modal?.querySelector("[data-order-action-close]:not(.proof-modal-backdrop)");
        const rejectionField = modal?.querySelector("[data-order-rejection-field]");
        const rejectionReason = modal?.querySelector("[data-order-rejection-reason]");
        if (!modal || !dialog || !icon || !eyebrow || !title || !description || !confirmButton || !rejectionField || !rejectionReason) {
            return;
        }

        const actions = {
            approve: {
                icon: "circle-check",
                eyebrow: "Approve order",
                title: "Approve order",
                description: "The order will move to the fulfillment queue.",
                confirm: "Approve order",
            },
            reject: {
                icon: "circle-x",
                eyebrow: "Reject order",
                title: "Reject order",
                description: "Explain why this order cannot be approved before confirming the rejection.",
                confirm: "Reject order",
                destructive: true,
                requiresReason: true,
            },
            fulfill: {
                icon: "package-check",
                eyebrow: "Fulfill order",
                title: "Mark order fulfilled",
                description: "This records the sale and deducts the reserved stock.",
                confirm: "Fulfill order",
            },
        };
        let activeForm = null;
        let returnFocus = null;
        let isSubmitting = false;

        function focusableElements() {
            return Array.from(dialog.querySelectorAll(
                "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
            )).filter((element) => !element.hidden);
        }

        function closeModal() {
            if (isSubmitting) return;
            modal.hidden = true;
            document.body.classList.remove("modal-is-open");
            rejectionReason.value = "";
            rejectionReason.setCustomValidity("");
            activeForm = null;
            returnFocus?.focus();
            returnFocus = null;
        }

        function openModal(form, submitter) {
            const action = actions[form.dataset.orderAction] || actions.approve;
            const orderId = form.dataset.orderId || "";
            activeForm = form;
            returnFocus = submitter instanceof HTMLElement ? submitter : document.activeElement;
            icon.innerHTML = `<i data-lucide="${action.icon}"></i>`;
            eyebrow.textContent = action.eyebrow;
            title.textContent = `${action.title}${orderId ? ` #${orderId}` : ""}?`;
            description.textContent = action.description;
            rejectionField.hidden = !action.requiresReason;
            rejectionReason.required = Boolean(action.requiresReason);
            rejectionReason.value = "";
            rejectionReason.setCustomValidity("");
            confirmButton.classList.toggle("danger", Boolean(action.destructive));
            confirmButton.classList.toggle("primary", !action.destructive);
            confirmButton.querySelector("span").textContent = action.confirm;
            modal.hidden = false;
            document.body.classList.add("modal-is-open");
            window.lucide?.createIcons();
            window.requestAnimationFrame(() => (action.requiresReason ? rejectionReason : (cancelButton || confirmButton)).focus());
        }

        document.addEventListener("submit", (event) => {
            const form = event.target;
            if (!(form instanceof HTMLFormElement) || !form.matches("[data-order-action-form]")) return;
            if (isSubmitting) return;
            event.preventDefault();
            openModal(form, event.submitter);
        });

        modal.querySelectorAll("[data-order-action-close]").forEach((button) => {
            button.addEventListener("click", closeModal);
        });

        confirmButton.addEventListener("click", () => {
            if (!activeForm || isSubmitting) return;
            if (activeForm.dataset.orderAction === "reject") {
                const cleanedReason = rejectionReason.value
                    .split("\n")
                    .map((line) => line.trim())
                    .filter(Boolean)
                    .join("\n");
                rejectionReason.setCustomValidity(
                    cleanedReason.length < 5 ? "Enter at least 5 characters explaining why the order was rejected." : "",
                );
                if (!rejectionReason.reportValidity()) return;
                const target = activeForm.querySelector("[data-order-rejection-value]");
                if (!target) return;
                target.value = cleanedReason;
            }
            isSubmitting = true;
            confirmButton.disabled = true;
            confirmButton.setAttribute("aria-busy", "true");
            confirmButton.querySelector("span").textContent = "Saving…";
            HTMLFormElement.prototype.submit.call(activeForm);
        });

        document.addEventListener("keydown", (event) => {
            if (modal.hidden) return;
            if (event.key === "Escape") {
                event.preventDefault();
                closeModal();
            }
        });

        modal.addEventListener("keydown", (event) => {
            if (event.key !== "Tab") return;
            const elements = focusableElements();
            if (!elements.length) {
                event.preventDefault();
                dialog.focus();
                return;
            }
            const first = elements[0];
            const last = elements[elements.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });
    }
    function formatMoney(value) {
        return `PHP ${Number(value || 0).toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        })}`;
    }

    function formatQuantity(value) {
        const quantity = Number(value || 0);
        return quantity.toLocaleString("en-US", {
            minimumFractionDigits: Number.isInteger(quantity) ? 0 : 2,
            maximumFractionDigits: Number.isInteger(quantity) ? 0 : 2,
        });
    }

    function bindCartAutosave() {
        const cart = document.querySelector("[data-cart]");
        if (!cart) {
            return;
        }

        const status = cart.querySelector("[data-cart-save-status]");
        const selectAll = cart.querySelector("[data-cart-select-all]");
        const confirmButton = cart.querySelector("[data-cart-confirm]");
        const confirmForm = cart.querySelector("#cart-confirm-form");
        const selectionHint = cart.querySelector("[data-cart-selection-hint]");
        const timers = new Map();

        function setStatus(text, state = "") {
            if (!status) {
                return;
            }
            status.textContent = text;
            status.dataset.state = state;
        }

        function recalculateCart() {
            let totalAmount = 0;
            let totalQuantity = 0;
            let selectedItems = 0;
            const cartItems = Array.from(cart.querySelectorAll("[data-cart-item]"));

            cartItems.forEach((item) => {
                const input = item.querySelector("[data-cart-quantity]");
                const selection = item.querySelector("[data-cart-select]");
                const lineTotal = item.querySelector("[data-cart-line-total]");
                if (!input) {
                    return;
                }

                const min = Number(input.getAttribute("min") || 1);
                const quantity = Math.max(min, Number(input.value || min));
                const unitPrice = Number(item.dataset.unitPrice || 0);
                const lineAmount = quantity * unitPrice;

                if (lineTotal) {
                    lineTotal.textContent = formatMoney(lineAmount);
                }
                const selected = Boolean(selection?.checked);
                item.classList.toggle("is-selected", selected);
                if (selected) {
                    selectedItems += 1;
                    totalQuantity += quantity;
                    totalAmount += lineAmount;
                }
            });

            cart.querySelectorAll("[data-cart-total]").forEach((element) => {
                element.textContent = formatMoney(totalAmount);
            });
            cart.querySelectorAll("[data-cart-count]").forEach((element) => {
                element.textContent = formatQuantity(totalQuantity);
            });
            cart.querySelectorAll("[data-cart-selected-items]").forEach((element) => {
                element.textContent = String(selectedItems);
            });
            if (selectAll) {
                selectAll.checked = cartItems.length > 0 && selectedItems === cartItems.length;
                selectAll.indeterminate = selectedItems > 0 && selectedItems < cartItems.length;
            }
            if (confirmButton) {
                confirmButton.disabled = selectedItems === 0;
            }
            if (selectionHint) {
                selectionHint.classList.toggle("has-selection", selectedItems > 0);
                const copy = selectionHint.querySelector("span");
                if (copy) {
                    copy.textContent = selectedItems > 0
                        ? `${selectedItems} product${selectedItems === 1 ? "" : "s"} will move to order review. Unchecked items will stay in your cart.`
                        : "Select at least one product to continue. Items you leave unchecked will stay in your cart.";
                }
            }
        }

        async function saveCartItem(form) {
            const productId = form.querySelector("input[name='product_id']")?.value;
            if (!productId) {
                return;
            }

            setStatus("Saving cart...", "saving");
            try {
                const response = await fetch(form.getAttribute("action") || window.location.pathname, {
                    method: "POST",
                    body: new FormData(form),
                    headers: {
                        "Accept": "application/json",
                        "X-Requested-With": "fetch",
                    },
                });
                const result = await response.json();
                if (!response.ok || result.ok === false) {
                    throw new Error(result.error || "Unable to update cart.");
                }

                const serverLineTotal = result.line_totals?.[productId];
                const item = form.closest("[data-cart-item]");
                const lineTotal = item?.querySelector("[data-cart-line-total]");
                if (lineTotal && typeof serverLineTotal === "number") {
                    lineTotal.textContent = formatMoney(serverLineTotal);
                }
                document.querySelectorAll("[data-cart-nav-count]").forEach((element) => {
                    element.textContent = formatQuantity(result.cart_count);
                });
                recalculateCart();
                setStatus("Cart saved.", "saved");
            } catch (error) {
                setStatus(error.message || "Cart could not be saved.", "error");
            }
        }

        cart.querySelectorAll("[data-cart-update-form]").forEach((form) => {
            const input = form.querySelector("[data-cart-quantity]");
            if (!input) {
                return;
            }

            form.addEventListener("submit", (event) => {
                event.preventDefault();
                recalculateCart();
                saveCartItem(form);
            });

            input.addEventListener("input", () => {
                const min = Number(input.getAttribute("min") || 1);
                if (Number(input.value || min) < min) {
                    input.value = String(min);
                }
                recalculateCart();
                window.clearTimeout(timers.get(form));
                timers.set(form, window.setTimeout(() => saveCartItem(form), 450));
            });
        });

        cart.querySelectorAll("[data-cart-select]").forEach((checkbox) => {
            checkbox.addEventListener("change", recalculateCart);
        });

        selectAll?.addEventListener("change", () => {
            cart.querySelectorAll("[data-cart-select]").forEach((checkbox) => {
                checkbox.checked = selectAll.checked;
            });
            recalculateCart();
        });

        confirmForm?.addEventListener("submit", (event) => {
            if (!cart.querySelector("[data-cart-select]:checked")) {
                event.preventDefault();
                recalculateCart();
            }
        });

        recalculateCart();
    }

    function bindInventoryProductsPagination() {
        document.addEventListener("click", async (event) => {
            const link = event.target.closest("[data-inventory-products-link]");
            if (!link) {
                return;
            }
            const region = link.closest("[data-inventory-products-region]");
            const partialUrl = link.dataset.partialUrl;
            if (!region || !partialUrl) {
                return;
            }

            event.preventDefault();
            const status = region.querySelector("[data-inventory-products-status]");
            region.classList.add("is-loading");
            if (status) {
                status.textContent = "Loading products...";
            }

            try {
                const response = await fetch(partialUrl, {
                    headers: {
                        "X-Requested-With": "fetch",
                    },
                });
                if (!response.ok) {
                    throw new Error("Products could not be loaded.");
                }
                const html = await response.text();
                const template = document.createElement("template");
                template.innerHTML = html.trim();
                const replacement = template.content.querySelector("[data-inventory-products-region]");
                if (!replacement) {
                    throw new Error("Products could not be loaded.");
                }
                region.replaceWith(replacement);
                window.history.pushState({}, "", link.href);
                const selectedType = new URL(link.href).searchParams.get("type") || "";
                const typeInput = document.querySelector("[data-inventory-products-type]");
                if (typeInput) typeInput.value = selectedType;
                if (window.lucide) {
                    window.lucide.createIcons();
                }
            } catch (error) {
                region.classList.remove("is-loading");
                if (status) {
                    status.textContent = error.message || "Products could not be loaded.";
                }
            }
        });
    }

    function bindRecipeRows() {
        const rows = document.getElementById("recipe-rows");
        const addButton = document.getElementById("add-ingredient");
        if (!rows || !addButton) {
            return;
        }

        function updateRemoveButtons() {
            const recipeRows = rows.querySelectorAll("[data-recipe-row]");
            recipeRows.forEach((row) => {
                const button = row.querySelector("[data-remove-ingredient]");
                if (button) {
                    button.disabled = recipeRows.length === 1;
                }
            });
        }

        rows.addEventListener("click", (event) => {
            const removeButton = event.target.closest("[data-remove-ingredient]");
            if (!removeButton) {
                return;
            }
            const recipeRows = rows.querySelectorAll("[data-recipe-row]");
            if (recipeRows.length <= 1) {
                return;
            }
            removeButton.closest("[data-recipe-row]").remove();
            updateRemoveButtons();
        });

        addButton.addEventListener("click", () => {
            const sourceRow = rows.querySelector("[data-recipe-row]");
            const newRow = sourceRow.cloneNode(true);
            newRow.querySelectorAll("input").forEach((input) => {
                input.value = "";
            });
            rows.appendChild(newRow);
            updateRemoveButtons();
            window.lucide?.createIcons();
        });

        updateRemoveButtons();
    }

    function bindInventoryWorkspaceModals() {
        const modals = Array.from(document.querySelectorAll("[data-inventory-modal]"));
        if (!modals.length) {
            return;
        }

        let activeModal = null;
        let returnFocus = null;
        const focusableSelector = [
            "button:not([disabled])",
            "input:not([disabled])",
            "select:not([disabled])",
            "textarea:not([disabled])",
            "a[href]",
            "summary",
            "[tabindex]:not([tabindex='-1'])",
        ].join(",");

        function updateModalQuery(name) {
            const url = new URL(window.location.href);
            if (name) {
                url.searchParams.set("modal", name);
            } else {
                url.searchParams.delete("modal");
            }
            window.history.replaceState({}, "", url);
        }

        function closeModal({ restoreFocus = true, updateUrl = true } = {}) {
            if (!activeModal) {
                return;
            }
            activeModal.hidden = true;
            activeModal = null;
            document.body.classList.remove("modal-is-open");
            if (updateUrl) {
                updateModalQuery("");
            }
            if (restoreFocus && returnFocus) {
                returnFocus.focus();
            }
            returnFocus = null;
        }

        function openModal(name, trigger = null, { updateUrl = true } = {}) {
            const modal = modals.find((item) => item.dataset.inventoryModal === name);
            if (!modal) {
                return;
            }
            if (activeModal && activeModal !== modal) {
                closeModal({ restoreFocus: false, updateUrl: false });
            }
            returnFocus = trigger || document.querySelector(`[data-inventory-modal-open="${name}"]`);
            activeModal = modal;
            modal.hidden = false;
            document.body.classList.add("modal-is-open");
            if (updateUrl) {
                updateModalQuery(name);
            }
            window.requestAnimationFrame(() => {
                const preferredFocus = modal.querySelector(".inventory-modal-body input:not([type='hidden']):not([disabled]), .inventory-modal-body select:not([disabled])")
                    || modal.querySelector("button[data-inventory-modal-close]");
                (preferredFocus || modal.querySelector("[role='dialog']"))?.focus();
            });
        }

        document.querySelectorAll("[data-inventory-modal-open]").forEach((trigger) => {
            trigger.addEventListener("click", () => openModal(trigger.dataset.inventoryModalOpen, trigger));
        });

        modals.forEach((modal) => {
            modal.querySelectorAll("[data-inventory-modal-close]").forEach((button) => {
                button.addEventListener("click", () => closeModal());
            });
        });

        document.addEventListener("keydown", (event) => {
            if (!activeModal) {
                return;
            }
            if (event.key === "Escape") {
                event.preventDefault();
                closeModal();
                return;
            }
            if (event.key !== "Tab") {
                return;
            }
            const focusable = Array.from(activeModal.querySelectorAll(focusableSelector)).filter((element) => element.offsetParent !== null);
            if (!focusable.length) {
                event.preventDefault();
                activeModal.querySelector("[role='dialog']")?.focus();
                return;
            }
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

        const requestedModal = new URLSearchParams(window.location.search).get("modal");
        if (requestedModal) {
            openModal(requestedModal, null, { updateUrl: false });
        }
    }

    let paymentCountdownTimer = null;

    function bindPaymentCountdowns() {
        const deadlineContainers = document.querySelectorAll("[data-payment-deadline]");
        if (!deadlineContainers.length) return;

        function updateCountdowns() {
            const now = Date.now();
            document.querySelectorAll("[data-payment-deadline]").forEach((container) => {
                const deadline = Date.parse(container.dataset.paymentDeadline || "");
                const output = container.querySelector("[data-payment-countdown]");
                if (!output || Number.isNaN(deadline)) return;
                const remaining = Math.max(0, deadline - now);
                const totalSeconds = Math.ceil(remaining / 1000);
                const minutes = Math.floor(totalSeconds / 60);
                const seconds = totalSeconds % 60;
                output.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
                container.dataset.urgency = totalSeconds <= 120 ? "critical" : totalSeconds <= 300 ? "warning" : "normal";
                if (remaining <= 0 && container.dataset.expired !== "true") {
                    container.dataset.expired = "true";
                    output.textContent = "Expired";
                    container.dispatchEvent(new CustomEvent("payment-expired", { bubbles: true }));
                    if (!container.dataset.reloadScheduled) {
                        container.dataset.reloadScheduled = "true";
                        window.setTimeout(() => window.location.reload(), 1500);
                    }
                }
            });
        }

        updateCountdowns();
        if (!paymentCountdownTimer) paymentCountdownTimer = window.setInterval(updateCountdowns, 1000);
    }

    function bindReceiptCheckoutForm(form) {
        if (form.dataset.receiptCheckoutBound === "true") return;
        form.dataset.receiptCheckoutBound = "true";

        const fileInput = form.querySelector("[data-receipt-files]");
        const fileList = form.querySelector("[data-receipt-file-list]");
        const cardStatus = form.querySelector("[data-receipt-card-status]");
        const status = form.querySelector("[data-receipt-status]");
        const transactionInput = form.querySelector("[data-receipt-transaction]");
        const amountInput = form.querySelector("[data-receipt-amount]");
        const dateInput = form.querySelector("[data-receipt-date]");
        const timeInput = form.querySelector("[data-receipt-time]");
        const confirmation = form.querySelector("[data-receipt-confirm]");
        const tokenInput = form.querySelector("[data-receipt-token]");
        const submitButton = form.querySelector("[data-receipt-submit]");
        const modal = form.querySelector("[data-receipt-modal]");
        const dialog = form.querySelector("[data-receipt-dialog]");
        const previews = form.querySelector("[data-receipt-previews]");
        const closeButtons = form.querySelectorAll("[data-receipt-modal-close]");
        const acceptButton = form.querySelector("[data-receipt-accept]");
        const editButton = form.querySelector("[data-receipt-edit]");
        const summary = form.querySelector("[data-receipt-summary]");
        const summaryReference = form.querySelector("[data-receipt-summary-reference]");
        const summaryAmount = form.querySelector("[data-receipt-summary-amount]");
        const summaryDate = form.querySelector("[data-receipt-summary-date]");
        const acceptedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
        const maxBytes = 5 * 1024 * 1024;
        const paymentInputs = [transactionInput, amountInput, dateInput, timeInput];
        let previewUrls = [];
        let priorFocus = null;
        let scanGeneration = 0;
        let isScanning = false;
        let detailsAccepted = false;
        let paymentExpired = false;

        const selectedFiles = () => Array.from(fileInput?.files || []);

        function validateFiles() {
            const filesToCheck = selectedFiles();
            if (filesToCheck.length < 1 || filesToCheck.length > 3) return "Choose between 1 and 3 receipt screenshots.";
            const invalidType = filesToCheck.find((file) => !acceptedTypes.has(file.type));
            if (invalidType) return `${invalidType.name} must be a JPG, PNG, or WebP image.`;
            const oversized = filesToCheck.find((file) => file.size > maxBytes);
            if (oversized) return `${oversized.name} is larger than 5 MB.`;
            return "";
        }

        function detailsAreComplete() {
            return Boolean(
                tokenInput.value
                && transactionInput.value.trim()
                && Number(amountInput.value) > 0
                && dateInput.value
                && timeInput.value
                && confirmation.checked
                && !validateFiles()
            );
        }

        function updateActionState() {
            const complete = detailsAreComplete() && !isScanning && !paymentExpired;
            acceptButton.disabled = !complete;
            submitButton.disabled = !(complete && detailsAccepted);
        }

        function resetScan(message = "Receipt changed. Reading the updated screenshot automatically.") {
            scanGeneration += 1;
            isScanning = false;
            detailsAccepted = false;
            tokenInput.value = "";
            paymentInputs.forEach((input) => {
                input.value = "";
                input.disabled = true;
            });
            confirmation.checked = false;
            confirmation.disabled = true;
            summary.hidden = true;
            status.textContent = message;
            status.dataset.state = "";
            cardStatus.textContent = message;
            cardStatus.dataset.state = "";
            updateActionState();
        }

        function renderPreviews() {
            previewUrls.forEach((url) => URL.revokeObjectURL(url));
            previewUrls = [];
            previews.replaceChildren();
            selectedFiles().forEach((file, index) => {
                const figure = document.createElement("figure");
                const image = document.createElement("img");
                const caption = document.createElement("figcaption");
                const url = URL.createObjectURL(file);
                previewUrls.push(url);
                image.src = url;
                image.alt = `Receipt screenshot ${index + 1}: ${file.name}`;
                caption.textContent = file.name;
                figure.append(image, caption);
                previews.appendChild(figure);
            });
        }

        function openModal() {
            priorFocus = document.activeElement;
            renderPreviews();
            modal.hidden = false;
            document.body.classList.add("receipt-modal-open");
            requestAnimationFrame(() => dialog.focus());
        }

        function closeModal() {
            modal.hidden = true;
            document.body.classList.remove("receipt-modal-open");
            if (!detailsAccepted && selectedFiles().length) {
                cardStatus.textContent = isScanning
                    ? "Receipt recognition is still running. Open the receipt to follow its progress."
                    : "Receipt details still need your confirmation. Open the receipt to continue.";
                cardStatus.dataset.state = "warning";
            }
            if (priorFocus instanceof HTMLElement) priorFocus.focus();
        }

        function renderFiles() {
            fileList.replaceChildren();
            selectedFiles().forEach((file, index) => {
                const row = document.createElement("span");
                const copy = document.createElement("span");
                const name = document.createElement("strong");
                const size = document.createElement("small");
                const remove = document.createElement("button");
                name.textContent = file.name;
                size.textContent = `${(file.size / (1024 * 1024)).toFixed(2)} MB`;
                copy.append(name, size);
                remove.type = "button";
                remove.setAttribute("aria-label", `Remove ${file.name}`);
                remove.innerHTML = '<i data-lucide="x"></i>';
                remove.addEventListener("click", () => {
                    const transfer = new DataTransfer();
                    selectedFiles().forEach((selected, selectedIndex) => {
                        if (selectedIndex !== index) transfer.items.add(selected);
                    });
                    fileInput.files = transfer.files;
                    handleSelectionChange();
                });
                row.append(copy, remove);
                fileList.appendChild(row);
            });
            window.lucide?.createIcons();
        }

        async function scanReceipt() {
            const error = validateFiles();
            if (error || paymentExpired) return;
            const generation = ++scanGeneration;
            isScanning = true;
            detailsAccepted = false;
            summary.hidden = true;
            paymentInputs.forEach((input) => { input.disabled = true; });
            confirmation.disabled = true;
            confirmation.checked = false;
            status.textContent = "Reading receipt… MEATTRACK is extracting the reference number, money sent, date, and time.";
            status.dataset.state = "scanning";
            cardStatus.textContent = "Reading receipt automatically…";
            cardStatus.dataset.state = "scanning";
            updateActionState();
            const body = new FormData();
            selectedFiles().forEach((file) => body.append("payment_proofs", file));
            try {
                const response = await fetch("/portal/reseller/orders/receipt-scan", {
                    method: "POST",
                    body,
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                });
                const result = await response.json();
                if (generation !== scanGeneration) return;
                if (!response.ok || !result.ok) throw new Error(result.error || "The receipt could not be read.");
                tokenInput.value = result.scan_token || "";
                transactionInput.value = result.transaction_number || "";
                amountInput.value = result.payment_amount || "";
                dateInput.value = result.payment_date || "";
                timeInput.value = result.payment_time || "";
                [...paymentInputs, confirmation].forEach((input) => { input.disabled = false; });
                const warnings = Array.isArray(result.warnings) ? result.warnings.filter(Boolean) : [];
                status.textContent = warnings.length
                    ? warnings.join(" ")
                    : "Receipt read. Check every detail against the screenshot before confirming.";
                status.dataset.state = warnings.length ? "warning" : "success";
                cardStatus.textContent = "Receipt read. Confirm the extracted details in the open window.";
                cardStatus.dataset.state = warnings.length ? "warning" : "success";
                const firstMissing = paymentInputs.find((input) => !input.value);
                (firstMissing || confirmation).focus();
            } catch (scanError) {
                if (generation !== scanGeneration) return;
                tokenInput.value = "";
                status.textContent = scanError.message || "The receipt could not be read. Try a clearer screenshot.";
                status.dataset.state = "error";
                cardStatus.textContent = status.textContent;
                cardStatus.dataset.state = "error";
            } finally {
                if (generation === scanGeneration) {
                    isScanning = false;
                    updateActionState();
                }
            }
        }

        function handleSelectionChange() {
            renderFiles();
            const error = validateFiles();
            resetScan(error || "Reading the selected receipt automatically…");
            if (error) {
                cardStatus.textContent = selectedFiles().length ? error : "Choose a clear screenshot. Receipt recognition starts automatically.";
                cardStatus.dataset.state = selectedFiles().length ? "error" : "";
                return;
            }
            openModal();
            scanReceipt();
        }

        fileInput.addEventListener("change", handleSelectionChange);

        paymentInputs.forEach((input) => {
            input.addEventListener("input", () => {
                detailsAccepted = false;
                summary.hidden = true;
                if (confirmation.checked) {
                    confirmation.checked = false;
                    status.textContent = "Payment details changed. Check them against the receipt and confirm again.";
                    status.dataset.state = "warning";
                }
                updateActionState();
            });
            input.addEventListener("change", updateActionState);
        });
        confirmation.addEventListener("change", () => {
            detailsAccepted = false;
            summary.hidden = true;
            updateActionState();
        });

        acceptButton.addEventListener("click", () => {
            updateActionState();
            if (acceptButton.disabled) {
                status.textContent = "Complete every payment field and confirm that it matches the receipt.";
                status.dataset.state = "error";
                (paymentInputs.find((input) => !input.value) || confirmation).focus();
                return;
            }
            detailsAccepted = true;
            summaryReference.textContent = transactionInput.value.trim();
            summaryAmount.textContent = `₱${Number(amountInput.value).toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
            summaryDate.textContent = `${dateInput.value} at ${timeInput.value}`;
            summary.hidden = false;
            cardStatus.textContent = "Payment details confirmed. Submit them before the timer expires.";
            cardStatus.dataset.state = "success";
            updateActionState();
            closeModal();
        });

        editButton.addEventListener("click", openModal);
        closeButtons.forEach((button) => button.addEventListener("click", closeModal));
        form.addEventListener("payment-expired", () => {
            paymentExpired = true;
            scanGeneration += 1;
            fileInput.disabled = true;
            confirmation.disabled = true;
            paymentInputs.forEach((input) => { input.disabled = true; });
            status.textContent = "The 15-minute payment window expired. This order can no longer accept payment.";
            status.dataset.state = "error";
            cardStatus.textContent = status.textContent;
            cardStatus.dataset.state = "error";
            updateActionState();
        });
        dialog.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                event.preventDefault();
                closeModal();
                return;
            }
            if (event.key !== "Tab") return;
            const focusable = Array.from(dialog.querySelectorAll("button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex='-1'])"));
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

        form.addEventListener("submit", (event) => {
            updateActionState();
            if (submitButton.disabled) {
                event.preventDefault();
                status.textContent = "Review the receipt, complete all payment details, and confirm them before placing the order.";
                status.dataset.state = "error";
                if (selectedFiles().length) openModal();
                return;
            }
            submitButton.disabled = true;
            submitButton.setAttribute("aria-busy", "true");
            submitButton.querySelector("span").textContent = "Submitting payment...";
        });

        renderFiles();
        updateActionState();
    }

    function bindReceiptCheckout() {
        document.querySelectorAll("[data-receipt-checkout]").forEach(bindReceiptCheckoutForm);
    }

    bindQuantitySteppers();
    bindProductSearch();
    bindProductModal();
    bindProofModal();
    bindOrderActionConfirmation();
    bindCartAutosave();
    bindInventoryProductsPagination();
    bindInventoryWorkspaceModals();
    bindRecipeRows();
    bindReceiptCheckout();
    bindPaymentCountdowns();
})();
