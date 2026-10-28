/* Native dialogs retain the normal server-rendered request and case POST flows. */
(() => {
  const workspace = document.querySelector(".lifecycle-returns-page");
  if (!workspace) return;
  const tabs = document.querySelector(".order-history-tabs");
  const activeTab = tabs?.querySelector(".active");
  if (activeTab) tabs.scrollLeft = activeTab.offsetLeft - tabs.offsetLeft - (tabs.clientWidth - activeTab.offsetWidth) / 2;
  const configurations = [
    { dialog: workspace.querySelector("[data-return-request-dialog]"), opener: workspace.querySelector("[data-return-request-open]"), request: true },
    { dialog: workspace.querySelector("[data-return-dialog]"), request: false },
  ].filter(config => config.dialog instanceof HTMLDialogElement);
  const syncScrollLock = () => {
    const open = configurations.some(config => config.dialog.open);
    document.documentElement.classList.toggle("return-dialog-is-open", open);
    document.body.classList.toggle("return-dialog-is-open", open);
  };
  configurations.forEach(config => {
    const { dialog } = config;
    const opener = config.opener || [...workspace.querySelectorAll("[data-return-open]")]
      .find(link => link.dataset.returnOpen === dialog.dataset.returnDialog);
    const open = () => {
      if (dialog.open) return;
      configurations.forEach(other => {
        if (other !== config && other.dialog.open) other.dialog.close();
      });
      const url = new URL(location.href);
      if (config.request) {
        url.searchParams.delete("case");
        if (dialog.dataset.returnOrder) {
          url.searchParams.set("order", dialog.dataset.returnOrder);
          url.searchParams.delete("request");
        } else {
          url.searchParams.delete("order");
          url.searchParams.set("request", "1");
        }
      } else {
        url.searchParams.delete("order");
        url.searchParams.delete("request");
        url.searchParams.set("case", dialog.dataset.returnDialog);
      }
      history.replaceState(null, "", url);
      dialog.showModal();
      syncScrollLock();
    };
    const close = () => dialog.close();
    dialog.querySelectorAll("[data-return-close]").forEach(button => button.addEventListener("click", close));
    let backdropPress = false;
    const outside = event => {
      const rect = dialog.getBoundingClientRect();
      return event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom;
    };
    dialog.addEventListener("pointerdown", event => {
      backdropPress = event.target === dialog && outside(event);
    });
    dialog.addEventListener("click", event => {
      if (backdropPress && event.target === dialog && outside(event)) close();
      backdropPress = false;
    });
    dialog.addEventListener("close", () => {
      syncScrollLock();
      // Another dialog may already have taken over its own URL and focus.
      if (configurations.some(other => other.dialog.open)) return;
      const url = new URL(location.href);
      (config.request ? ["order", "request"] : ["case"]).forEach(key => url.searchParams.delete(key));
      ["message", "error"].forEach(key => url.searchParams.delete(key));
      history.replaceState(null, "", url);
      (opener || workspace.querySelector("[data-return-list-heading]"))?.focus({ preventScroll: true });
    });
    opener?.addEventListener("click", event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      open();
    });
    config.open = open;
  });
  const requested = configurations.find(config => config.request && config.dialog.dataset.autoOpen === "true");
  (requested || configurations.find(config => !config.request))?.open();
})();
