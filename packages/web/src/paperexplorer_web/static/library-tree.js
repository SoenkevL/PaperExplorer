// The Collections pane: a WAI-ARIA tree the server renders with the selection's ancestors open.
// This script reopens the branches the reader left open (localStorage `pe.tree.open`), moves
// through the tree by keyboard with a roving tabindex (selection follows focus: moving selects,
// by clicking the item's link, which htmx swaps in), and opens the pane behind its button in a
// narrow window. A move by keyboard replaces the address rather than pushing one, so Back skips
// the Collections passed on the way. The page works without it, the tree as rendered.
(() => {
  const STORE = "pe.tree.open";
  let refocus = false; // whether the tree had focus when a selection went out, to take it back

  const stored = () => {
    try {
      const keys = JSON.parse(localStorage.getItem(STORE) || "[]");
      return new Set(Array.isArray(keys) ? keys : []);
    } catch {
      return new Set();
    }
  };
  const store = (keys) => {
    try {
      localStorage.setItem(STORE, JSON.stringify([...keys]));
    } catch {}
  };

  const tree = () => document.getElementById("library-tree");
  const items = (root) => [...root.querySelectorAll('[role="treeitem"]')];
  const visible = (root) => items(root).filter((item) => !item.parentElement.closest("[hidden]"));
  const group = (item) => item.querySelector(':scope > [role="group"]');
  const parentOf = (item) => item.parentElement.closest('[role="treeitem"]');
  const isOpen = (item) => item.getAttribute("aria-expanded") === "true";

  const setOpen = (item, open) => {
    if (!item.hasAttribute("aria-expanded")) return;
    item.setAttribute("aria-expanded", String(open));
    group(item).hidden = !open;
    const keys = stored();
    if (open) keys.add(item.dataset.key);
    else keys.delete(item.dataset.key);
    store(keys);
  };

  const focusItem = (item, select) => {
    const root = tree();
    for (const other of items(root)) other.tabIndex = other === item ? 0 : -1;
    item.focus();
    if (select && item.getAttribute("aria-selected") !== "true") {
      for (const other of items(root)) other.setAttribute("aria-selected", "false");
      item.setAttribute("aria-selected", "true");
      refocus = true;
      const link = item.querySelector(":scope > .tree-row > a");
      link.removeAttribute("hx-push-url");
      link.setAttribute("hx-replace-url", "true");
      link.click();
    }
  };

  const init = () => {
    const root = tree();
    if (!root) return;
    const open = stored();
    for (const item of items(root)) {
      if (item.hasAttribute("aria-expanded") && open.has(item.dataset.key)) {
        item.setAttribute("aria-expanded", "true");
        group(item).hidden = false;
      }
    }
    // Back to the tree only if the reader has not moved on while the selection loaded
    const here = document.activeElement;
    if (refocus && (!here || here === document.body || root.contains(here))) {
      (root.querySelector('[tabindex="0"]') || items(root)[0])?.focus();
    }
    refocus = false;
  };

  document.addEventListener("click", (event) => {
    const twisty = event.target.closest("#library-tree .twisty");
    if (twisty) {
      const item = twisty.closest('[role="treeitem"]');
      setOpen(item, !isOpen(item));
      return;
    }
    const link = event.target.closest("#library-tree a");
    if (link) {
      // The item, not its link, holds focus, so the arrows go on from it
      const item = link.closest('[role="treeitem"]');
      for (const other of items(tree())) other.tabIndex = other === item ? 0 : -1;
      item.focus();
      refocus = true;
    }
    const toggle = event.target.closest(".collections-toggle");
    if (toggle) {
      const browser = document.getElementById("library-browser");
      const opened = !browser.classList.contains("pane-open");
      browser.classList.toggle("pane-open", opened);
      toggle.setAttribute("aria-expanded", String(opened));
    }
  });

  document.addEventListener("keydown", (event) => {
    const item = event.target.closest?.('#library-tree [role="treeitem"]');
    if (!item || event.target !== item || event.altKey || event.ctrlKey || event.metaKey) return;
    const shown = visible(tree());
    const at = shown.indexOf(item);
    let next = null;
    switch (event.key) {
      case "ArrowDown":
        next = shown[at + 1];
        break;
      case "ArrowUp":
        next = shown[at - 1];
        break;
      case "ArrowRight":
        if (item.hasAttribute("aria-expanded")) {
          if (isOpen(item)) next = group(item).querySelector('[role="treeitem"]');
          else setOpen(item, true);
        }
        break;
      case "ArrowLeft":
        if (isOpen(item)) setOpen(item, false);
        else next = parentOf(item);
        break;
      case "Home":
        next = shown[0];
        break;
      case "End":
        next = shown[shown.length - 1];
        break;
      case "Enter":
      case " ":
        item.querySelector(":scope > .tree-row > a").click();
        refocus = true;
        break;
      case "*":
        for (const sibling of item.parentElement.children) setOpen(sibling, true);
        break;
      default:
        if (event.key.length === 1 && event.key.trim()) {
          const key = event.key.toLocaleLowerCase();
          const after = [...shown.slice(at + 1), ...shown.slice(0, at)];
          next =
            after.find((other) =>
              other.querySelector(":scope > .tree-row > a").textContent.trim().toLocaleLowerCase().startsWith(key),
            ) || null;
          break;
        }
        return; // Tab and the rest go on as the browser takes them
    }
    event.preventDefault();
    if (next) focusItem(next, true);
  });

  // The tree comes again with each selection (out of band) and with Back and Forward.
  document.addEventListener("htmx:load", (event) => {
    if (event.target.id === "library-tree" || event.target.querySelector?.("#library-tree")) init();
    // A selection closes the pane a narrow window opened, as its new button says it is closed
    if (event.target.id === "library-works") document.getElementById("library-browser")?.classList.remove("pane-open");
  });
  // History keeps the page's markup: the search box keeps what was typed, not what it rendered with
  document.addEventListener("htmx:beforeHistorySave", () => {
    const search = document.getElementById("library-search");
    if (search) search.setAttribute("value", search.value);
  });
  document.addEventListener("htmx:historyRestore", init);
  document.addEventListener("DOMContentLoaded", init);
})();
