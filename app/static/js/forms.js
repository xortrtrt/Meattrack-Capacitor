(function () {
    let errorSequence = 0;
    const clearError = input => {
        const id = input.dataset.portalErrorId;
        if (id) {
            document.getElementById(id)?.remove();
            const help = (input.getAttribute('aria-describedby') || '').split(/\s+/).filter(value => value && value !== id);
            if (help.length) input.setAttribute('aria-describedby', help.join(' '));
            else input.removeAttribute('aria-describedby');
        }
        input.removeAttribute('aria-invalid');
    };
    const showError = (input, message) => {
        clearError(input);
        const id = input.dataset.portalErrorId ||= `portal-field-error-${++errorSequence}`;
        const error = document.createElement('small');
        error.id = id; error.className = 'portal-field-error'; error.textContent = message;
        // Keep the error outside a wrapping label so the control's name stays stable.
        (input.closest('label') || input).insertAdjacentElement('afterend', error);
        input.setAttribute('aria-invalid', 'true');
        input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), id].filter(Boolean).join(' '));
    };
    const validate = (form, {focus = true} = {}) => {
        let first = null;
        [...form.elements].forEach(input => {
            if (!input.willValidate) return;
            clearError(input);
            if (!input.validity.valid) {
                const label = input.getAttribute('aria-label') || input.labels?.[0]?.textContent.trim().split('\n')[0] || 'This field';
                showError(input, `${label}: ${input.validationMessage}`);
                first ||= input;
            }
        });
        if (first && focus) { first.scrollIntoView({block: 'center'}); first.focus(); }
        return !first;
    };
    document.querySelectorAll('[data-password-change-form]').forEach(form => {
        form.addEventListener('submit', event => {
            const password = form.elements.namedItem('new_password');
            const confirmation = form.elements.namedItem('confirm_password');
            if (confirmation && password) confirmation.setCustomValidity(confirmation.value === password.value ? '' : 'Passwords do not match.');
            if (!validate(form)) event.preventDefault();
        });
        form.addEventListener('input', event => {
            event.target.setCustomValidity?.('');
            clearError(event.target);
        });
    });
    const dirty = new Set();
    let submitted = false, discardPending = false;
    const confirmDiscard = async scope => {
        const changed = [...dirty].filter(form => scope.contains(form));
        if (!changed.length || submitted) return true;
        if (discardPending) return false;
        const discardDialog = document.createElement('dialog');
        discardDialog.className = 'portal-discard-dialog';
        discardDialog.setAttribute('aria-labelledby', 'portal-discard-title');
        const title = document.createElement('h3'); title.id = 'portal-discard-title'; title.textContent = 'Discard unsaved changes?';
        const description = document.createElement('p'); description.textContent = 'Your unsaved return details will be cleared. Stay here to keep editing.';
        description.id = 'portal-discard-description';
        discardDialog.setAttribute('aria-describedby', description.id);
        const actions = document.createElement('div'); actions.className = 'portal-discard-actions';
        const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'button subtle'; cancel.textContent = 'Keep editing'; cancel.autofocus = true;
        const discard = document.createElement('button'); discard.type = 'button'; discard.className = 'button primary'; discard.textContent = 'Discard changes';
        actions.append(cancel, discard); discardDialog.append(title, description, actions); document.body.append(discardDialog);
        const opener = document.activeElement;
        const choice = await new Promise(resolve => {
            discardPending = true;
            let settled = false;
            const finish = discardChoice => {
                if (settled) return;
                settled = true; discardPending = false;
                if (discardDialog.open) discardDialog.close();
                discardDialog.remove(); opener?.focus(); resolve(discardChoice);
            };
            cancel.addEventListener('click', () => finish(false));
            discard.addEventListener('click', () => finish(true));
            discardDialog.addEventListener('cancel', event => { event.preventDefault(); finish(false); });
            discardDialog.addEventListener('close', () => finish(false), {once: true});
            discardDialog.showModal(); cancel.focus();
        });
        if (choice) changed.forEach(form => {
            form.reset();
            form.querySelectorAll('input,textarea,select').forEach(input => {
                input.setCustomValidity?.(''); clearError(input);
            });
            form.querySelectorAll('select').forEach(input => input.dispatchEvent(new Event('change', {bubbles: true})));
            form.querySelector('[name="quantity"]')?.dispatchEvent(new Event('input', {bubbles: true}));
            dirty.delete(form);
        });
        return choice;
    };
    const allowNavigation = () => { submitted = true; dirty.clear(); };
    window.PortalForms = {validate, showError, clearError, confirmDiscard, allowNavigation};
    window.addEventListener('beforeunload', event => {
        if (!submitted && dirty.size) { event.preventDefault(); event.returnValue = ''; }
    });
    document.addEventListener('click', async event => {
        const link = event.target.closest('a[href]');
        if (!link || submitted || !dirty.size || event.defaultPrevented || link.target === '_blank' || link.hasAttribute('download') || link.hasAttribute('data-return-tab') || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (await confirmDiscard(document)) location.assign(link.href);
    });
    const returnFormSelector = '[data-return-details] form, [data-return-confirmation-surface] form, form[data-return-intent], [data-refund-conversion], .return-request-form';
    document.querySelectorAll('[data-return-details] textarea, [data-return-confirmation-surface] textarea, form[data-return-intent] textarea, [data-refund-conversion] textarea, .return-request-form textarea').forEach(input => {
        const grow = () => { input.style.height = 'auto'; input.style.height = `${Math.max(88, input.scrollHeight)}px`; };
        input.addEventListener('input', grow); grow();
    });
    document.querySelectorAll(returnFormSelector).forEach(form => {
        form.noValidate = true;
        form.addEventListener('submit', event => {
            if (form.matches('.return-request-form[action$="submit"]') || form.dataset.returnConfirmationBound === 'true') return;
            if (!validate(form)) event.preventDefault();
        });
        form.addEventListener('input', event => {
            dirty.add(form);
            if (event.target.hasAttribute('aria-invalid')) { event.target.setCustomValidity?.(''); clearError(event.target); }
        });
        form.addEventListener('change', () => dirty.add(form));
    });
    // Observe after form-level validation/async handlers have prevented submission.
    // A microtask in an earlier form listener can run before the next listener.
    document.addEventListener('submit', event => {
        if (!event.defaultPrevented && event.target.matches(returnFormSelector)) allowNavigation();
    });
    document.addEventListener('click', event => {
        const add = event.target.closest('[data-edit-recipe-add]');
        const remove = event.target.closest('[data-edit-recipe-remove]');
        const form = (add || remove)?.closest('[data-recipe-editor]');
        if (!form) return;
        const rows = form.querySelector('[data-edit-recipe-rows]');
        if (add && rows.children.length < 200) {
            const fragment = form.querySelector('[data-edit-recipe-template]').content.cloneNode(true);
            rows.append(fragment); rows.lastElementChild.querySelector('select').focus();
        }
        if (remove) {
            const row = remove.closest('[data-edit-recipe-row]');
            if (rows.children.length > 1) { row.remove(); rows.lastElementChild.querySelector('select').focus(); }
            else { row.querySelectorAll('input').forEach(input => { input.value = ''; }); row.querySelector('select').value = ''; row.querySelector('select').focus(); }
        }
    });
    document.addEventListener('input', event => {
        if (event.target.closest('[data-recipe-editor]')) clearError(event.target);
    });
    document.addEventListener('submit', event => {
        const form = event.target.closest('[data-recipe-editor]');
        if (!form) return;
        if (form.dataset.saving === 'true' || !validate(form)) { event.preventDefault(); return; }
        form.dataset.saving = 'true';
        const button = form.querySelector('[type="submit"]'); button.disabled = true; button.classList.add('is-loading');
    }, true);
    const stockDraft = document.getElementById('inventory-stock-draft');
    if (stockDraft) {
        const draft = JSON.parse(stockDraft.textContent);
        const form = document.querySelector(`form[action="/portal/team-leader/stock-actions/${draft.action}"]`);
        if (form) Object.entries(draft.values).forEach(([name, value]) => {
            const input = form.elements.namedItem(name); if (input) input.value = value;
        });
    }
    // Inventory mutations share field errors, accessible confirmation and duplicate prevention.
    document.querySelectorAll('form[action*="/stock-actions/"]').forEach(form => {
        let committing = false;
        form.noValidate = true;
        form.addEventListener('input', event => clearError(event.target));
        form.addEventListener('submit', event => {
            if (committing) { event.preventDefault(); return; }
            if (!validate(form)) { event.preventDefault(); return; }
            if (form.dataset.confirm && form.dataset.confirmed !== 'true') {
                event.preventDefault();
                const dialog = document.createElement('dialog');
                dialog.className = 'portal-discard-dialog';
                const title = document.createElement('h2'); title.textContent = 'Confirm stock action';
                const copy = document.createElement('p'); copy.textContent = form.dataset.confirm;
                const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'button subtle'; cancel.textContent = 'Cancel';
                const accept = document.createElement('button'); accept.type = 'button'; accept.className = 'button danger'; accept.textContent = form.querySelector('[type="submit"]').textContent.trim();
                dialog.append(title, copy, cancel, accept); document.body.append(dialog);
                dialog.setAttribute('aria-label', 'Confirm stock action');
                dialog.addEventListener('close', () => { dialog.remove(); form.querySelector('[type="submit"]').focus(); });
                cancel.addEventListener('click', () => dialog.close());
                accept.addEventListener('click', () => { form.dataset.confirmed = 'true'; dialog.close(); form.requestSubmit(); });
                dialog.showModal(); cancel.focus();
                return;
            }
            committing = true;
            form.querySelectorAll('[type="submit"]').forEach(button => { button.disabled = true; });
        });
    });
    document.querySelectorAll("form").forEach((form) => {
        form.addEventListener("submit", event => {
            if (event.defaultPrevented) return;
            if (form.hasAttribute("data-chatbot-form") || form.hasAttribute("data-async-form")) {
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
    function bindOtpModal() {
        const modal = document.querySelector("[data-otp-modal]");
        if (!modal) {
            return;
        }
        const input = modal.querySelector("[data-otp-modal-input]");

        function close() {
            modal.hidden = true;
            document.body.classList.remove("modal-is-open");
        }

        document.body.classList.add("modal-is-open");
        input?.focus();

        modal.querySelectorAll("[data-otp-modal-close]").forEach((button) => {
            button.addEventListener("click", close);
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !modal.hidden) {
                close();
            }
        });
    }
    function bindPasswordRuleTracking() {
        document.querySelectorAll("[data-password-rules-input]").forEach((input) => {
            const ruleList = input.closest("label")?.querySelector("[data-password-rule-list]");
            if (!ruleList) {
                return;
            }
            const rules = {
                length: (value) => value.length >= 8,
                uppercase: (value) => /[A-Z]/.test(value),
                lowercase: (value) => /[a-z]/.test(value),
                number: (value) => /[0-9]/.test(value),
                special: (value) => /[^A-Za-z0-9]/.test(value),
            };

            function updateRules() {
                const value = input.value || "";
                Object.entries(rules).forEach(([rule, validator]) => {
                    const item = ruleList.querySelector(`[data-password-rule="${rule}"]`);
                    if (!item) {
                        return;
                    }
                    const passed = validator(value);
                    item.classList.toggle("is-met", passed);
                    item.classList.toggle("is-missing", !passed);
                    item.setAttribute("aria-label", `${item.textContent.trim()}: ${passed ? "met" : "missing"}`);
                });
            }

            input.addEventListener("input", updateRules);
            input.addEventListener("blur", updateRules);
            updateRules();
        });
    }
    bindOtpModal();
    bindPasswordRuleTracking();
})();
