"""The server's declaration of a table's columns."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import KW_ONLY
from dataclasses import dataclass
from typing import Any
from typing import Literal

type ColumnType = Literal['string', 'number', 'date', 'boolean']


@dataclass(frozen=True)
class Column:
    """A column the client may see, search, filter and sort on.

    Args:
        name: The name the client uses (``columns[i].name``, else ``columns[i].data``, and SearchBuilder ``origData``).
        field: The source's name for it (a SQL column or an index field), when it differs from ``name``.
        type: How its filters are built: ``'string'``, ``'number'``, ``'date'`` or ``'boolean'``. The client's
            SearchBuilder type is never used. ``None`` lets the backend decide: SQL reads the column's SQL type (a
            MySQL ``TINYINT(1)`` read by reflection is a number, so declare it ``'boolean'``), OpenSearch assumes a
            string, so a field with a boolean mapping is declared ``'boolean'``.
        searchable: Whether the global search and column searches may use it.
        orderable: Whether the client may sort on it. ``None`` lets the backend decide: yes, except for an OpenSearch
            string column that is analysed text only, which has no whole string to sort by (declaring that one
            orderable raises ``SchemaError``).
        search_value: Transforms a string the user searches this column for before it reaches the query, for example
            to encrypt or encode it the way the source stores it. The column must be a string (PRD-D14, else
            ``SchemaError``), and its searches compare the whole transformed value by equality (PRD-D15 to PRD-D18).
        fold_case: Whether an exact match on a string column (SearchBuilder's ``=`` and ``!=``, a multi-select, a
            ``multi_values`` list) ignores case. ``False`` sends a plain comparison, which a plain index on the
            column can serve and which matches case as the column's collation or the field's mapping decides.
            Searches ignore case either way, and a column of another type does not read it. In SQL only a column
            whose SQLAlchemy type is a string type or an ``Enum`` is folded, whatever this says: a column with a type
            of its own (a ``TypeDecorator``, ``Uuid``) compares as that type compares, and an expression without a
            type is compared as sent.
    """

    name: str
    _: KW_ONLY
    field: str | None = None
    type: ColumnType | None = None
    searchable: bool = True
    orderable: bool | None = None
    search_value: Callable[[str], Any] | None = None
    fold_case: bool = True

    @property
    def source(self) -> str:
        """The source's name for the column."""
        return self.field or self.name
