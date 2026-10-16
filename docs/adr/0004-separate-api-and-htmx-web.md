# Separate JSON API and server-rendered HTMX web app

The backend is a FastAPI JSON API; the frontend is a separate FastAPI app rendering Jinja templates with HTMX, each in its own container. This gives a real client/server split over HTTP while keeping the whole stack in typed Python; an SPA framework was rejected for v1 because learning JavaScript tooling is not a goal. The web app talks to the database only through the API. The API returns structured JSON only (blocks plus Citation offsets), never HTML, so any frontend can be built on it.
