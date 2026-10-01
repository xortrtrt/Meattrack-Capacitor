export function bindMobileMenu(toggleSelector, navSelector) {
    const toggle = document.querySelector(toggleSelector);
    const nav = document.querySelector(navSelector);
    if (!toggle || !nav) {
        return;
    }

    let returnFocusOnClose = false;

    function setOpen(open, options = {}) {
        toggle.setAttribute("aria-expanded", String(open));
        toggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
        nav.classList.toggle("is-open", open);
        document.body.classList.toggle("mobile-menu-open", open);

        if (open) {
            returnFocusOnClose = true;
            window.requestAnimationFrame(() => nav.querySelector("a")?.focus());
        } else if (options.restoreFocus && returnFocusOnClose) {
            toggle.focus();
            returnFocusOnClose = false;
        }
    }

    toggle.addEventListener("click", () => {
        setOpen(toggle.getAttribute("aria-expanded") !== "true");
    });

    document.addEventListener("pointerdown", (event) => {
        if (toggle.getAttribute("aria-expanded") !== "true") {
            return;
        }
        if (nav.contains(event.target) || toggle.contains(event.target)) {
            return;
        }
        setOpen(false);
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setOpen(false, { restoreFocus: true });
            return;
        }
        if (event.key !== "Tab" || toggle.getAttribute("aria-expanded") !== "true") {
            return;
        }
        const focusable = [toggle, ...nav.querySelectorAll("a, button")].filter(
            (element) => !element.hasAttribute("disabled") && element.getClientRects().length > 0
        );
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
        }
    });

    window.addEventListener("resize", () => {
        if (window.innerWidth > 1100 && toggle.getAttribute("aria-expanded") === "true") {
            setOpen(false);
        }
    });

    nav.querySelectorAll("a").forEach((link) => {
        link.addEventListener("click", () => setOpen(false));
    });
}

