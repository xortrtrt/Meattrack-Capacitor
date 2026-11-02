/* Presentation-only enhancements; portal.js owns the modal data and cart form. */
(() => {
    function enhanceCatalog() {
        const modal = document.querySelector('.reseller-order-page [data-product-modal]');
        const dialog = modal?.querySelector('[role="dialog"]');
        const quantity = modal?.querySelector('[data-product-modal-quantity]');
        if (!modal || !dialog || !quantity) return;

        const steps = modal.querySelectorAll('[data-quantity-step]');
        const close = dialog.querySelector('[data-product-modal-close]');

        function updateSteps() {
            const value = Number(quantity.value);
            steps.forEach((button) => {
                const atLimit = Number(button.dataset.quantityStep) < 0
                    ? value <= Number(quantity.min)
                    : value >= Number(quantity.max);
                button.disabled = quantity.disabled || atLimit;
            });
        }

        quantity.addEventListener('input', updateSteps);
        new MutationObserver(() => {
            if (modal.hidden) return;
            updateSteps();
            const stock = dialog.querySelector('[data-product-modal-stock]');
            if (stock && Number(quantity.max) !== 1) {
                stock.textContent = stock.textContent.replace(/ pack$/, ' packs');
            }
            // The existing modal focuses quantity; unavailable products need a focus target too.
            if (quantity.disabled) window.requestAnimationFrame(() => close?.focus());
        }).observe(modal, { attributes: true, attributeFilter: ['hidden'] });

        dialog.addEventListener('keydown', (event) => {
            if (event.key !== 'Tab') return;
            const controls = Array.from(dialog.querySelectorAll(
                'button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), a[href], [tabindex="0"]'
            )).filter((element) => element.getClientRects().length);
            const first = controls[0];
            const last = controls[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', enhanceCatalog, { once: true });
    } else {
        enhanceCatalog();
    }
})();
