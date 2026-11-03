# Resolving References: registries, latencies and limits

Measured 2026-10-28 to 2026-11-01 from the Mac on a home connection, with no OpenAlex key and no Crossref `mailto`, mostly one request at a time. Inputs: the 279 References of the four fixture papers (`tests/fixtures/grobid/`) and the Zotero library (read only). The decisions built on these numbers are in [design-resolution.md](../design-resolution.md).

## Endpoints

| Call | Returns | p50 / p90 | Limit |
|---|---|---|---|
| doi.org handle API `/api/handles/{doi}` | whether the DOI exists, its landing URL; no metadata | 63 / 125 ms | none published |
| doi.org content negotiation (CSL-JSON) | metadata; a Crossref DOI redirects to `api.crossref.org/v1/works/{doi}/transform` | 257 / 381 ms | Crossref's, since it is Crossref |
| Crossref `/works/{doi}` | metadata of exactly that DOI, with `type` and `relation` | 137 / 384 ms | 5/s, 1 at a time (public); 10/s, 3 (polite, `mailto`) |
| Crossref `/works?filter=doi:a,doi:b,…` | up to 100 DOIs per call | 1.1–1.5 s per call | a list query: 1/s, 1 at a time (public); 3/s, 3 (polite) |
| Crossref `query.bibliographic` | candidates from a raw Reference string | 330 / 788 ms | a list query; 4 of 8 calls at ~3/s got 429 |
| OpenAlex `/works/doi:{doi}` | metadata, open-access links, abstract, OpenAlex ID | 276 / 791 ms | free; 10/s keyless, 30/s with a free key; 8 at once drew 25 429s in 40 |
| OpenAlex `/works?filter=doi:a\|b\|…` | up to 100 DOIs per call | ~0.5 s per call | $0.0001 per call |
| OpenAlex `filter=title.search:` | candidates by title | 174 / 417 ms | $0.001 per call |
| OpenAlex `search=` | candidates by relevance | 478 / 939 ms | $0.001 per call |
| Semantic Scholar by DOI, `search/match` | metadata | 470 / 810 ms | keyless shared pool (2 of 4 got 429); 1/s with a key |

Crossref's response headers carry the limits that apply (`x-rate-limit-limit`, `x-rate-limit-interval`, `x-concurrency-limit`); list queries showed `1`, `1s`, `1` on the public pool. OpenAlex's carry the cost and the day's budget (`x-ratelimit-cost-usd`, `x-ratelimit-remaining-usd`); a singleton get costs nothing and still reports the budget. OpenAlex's daily budget is $0.10 without a key and $1 with a free one, reset at 00:00 UTC: 100 or 1,000 title searches a day.

Measured 2026-11-03, keyless:

- Crossref's 429s carry no `Retry-After` and none of its rate headers; retries a second apart drew more of them. Even one call a second, one at a time, draws an occasional 429.
- OpenAlex's 429 on a burst (30 at once: 19 refused) has `retry-after: 1`, `x-ratelimit-remaining-usd: 0` and `x-ratelimit-remaining: 0`, with the message "10 requests per second" and $0.0686 of the day in fact left. A singleton right after reported the true budget.
- A comma in a filter value gets 400 from both. OpenAlex accepts `doi:"10.1000/a,b"|10.1/x`; Crossref has no quoting. Crossref's `/works/{doi}` answers with the DOI percent-encoded (`10.1002%2Fana.24779`, also APA's `//` form), 404 for an unknown DOI, with limits of its own (`5`, `1s`, `1`). No real DOI with a comma turned up to test a hit.
- `query.bibliographic` is fine at 6,000 characters, 431 at 8,000, 414 at 12,000.

## Coverage

- **Fixture References:** 207 of 280 (74%) carry a DOI from GROBID, 68 a title only, 4 nothing usable. GROBID missed one printed DOI. A regex over the printed text does worse: the text breaks DOIs at line ends (`10.1016/j.` …) and GROBID rejoins them. No fixture Reference prints an arXiv ID.
- **OpenAlex** found all 206 distinct fixture DOIs in 3 batch calls (1.55 s). Three came back under another DOI: APA's `10.1037//0022-006x.69.4.597` form; a Research Square preprint (`10.21203/rs.3.rs-1763486/v1`) merged into its *Brain Topography* article; a JSTOR DOI whose OpenAlex record carries the Taylor & Francis DOI.
- **Crossref** returns the printed preprint DOI as `posted-content` / `preprint` with `relation.is-preprint-of` naming the journal DOI. It answers 404 for DataCite DOIs (arXiv `10.48550/arXiv.1706.03762`, which OpenAlex has; a Zenodo DOI, which neither has).

## The Zotero library

- 579 top-level items: 530 journal articles, 16 preprints, 16 web pages, 14 books and chapters, 2 conference papers, 1 dataset. 526 (91%) have a DOI, every one a title, 15 mention arXiv.
- Three DOIs sit on two items each, every pair with the same title and journal: `10.1038/s41593-025-02069-z` (`U34JYI4S` with PDF, `TWYMFEDH` without), `10.1016/j.clinph.2023.01.017` (`MQDRIH39`, `GSTKEVN8`, both with PDF), `10.1093/nc/niac008` (`VABG3PLX` in 3 collections, `9B8RGTKE` in 1, both with PDF).
- A full read takes 16–32 s (6 pages of 100). An incremental read (`since=<version>`) takes 160 ms when nothing changed.
- The fixture papers' References: 10 of 279 are in the library by DOI; 3 more that print no DOI match a library title at ≥ 0.9 (e.g. "On the complexity of an finite sequence" → Lempel & Ziv 1976, 0.95). The Seed's overlap is not yet measured.
- The configured Zotero key can write to the library.

## Estimates for one Seed

About 182 References: ~135 with a DOI, ~45 with a title only.

- doi.org per DOI, then OpenAlex by title: ~35 s for the DOIs on Crossref's public pool (one at a time), ~14 s polite, an OpenAlex pass for links and abstracts on top, ~5 s of title searches.
- Crossref and OpenAlex DOI batches at once, then title searches: ~2.5 s (Crossref-bound) plus 2–5 s; ~5–10 s with the Crossref fallback. $0.045, half a keyless day.
- One level deeper (~180 Sources × ~70 References ≈ 12,600): ~95 batch calls per registry, about a minute; ~3,200 title searches, $3.20, more than three days of a free key. The budget binds, not latency.

## Set aside

- GROBID's consolidation: one Crossref call per entry under the same limits, and parsing would depend on the network.
- biblio-glutton, or the OpenAlex or Crossref dumps: lookups in milliseconds, but hundreds of GB and hours of loading.
- Semantic Scholar: slower, throttled without a key.

## Sources

- [Crossref: access and authentication](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)
- [Crossref: announcing changes to REST API rate limits](https://crossref.org/blog/announcing-changes-to-rest-api-rate-limits/)
- [OpenAlex: authentication and rate limits](https://help.openalex.org/api-reference/authentication)
- [OpenAlex: example costs](https://help.openalex.org/access/example-costs/)
- [DOI content negotiation](https://citation.doi.org/docs.html)
- [Semantic Scholar API tutorial](https://webflow.semanticscholar.org/product/api/tutorial)
