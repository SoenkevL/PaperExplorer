// The Conversation sidebar: its drawer, its Question box, and the Pointers and exact Quotes of
// its Answers, which lead to their place in the reader.
//
// Every offset counts code points of the block's text, as the API's do. JavaScript strings count
// UTF-16 units, so a block's text nodes are walked code point by code point.
(() => {
  "use strict";

  const WIDE = window.matchMedia("(min-width: 92rem)");

  function setDrawer(open) {
    const sidebar = document.getElementById("conversation");
    if (!sidebar) return;
    sidebar.classList.toggle("open", open);
    for (const button of document.querySelectorAll(".drawer-toggle")) {
      button.setAttribute("aria-expanded", String(open));
    }
    if (open) sidebar.querySelector("textarea")?.focus();
  }

  // The text node and the UTF-16 offset in it of code point `offset` of `block`'s text.
  function locate(block, offset) {
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
    let seen = 0;
    let last = null;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      let units = 0;
      for (const ch of node.data) {
        if (seen === offset) return [node, units];
        seen += 1;
        units += ch.length;
      }
      last = node;
    }
    return last ? [last, last.data.length] : [block, 0];
  }

  function span(block, start, end) {
    const range = document.createRange();
    range.setStart(...locate(block, start));
    range.setEnd(...locate(block, end));
    return range;
  }

  // Scroll the reader to a Pointer's or an exact Quote's place and highlight it: the place
  // strongly, the rest of a Pointer's Chunk faintly. The block's DOM is left as it is (the CSS
  // Custom Highlight API), so its Citations stay links.
  function show(link) {
    const at = link.dataset;
    const block = document.getElementById(`b${at.block}`);
    if (!block) return false;
    const place = span(block, Number(at.start), Number(at.end));
    for (const old of document.querySelectorAll(".pointed")) old.classList.remove("pointed");
    if (window.CSS?.highlights && window.Highlight) {
      CSS.highlights.set("pointer-place", new Highlight(place));
      if (at.chunkStart !== undefined) {
        const chunk = span(block, Number(at.chunkStart), Number(at.chunkEnd));
        CSS.highlights.set("pointer-chunk", new Highlight(chunk));
      } else {
        CSS.highlights.delete("pointer-chunk");
      }
    } else {
      block.classList.add("pointed");
    }
    if (!WIDE.matches) setDrawer(false);
    const top = place.getBoundingClientRect().top;
    window.scrollBy({ top: top - window.innerHeight / 3, behavior: "smooth" });
    return true;
  }

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const sidebar = document.getElementById("conversation");
    if (event.target.closest(".drawer-toggle")) setDrawer(!sidebar?.classList.contains("open"));
    if (event.target.closest(".drawer-close")) setDrawer(false);
    const link = event.target.closest("a[data-block]");
    if (link && show(link)) event.preventDefault();
  });

  // Enter asks; Shift+Enter starts a new line.
  document.addEventListener("keydown", (event) => {
    const box = event.target;
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    if (!(box instanceof HTMLTextAreaElement) || !box.form?.classList.contains("ask")) return;
    event.preventDefault();
    box.form.requestSubmit();
  });

  // Keep the newest Answer in view as it streams, unless the reader has scrolled up.
  let follow = true;
  document.addEventListener("htmx:afterRequest", (event) => {
    const form = event.detail.elt;
    if (!(form instanceof HTMLFormElement) || !form.classList.contains("ask")) return;
    if (event.detail.successful) form.querySelector("textarea").value = "";
    follow = true;
  });
  document.addEventListener("DOMContentLoaded", () => {
    const turns = document.getElementById("turns");
    if (!turns) return;
    turns.addEventListener("scroll", () => {
      follow = turns.scrollHeight - turns.scrollTop - turns.clientHeight < 48;
    });
    new MutationObserver(() => {
      if (follow) turns.scrollTop = turns.scrollHeight;
    }).observe(turns, { childList: true, subtree: true, characterData: true });
  });
})();
