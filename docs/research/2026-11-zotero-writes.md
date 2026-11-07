# Zotero Web API v3: write requests

Question: what does PaperExplorer need to know to write to a Zotero user library through the Web API v3 (create a top-level Collection and subcollections, create regular items from Crossref/OpenAlex metadata, add or remove one collection key and one tag on an existing item, and find an item by DOI before creating one) without disturbing anything else in the library? Read on 2026-11-07 from the v3 docs ([write requests](https://www.zotero.org/support/dev/web_api/v3/write_requests), [basics](https://www.zotero.org/support/dev/web_api/v3/basics), [syncing](https://www.zotero.org/support/dev/web_api/v3/syncing), [types and fields](https://www.zotero.org/support/dev/web_api/v3/types_and_fields); the write page says "Last updated 2026-07-29"), the server source `zotero/dataserver` at commit `caa95bef` (with its API test suite), the client source `zotero/zotero` at commit `228f132e`, and the item schema `zotero/zotero-schema` at `62e983a2`, the commit the dataserver pins as a submodule.

No request was sent to api.zotero.org. Every claim is documented, read from source, or inferred, and says which. Source links are permalinks to those commits; `DS` below is `https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7`, `ZC` is `https://github.com/zotero/zotero/blob/228f132e1a2c38745ae07bea6c1600b8bddc3b73`, and `ZS` is `https://github.com/zotero/zotero-schema/blob/62e983a2e575fe9b9a3677ad7c9772080b67a1e4/schema.json`.

## Answer

| Question | Answer | Basis |
|---|---|---|
| Create collections | `POST /users/<id>/collections`, a JSON array of `{"name", "parentCollection"}`; `200` with a per-index write report. Each object commits in its own transaction, in array order, so a parent can come before its child in one request if the parent carries a client-made `key` | Docs; order and per-object commit from source |
| Write report | `{"successful": {idx: <full object>}, "success": {idx: key}, "unchanged": {idx: key}, "failed": {idx: {"key"?, "code", "message", "data"?}}}`; indexes are strings of array positions; `success` is deprecated | Docs (partly) and server source |
| Objects per request | 50 collections, items or searches; 51 gives `413` for the whole request | Docs and server source |
| Item template | Not required. A new item needs only `itemType`; every other property is optional | Docs and server source |
| Unknown field / bad itemType | Per-object `failed` with `code: 400` (`Invalid property 'x'`, `'x' is not a valid itemType`, `'x' is not a valid field for type 'y'`); the request is still `200` | Server source |
| Preprint | `preprint` type exists (schema since 2022) and has `DOI`; `repository` is its publisher, `archiveID` its number | Schema source |
| DOI on other types | All 37 non-note types have `DOI` in the schema the server pins (since Dec 2025); before, only 5 did, so older items keep `DOI: ...` in `extra` | Schema source; `extra` remnant inferred |
| `Zotero-Write-Token` | 5-32 chars (docs: 32), per API key, remembered 12 h after a request that got past validation; reuse gives `412` for the whole request, even if the first one had per-object failures | Docs and server source |
| `collections` / `tags` on PATCH | Replaced wholesale. Adding one key is read-modify-write | Docs and server source |
| PATCH precondition | Single-object PATCH needs the item's version (`If-Unmodified-Since-Version` or `version` in the body), else `428`; a stale version gives `412`; success is `204` with `Last-Modified-Version` = new library version | Docs and server source |
| Library header on POST | `If-Unmodified-Since-Version: <library version>` makes the whole POST `412` if anything in the library changed; without it, each object with a `key` needs its own `version`, else per-object `428` | Docs and server source |
| Idempotent create | Send a client-made `key` with `"version": 0`: a repeat fails per-object with `412` instead of making a duplicate | Server source and API test |
| Search by DOI | None on the server. `q` matches title, note title, creator name and 4-digit year; `qmode=everything` adds attachment full text only. No field-level search exists | Server source; docs say "Searching of other fields will be possible in the future" |
| Trashed items | Left out of `/items` and `q` unless `includeTrashed=1`; a trashed item carries `data.deleted: 1` | Docs and server source |
| Rate limits | `Backoff: <s>` on any response; `429` with `Retry-After` for rate, concurrency and too many 400/401/403; `503` with `Retry-After` for maintenance; at most 4 concurrent requests | Docs and server source |
| Key without write | `403 Write access denied` for the whole request | Docs and server source |
| Tag object | `{"tag": "pe:seed"}` or `{"tag": "pe:seed", "type": 1}`; `type` 0 manual (default), 1 automatic; up to 255 chars; settable at create time | Docs and server source |

## 1. Creating Collections

Documented ([write requests, Creating a Collection](https://www.zotero.org/support/dev/web_api/v3/write_requests#creating_a_collection), [Creating Multiple Objects](https://www.zotero.org/support/dev/web_api/v3/write_requests#creating_multiple_objects)):

```http
POST <userOrGroupPrefix>/collections
Content-Type: application/json
Zotero-Write-Token: <write token> or If-Unmodified-Since-Version: <last library version>
```

```json
[
  {
    "name" : "My Collection",
    "parentCollection": "QRST9876"
  },
  {
    "name": "My Other Collection"
  }
]
```

The documented `200` response, verbatim (the docs' own sample has a missing comma and a trailing comma):

```
Content-Type: application/json
Last-Modified-Version: <library version>

{
  "successful": {
    "0": "<saved object>",
    "2": "<saved object>"
  },
  "unchanged": {
    "4": "<objectKey>"
  }
  "failed": {
    "1": {
      "key": "<objectKey>",
      "code": <HTTP response code>,
      "message": "<error message>"
    },
    "3": {
      "key": "<objectKey>",
      "code": <HTTP response code>,
      "message": "<error message>"
    },
  }
}
```

"The keys of the successful, unchanged, and failed objects are the numeric indexes of the Zotero objects in the uploaded array. The Last-Modified-Version is the version that has been assigned to any Zotero objects in the successful object" (documented, same section). Status codes listed: `200`, `409` (library locked), `412` (stale `If-Unmodified-Since-Version` or reused write token). Up to 50 objects per request (documented).

From source:

- **The report** is built by `Zotero_Results::generateReport()` ([DS model/Results.inc.php L62-L106](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Results.inc.php#L62-L106)). For v3 it always has four members: `successful` (index to the full response object, the same shape `GET /collections/<key>` returns, with `key`, `version`, `library`, `links`, `meta`, `data`), `success` (index to key, marked "Deprecated" in source), `unchanged` (index to key) and `failed`. Each failure is `{"key", "code", "message"}` plus `data` for some errors; `key` is left out when the object had none, so a failed *new* object has no `key`. `message` is passed through `htmlspecialchars`. Empty members are `{}`, not `[]`. The docs' sample omits `success`.
- **Per-object transactions.** `updateMultipleFromJSON()` loops over the array, opens a transaction per object, bumps the library version, saves, and commits or rolls back that object alone ([DS model/DataObjects.inc.php L315-L389](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/DataObjects.inc.php#L315-L389)). A partial success is normal: index 0 can be saved while index 1 failed.
- **Subcollection in the same request.** Since objects are saved in array order, a child whose `parentCollection` is the key of an earlier object in the same array finds it saved. That needs the parent to carry a client-made `key` (pattern `/[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}/`, [write requests, Object Keys](https://www.zotero.org/support/dev/web_api/v3/write_requests#object_keys)) and `"version": 0` (see section 5). The docs say this for items only ("the child items must appear after the parent item in the array", [Both PUT and PATCH](https://www.zotero.org/support/dev/web_api/v3/write_requests#both_put_and_patch)); for collections it is inferred from the loop. The simpler path is two requests: create the root, read its key from `successful["0"].key`, then create the children.
- **Validation** ([DS model/Collections.inc.php L348-L430](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collections.inc.php#L348-L430)): `name` is required for a new collection, must be a non-empty string, at most 255 characters ([`$maxLength` L44](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collections.inc.php#L44)); a longer name fails with `413`. `parentCollection` must be a collection key or `false`. Omitting it on a new collection makes it top-level ([L287-L295](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collections.inc.php#L287-L295)). A missing parent fails per-object with `409` and message `Parent collection <key> not found` ([DS model/Errors.inc.php L109-L128](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Errors.inc.php#L109-L128); [test collection.test.js L461](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/tests/remote/tests/3/collection.test.js#L461)).
- **Limit.** `$maxWriteCollections = 50`, likewise items and searches ([DS model/API.inc.php L36-L38](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/API.inc.php#L36-L38)). More than 50 throws `Cannot add more than 50 collections at a time` with `Z_ERROR_UPLOAD_TOO_LARGE` before any object is saved ([DS model/DataObjects.inc.php L443-L448](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/DataObjects.inc.php#L443-L448)), which maps to a request-level `413` ([DS model/Errors.inc.php L166-L169](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Errors.inc.php#L166-L169)).
- **Names are not unique.** Nothing in `validateJSONCollection` or `save()` checks for a sibling with the same name (read from source). A retried create without a write token or key makes a second collection with the same name.

A successful one-collection create, constructed from the source and the collection object in [2026-11-zotero-collections.md](2026-11-zotero-collections.md), not observed:

```json
{
  "successful": {
    "0": {
      "key": "N7W92H48",
      "version": 813,
      "library": {"type": "user", "id": 475425, "name": "...", "links": {"alternate": {"href": "...", "type": "text/html"}}},
      "links": {"self": {"href": "...", "type": "application/json"}, "alternate": {"href": "...", "type": "text/html"}},
      "meta": {"numCollections": 0, "numItems": 0},
      "data": {"key": "N7W92H48", "version": 813, "name": "PaperExplorer", "parentCollection": false, "relations": {}}
    }
  },
  "success": {"0": "N7W92H48"},
  "unchanged": {},
  "failed": {}
}
```

## 2. Creating items

Documented ([write requests, Creating an Item](https://www.zotero.org/support/dev/web_api/v3/write_requests#creating_an_item)), verbatim:

```http
POST <userOrGroupPrefix>/items
Content-Type: application/json
Zotero-Write-Token: <write token> or If-Unmodified-Since-Version: <last library version>
```

```json
[
  {
    "itemType" : "book",
    "title" : "My Book",
    "creators" : [
      {
        "creatorType":"author",
        "firstName" : "Sam",
        "lastName" : "McAuthor"
      },
      {
        "creatorType":"editor",
        "name" : "John T. Singlefield"
      }
    ],
    "tags" : [
      { "tag" : "awesome" },
      { "tag" : "rad", "type" : 1 }
    ],
    "collections" : [
      "BCDE3456", "CDEF4567"
    ],
    "relations" : {
      "owl:sameAs" : "http://zotero.org/groups/1/items/JKLM6543",
      "dc:relation" : "http://zotero.org/groups/1/items/PQRS6789",
      "dc:replaces" : "http://zotero.org/users/1/items/BCDE5432"
    }
  }
]
```

The docs list `200` (see the JSON for each write), `400` (invalid type/field, unparseable JSON), `409` (library locked), `412` and `413` (too many items). The docs' sample response shows only `success`; the v3 server also sends `successful` with full objects, as in section 1.

**Template not required.** The docs say to start from a template "first get empty JSON for the item type with an item template request (or use a cached version of the template)", then contradict the need for it: "All properties other than itemType, tags, collections, and relations are optional" (documented). The source is stricter about what is required and looser about the rest: for a new item the required list is `['itemType']` only ([DS model/Items.inc.php L2038-L2064](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L2038-L2064)); `tags`, `relations` and `collections` are required only for a full update (PUT) of an existing item. So `[{"itemType": "journalArticle", "title": "..."}]` is accepted (read from source). The template endpoint `GET /items/new?itemType=journalArticle` returns `itemType`, every valid field as `""`, one empty primary creator (`{"creatorType": "author", "firstName": "", "lastName": ""}`), `tags: []`, `collections: []`, `relations: {}` ([DS controllers/MappingsController.php L144-L250](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/MappingsController.php#L144-L250); docs [types and fields, Getting a Template](https://www.zotero.org/support/dev/web_api/v3/types_and_fields#getting_a_template_for_a_new_item)). The server deliberately ignores that single nameless creator on create ([Items.inc.php L2229-L2235](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L2229-L2235)).

**Fields for `journalArticle`** in the pinned schema ([ZS](https://github.com/zotero/zotero-schema/blob/62e983a2e575fe9b9a3677ad7c9772080b67a1e4/schema.json)): `title, abstractNote, publicationTitle, publisher, place, date, volume, issue, section, partNumber, partTitle, pages, series, seriesTitle, seriesText, journalAbbreviation, DOI, citationKey, url, accessDate, PMID, PMCID, ISSN, archive, archiveLocation, shortTitle, language, libraryCatalog, callNumber, rights, extra`; creator types `author, contributor, editor, translator, reviewedAuthor` (read from source; the live list is `GET /itemTypeFields?itemType=journalArticle`, documented). Field names are case-sensitive: it is `DOI`, not `doi`.

**Validation** (`validateJSONItem`, [DS model/Items.inc.php L1994-L2564](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L1994-L2564)), each a per-object `failed` entry with `code: 400` (`Z_ERROR_INVALID_INPUT` maps to 400, [Errors.inc.php L49-L53](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Errors.inc.php#L49-L53)) while the request itself is `200`:

- Unknown property name: `Invalid property '<key>'` ([L2557-L2560](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L2557-L2560)).
- Known field, wrong type (e.g. `publicationTitle` on `preprint`): `'<field>' is not a valid field for type '<type>'`, but only if the value is non-empty; `""` is accepted and dropped ([DS model/Item.inc.php L775-L783](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Item.inc.php#L775-L783)).
- Invalid itemType: `'<val>' is not a valid itemType` ([Items.inc.php L2106-L2116](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L2106-L2116)).
- Creators ([L2213-L2285](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L2213-L2285)): each needs `creatorType`; either `name` alone or both `firstName` and `lastName` (each requires the other key to be present, even as `""`); `name` excludes both; any other key is `Invalid creator property`. A creator type invalid for the item type fails, except `author`, which is accepted for every type. A nameless creator fails unless it is the only one on a new item. A single-field name (`name`) is stored as `fieldMode 1` ([L1730-L1740](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L1730-L1740)).
- `date` is free text; the server stores it as a multipart date it parses itself ([Item.inc.php L785-L791](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Item.inc.php#L785-L791)). `accessDate` must be ISO 8601, `YYYY-MM-DD[ hh:mm:ss]` or `CURRENT_TIMESTAMP` ([Items.inc.php L2504-L2513](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L2504-L2513)).
- A field value that is too long fails with `413` and `data: {"field", "value"}` ([Errors.inc.php L60-L69](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Errors.inc.php#L60-L69)).

**`collections` at create time** is accepted (documented, and in the example). Each entry must look like a key, else `400 '<k>' is not a valid collection key` ([Items.inc.php L2170-L2182](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L2170-L2182)); a key that does not exist in the library fails at save with `409 Collection <key> not found` ([Item.inc.php L1605-L1616](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Item.inc.php#L1605-L1616); [test item.test.js L2947](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/tests/remote/tests/3/item.test.js#L2947)). A trashed collection still exists, so the item would be filed into it (inferred).

A journalArticle from Crossref metadata, constructed from the rules above, not observed:

```json
[
  {
    "itemType": "journalArticle",
    "title": "Attention Is All You Need",
    "creators": [
      {"creatorType": "author", "firstName": "Ashish", "lastName": "Vaswani"},
      {"creatorType": "author", "name": "Google Brain"}
    ],
    "date": "2017-06-12",
    "publicationTitle": "Advances in Neural Information Processing Systems",
    "DOI": "10.48550/arXiv.1706.03762",
    "url": "https://doi.org/10.48550/arXiv.1706.03762",
    "extra": "",
    "collections": ["N7W92H48"],
    "tags": [{"tag": "pe:seed"}]
  }
]
```

**Preprints.** `preprint` was added to the schema in March 2022 ([zotero-schema commit 18730572](https://github.com/zotero/zotero-schema/commit/187305727c5866aef7004fdc8d5ec577a0969bc7)) and is a type in Zotero 7. Its fields at the pinned commit are `title, abstractNote, genre (base: type), repository (base: publisher), archiveID (base: number), place, date, series, seriesNumber, DOI, citationKey, url, accessDate, archive, archiveLocation, shortTitle, language, libraryCatalog, callNumber, rights, extra` ([ZS](https://github.com/zotero/zotero-schema/blob/62e983a2e575fe9b9a3677ad7c9772080b67a1e4/schema.json)). So it has `DOI`, but no `publicationTitle`: the server (`arXiv`, `bioRxiv`) goes in `repository`, an arXiv ID like `arXiv:1706.03762` in `archiveID`. Crossref's `posted-content` (subtype `preprint`) and OpenAlex `type: preprint` fit it (inferred mapping).

**DOI on other types.** Schema commit [55a13120](https://github.com/zotero/zotero-schema/commit/55a13120bab31e95d6dc88a82fbfa4bbb60e069c) (2025-12-20), "Add DOI to all item types", gave every regular type a `DOI` field. Schema version 33 just before it had `DOI` on only `conferencePaper, dataset, journalArticle, preprint, standard` (read from schema source at [81b5f0cc](https://github.com/zotero/zotero-schema/blob/81b5f0cc6ad07942cb72b65ce594170c8f05a6cf/schema.json)). The dataserver pins schema version 42, which has the new fields. Items made before that, for example a `book` or `report`, will often hold the DOI in `extra` as a line `DOI: 10.x/y`, which was the Zotero convention for types without the field (inferred; whether the client migrates those lines into the new field on upgrade was not checked).

## 3. `Zotero-Write-Token`

Documented ([write requests, Zotero-Write-Token](https://www.zotero.org/support/dev/web_api/v3/write_requests#zotero-write-token)): an optional header "containing a client-generated random 32-character identifier string", for unversioned write requests, "to prevent them from being processed more than once". "The Zotero server caches write tokens for successful requests for 12 hours, and subsequent requests from the same API key using the same write token will be rejected with a 412 Precondition Failed status code. If a request fails, the write token will not be stored." With `If-Unmodified-Since-Version` or object versions it "is redundant and should be omitted".

```http
Zotero-Write-Token: 19a4f01ad623aa7214f82347e3711f56
```

From source ([DS controllers/ApiController.php](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php)):

- Format: any string of 5 to 32 characters, else `400 Write token must be 5-32 characters in length`; it needs an API key ([L1206-L1217](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L1206-L1217)). The cache key is `md5(apiKey + "_" + token)`, so tokens are scoped to the API key, not the library.
- Lifetime: `$writeTokenCacheTime = 43200; // 12 hours` ([L30](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L30)), stored in memcached, so it can also be evicted earlier (inferred).
- Check: on every write method, before the body is read, `412 Write token already used` if the token is cached ([L287-L295](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L287-L295)). Tested by [general.test.js L39-L67](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/tests/remote/tests/3/general.test.js#L39-L67).
- Store: after `updateMultipleFromJSON()` returns, whatever its per-object outcome ([CollectionsController L161-L163](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/CollectionsController.php#L161-L163); [ItemsController L617-L619](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ItemsController.php#L617-L619)). So "successful request" means a `200`, even one whose report has `failed` entries; re-sending the same token to retry only the failed objects gets `412` (inferred from source). A request-level error (`400`, `412`, `413`, `5xx`) throws before the store, so the token stays free.
- Not atomic: the check is a memcached `get` and the store a later `set`, so two concurrent requests with one token can both pass (inferred from source).
- The `412` for a reused token carries no write report. A client that lost the first response cannot learn the new keys from the retry; it has to look the objects up (inferred).

## 4. `PATCH /items/<key>`

Documented ([write requests, Partial-item updating](https://www.zotero.org/support/dev/web_api/v3/write_requests#partial-item_updating_patch)), verbatim (the docs' sample lacks a comma):

```http
PATCH <userOrGroupPrefix>/items/<itemKey>
If-Unmodified-Since-Version: <last item version>
```

```json
{
  "date" : "2013"
  "collections" : [
    "BCDE3456", "CDEF4567"
  ]
}
```

"Properties not included in the uploaded JSON are left untouched on the server." "Array properties are interpreted as complete lists, so omitting a collection key would cause the item to be removed from that collection." Success is `204 No Content`; `412` when "the item has changed since retrieval" (documented). "PUT and PATCH requests must include the item's current version number in either the version property or the If-Unmodified-Since-Version header" ([Both PUT and PATCH](https://www.zotero.org/support/dev/web_api/v3/write_requests#both_put_and_patch)).

From source:

- **Wholesale replace.** `setCollections($keys)` replaces the item's list with the given keys ([DS model/Item.inc.php L3903-L3918](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Item.inc.php#L3903-L3918)); `setTags($tags)` empties the list and rebuilds it from the given array, dropping blank names ([L3830-L3863](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Item.inc.php#L3830-L3863)). There is no add or remove operator. Adding one collection key or one tag means: GET the item, take `data.collections` / `data.tags`, change one entry, PATCH the whole array with the version from that GET.
- **Version required.** `checkSingleObjectWriteVersion()` ([DS controllers/ApiController.php L1007-L1119](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L1007-L1119)) takes the version from `If-Unmodified-Since-Version` or the body's `version`; both given and different is `400`. For an existing item with neither: `428 Either If-Unmodified-Since-Version or object version property must be provided for key-based writes`. Item version higher than given: `412 Item has been modified since specified version (expected X, found Y)`, with `Last-Modified-Version` set to the item's current version. For a key that does not exist, PATCH without a version is `404`, and with version > 0 is `412`. The header here is the **item's** version, not the library's.
- **204 and its header.** After the write, `handleObjectWrite()` returns `204` ([L919-L992](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L919-L992)) and the response handler sends `Last-Modified-Version: <libraryVersion>` ([L1399-L1402](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L1399-L1402)). That is the new library version, which is also the item's new version, because every write gives the changed object the bumped library version (docs: [syncing, Version Numbers](https://www.zotero.org/support/dev/web_api/v3/syncing#version_numbers)). A PATCH that changes nothing returns the original library version. On `4xx`/`5xx` other than `412`, no `Last-Modified-Version` is sent ([L1250-L1262](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L1250-L1262)).
- **Date Modified.** If only `collections`, `relations` or `inPublications` changed, the server leaves `dateModified` and "last modified by" alone ([DS model/Items.inc.php L1915-L1926](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L1915-L1926)). A tag change does bump `dateModified` (inferred from the same lines).
- **The library-wide tag delete is not a remove.** `DELETE /tags?tag=...` ([write requests, Deleting Multiple Tags](https://www.zotero.org/support/dev/web_api/v3/write_requests#deleting_multiple_tags)) deletes a tag from every item in the library (documented). Removing `pe:seed` from one item goes through PATCH.

Adding collection `N7W92H48` to an item that is in `AB12CD34` with tags `ml` and `to-read` (constructed, not observed):

```http
GET /users/475425/items/X42A7DEF
→ 200, Last-Modified-Version: 950, data.version 940, data.collections ["AB12CD34"], data.tags [{"tag": "ml"}, {"tag": "to-read", "type": 1}]

PATCH /users/475425/items/X42A7DEF
If-Unmodified-Since-Version: 940
Content-Type: application/json

{"collections": ["AB12CD34", "N7W92H48"]}

→ 204 No Content
  Last-Modified-Version: 951
```

The same can be done for up to 50 items at once through `POST /items` with `key` and `version` in each object, since "POST follows PATCH semantics" ([Updating Multiple Objects](https://www.zotero.org/support/dev/web_api/v3/write_requests#updating_multiple_objects), documented; the multi-object path passes `$partialUpdate = true`, [DataObjects.inc.php L351-L354](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/DataObjects.inc.php#L351-L354)). Per-item `412` then lands in `failed`, not as a request-level error.

## 5. Versions on POST

Documented: "As an alternative to individual version properties, the last-known library version can be passed via the If-Unmodified-Since-Version HTTP Header" ([Updating Multiple Objects](https://www.zotero.org/support/dev/web_api/v3/write_requests#updating_multiple_objects)); `412` when "the library has changed since the specified version". The [syncing](https://www.zotero.org/support/dev/web_api/v3/syncing) page calls the per-object path "the default API usage mode".

From source:

- **Library header.** `checkLibraryIfUnmodifiedSinceVersion()` rejects the whole request with `412 Library has been modified since specified version (expected X, found Y)` if the library moved ([DS controllers/ApiController.php L1133-L1157](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L1133-L1157)); the first object's version bump repeats the check atomically in SQL (`WHERE version <= ?`, [DS model/Libraries.inc.php L365-L395](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Libraries.inc.php#L365-L395)). In a library the user edits at the same time, any change anywhere gives a `412`.
- **Per-object `version`.** Without the library header the controller calls `updateMultipleFromJSON(..., $requireVersion = 1)` ([ItemsController L572-L583](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ItemsController.php#L572-L583)). `checkJSONObjectVersion()` ([DS model/API.inc.php L1215-L1311](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/API.inc.php#L1215-L1311)) then, per object:
  - no `key`: a new object, no version needed;
  - `key` and no `version`: per-object `428` ("Either If-Unmodified-Since-Version or 'version' property must be provided for 'key'-based writes"), whether or not the key exists ([version.test.js L128-L155](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/tests/remote/tests/3/version.test.js#L128-L155));
  - `key` and `version` lower than the stored one: per-object `412`;
  - `key` and `version > 0` for a key that does not exist: per-object `404`;
  - `key` and `"version": 0`: creates the object if the key is free; if it exists, per-object `412` ([version.test.js L212-L224](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/tests/remote/tests/3/version.test.js#L212-L224)).

  So yes, `version` in a POST body is how existing objects are updated, and `key` + `"version": 0` is a create that cannot run twice. (`HTTPException` codes survive into the report: [Errors.inc.php L178-L195](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Errors.inc.php#L178-L195).)

## 6. Searching by DOI

Documented ([basics, Search Parameters](https://www.zotero.org/support/dev/web_api/v3/basics#search_parameters)): `q` is "Quick search. Searches titles and individual creator fields by default. ... Currently supports phrase searching only." `qmode` is `titleCreatorYear` (default) or `everything`: "To include full-text content, use everything. Searching of other fields will be possible in the future." `includeTrashed` is `0` by default (except in `/trash`). `/items` lists "All items in the library, excluding trashed items" ([basics, Items](https://www.zotero.org/support/dev/web_api/v3/basics#items)).

From source, `Zotero_Items::search()` ([DS model/Items.inc.php L344-L367](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L344-L367)) builds, for each word or quoted phrase of `q`:

```sql
AND ( IDT.value LIKE '%part%'              -- title and its base-mapped fields
   OR INo.title LIKE '%part%'              -- note title
   OR TRIM(CONCAT(firstName, ' ', lastName)) LIKE '%part%'
   OR SUBSTR(IDD.value, 1, 4) = 'part'     -- year of date
   [OR I.key IN (<full-text hits>)] )      -- only qmode=everything
```

- **`q=<doi>&qmode=everything` does not match the `DOI` field, nor `extra`.** The only addition in `everything` mode is `Zotero_FullText::searchInLibrary()`, the indexed text of attachments. A DOI printed in an attached PDF would match the *attachment* item, not its parent (the full-text test returns `attachmentKey` on `/items` and `parentKey` only on `/items/top`, [fulltext.test.js L310-L357](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/tests/remote/tests/3/fulltext.test.js#L310-L357)). An item without a PDF is not found by DOI at all.
- **`qmode=titleCreatorYear` does not either.** It searches the four columns above.
- **No exact-field search.** The item search parameters are `itemKey`, `itemType`, `q`, `since`, `tag`, `includeTrashed`, `qmode` (documented). Saved searches can be stored but "Only search metadata is currently available, not search results" ([basics, Searches](https://www.zotero.org/support/dev/web_api/v3/basics#searches)).
- **The client differs.** Zotero desktop's "All Fields & Tags" mode (`quicksearch-everything`) adds a `field` condition over every field ([ZC chrome/content/zotero/xpcom/data/search.js L315-L366](https://github.com/zotero/zotero/blob/228f132e1a2c38745ae07bea6c1600b8bddc3b73/chrome/content/zotero/xpcom/data/search.js#L315-L366)), and the desktop local API maps `qmode` onto it ([ZC server_localAPI.js L1132-L1135](https://github.com/zotero/zotero/blob/228f132e1a2c38745ae07bea6c1600b8bddc3b73/chrome/content/zotero/xpcom/server/server_localAPI.js#L1132-L1135)). So a DOI search that works against `localhost:23119/api/` will not work against api.zotero.org (read from both sources). The docs warn that local `q` results "may not match the Web API exactly".
- **Trash.** Without `includeTrashed=1`, items in the trash are filtered out (`AND DI.itemID IS NULL`, [Items.inc.php L330-L337](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L330-L337)). A trashed item's JSON has `"deleted": 1`, an integer, unlike a collection's `true` ([DS model/Item.inc.php L4896-L4899](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Item.inc.php#L4896-L4899)).

A DOI lookup therefore has to read item data and compare locally: the `DOI` field, and for any type the `extra` lines matching `^DOI:\s*(.+)$`, case-insensitively, after normalising both sides (lower case, no `https://doi.org/` prefix). PaperExplorer's Library sync already mirrors items with `since=` (see [2026-11-zotero-collections.md](2026-11-zotero-collections.md) and [syncing](https://www.zotero.org/support/dev/web_api/v3/syncing#full-library_syncing)); checking the mirror right after a `since=` refresh is the cheap path (inferred).

## 7. Rate limiting and errors

Documented ([basics, Rate Limiting](https://www.zotero.org/support/dev/web_api/v3/basics#rate_limiting), [HTTP Status Codes](https://www.zotero.org/support/dev/web_api/v3/basics#http_status_codes)):

- `Backoff: <seconds>` "can be included in any response, including successful ones": perform the minimum requests needed for consistency, then stop for that long.
- `429 Too Many Requests`, "potentially with a Retry-After: <seconds> header": wait at least that long, or back off exponentially if absent; reduce rate and concurrency; "Clients should also generally make no more than 4 concurrent requests"; prefer batched writes.
- `Retry-After` may come with `503 Service Unavailable` during maintenance.
- `403 Forbidden` for an invalid key or insufficient privileges; `412`/`428` for version and write-token errors; `413` for too many objects; `409` when the library is locked; `417` if an `Expect` header is sent (relevant for clients that send `Expect: 100-continue` on large bodies; httpx does not by default, inferred).

From source:

- `Backoff` is a server-wide config value sent on every response while set ([DS controllers/ApiController.php L81-L83](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L81-L83)).
- Writes have their own per-user bucket, `user_writes` ([L739-L790](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L739-L790)); searches with `q` have `user_searches`. The `429` paths ([L660-L730](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L660-L730)) are: `Request rate limit exceeded` with `Retry-After` = bucket capacity / refill rate; `Concurrent request limit exceeded` with a random `Retry-After` of 10-30 s; `Too many invalid requests` when the per-user error bucket is empty. The error bucket counts responses `400`, `401`, `403` (and anonymous `404`) ([L1604-L1615](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L1604-L1615)), so a client that keeps sending bad requests gets throttled. Per-object failures inside a `200` are not counted (inferred). Production numbers live in a gitignored config; the sample's defaults (20/s refill, 100 burst, log-only) are not what api.zotero.org enforces ([DS include/config/rate_limits.inc.php-sample](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/include/config/rate_limits.inc.php-sample)).
- A key without write access gets `403 Write access denied` for the whole request, before any object is read ([ItemsController L32-L37](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ItemsController.php#L32-L37); [CollectionsController L36-L40](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/CollectionsController.php#L36-L40)). Repeated bad keys give `429 Too many authentication failures` ([L268-L282](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/ApiController.php#L268-L282)).
- `413` arises at request level for more than 50 objects, and per object for a field, creator, collection name, note or tag that is too long ([Errors.inc.php L60-L107](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Errors.inc.php#L60-L107)).
- Shard read-only or unavailable maps to `503` with the maintenance message ([Errors.inc.php L171-L175](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Errors.inc.php#L171-L175)).

## 8. Tags

Documented: tags are objects in an array, `{ "tag" : "awesome" }` or `{ "tag" : "rad", "type" : 1 }`, and they appear in the create example, so they may be set at create time ([Creating an Item](https://www.zotero.org/support/dev/web_api/v3/write_requests#creating_an_item)).

From source ([DS model/Items.inc.php L2131-L2168](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Items.inc.php#L2131-L2168)): each tag must be an object with only `tag` (a scalar) and optionally `type` (numeric); any other key is `400 Invalid tag property`. `type` defaults to `0` and names are trimmed ([Item.inc.php L3849-L3860](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Item.inc.php#L3849-L3860)). Blank names are dropped silently. Names over 255 characters fail with `413` ([Tags.inc.php L28](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Tags.inc.php#L28); [Tag.inc.php L720-L723](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Tag.inc.php#L720-L723)). `type: 1` is an automatic tag: the client's "remove automatic tags" acts on `type=1` ([ZC chrome/content/zotero/xpcom/data/tags.js L478-L483](https://github.com/zotero/zotero/blob/228f132e1a2c38745ae07bea6c1600b8bddc3b73/chrome/content/zotero/xpcom/data/tags.js#L478-L483)), and the docs don't define the numbers. The colon in `pe:seed` needs no escaping; finding seeded items is `GET /items?tag=pe%3Aseed` ([basics, Search Syntax](https://www.zotero.org/support/dev/web_api/v3/basics#search_syntax), documented).

```json
"tags": [{"tag": "ml"}, {"tag": "to-read", "type": 1}, {"tag": "pe:seed"}]
```

## Implications for PaperExplorer

- Collections and items: POST without a template, `itemType` plus the fields we have. Map Crossref `journal-article` to `journalArticle` (`publicationTitle`) and `posted-content`/OpenAlex `preprint` to `preprint` (`repository`, `archiveID`); never send `publicationTitle` on a preprint, or that item fails with `400`.
- Make creates safe to retry: generate each new object's 8-character `key` locally and send `"version": 0`. A retry then gets a per-object `412` for objects already made, never a duplicate, and we know every key before the response. Prefer this to `Zotero-Write-Token`, whose `412` hides what the first request did.
- Treat a POST `200` as a report to read, not a success: check `failed` per index (400 for our bugs, 409 for a missing collection, 412/428 for versions, 413 for length) and `successful` for the new versions.
- Membership and the `pe:seed` tag: GET the item, change one entry of `data.collections` or `data.tags`, PATCH the full array with `If-Unmodified-Since-Version: <item version>`. On `412`, re-read and retry a bounded number of times. Never use `DELETE /tags`. Do not send `If-Unmodified-Since-Version` as a library version on POST; per-object versions keep the user's concurrent edits from blocking us.
- DOI lookup cannot use `q`. Compare normalised DOIs against the mirrored items (the `DOI` field and `DOI:` lines in `extra`), including trashed items (`includeTrashed=1` in sync), and decide what a trashed match means before creating a new item.
- Honour `Backoff` and `Retry-After` on `429`/`503`, keep writes batched (up to 50) and serial; stop rather than retry on `400`/`403`, since those refill the error bucket.
- Check the key's write access once (a `403` on the first write) and surface it as a setup problem.

## Open

- Nothing here was observed against api.zotero.org. The inferred points worth a probe on a test library: the write token being stored after a `200` with failures; `"version": 0` creates; the write report's exact `successful` object; whether a collection-only PATCH leaves `dateModified` alone.
- Whether Zotero desktop moves `DOI:` lines from `extra` into the new `DOI` field of non-article types after schema 42 was not checked.
- Production rate-limit numbers are not public.
