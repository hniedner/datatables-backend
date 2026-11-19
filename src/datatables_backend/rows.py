"""How a row from the source becomes a row of the response."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

type Row = dict[str, Any]


@dataclass(frozen=True)
class RowHooks:
    """Optional hooks applied to each row, keyed by the columns' client-facing names.

    Args:
        shape: Returns the row the client receives, for example ``lambda row: Model(**row).model_dump(mode='json')``.
        row_id: Returns the row's ``DT_RowId`` (the ``id`` of its ``<tr>``); a falsy result adds nothing.
        row_data: Returns the row's ``DT_RowData`` (jQuery ``data()`` on its ``<tr>``); a falsy result adds nothing.
    """

    shape: Callable[[Row], Row] | None = None
    row_id: Callable[[Row], Any] | None = None
    row_data: Callable[[Row], dict[str, Any] | None] | None = None

    def apply(self, row: Row) -> Row:
        shaped = self.shape(row) if self.shape else row
        if self.row_id and (row_id := self.row_id(shaped)):
            shaped['DT_RowId'] = row_id
        if self.row_data and (row_data := self.row_data(shaped)):
            shaped['DT_RowData'] = row_data
        return shaped
