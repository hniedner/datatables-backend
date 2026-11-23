"""Server-side processing for DataTables from SQL (SQLAlchemy 2) and OpenSearch.

The core needs only Pydantic. The backends are imported from their own modules, so each needs only its own extra:
``datatables_backend.sqlalchemy`` (``[sqlalchemy]``), ``datatables_backend.opensearch`` (no client library is
imported; ``[opensearch]`` installs one) and ``datatables_backend.fastapi`` (``[fastapi]``).
"""

__version__ = '0.1.0rc2'

from .cache import TotalCache
from .columns import Column
from .columns import ColumnType
from .protocol import DTColumn
from .protocol import DTOrder
from .protocol import DTParams
from .protocol import DTResponse
from .protocol import InvalidFilter
from .protocol import MalformedRequest
from .protocol import PartialResultError
from .protocol import SchemaError
from .protocol import Search
from .protocol import SearchCriterion
from .protocol import SearchGroup
from .protocol import parse_request
from .rows import RowHooks

__all__ = [
    'Column',
    'ColumnType',
    'DTColumn',
    'DTOrder',
    'DTParams',
    'DTResponse',
    'InvalidFilter',
    'MalformedRequest',
    'PartialResultError',
    'RowHooks',
    'SchemaError',
    'Search',
    'SearchCriterion',
    'SearchGroup',
    'TotalCache',
    'parse_request',
]
