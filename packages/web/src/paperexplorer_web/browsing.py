"""Browsing the Library by Collection: what is selected, and the tree the pane draws."""

from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel

from paperexplorer_api.schemas import BranchCount, CollectionCount, CollectionTree

type View = Literal["list", "collections"]

# The cookies that remember the view and the selection, so the server renders them with no flash.
VIEW_COOKIE = "pe.library_view"
COLLECTION_COOKIE = "pe.collection"
DEEP_COOKIE = "pe.deep"
# What `pe.collection` holds for the two entries that are not Collections; Zotero's keys are
# eight characters of upper-case letters and digits, so neither can clash with one.
WHOLE_LIBRARY = "library"
UNFILED = "unfiled"
# Zotero nests Collections to any depth; a parent key the tree cannot reach stops at this depth.
MAX_DEPTH = 32


class Selection(BaseModel):
    """What the Collections view shows: one Collection (`deep`: its branch), the unfiled, or all."""

    collection: str | None = None
    unfiled: bool = False
    deep: bool = False

    @property
    def cookie(self) -> str:
        """What `pe.collection` holds for it."""
        return self.collection or (UNFILED if self.unfiled else WHOLE_LIBRARY)

    @property
    def is_branch(self) -> bool:
        """Whether it is narrower than the whole Library."""
        return self.collection is not None or self.unfiled


def view_of(params: Mapping[str, str], cookies: Mapping[str, str]) -> View:
    """The view to render: the URL's, a selection in the URL's, else the cookie's (Collections)."""
    asked = params.get("view")
    if asked in ("list", "collections"):
        return asked
    if "collection" in params or "unfiled" in params:
        return "collections"
    return "list" if cookies.get(VIEW_COOKIE) == "list" else "collections"


def selection_of(params: Mapping[str, str], cookies: Mapping[str, str]) -> Selection:
    """The URL's selection with its own box, else the one the cookies remember.

    `?collection=` with no key names the whole Library, so a link can select it over the cookie.
    """
    if "collection" in params or "unfiled" in params:
        key = params.get("collection") or None
        if key is None and params.get("unfiled") == "1":
            return Selection(unfiled=True)
        return Selection(collection=key, deep=key is not None and params.get("deep") == "1")
    remembered = cookies.get(COLLECTION_COOKIE, WHOLE_LIBRARY)
    if remembered == WHOLE_LIBRARY:
        return Selection()
    if remembered == UNFILED:
        return Selection(unfiled=True)
    return Selection(collection=remembered, deep=cookies.get(DEEP_COOKIE) == "1")


def address(selection: Selection, query: str = "") -> str:
    """The landing page's URL for a selection, with the search as it stands."""
    params: dict[str, str] = {}
    if selection.unfiled:
        params["unfiled"] = "1"
    else:
        params["collection"] = selection.collection or ""
        if selection.deep:
            params["deep"] = "1"
    if query.split():
        params["q"] = query
    return f"/?{urlencode(params)}"


class Node(BaseModel):
    """One Collection as the pane and the Works side show it."""

    key: str
    name: str
    level: int
    works: int
    ingested: int
    path: str  # every name from its root down, for a hover
    children: list[Node]
    url: str
    open: bool = False
    selected: bool = False
    matches: int | None = None  # its branch's matches while searching
    exploration: bool = False  # it mirrors an Exploration under way

    @property
    def collections_within(self) -> int:
        """How many subcollections it holds directly."""
        return len(self.children)


class Entry(BaseModel):
    """*Whole Library* or *Not in a collection*: an entry of the pane that is no Collection."""

    name: str
    works: int
    ingested: int
    url: str
    selected: bool
    matches: int | None = None


