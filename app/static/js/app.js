(function () {
    if (window.lucide) {
        window.lucide.createIcons();
    }

    function bindToasts() {
        document.querySelectorAll("[data-toast]").forEach((toast) => {
            const close = toast.querySelector("[data-toast-close]");
            const persistent = toast.dataset.toastPersistent === "true";
            const dismiss = () => {
                toast.classList.add("is-hiding");
                window.setTimeout(() => toast.remove(), 180);
            };
            close?.addEventListener("click", dismiss);
            if (!persistent) {
                window.setTimeout(dismiss, 5200);
            }
        });
    }

    bindToasts();

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

    function bindMobileMenu(toggleSelector, navSelector) {
        const toggle = document.querySelector(toggleSelector);
        const nav = document.querySelector(navSelector);
        if (!toggle || !nav) {
            return;
        }

        function setOpen(open) {
            toggle.setAttribute("aria-expanded", String(open));
            nav.classList.toggle("is-open", open);
        }

        toggle.addEventListener("click", () => {
            setOpen(toggle.getAttribute("aria-expanded") !== "true");
        });

        nav.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", () => setOpen(false));
        });
    }

    bindMobileMenu("[data-mobile-nav-toggle]", "[data-mobile-nav]");

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
                const value = Number(input.value || min || 0);
                input.value = String(Math.max(min, value + step));
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
            }
            if (addButton) {
                addButton.setAttribute("aria-label", `Add ${productName} to cart`);
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

        document.querySelectorAll("[data-product-modal-open]").forEach((button) => {
            button.addEventListener("click", () => {
                const card = button.closest("[data-product-card]");
                if (card) {
                    openModal(card);
                }
            });
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
        let lastFocused = null;

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

        document.querySelectorAll("[data-proof-modal-open]").forEach((trigger) => {
            trigger.addEventListener("click", (event) => {
                event.preventDefault();
                openModal(trigger);
            });
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

            cart.querySelectorAll("[data-cart-item]").forEach((item) => {
                const input = item.querySelector("[data-cart-quantity]");
                const lineTotal = item.querySelector("[data-cart-line-total]");
                if (!input) {
                    return;
                }

                const min = Number(input.getAttribute("min") || 1);
                const quantity = Math.max(min, Number(input.value || min));
                const unitPrice = Number(item.dataset.unitPrice || 0);
                const lineAmount = quantity * unitPrice;

                totalQuantity += quantity;
                totalAmount += lineAmount;

                if (lineTotal) {
                    lineTotal.textContent = formatMoney(lineAmount);
                }
            });

            cart.querySelectorAll("[data-cart-total]").forEach((element) => {
                element.textContent = formatMoney(totalAmount);
            });
            cart.querySelectorAll("[data-cart-count]").forEach((element) => {
                element.textContent = `${formatQuantity(totalQuantity)} packs in cart`;
            });
            document.querySelectorAll("[data-cart-nav-count]").forEach((element) => {
                element.textContent = formatQuantity(totalQuantity);
            });
        }

        async function saveCartItem(form) {
            const productId = form.querySelector("input[name='product_id']")?.value;
            if (!productId) {
                return;
            }

            setStatus("Saving cart...", "saving");
            try {
                const response = await fetch(form.action, {
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
                cart.querySelectorAll("[data-cart-total]").forEach((element) => {
                    element.textContent = formatMoney(result.cart_total);
                });
                cart.querySelectorAll("[data-cart-count]").forEach((element) => {
                    element.textContent = `${formatQuantity(result.cart_count)} packs in cart`;
                });
                document.querySelectorAll("[data-cart-nav-count]").forEach((element) => {
                    element.textContent = formatQuantity(result.cart_count);
                });
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

        recalculateCart();
    }

    function bindInventoryMovementChart() {
        const canvas = document.querySelector("[data-inventory-movement-chart]");
        const payload = document.querySelector("[data-inventory-movement-json]");
        if (!canvas || !payload) {
            return;
        }

        let rows = [];
        try {
            rows = JSON.parse(payload.textContent || "[]");
        } catch (error) {
            rows = [];
        }

        if (!rows.length || !window.Chart) {
            return;
        }

        const labels = rows.map((row) => row.name);
        const stockIn = rows.map((row) => Number(row.total_in || 0));
        const stockOut = rows.map((row) => Number(row.total_out || 0));
        const units = rows.map((row) => row.unit || "pack");

        new window.Chart(canvas.getContext("2d"), {
            type: "bar",
            data: {
                labels,
                datasets: [
                    {
                        label: "Stock in",
                        data: stockIn,
                        backgroundColor: "rgba(223, 190, 29, 0.82)",
                        borderColor: "#d9b90f",
                        borderWidth: 1,
                        borderRadius: 8,
                    },
                    {
                        label: "Stock out",
                        data: stockOut,
                        backgroundColor: "rgba(91, 48, 25, 0.82)",
                        borderColor: "#5b3019",
                        borderWidth: 1,
                        borderRadius: 8,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: "y",
                scales: {
                    x: {
                        beginAtZero: true,
                        ticks: {
                            precision: 0,
                            color: "#675b44",
                        },
                        grid: {
                            color: "rgba(215, 201, 154, 0.45)",
                        },
                    },
                    y: {
                        ticks: {
                            color: "#23150f",
                            font: {
                                weight: 700,
                            },
                        },
                        grid: {
                            display: false,
                        },
                    },
                },
                plugins: {
                    legend: {
                        labels: {
                            color: "#23150f",
                            boxWidth: 14,
                            boxHeight: 14,
                            useBorderRadius: true,
                        },
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const unit = units[context.dataIndex] || "pack";
                                return `${context.dataset.label}: ${formatQuantity(context.parsed.x)} ${unit}`;
                            },
                        },
                    },
                },
            },
        });

        const fallback = document.querySelector("[data-inventory-movement-fallback]");
        if (fallback) {
            fallback.hidden = true;
        }
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

    bindQuantitySteppers();
    bindProductSearch();
    bindProductModal();
    bindProofModal();
    bindCartAutosave();
    bindInventoryMovementChart();
    bindInventoryProductsPagination();

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
        bubble.className = `message ${type === 'user' ? 'user-message' : 'bot-message'}`;
        bubble.textContent = text;
        messages.appendChild(bubble);
        messages.scrollTop = messages.scrollHeight;
        return bubble;
    }

    toggle.addEventListener("click", () => setOpen(panel.hidden));
    closeButton.addEventListener("click", () => setOpen(false));
    document.querySelectorAll("[data-open-chatbot]").forEach((button) => {
        button.addEventListener("click", () => {
            setOpen(true);
            input.value = "I want to be a reseller";
        });
    });

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
