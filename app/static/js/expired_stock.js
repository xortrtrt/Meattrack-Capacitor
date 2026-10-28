/* Keep custody stages separate while preserving links from return receipts. */
(() => {
  const workspace = document.querySelector(".lifecycle-expired-page");
  const tablist = workspace?.querySelector("[data-custody-tabs]");
  if (!tablist) return;

  const tabs = [...tablist.querySelectorAll("[data-custody-tab]")];
  const panels = [...workspace.querySelectorAll("[data-custody-panel]")];
  const storageKey = `expired-stock-tab:${location.pathname}`;
  const panelForHash = () => {
    const target = document.getElementById(location.hash.slice(1));
    return target?.closest("[data-custody-panel]");
  };
  const savedPanel = () => {
    try {
      return panels.find(panel => panel.id === sessionStorage.getItem(storageKey));
    } catch {
      return null;
    }
  };

  tablist.setAttribute("role", "tablist");
  panels.forEach(panel => {
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", `tab-${panel.id}`);
    panel.setAttribute("tabindex", "0");
  });
  tabs.forEach(tab => {
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", tab.hash.slice(1));
  });

  const select = (panel, updateUrl = false) => {
    panels.forEach(item => { item.hidden = item !== panel; });
    tabs.forEach(tab => {
      const active = tab.hash === `#${panel.id}`;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    try { sessionStorage.setItem(storageKey, panel.id); } catch { /* Storage may be disabled. */ }
    if (updateUrl && location.hash !== `#${panel.id}`) {
      history.pushState(null, "", `#${panel.id}`);
    }
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      select(panels[index], true);
    });
    tab.addEventListener("keydown", event => {
      if (event.key === " " || event.key === "Enter") {
        event.preventDefault();
        select(panels[index], true);
        return;
      }
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next === undefined) return;
      event.preventDefault();
      tabs[next].focus();
      select(panels[next], true);
    });
  });

  select(panelForHash() || savedPanel() || panels[0]);
  const followLocation = () => select(panelForHash() || panels[0]);
  window.addEventListener("hashchange", followLocation);
  window.addEventListener("popstate", followLocation);
})();
