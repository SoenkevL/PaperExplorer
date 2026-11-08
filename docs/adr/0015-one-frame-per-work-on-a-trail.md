# A Trail keeps each Work alive in a frame of its own

A Jump must leave the citing Work exactly as it was: the sheet's scroll, the open Citation, the Conversation's turns, and an Answer still streaming. None of that can be read back from the server: a Conversation's turns exist only in the page's DOM, as the API has no route for them, and the sheet scrolls on its own, so the browser does not restore it. So the Work's page stays alive while another is read.

`/works/{id}` is therefore a small shell holding one same-origin iframe per Work on the Trail, and the frame is today's page at `/works/{id}/reader`. The shell pushes the address, keeps the Trail, and shows one frame. Hidden frames stay laid out (`visibility: hidden`, `inert`), so they keep their scroll and streams. At most eight are kept, the least recently shown going first, never one still answering; a Work whose frame went loads fresh, as a reload does.

## Considered Options

- **Every Work's panes in one document:** no frames, but every id (`#panel`, `#turns`, `#conversation`) and `hx-target` would need namespacing per Work, across every template and script.
- **Detaching a Work's nodes while away:** one document with unique ids at any moment, but the SSE extension closes a stream once its element leaves the body, which would cut off an Answer.
- **Plain links with state saved to the address:** scroll and Citation could return, but the Conversation's turns could not without storing them first, and an Answer would stop.

## Consequences

- The Work page's templates and scripts stay nearly as they are, each frame being a whole page with its own htmx.
- Links in a frame open at the top (`<base target="_top">`), and a Jump is a message to the shell.
- The frames share `localStorage`, so the theme follows through a `storage` listener.
- Each frame holds a whole Work's DOM, which is why the shell keeps only eight.
- A reload starts the current Work fresh; if Conversations move to Postgres, a frame could be rebuilt from them and this limit eased.
