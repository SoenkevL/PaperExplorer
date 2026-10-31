// The page's chrome: the theme, the reader's side panes and the Citation sheet's height, each
// remembered in this browser. A side pane is a column while the window is wide enough for it,
// and an overlay below that: the Conversation a drawer below 64rem, the Citation a bottom sheet
// below 80rem (style.css has the same widths). Columns are turned on and off; overlays opened
// and closed.
(() => {
  "use strict";

  const OVERLAY = {
    conversation: window.matchMedia("(max-width: 64rem)"),
    citation: window.matchMedia("(max-width: 80rem)"),
  };
  const DARK = window.matchMedia("(prefers-color-scheme: dark)");
  const SHEET_MIN = 0.25;
  const SHEET_MAX = 0.85;

  // Storage can be missing or refuse (a private window, blocked site data): the page works
  // without it and simply forgets.
  function load(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }
  function save(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {}
  }

  function toggleTheme() {
    const root = document.documentElement;
    const now = root.dataset.theme || (DARK.matches ? "night" : "paper");
    root.dataset.theme = now === "night" ? "paper" : "night";
    save("pe.theme", root.dataset.theme);
    render();
  }

  // Which side panes are on as columns.
  const columns = { conversation: true, citation: true };
  try {
    const stored = JSON.parse(load("pe.panes") || "{}");
    for (const name of Object.keys(columns)) {
      if (typeof stored[name] === "boolean") columns[name] = stored[name];
    }
  } catch {}

  const pane = (name) => document.getElementById(name);
  const reader = () => document.querySelector(".reader");

  function render() {
    const theme = document.documentElement.dataset.theme || (DARK.matches ? "night" : "paper");
    for (const button of document.querySelectorAll(".theme-toggle")) {
      button.setAttribute("aria-pressed", String(theme === "night"));
    }
    for (const name of Object.keys(columns)) {
      reader()?.classList.toggle(`no-${name}`, !columns[name]);
      for (const button of document.querySelectorAll(`.pane-toggle[data-pane="${name}"]`)) {
        button.setAttribute("aria-pressed", String(columns[name]));
      }
    }
    // A pane's × turns a column off, or closes a drawer or sheet.
    for (const button of document.querySelectorAll(".pane-close")) {
      const name = button.dataset.pane;
      const what = name === "citation" ? "Citation" : "Conversation";
      const overlay = OVERLAY[name]?.matches;
      const form = name === "citation" ? "sheet" : "drawer";
      button.setAttribute("aria-label", overlay ? `Close the ${what} ${form}` : `Hide the ${what} pane`);
    }
    const drawer = pane("conversation");
    for (const button of document.querySelectorAll(".drawer-toggle")) {
      button.setAttribute("aria-expanded", String(Boolean(drawer?.classList.contains("open"))));
    }
    // The floating Citation button brings back a closed sheet, once there is a Citation to show.
    const sheet = pane("citation");
    const shown = Boolean(sheet?.classList.contains("open"));
    const filled = Boolean(document.getElementById("panel")?.childElementCount);
    for (const button of document.querySelectorAll(".sheet-toggle")) {
      button.hidden = !OVERLAY.citation.matches || shown || !filled;
      button.setAttribute("aria-expanded", String(shown));
    }
  }

  function setPane(name, on) {
    const element = pane(name);
    if (!element) return;
    if (OVERLAY[name].matches) {
      const was = element.classList.contains("open");
      element.classList.toggle("open", on);
      if (on && name === "conversation") element.querySelector("textarea")?.focus();
      // Closing an overlay that held the focus hands it back to the button that opens it.
      if (was && !on && element.contains(document.activeElement)) {
        document.querySelector(`.floating [aria-controls="${name}"]`)?.focus();
      }
    } else {
      columns[name] = on;
      save("pe.panes", JSON.stringify(columns));
    }
    render();
  }

  function isShown(name) {
    return OVERLAY[name].matches ? Boolean(pane(name)?.classList.contains("open")) : columns[name];
  }

  // The sheet's height, as a share of the window between SHEET_MIN and SHEET_MAX.
  function setSheetHeight(share, remember) {
    const clamped = Math.min(SHEET_MAX, Math.max(SHEET_MIN, share));
    pane("citation")?.style.setProperty("--sheet-height", `${(clamped * 100).toFixed(1)}dvh`);
    if (remember) save("pe.sheet", String(clamped));
    return clamped;
  }
  function sheetHeight() {
    const sheet = pane("citation");
    return sheet ? sheet.getBoundingClientRect().height / window.innerHeight : 0.5;
  }

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const target = event.target;
    if (target.closest(".theme-toggle")) toggleTheme();
    const toggle = target.closest(".pane-toggle");
    if (toggle) setPane(toggle.dataset.pane, !isShown(toggle.dataset.pane));
    const close = target.closest(".pane-close");
    if (close) setPane(close.dataset.pane, false);
    if (target.closest(".drawer-toggle")) setPane("conversation", !isShown("conversation"));
    if (target.closest(".sheet-toggle")) setPane("citation", true);
    // The Citation being opened is marked in the Work until another takes its place.
    const citation = target.closest("a.citation");
    if (citation) {
      for (const old of document.querySelectorAll("a.citation.on")) old.classList.remove("on");
      citation.classList.add("on");
    }
  });

  // A Citation opened always shows, even in a pane that was turned off.
  document.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target?.id === "panel") setPane("citation", true);
  });

  // Another script (conversation.js) closes an overlay this way, e.g. to show a place in the Work.
  document.addEventListener("pane:close", (event) => {
    const name = event.detail;
    if (OVERLAY[name]?.matches) setPane(name, false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      for (const name of Object.keys(OVERLAY)) {
        if (OVERLAY[name].matches && pane(name)?.classList.contains("open")) setPane(name, false);
      }
    }
    if (event.target instanceof Element && event.target.closest(".sheet-handle")) {
      const step = { ArrowUp: 0.05, ArrowDown: -0.05 }[event.key];
      if (step) {
        event.preventDefault();
        setSheetHeight(sheetHeight() + step, true);
      }
    }
  });

  // Dragging the handle resizes the sheet; the last height is remembered.
  document.addEventListener("pointerdown", (event) => {
    const handle = event.target instanceof Element && event.target.closest(".sheet-handle");
    const sheet = pane("citation");
    if (!handle || !sheet) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    sheet.classList.add("sheet-dragging");
    const move = (e) => setSheetHeight((window.innerHeight - e.clientY) / window.innerHeight, false);
    const end = (e) => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
      sheet.classList.remove("sheet-dragging");
      // A cancelled drag reports no usable position: keep the height it reached.
      const up = e.type === "pointerup";
      setSheetHeight(up ? (window.innerHeight - e.clientY) / window.innerHeight : sheetHeight(), true);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  });

  // Crossing a width turns an overlay back into a column (or the other way): start it closed.
  DARK.addEventListener("change", render);
  for (const [name, query] of Object.entries(OVERLAY)) {
    query.addEventListener("change", () => {
      pane(name)?.classList.remove("open");
      render();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const stored = Number(load("pe.sheet"));
    if (stored) setSheetHeight(stored, false);
    render();
  });
})();