class Tree(BaseModel):
    """The pane: the whole Library, the root Collections by name, the Items in no Collection."""

    library: Entry
    roots: list[Node]
    unfiled: Entry
    by_key: dict[str, Node]
    searching: bool

    def ancestors(self, key: str) -> list[Node]:
        """The Collections above this one, from its root down."""
        node = self.by_key.get(key)
        if node is None:
            return []
        above: list[Node] = []
        for candidate in self.by_key.values():
            if candidate is not node and _holds(candidate, node):
                above.append(candidate)
        return sorted(above, key=lambda n: n.level)

    def path(self, key: str) -> str:
        """A Collection's names from its root down; its key alone if the tree has gone."""
        node = self.by_key.get(key)
        return node.path if node else key


def _holds(branch: Node, node: Node) -> bool:
    return any(child is node or _holds(child, node) for child in branch.children)


def tree(
    collections: CollectionTree,
    selection: Selection,
    query: str = "",
    matches: CollectionTree | None = None,
) -> Tree:
    """The pane for these Collections, the selection's ancestors open; `matches` while searching."""
    found = {c.key: c for c in matches.collections} if matches else {}
    children: dict[str | None, list[CollectionCount]] = {}
    known = {c.key for c in collections.collections}
    for c in collections.collections:  # by name already, so each level stays by name
        parent = c.parent_key if c.parent_key in known else None
        children.setdefault(parent, []).append(c)
    by_key: dict[str, Node] = {}

    def build(c: CollectionCount, level: int, above: str) -> Node:
        path = f"{above} / {c.name}" if above else c.name
        node = Node(
            key=c.key,
            name=c.name,
            level=level,
            works=c.works,
            ingested=c.ingested,
            path=path,
            children=[],
            url=address(Selection(collection=c.key), query),
            selected=c.key == selection.collection,
            matches=found[c.key].works if c.key in found else (0 if matches else None),
            exploration=c.exploration,
        )
        by_key[c.key] = node
        if level < MAX_DEPTH:
            node.children = [build(k, level + 1, path) for k in children.get(c.key, [])]
        return node

    roots = [build(c, 1, "") for c in children.get(None, [])]
    built = Tree(
        library=_entry(
            "Whole Library",
            collections.library,
            matches.library if matches else None,
            address(Selection(), query),
            selected=not selection.is_branch,
        ),
        roots=roots,
        unfiled=_entry(
            "Not in a collection",
            collections.unfiled,
            matches.unfiled if matches else None,
            address(Selection(unfiled=True), query),
            selected=selection.unfiled,
        ),
        by_key=by_key,
        searching=matches is not None,
    )
    if selection.collection:
        for node in built.ancestors(selection.collection):
            node.open = True
    return built


def _entry(
    name: str, count: BranchCount, found: BranchCount | None, url: str, *, selected: bool
) -> Entry:
    return Entry(
        name=name,
        works=count.works,
        ingested=count.ingested,
        url=url,
        selected=selected,
        matches=found.works if found else None,
    )


class Crumb(BaseModel):
    """A step of the breadcrumbs, from *Library* down."""

    name: str
    url: str


class WorksSide(BaseModel):
    """What the Works side shows above the rows for the selection."""

    name: str
    crumbs: list[Crumb]  # those above it, each a link
    works: int
    ingested: int
    collection: Node | None  # the Collection selected, if one is
    deep_url: str | None  # the same selection with "Show Works in subcollections" turned over


def works_side(pane: Tree, selection: Selection, query: str = "") -> WorksSide:
    """The breadcrumbs, name, meter and subcollections of what is selected."""
    library = Crumb(name="Library", url=address(Selection(), query))
    node = pane.by_key.get(selection.collection) if selection.collection else None
    if node is not None:
        above = [Crumb(name=n.name, url=n.url) for n in pane.ancestors(node.key)]
        turned = selection.model_copy(update={"deep": not selection.deep})
        return WorksSide(
            name=node.name,
            crumbs=[library, *above],
            works=node.works,
            ingested=node.ingested,
            collection=node,
            deep_url=address(turned, query),
        )
    entry = pane.unfiled if selection.unfiled else pane.library
    return WorksSide(
        name=entry.name,
        crumbs=[library] if selection.unfiled else [],
        works=entry.works,
        ingested=entry.ingested,
        collection=None,
        deep_url=None,
    )
