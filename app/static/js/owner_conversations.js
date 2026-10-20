(function () {
    const requests = new WeakMap();

    document.addEventListener("submit", async (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || !form.matches("[data-owner-transcript-form]")) return;
        event.preventDefault();
        const dialog = form.closest("dialog");
        if (!dialog || requests.has(dialog) || !form.reportValidity()) return;
        const result = dialog.querySelector("[data-owner-transcript-result]");
        const error = dialog.querySelector("[data-owner-transcript-error]");
        const button = form.querySelector("button[type='submit']");
        const buttonContent = button.innerHTML;
        const controller = new AbortController();
        requests.set(dialog, controller);
        error.hidden = true;
        button.disabled = true;
        button.textContent = "Opening transcript…";
        result.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: new FormData(form),
                headers: {
                    "X-Transcript-View": "modal",
                    "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "",
                    Accept: "text/html",
                },
                credentials: "same-origin",
                redirect: "error",
                signal: controller.signal,
            });
            if (!response.ok) {
                let message = "Unable to open this transcript. Please try again.";
                if (response.headers.get("content-type")?.includes("application/json")) {
                    const payload = await response.json();
                    if (typeof payload.error === "string") message = payload.error;
                }
                throw new Error(message);
            }
            const html = await response.text();
            if (!dialog.open || dialog.classList.contains("is-closing")) return;
            result.innerHTML = html;
            form.hidden = true;
            result.setAttribute("tabindex", "-1");
            result.focus({ preventScroll: true });
        } catch (failure) {
            if (failure.name === "AbortError") return;
            error.textContent = failure.message === "Failed to fetch"
                ? "Unable to open the transcript. Check your connection or sign in again."
                : failure.message;
            error.hidden = false;
        } finally {
            requests.delete(dialog);
            result.removeAttribute("aria-busy");
            button.disabled = false;
            button.innerHTML = buttonContent;
            button.classList.remove("is-loading");
        }
    });

    document.addEventListener("close", (event) => {
        const dialog = event.target;
        if (!(dialog instanceof HTMLDialogElement) || !dialog.matches("[data-owner-conversation-dialog]")) return;
        requests.get(dialog)?.abort();
        requests.delete(dialog);
        dialog.querySelector("[data-owner-transcript-result]").replaceChildren();
        dialog.querySelector("[data-owner-transcript-error]").hidden = true;
        const form = dialog.querySelector("[data-owner-transcript-form]");
        form.hidden = false;
        form.reset();
    }, true);
})();
