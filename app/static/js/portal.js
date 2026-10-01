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

    function bindInquiryRejection() {
        const modal = document.querySelector("[data-inquiry-reject-modal]");
        if (!modal) return;
        const form = modal.querySelector("[data-inquiry-reject-form]");
        const reason = modal.querySelector("[data-inquiry-reject-reason]");
        const business = modal.querySelector("[data-inquiry-reject-business]");
        let returnFocus = null;

        function closeModal() {
            modal.hidden = true;
            document.body.classList.remove("modal-is-open");
            form.reset();
            returnFocus?.focus();
            returnFocus = null;
        }

        document.querySelectorAll("[data-inquiry-reject-open]").forEach((button) => {
            button.addEventListener("click", () => {
                returnFocus = button;
                form.action = button.dataset.inquiryRejectAction || "";
                business.textContent = button.dataset.inquiryRejectBusiness || "this applicant";
                modal.hidden = false;
                document.body.classList.add("modal-is-open");
                window.requestAnimationFrame(() => reason.focus());
            });
        });
        modal.querySelectorAll("[data-inquiry-reject-close]").forEach((button) => {
            button.addEventListener("click", closeModal);
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !modal.hidden) closeModal();
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

    bindQuantitySteppers();
    bindProductSearch();
    bindProductModal();
    bindProofModal();
    bindCartAutosave();
    bindInventoryProductsPagination();
    bindRecipeRows();
})();
