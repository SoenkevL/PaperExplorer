# Checking that a DOI exists

Question: is there a free, fast and certain way to tell whether a DOI exists, that is, whether it is registered and resolves to a website? Measured 2026-11-01 from the Mac, one request at a time, no key. The latencies for the same endpoint over more DOIs are in [2026-10-doi-resolution.md](2026-10-doi-resolution.md) (p50 63 ms, p90 125 ms).

## Answer

Yes, for *registered*: the doi.org Proxy Server REST API, `GET https://doi.org/api/handles/{doi}?type=URL`. It is free, needs no key, answers in about 80–170 ms, and it is the authoritative record: every DOI from every Registration Agency (Crossref, DataCite, mEDRA, …) is a Handle that this system resolves, so no registry can know a DOI that it does not.

- `responseCode: 1` with HTTP 200: the DOI exists; `values[].data.value` of type `URL` is the landing page the registrant set.
- `responseCode: 100` with HTTP 404: the Handle is not found, so the DOI does not exist (or is malformed).
- `responseCode: 200` with HTTP 200: the Handle exists but has no values of the requested type (no URL).
- `responseCode: 2` with HTTP 500: an error; it says nothing about the DOI, so retry.

([DOI resolution documentation, section 5](https://www.doi.org/the-identifier/resources/factsheets/doi-resolution-documentation))

No, for *resolves to a working website*: nothing free makes that certain. The landing URL is what the publisher registered, not proof that the page answers. Following it is slow (0.2–1 s) and the answers mislead: OUP returns 403 to a scripted client, Nature bounces through four redirects to a cookie error page and returns 200 anyway.

## Measurements

| DOI | HTTP | responseCode | Landing URL | Time |
|---|---|---|---|---|
| `10.1038/s41593-025-02069-z` (Crossref) | 200 | 1 | nature.com/articles/… | 91 ms |
| `10.1016/j.clinph.2023.01.017` (Crossref) | 200 | 1 | linkinghub.elsevier.com/… | 140 ms |
| `10.48550/arXiv.1706.03762` (DataCite) | 200 | 1 | arxiv.org/abs/1706.03762 | 82 ms |
| `10.21203/rs.3.rs-1763486/v1` (preprint) | 200 | 1 | researchsquare.com/… | 82 ms |
| `10.1037//0022-006x.69.4.597` (APA `//` alias) | 200 | 1 | doi.apa.org/getdoi.cfm?… | 77 ms |
| `10.1038/thisdoesnotexist123` | 404 | 100 | — | 139 ms |
| `10.9999/fake.1` | 404 | 100 | — | 103 ms |
| `notadoi` | 404 | 100 | — | 169 ms |

Following the landing page (`curl -L`): Elsevier 200 in 0.5 s, arXiv 200 in 0.16 s, Nature 200 after 4 redirects to `?error=cookies_not_supported` in 1.0 s, OUP 403 in 0.23 s.

## Alternatives

- **`HEAD https://doi.org/{doi}`** (no redirect follow): 302 with `Location` for a registered DOI, 404 for an unknown one. Same certainty as the Handle API; the API is the documented, JSON form.
- **Which RA?**, `GET https://doi.org/ra/{doi},{doi},…`: several DOIs per request; each comes back with its RA (`Crossref`, `DataCite`, …) or `"status": "DOI does not exist"`. The documentation adds `Invalid DOI` and `Unknown` as error states ([section 4](https://www.doi.org/the-identifier/resources/factsheets/doi-resolution-documentation)). Useful to check a batch, and to know which registry to ask for metadata.
- **Crossref or OpenAlex**: a hit proves existence, but a miss does not prove absence: Crossref answers 404 for DataCite DOIs, and neither has every Zenodo DOI ([2026-10-doi-resolution.md](2026-10-doi-resolution.md#coverage)).

## Caveats

- Existence is not correctness: a real DOI can belong to another Work (a DOI Conflict). Only metadata (Crossref, OpenAlex) can say whether it agrees with the Reference.
- doi.org publishes no rate limit for the Handle API or Which RA?.
- All 210 fixture DOIs are registered, so on the fixtures the check marks none Unregistered. It also costs about as long as one Crossref batch, so it saves little time. What it adds is a certain verdict, free and before any Registry Check: the `verify_dois` Stage ([ADR 0012](../adr/0012-verify-dois-stage.md)).
