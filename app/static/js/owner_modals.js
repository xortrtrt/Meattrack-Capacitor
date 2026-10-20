(function () {
    let returnFocus = null;

    function closeDialog(dialog) {
        if (!(dialog instanceof HTMLDialogElement) || !dialog.open
            || dialog.classList.contains("is-closing")) return;
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            dialog.close();
            return;
        }
        dialog.classList.add("is-closing");
        let fallback;
        const finish = () => {
            window.clearTimeout(fallback);
            dialog.removeEventListener("animationend", onEnd);
            dialog.close();
        };
        const onEnd = (event) => {
            if (event.target === dialog) finish();
        };
        dialog.addEventListener("animationend", onEnd);
        fallback = window.setTimeout(finish, 220);
    }

    document.addEventListener("click", (event) => {
        const opener = event.target.closest("[data-owner-order-open], [data-owner-conversation-open], [data-owner-modal-open]");
        if (opener) {
            const dialog = document.getElementById(opener.dataset.ownerOrderOpen || opener.dataset.ownerConversationOpen || opener.dataset.ownerModalOpen);
            if (!(dialog instanceof HTMLDialogElement) || dialog.open) return;
            returnFocus = opener;
            dialog.showModal();
            document.body.classList.add("modal-is-open");
            return;
        }

        const close = event.target.closest("[data-owner-order-close], [data-owner-conversation-close], [data-owner-modal-close]");
        if (close) {
            closeDialog(close.closest("dialog"));
            return;
        }

        // Native dialogs receive backdrop clicks on the dialog itself.
        const dialog = event.target;
        if (dialog instanceof HTMLDialogElement && dialog.matches("[data-owner-order-dialog], [data-owner-conversation-dialog], [data-owner-modal]")) {
            const bounds = dialog.getBoundingClientRect();
            if (event.clientX < bounds.left || event.clientX > bounds.right
                || event.clientY < bounds.top || event.clientY > bounds.bottom) closeDialog(dialog);
        }
    });

    document.addEventListener("cancel", (event) => {
        if (!(event.target instanceof HTMLDialogElement)
            || !event.target.matches("[data-owner-order-dialog], [data-owner-conversation-dialog], [data-owner-modal]")) return;
        event.preventDefault();
        closeDialog(event.target);
    }, true);

    // A read-only report may have just one focusable control. Keep Tab inside
    // the dialog rather than letting the browser move focus into its chrome.
    document.addEventListener("keydown", (event) => {
        if (event.key !== "Tab") return;
        const dialog = document.querySelector("dialog[data-owner-modal][open]");
        if (!dialog) return;
        const controls = [...dialog.querySelectorAll("button, a[href], input, select, textarea, summary, [tabindex]")]
            .filter((control) => !control.disabled && control.tabIndex >= 0 && control.getClientRects().length);
        if (!controls.length) { event.preventDefault(); return; }
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first.focus();
        }
    });

    // Keep native focus trapping until the exit animation has finished.
    document.addEventListener("close", (event) => {
        if (!(event.target instanceof HTMLDialogElement)
            || !event.target.matches("[data-owner-order-dialog], [data-owner-conversation-dialog], [data-owner-modal]")) return;
        document.body.classList.remove("modal-is-open");
        event.target.classList.remove("is-closing");
        if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
        returnFocus = null;
    }, true);
})();
