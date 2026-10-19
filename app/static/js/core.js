(function () {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    if (csrfToken) {
        const ensureCsrfToken = (form) => {
            if (!form.querySelector('input[name="csrf_token"]')) {
                const input = document.createElement("input");
                input.type = "hidden";
                input.name = "csrf_token";
                input.value = csrfToken;
                form.appendChild(input);
            }
        };
        document.querySelectorAll('form[method="post" i]').forEach(ensureCsrfToken);
        document.addEventListener("submit", (event) => {
            const form = event.target;
            if (form instanceof HTMLFormElement && form.method.toLowerCase() === "post") {
                ensureCsrfToken(form);
            }
        }, true);
        const nativeFetch = window.fetch.bind(window);
        window.fetch = function (input, init = {}) {
            const url = typeof input === "string" ? input : input.url;
            const target = new URL(url, window.location.href);
            if (target.origin === window.location.origin) {
                const headers = new Headers(init.headers || (typeof input !== "string" ? input.headers : undefined));
                headers.set("X-CSRF-Token", csrfToken);
                init = { ...init, headers };
            }
            return nativeFetch(input, init);
        };
    }
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
})();
