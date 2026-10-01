export function bindLandingExperience() {
    if (!document.body.classList.contains("landing-page")) {
        return;
    }

    const nav = document.querySelector("[data-landing-nav]");
    const hero = document.querySelector(".landing-hero");
    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frameRequested = false;

    function updateLandingScrollState() {
        nav?.classList.toggle("is-scrolled", window.scrollY > 28);
        document.body.classList.toggle("landing-has-scrolled", window.scrollY > 120);
        if (!prefersReducedMotion && hero && window.innerWidth > 1100) {
            const shift = Math.min(34, window.scrollY * 0.055);
            hero.style.setProperty("--hero-shift", `${shift}px`);
        } else if (hero) {
            hero.style.setProperty("--hero-shift", "0px");
        }
        frameRequested = false;
    }

    function requestLandingScrollUpdate() {
        if (frameRequested) {
            return;
        }
        frameRequested = true;
        window.requestAnimationFrame(updateLandingScrollState);
    }

    updateLandingScrollState();
    window.addEventListener("scroll", requestLandingScrollUpdate, { passive: true });

    if (prefersReducedMotion) {
        return;
    }

    document.body.classList.add("landing-motion-ready", "landing-reveal-ready");
    const revealNodes = Array.from(
        document.querySelectorAll("[data-landing-reveal], [data-landing-reveal-item]")
    );
    revealNodes.forEach((node, index) => {
        if (node.hasAttribute("data-landing-reveal-item")) {
            node.style.setProperty("--landing-reveal-delay", `${(index % 3) * 70}ms`);
        }
    });

    if (!("IntersectionObserver" in window)) {
        revealNodes.forEach((node) => node.classList.add("is-visible"));
        return;
    }

    const revealObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) {
                return;
            }
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
        });
    }, { rootMargin: "0px 0px -10% 0px", threshold: 0.08 });

    revealNodes.forEach((node) => revealObserver.observe(node));
}

export function bindLandingProductModal() {
    const modal = document.querySelector("[data-landing-product-modal]");
    if (!modal) {
        return;
    }

    const panel = modal.querySelector(".landing-product-modal-panel");
    const closeButton = modal.querySelector(".landing-product-modal-close");
    const image = modal.querySelector("[data-landing-product-modal-image]");
    const category = modal.querySelector("[data-landing-product-modal-category]");
    const name = modal.querySelector("[data-landing-product-modal-name]");
    const price = modal.querySelector("[data-landing-product-modal-price]");
    const pack = modal.querySelector("[data-landing-product-modal-pack]");
    const inquireButton = modal.querySelector("[data-landing-product-inquire]");
    let lastFocused = null;

    function openModal(product) {
        const productName = product.dataset.productName || "Batangas Premium product";
        lastFocused = document.activeElement;

        if (image) {
            image.src = product.dataset.productImage || "";
            image.alt = productName;
        }
        if (category) {
            category.textContent = product.dataset.productCategory || "Frozen product";
        }
        if (name) {
            name.textContent = productName;
        }
        if (price) {
            price.textContent = product.dataset.productPrice || "Ask for current pricing";
        }
        if (pack) {
            pack.textContent = product.dataset.productPack || "Frozen retail pack";
        }
        if (inquireButton) {
            inquireButton.dataset.chatbotPrompt = `I'm interested in ${productName}. Can you tell me more?`;
        }

        modal.hidden = false;
        document.body.classList.add("modal-is-open");
        window.requestAnimationFrame(() => {
            modal.classList.add("is-open");
            closeButton?.focus();
        });
    }

    function closeModal() {
        if (modal.hidden) {
            return;
        }
        modal.classList.remove("is-open");
        modal.hidden = true;
        document.body.classList.remove("modal-is-open");
        if (lastFocused && typeof lastFocused.focus === "function") {
            lastFocused.focus();
        }
    }

    document.querySelectorAll("[data-landing-product-open]").forEach((trigger) => {
        trigger.addEventListener("click", () => {
            const product = trigger.closest("[data-landing-product]");
            if (product) {
                openModal(product);
            }
        });
    });

    modal.querySelectorAll("[data-landing-product-modal-close]").forEach((trigger) => {
        trigger.addEventListener("click", closeModal);
    });
    inquireButton?.addEventListener("click", closeModal);

    document.addEventListener("keydown", (event) => {
        if (modal.hidden) {
            return;
        }
        if (event.key === "Escape") {
            closeModal();
            return;
        }
        if (event.key !== "Tab" || !panel) {
            return;
        }

        const focusable = Array.from(
            panel.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])')
        );
        if (!focusable.length) {
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
}


