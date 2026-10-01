(function () {
    function bindProfilePictureEditor() {
        if (document.documentElement.dataset.profilePictureEditorBound === "true") {
            return;
        }
        document.documentElement.dataset.profilePictureEditorBound = "true";

        const objectUrls = new WeakMap();
        const focusedBeforeOpen = new WeakMap();
        let activeDrag = null;

        function parts(widget) {
            const modal = widget?.querySelector("[data-profile-picture-modal]");
            const fileInput = widget?.querySelector("[data-profile-picture-file]");
            const editorPreview = widget?.querySelector("[data-profile-picture-editor-preview]");
            const editorImage = widget?.querySelector("[data-profile-picture-editor-image]");
            const xInput = widget?.querySelector("[data-profile-picture-x]");
            const yInput = widget?.querySelector("[data-profile-picture-y]");
            const scaleInput = widget?.querySelector("[data-profile-picture-scale]");
            const zoomLabel = widget?.querySelector("[data-profile-picture-zoom-label]");
            if (!widget || !modal || !fileInput || !editorPreview || !editorImage || !xInput || !yInput || !scaleInput) {
                return null;
            }
            return {
                modal,
                fileInput,
                editorPreview,
                editorImage,
                placeholder: editorPreview.querySelector("span"),
                xInput,
                yInput,
                scaleInput,
                zoomLabel,
            };
        }

        function clamp(value, min, max) {
            return Math.min(max, Math.max(min, value));
        }

        function closestElement(target, selector) {
            return target instanceof Element ? target.closest(selector) : null;
        }

        function widgetFrom(target) {
            return closestElement(target, "[data-profile-picture-widget]");
        }

        function setPosition(ui, x, y) {
            ui.xInput.value = String(Math.round(clamp(x, 0, 100)));
            ui.yInput.value = String(Math.round(clamp(y, 0, 100)));
        }

        function syncPreview(widget) {
            const ui = parts(widget);
            if (!ui) return;
            ui.editorPreview.style.setProperty("--profile-x", `${ui.xInput.value}%`);
            ui.editorPreview.style.setProperty("--profile-y", `${ui.yInput.value}%`);
            ui.editorPreview.style.setProperty("--profile-scale", ui.scaleInput.value);
            ui.editorPreview.classList.toggle("has-image", !ui.editorImage.hidden);
            if (ui.zoomLabel) {
                ui.zoomLabel.textContent = `${Math.round(Number(ui.scaleInput.value || 1) * 100)}%`;
            }
        }

        function resetEditor(widget) {
            const ui = parts(widget);
            if (!ui) return;
            const existingUrl = objectUrls.get(widget);
            if (existingUrl) {
                URL.revokeObjectURL(existingUrl);
                objectUrls.delete(widget);
            }
            ui.fileInput.value = "";
            ui.editorImage.hidden = true;
            ui.editorPreview.classList.remove("has-image", "is-dragging");
            ui.editorImage.removeAttribute("src");
            if (ui.placeholder) {
                ui.placeholder.hidden = false;
            }
            ui.xInput.value = "50";
            ui.yInput.value = "50";
            ui.scaleInput.value = "1";
            syncPreview(widget);
        }

        function openModal(widget) {
            const ui = parts(widget);
            if (!ui) return;
            focusedBeforeOpen.set(widget, document.activeElement);
            resetEditor(widget);
            ui.modal.dataset.readyToClose = "false";
            ui.modal.hidden = false;
            document.body.classList.add("modal-is-open");
            window.requestAnimationFrame(() => ui.fileInput.focus());
            window.setTimeout(() => {
                ui.modal.dataset.readyToClose = "true";
            }, 80);
        }

        document.querySelectorAll("button[data-profile-picture-open]").forEach((button) => {
            button.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                openModal(widgetFrom(button));
            });
        });

        function closeModal(widget) {
            const ui = parts(widget);
            if (!ui) return;
            ui.modal.hidden = true;
            ui.modal.dataset.readyToClose = "false";
            document.body.classList.remove("modal-is-open");
            resetEditor(widget);
            const previousFocus = focusedBeforeOpen.get(widget);
            if (previousFocus && typeof previousFocus.focus === "function") {
                previousFocus.focus();
            }
        }

        function adjustZoom(widget, amount) {
            const ui = parts(widget);
            if (!ui) return;
            const nextScale = clamp(Number(ui.scaleInput.value || 1) + Number(amount || 0), 1, 2);
            ui.scaleInput.value = nextScale.toFixed(2).replace(/\.?0+$/, "");
            syncPreview(widget);
        }

        document.querySelectorAll("[data-profile-picture-zoom]").forEach((button) => {
            button.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                adjustZoom(widgetFrom(button), button.dataset.profilePictureZoom);
            });
        });

        document.querySelectorAll("[data-profile-picture-close]:not(.profile-picture-modal-backdrop)").forEach((button) => {
            button.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                closeModal(widgetFrom(button));
            });
        });

        document.querySelectorAll(".profile-picture-modal-backdrop").forEach((backdrop) => {
            backdrop.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                const widget = widgetFrom(backdrop);
                const ui = parts(widget);
                if (ui?.modal.dataset.readyToClose === "true") {
                    closeModal(widget);
                }
            });
        });

        document.addEventListener("click", (event) => {
            const openButton = closestElement(event.target, "[data-profile-picture-open]");
            if (openButton) {
                event.preventDefault();
                openModal(widgetFrom(openButton));
                return;
            }
            const zoomButton = closestElement(event.target, "[data-profile-picture-zoom]");
            if (zoomButton) {
                event.preventDefault();
                const widget = widgetFrom(zoomButton);
                adjustZoom(widget, zoomButton.dataset.profilePictureZoom);
                return;
            }
            const closeButton = closestElement(event.target, "[data-profile-picture-close]");
            if (closeButton && !closeButton.classList.contains("profile-picture-modal-backdrop")) {
                closeModal(widgetFrom(closeButton));
            }
        });

        document.addEventListener("pointerdown", (event) => {
            const preview = closestElement(event.target, "[data-profile-picture-editor-preview]");
            if (preview) {
                const widget = widgetFrom(preview);
                const ui = parts(widget);
                if (!ui || ui.editorImage.hidden) return;
                event.preventDefault();
                const rect = ui.editorPreview.getBoundingClientRect();
                activeDrag = {
                    widget,
                    pointerId: event.pointerId,
                    startClientX: event.clientX,
                    startClientY: event.clientY,
                    startX: Number(ui.xInput.value || 50),
                    startY: Number(ui.yInput.value || 50),
                    width: Math.max(1, rect.width),
                    height: Math.max(1, rect.height),
                };
                ui.editorPreview.classList.add("is-dragging");
                ui.editorPreview.setPointerCapture?.(event.pointerId);
                return;
            }
            const backdrop = closestElement(event.target, ".profile-picture-modal-backdrop");
            if (!backdrop) return;
            const widget = widgetFrom(backdrop);
            const ui = parts(widget);
            if (ui?.modal.dataset.readyToClose === "true") {
                closeModal(widget);
            }
        });

        document.addEventListener("pointermove", (event) => {
            if (!activeDrag || event.pointerId !== activeDrag.pointerId) return;
            const ui = parts(activeDrag.widget);
            if (!ui) return;
            const scale = Math.max(1, Number(ui.scaleInput.value || 1));
            const xDelta = ((event.clientX - activeDrag.startClientX) / activeDrag.width) * (100 / scale);
            const yDelta = ((event.clientY - activeDrag.startClientY) / activeDrag.height) * (100 / scale);
            setPosition(ui, activeDrag.startX - xDelta, activeDrag.startY - yDelta);
            syncPreview(activeDrag.widget);
        });

        function endDrag(event) {
            if (!activeDrag || event.pointerId !== activeDrag.pointerId) return;
            const ui = parts(activeDrag.widget);
            ui?.editorPreview.classList.remove("is-dragging");
            ui?.editorPreview.releasePointerCapture?.(event.pointerId);
            activeDrag = null;
        }

        document.addEventListener("pointerup", endDrag);
        document.addEventListener("pointercancel", endDrag);

        document.addEventListener("keydown", (event) => {
            if (event.key !== "Escape") return;
            document.querySelectorAll("[data-profile-picture-modal]:not([hidden])").forEach((modal) => {
                closeModal(widgetFrom(modal));
            });
        });

        document.addEventListener("change", (event) => {
            const fileInput = closestElement(event.target, "[data-profile-picture-file]");
            if (!fileInput) return;
            const widget = widgetFrom(fileInput);
            const ui = parts(widget);
            if (!ui) return;
            const existingUrl = objectUrls.get(widget);
            if (existingUrl) {
                URL.revokeObjectURL(existingUrl);
                objectUrls.delete(widget);
            }
            const file = ui.fileInput.files?.[0];
            if (!file) {
                ui.editorImage.hidden = true;
                ui.editorPreview.classList.remove("has-image");
                if (ui.placeholder) {
                    ui.placeholder.hidden = false;
                }
                syncPreview(widget);
                return;
            }
            const objectUrl = URL.createObjectURL(file);
            objectUrls.set(widget, objectUrl);
            ui.editorImage.src = objectUrl;
            ui.editorImage.hidden = false;
            if (ui.placeholder) {
                ui.placeholder.hidden = true;
            }
            ui.xInput.value = "50";
            ui.yInput.value = "50";
            ui.scaleInput.value = "1";
            syncPreview(widget);
        });

        document.addEventListener("input", (event) => {
            if (!closestElement(event.target, "[data-profile-picture-x], [data-profile-picture-y], [data-profile-picture-scale]")) {
                return;
            }
            syncPreview(widgetFrom(event.target));
        });

        document.querySelectorAll("[data-profile-picture-widget]").forEach(syncPreview);
    }
    bindProfilePictureEditor();
})();
