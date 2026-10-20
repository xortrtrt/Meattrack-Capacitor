(function () {
    // Only navigation metadata is added client-side. The server owns field allowlists.
    document.addEventListener("submit", (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== "post") return;
        let input = form.querySelector('input[name="_owner_return_to"]');
        if (!input) {
            input = document.createElement("input");
            input.type = "hidden";
            input.name = "_owner_return_to";
            form.append(input);
        }
        input.value = window.location.pathname + window.location.search;
    }, true);

    function restore() {
        const payload = document.getElementById("owner-action-resume");
        if (!payload) return;
        let draft;
        try { draft = JSON.parse(payload.textContent); } catch { return; }
        payload.remove();
        const form = [...document.forms].find((candidate) => {
            if (new URL(candidate.action, location.href).pathname !== draft.action) return false;
            if (draft.action === "/portal/owner/products") {
                return candidate.elements.namedItem("product_id")?.value === draft.fields.product_id;
            }
            return true;
        });
        if (!form || draft.discarded) {
            const alert = document.createElement("p");
            alert.className = "flash error";
            alert.setAttribute("role", "alert");
            alert.textContent = draft.discarded
                ? "Your draft was too large to restore. Please re-enter and review the action."
                : "The original action is no longer available on this page. Review the current records and restart the action.";
            document.querySelector(".portal-main")?.append(alert);
            return;
        }
        for (const [name, value] of Object.entries(draft.fields)) {
            const controls = [...form.elements].filter((control) => control.name === name
                && (control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement));
            for (const control of controls) {
                if (control.type === "checkbox" || control.type === "radio") control.checked = control.value === value;
                else if (control.type !== "file") control.value = value;
            }
        }
        for (let parent = form.parentElement; parent; parent = parent.parentElement) {
            if (parent instanceof HTMLDetailsElement) parent.open = true;
        }
        const dialog = form.closest("dialog");
        if (dialog) {
            const opener = [...document.querySelectorAll("[data-owner-modal-open], [data-owner-order-open], [data-owner-conversation-open]")]
                .find((el) => (el.dataset.ownerModalOpen || el.dataset.ownerOrderOpen || el.dataset.ownerConversationOpen) === dialog.id);
            if (opener) opener.click();
        }
        form.scrollIntoView({ block: "center", behavior: "instant" });
        const focus = [...form.elements].find((control) => control.type !== "hidden" && !control.disabled && control.getClientRects().length);
        focus?.focus({ preventScroll: true });
        // Deliberately never submit: authorization and business state are checked again on review.
    }
    if (document.readyState === "complete") restore();
    else window.addEventListener("load", restore, { once: true });
})();
