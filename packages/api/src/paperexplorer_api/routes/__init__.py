"""Route groups; each is built around a way to get a store connection."""

from collections.abc import Callable
from contextlib import AbstractContextManager

import psycopg
from psycopg.rows import TupleRow

type Conn = psycopg.Connection[TupleRow]
type Connections = Callable[[], AbstractContextManager[psycopg.Connection[TupleRow]]]
