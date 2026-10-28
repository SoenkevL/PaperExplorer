"""bge-m3's tokenizer, read once from the copy that ships beside this module.

`bge-m3-tokenizer.json.xz` is `tokenizer.json` of BAAI/bge-m3 (MIT licence) at Hugging Face
revision 5617a9f61b028005a4858fdac845db406aefb181, sha256 21106b6d…810dd08 before compression,
xz-compressed to 2.4 MB. It travels with the package, so tests, the Mac and the containers count
tokens offline and exactly as the embedding model reads them.
"""

import lzma
from functools import cache
from importlib import resources
from typing import Protocol, cast

from tokenizers import Tokenizer

_FILE = "bge-m3-tokenizer.json.xz"


class _Encoding(Protocol):
    @property
    def offsets(self) -> list[tuple[int, int]]: ...


class _Tokenizer(Protocol):
    """The part of `tokenizers.Tokenizer` used here; its stubs leave arguments untyped."""

    def encode(self, sequence: str, *, add_special_tokens: bool) -> _Encoding: ...


@cache
def _tokenizer() -> _Tokenizer:
    data = resources.files("paperexplorer.chunking").joinpath(_FILE).read_bytes()
    tokenizer = Tokenizer.from_str(lzma.decompress(data).decode("utf-8"))  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return cast(_Tokenizer, tokenizer)


def token_offsets(text: str) -> list[tuple[int, int]]:
    """`[start, end)` code-point offsets of each bge-m3 token of `text`, without special tokens.

    A token that begins a word covers the space before it.
    """
    return _tokenizer().encode(text, add_special_tokens=False).offsets
