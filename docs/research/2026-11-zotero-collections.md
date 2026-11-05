# Zotero Web API v3: collections, trash and deletions

Question: what does a Library sync need to know to mirror a Zotero library's collection tree through the Web API v3, including Zotero 7's collection trash and permanent deletions? Read on 2026-11-05 from the v3 docs ([basics](https://www.zotero.org/support/dev/web_api/v3/basics), [syncing](https://www.zotero.org/support/dev/web_api/v3/syncing), [write requests](https://www.zotero.org/support/dev/web_api/v3/write_requests)), the server source `zotero/dataserver` at commit `caa95bef`, and the client source `zotero/zotero` at commit `e593460e`.

No request was sent to api.zotero.org. Every claim is documented or read from source, and says which. Source links are permalinks to those commits; `DS` below is `https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7` and `ZC` is `https://github.com/zotero/zotero/blob/e593460edcb2a64ec65e1ac504ec443c128a626a`.

## Answer

| Question | Answer | Basis |
|---|---|---|
| Root collection's `parentCollection` | Present, `false` | Docs example and server source |
| `data.deleted` on a trashed collection | `true` (boolean), and the key is absent when not trashed | Server source; docs silent |
| Trashed collections in `/collections` | Listed by default; `includeTrashed` does not apply to collections | Server source; docs list `includeTrashed` for items only |
| `/collections/trash` | Does not exist (404) | Docs list none; server routing |
| `/deleted?since=` | Object with `collections`, `items`, `searches`, `tags`, `settings` arrays of keys (tag names for `tags`) | Server source; the docs example omits `settings` |
| Permanent delete of a parent | `/deleted` lists exactly the keys that were `DELETE`d. The Zotero client sends every descendant's key; a bare API `DELETE` of the parent cascades on the server but logs only the parent's key | Server and client source |
| Trash of a parent | Server marks only the collections whose JSON says `deleted: true`. The client trashes descendants locally, but by reading the source it uploads `deleted: true` for the parent only | Inferred from client source, not observed |
| `/collections/top` | Exists | Docs and server routing |

## 1. The collection object

`GET /users/<id>/collections` with `format=json` (the v3 default) returns a JSON array. The docs link a sample response for `https://api.zotero.org/users/475425/collections?v=3` ([basics, Example Requests](https://www.zotero.org/support/dev/web_api/v3/basics#example_requests); [sample gist](https://gist.github.com/dstillman/0bc17ca64ee7d3bc9063)). One element of it, verbatim except whitespace:

```json
{
  "key": "N7W92H48",
  "version": 1,
  "library": {
    "type": "user",
    "id": 475425,
    "name": "Z public library",
    "links": {
      "alternate": {"href": "https://www.zotero.org/z_public_library", "type": "text/html"}
    }
  },
  "links": {
    "self": {"href": "https://api.zotero.org/users/475425/collections/N7W92H48", "type": "application/json"},
    "alternate": {"href": "https://www.zotero.org/z_public_library/collections/N7W92H48", "type": "text/html"}
  },
  "meta": {"numCollections": 1, "numItems": 16},
  "data": {
    "key": "N7W92H48",
    "version": 1,
    "name": "LoC",
    "parentCollection": false,
    "relations": {}
  }
}
```

A trashed subcollection would differ only in `data` (constructed from the source below, not observed):

```json
"data": {"key": "ABCD2345", "version": 812, "name": "Old reading", "parentCollection": "N7W92H48", "relations": {}, "deleted": true}
```

Field by field, from `Zotero_Collection::toJSON()` ([DS model/Collection.inc.php L708-L736](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collection.inc.php#L708-L736)):

- `key`, `version`: always, for `v >= 3`.
- `name`: always.
- `parentCollection`: always present; the parent's key, or `false` for a root (`$parentKey ? $parentKey : false`). The write docs also send `"parentCollection": false` to make a collection top-level ([write requests, Updating an Existing Collection](https://www.zotero.org/support/dev/web_api/v3/write_requests#updating_an_existing_collection)). The client's own `toJSON` does the same ([ZC collection.js L826-L838](https://github.com/zotero/zotero/blob/e593460edcb2a64ec65e1ac504ec443c128a626a/chrome/content/zotero/xpcom/data/collection.js#L826-L838)).
- `relations`: always, an object (empty `{}` in the sample).
- `deleted`: only when the collection is in the trash, and then `true`. It is never `false` and never `1` in output. It is read from the `deletedCollections` table ([DS model/DataObject.inc.php L291-L313](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/DataObject.inc.php#L291-L313)). On write the server requires a boolean (`'deleted' must be a boolean`) ([DS model/Collections.inc.php L306-L307, L437-L440](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collections.inc.php#L306-L440)). The v3 docs do not mention `deleted` on collections at all.

`meta.numCollections` counts direct children only (`WHERE parentCollectionID=?`), trashed children included. `meta.numItems` excludes trashed items ([DS model/Collection.inc.php L318-L340](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collection.inc.php#L318-L340), set in `toResponseJSON` at [L636-L690](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collection.inc.php#L636-L690)). The docs do not define either count.

## 2. Paging, headers and `since`

- `limit`: integer 1-100, default 25; `start`: index of the first result, default 0 ([basics, Sorting and Pagination](https://www.zotero.org/support/dev/web_api/v3/basics#sorting_and_pagination)). In source, a `limit` above 100 is clamped to 100 silently rather than rejected ([DS model/API.inc.php L299-L330](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/API.inc.php#L299-L330), `getLimitMax` at [L729](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/API.inc.php#L729)). `format=keys` and `format=versions` have no default or maximum limit ([basics, Format](https://www.zotero.org/support/dev/web_api/v3/basics#format); [syncing](https://www.zotero.org/support/dev/web_api/v3/syncing#format_versions)).
- `Total-Results`: total matches for a multi-object read; a response holds at most 100 ([basics](https://www.zotero.org/support/dev/web_api/v3/basics#sorting_and_pagination)). Sent for `v >= 3` ([DS model/API.inc.php L1045-L1050](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/API.inc.php#L1045-L1050)).
- `Link`: `rel=first|prev|next|last` when more results exist ([basics](https://www.zotero.org/support/dev/web_api/v3/basics#sorting_and_pagination)).
- `Last-Modified-Version`: the current library version on multi-object requests; `If-Modified-Since-Version` with it gives `304` when nothing changed ([basics, Caching](https://www.zotero.org/support/dev/web_api/v3/basics#caching)). For `/collections` it is the library version, not the newest collection's ([DS controllers/CollectionsController.php L113](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/CollectionsController.php#L113)).
- `since=<v>`: "Return only objects modified after the specified library version" ([basics, Search Parameters](https://www.zotero.org/support/dev/web_api/v3/basics#search_parameters)). For collections this is `version > since` in SQL, with no deleted filter ([DS model/Collections.inc.php L46-L92](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collections.inc.php#L46-L92)), so a collection trashed or restored since `v` comes back with its new state. Permanently deleted collections do not; they come from `/deleted`. The sync recipe is `GET /collections?since=<v>&format=versions`, then fetch changed keys with `?collectionKey=<k>,<k>,...` ([syncing, Full-Library Syncing](https://www.zotero.org/support/dev/web_api/v3/syncing#full-library_syncing)); a `collectionKey` list forces the limit to 100 ([DS model/API.inc.php L495-L501](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/API.inc.php#L495-L501)).

The docs advise checking `Last-Modified-Version` on each response against the first one and restarting if it moved ([syncing](https://www.zotero.org/support/dev/web_api/v3/syncing#full-library_syncing)).

## 3. Trashed collections

- **Marked how:** `data.deleted: true`, see section 1.
- **Listed by default:** yes. `Zotero_Collections::search()` selects from `collections` with no join on `deletedCollections` and no `includeTrashed` check ([DS model/Collections.inc.php L46-L92](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collections.inc.php#L46-L92)). The same holds for `/collections/top` and `/collections/<key>/collections`, which use the same search ([DS controllers/CollectionsController.php L113-L141, L209-L214](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/CollectionsController.php#L113-L214)).
- **`includeTrashed`:** the docs list it under "Search Parameters (Items Endpoints)" only ([basics](https://www.zotero.org/support/dev/web_api/v3/basics#search_parameters)), and the server parses it ([DS model/API.inc.php L503](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/API.inc.php#L503)) but the collection search never reads it. It has no effect on collections. The sync recipe's collection request carries no `includeTrashed`, unlike its item requests ([syncing](https://www.zotero.org/support/dev/web_api/v3/syncing#full-library_syncing)).
- **`/collections/trash`:** not in the docs' URL table, which lists `/items/trash` only ([basics, Collections and Items](https://www.zotero.org/support/dev/web_api/v3/basics#collections)). No route exists for it ([DS include/config/routes.inc.php](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/include/config/routes.inc.php#L46-L48)). It falls to the generic `/:controller/:objectKey` route, where `trash` is not a valid 8-character key, so it answers 404 ([routes L117](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/include/config/routes.inc.php#L117); [DS controllers/CollectionsController.php L68-L70](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/CollectionsController.php#L68-L70)). Inferred from source, not observed.

A client that wants "live" collections must filter `data.deleted` itself. The Zotero client does the same locally (`getChildCollections(includeTrashed=false)` filters `!c.deleted`, [ZC collection.js L202-L212](https://github.com/zotero/zotero/blob/e593460edcb2a64ec65e1ac504ec443c128a626a/chrome/content/zotero/xpcom/data/collection.js#L202-L212)).

## 4. `GET /deleted?since=<v>`

`since` is required; without it the server answers 400 (`'since' parameter must be provided`). The response is one JSON object of five arrays, each the keys logged in `syncDeleteLogKeys` with `version > since` ([DS controllers/DeletedController.php](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/DeletedController.php#L66-L88); [DS model/DataObjects.inc.php L542-L569](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/DataObjects.inc.php#L542-L569)). `tags` holds tag names, not keys. The docs' example ([syncing, Get deleted data](https://www.zotero.org/support/dev/web_api/v3/syncing#ii_get_deleted_data)) shows four arrays and omits `settings`, which the source always includes:

```http
GET /users/475425/deleted?since=800
Last-Modified-Version: 812
Content-Type: application/json
```

```json
{
  "collections": ["ABCD2345", "EFGH6789"],
  "items": ["QRST9876"],
  "searches": [],
  "tags": ["to-read"],
  "settings": []
}
```

Keys and values here are illustrative. Empty types come back as `[]` ([DataObjects L564-L566](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/DataObjects.inc.php#L564-L566)). The response is not paged. Trashing writes nothing to this log; only a permanent delete does ([DS model/DataObjects.inc.php L721-L752](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/DataObjects.inc.php#L721-L752)).

## 5. Descendants

The docs are silent on both cases. From source:

**Permanent delete, server side.** `DELETE /collections/<key>` (or `?collectionKey=`, up to 50 per request, [write requests](https://www.zotero.org/support/dev/web_api/v3/write_requests#deleting_multiple_collections)) runs `Zotero_DataObjects::delete()` once per requested key. It deletes the row and lets the foreign key `collections_ibfk_1 ... ON DELETE CASCADE` remove the subtree ([DS misc/shard.sql L387](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/misc/shard.sql#L387)), or, past MySQL's 15-level cascade limit, `deleteSubcollections()` removes them bottom-up ([DS model/Collections.inc.php L451-L476](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collections.inc.php#L451-L476)). Either way it writes **one** `syncDeleteLogKeys` row, for the requested key ([DS model/DataObjects.inc.php L584-L752](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/DataObjects.inc.php#L584-L752)). So a raw API delete of a parent leaves its descendants out of `/deleted`; they simply stop appearing in `/collections`.

**Permanent delete, from the Zotero client.** `Collection._eraseData()` collects every descendant collection, trashed ones included, and erases them in the same transaction ([ZC collection.js L690-L745](https://github.com/zotero/zotero/blob/e593460edcb2a64ec65e1ac504ec443c128a626a/chrome/content/zotero/xpcom/data/collection.js#L690-L745)). `trash()`, which runs first, puts each descendant in `notifierData` ([L600-L636](https://github.com/zotero/zotero/blob/e593460edcb2a64ec65e1ac504ec443c128a626a/chrome/content/zotero/xpcom/data/collection.js#L600-L636)); `_finalizeErase()` queues one `delete` notification for all of them ([ZC dataObject.js `_finalizeErase`](https://github.com/zotero/zotero/blob/e593460edcb2a64ec65e1ac504ec443c128a626a/chrome/content/zotero/xpcom/data/dataObject.js)); the delete-log listener writes each key to `syncDeleteLog` ([ZC sync/syncEventListeners.js L36-L60](https://github.com/zotero/zotero/blob/e593460edcb2a64ec65e1ac504ec443c128a626a/chrome/content/zotero/xpcom/sync/syncEventListeners.js#L36-L60)); and sync sends them as `DELETE /collections?collectionKey=...` ([syncing, Local object deletions](https://www.zotero.org/support/dev/web_api/v3/syncing#full-library_syncing)). So in the common case, a user emptying the trash in Zotero desktop, every descendant key does reach `/deleted`. A reader must still tolerate the raw-API case: treat a collection whose parent vanished as deleted too.

**Trash.** On the client, `trash()` inserts the parent and all non-trashed descendants into the local `deletedCollections` ([ZC collection.js L600-L686](https://github.com/zotero/zotero/blob/e593460edcb2a64ec65e1ac504ec443c128a626a/chrome/content/zotero/xpcom/data/collection.js#L600-L686)). It is reached through the parent's `save()` when `deleted` changes ([L348-L359](https://github.com/zotero/zotero/blob/e593460edcb2a64ec65e1ac504ec443c128a626a/chrome/content/zotero/xpcom/data/collection.js#L348-L359)), which marks only the parent unsynced; `trash()` does not touch the descendants' `synced` flag. Inferred, not observed: only the parent is uploaded with `deleted: true`, so on the server the descendants keep no `deleted` key and stay in `/collections` as ordinary collections under a trashed parent. Other clients reproduce the local cascade when they apply the parent's `deleted: true` through the same `save()`. On the server, setting `deleted` touches only that one collection's `deletedCollections` row ([DS model/Collection.inc.php L232-L239](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collection.inc.php#L232-L239)). A reader should therefore treat a collection as trashed when it or any ancestor has `data.deleted: true`.

## 6. `/collections/top`

Exists: "Top-level collections in the library" ([basics, Collections](https://www.zotero.org/support/dev/web_api/v3/basics#collections)). The generic `/:controller/top` route sets `subset=top` ([DS include/config/routes.inc.php L19-L20](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/include/config/routes.inc.php#L19-L20)), and the controller searches with `parentCollectionID IS NULL` ([DS controllers/CollectionsController.php L134-L140](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/controllers/CollectionsController.php#L134-L140); [Collections.inc.php L61-L63](https://github.com/zotero/dataserver/blob/caa95bef3abbc8e6ca48bd1ddb3daeb759ccd8f7/model/Collections.inc.php#L61-L63)). It is GET-only and includes trashed top-level collections. Also documented: `/collections/<key>/collections` for direct subcollections ([basics](https://www.zotero.org/support/dev/web_api/v3/basics#collections)).

## Open

- Nothing here was observed against api.zotero.org. A probe that trashes a parent with a child in Zotero desktop, syncs, and reads `/collections?since=` would settle the inferred trash behaviour in section 5.
- The local API (Zotero desktop's `/api/`) has no limit cap and its own versions ([basics](https://www.zotero.org/support/dev/web_api/v3/basics#sorting_and_pagination)); it was not studied.
