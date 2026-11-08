// The shell of a Work's page (ADR 0015): one frame per Work on the Trail, one shown. A Jump shows
// the Source's frame and pushes the address; going back shows the frame as the reader left it.
// Each history entry holds the Trail up to it, so Back, Forward and a reload all agree on it.
(() => {
  "use strict";

  const MAX_FRAMES = 8; // each holds a whole Work's page
  const LONGEST_TRAIL = 4; // beyond this, the middle collapses

  const host = document.getElementById("frames");
  const frames = new Map();
  const wired = new WeakSet();
  let shownAt = 0;
  let current = null;

  const address = ({ work, citation }) => `/works/${work}${citation ? `?citation=${citation}` : ""}`;
  const reader = ({ work, citation }) => `/works/${work}/reader${citation ? `?citation=${citation}` : ""}`;

  const first = host.querySelector("iframe");
  const opened = {
    work: Number(first.dataset.work),
    citation: first.dataset.citation ? Number(first.dataset.citation) : null,
    label: first.dataset.label,
  };
  const kept = history.state?.trail;
  let trail = kept?.at(-1)?.work === opened.work ? kept : [opened];
  history.replaceState({ trail }, "");

  function create(entry) {
    const frame = document.createElement("iframe");
    frame.title = entry.label;
    frame.src = reader(entry);
    frame.addEventListener("load", () => wire(frame));
    host.append(frame);
    frames.set(entry.work, { frame, shown: 0 });
    return frames.get(entry.work);
  }

  // Each page the frame loads gets the shell's clicks, and the Trail if it is the one shown.
  function wire(frame) {
    const page = frame.contentDocument;
    if (!page || wired.has(page)) return;
    wired.add(page);
    page.addEventListener("click", onClick);
    if (frame === current) render();
  }

  function onClick(event) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    const link = event.target.closest?.("a.jump, a[data-trail-at]");
    if (!link) return;
    event.preventDefault();
    if (link.dataset.trailAt !== undefined) {
      history.go(Number(link.dataset.trailAt) - (trail.length - 1));
      return;
    }
    jump({
      work: Number(link.dataset.work),
      citation: Number(link.dataset.citation),
      label: link.dataset.label,
    });
  }

  function jump(entry) {
    const open = frames.get(entry.work);
    trail = [...trail, entry];
    history.pushState({ trail }, "", address(entry));
    show(entry);
    // A frame already open has its own arrival behind it: ask again for this Citation.
    open?.frame.contentWindow?.htmx?.ajax("POST", `/works/${entry.work}/arrival`, {
      target: "#arrival",
      values: { citation: entry.citation },
    });
  }

  function show(entry) {
    const held = frames.get(entry.work) ?? create(entry);
    held.shown = ++shownAt;
    current = held.frame;
    for (const { frame } of frames.values()) {
      frame.classList.toggle("on", frame === current);
      frame.inert = frame !== current;
    }
    render();
    current.contentWindow?.focus();
    evict();
  }

  // The longest unseen frame goes first, never one still answering or running a Check.
  function evict() {
    while (frames.size > MAX_FRAMES) {
      const candidates = [...frames.entries()]
        .filter(([, { frame }]) => frame !== current && !frame.contentDocument?.querySelector("[sse-connect]"))
        .sort(([, a], [, b]) => a.shown - b.shown);
      if (!candidates.length) return;
      const [work, { frame }] = candidates[0];
      frame.remove();
      frames.delete(work);
    }
  }

  function render() {
    const page = current?.contentDocument;
    if (!page) return;
    if (page.title) document.title = page.title;
    const path = page.getElementById("trail-path");
    if (!path) return;
    // Alone on the Trail, a Work shows its title as the server drew it.
    path.dataset.alone ??= path.innerHTML;
    if (trail.length < 2) {
      path.innerHTML = path.dataset.alone;
      return;
    }
    const nodes = [];
    const add = (node) => {
      if (nodes.length) nodes.push(separator(page, "›"));
      else nodes.push(separator(page, "/"));
      nodes.push(node);
    };
    const hidden = trail.length > LONGEST_TRAIL ? trail.slice(1, -2) : [];
    trail.forEach((entry, at) => {
      if (hidden.includes(entry)) {
        if (entry === hidden[0]) {
          const more = page.createElement("span");
          more.className = "sep";
          more.textContent = "…";
          more.title = hidden.map((h) => h.label).join(" › ");
          add(more);
        }
        return;
      }
      if (at === trail.length - 1) {
        const here = page.createElement("span");
        here.className = "here";
        here.setAttribute("aria-current", "page");
        here.textContent = entry.label;
        add(here);
        return;
      }
      const step = page.createElement("a");
      step.className = "step";
      step.href = address(entry);
      step.dataset.trailAt = String(at);
      step.textContent = entry.label;
      add(step);
    });
    path.replaceChildren(...nodes);
  }

  function separator(page, text) {
    const sep = page.createElement("span");
    sep.className = "sep";
    sep.textContent = text;
    return sep;
  }

  window.addEventListener("popstate", (event) => {
    if (!event.state?.trail) return;
    trail = event.state.trail;
    show(trail.at(-1));
  });

  frames.set(opened.work, { frame: first, shown: ++shownAt });
  current = first;
  first.addEventListener("load", () => wire(first));
  // The frame may have loaded before this script ran.
  if (first.contentDocument?.readyState === "complete") wire(first);
})();
