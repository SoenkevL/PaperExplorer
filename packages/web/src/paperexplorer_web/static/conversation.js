// The Conversation pane: its Question box, and the Pointers and exact Quotes of its Answers,
// which lead to their place in the Work. Opening and closing the pane is panes.js's.
//
// Every offset counts code points of the block's text, as the API's do. JavaScript strings count
// UTF-16 units, so a block's text nodes are walked code point by code point.
(() => {
  "use strict";

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
    // A drawer over the Work would hide the place: close it (panes.js keeps columns open).
    document.dispatchEvent(new CustomEvent("pane:close", { detail: "conversation" }));
    // The Work scrolls on its own sheet, not the window.
    const sheet = block.closest(".sheet");
    if (sheet) {
      const top = place.getBoundingClientRect().top - sheet.getBoundingClientRect().top;
      const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      sheet.scrollBy({ top: top - sheet.clientHeight / 3, behavior: still ? "auto" : "smooth" });
    }
    return true;
  }

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
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
