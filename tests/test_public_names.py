"""The names a module offers: ``from module import *`` gives an adopter exactly the approved list, and no helper."""

import importlib

import pytest

APPROVED = {
    'datatables_backend': {
        'Column',
        'DTParams',
        'DTResponse',
        'parse_request',
        'RowHooks',
        'TotalCache',
        'TotalStore',
        'AsyncTotalStore',
        'SchemaError',
        'MalformedRequest',
        'PartialResultError',
        'ColumnType',
        'DTColumn',
        'DTOrder',
        'Search',
        'SearchCriterion',
        'SearchGroup',
        'ErrorCode',
        'MAX_PAGE_LENGTH',
    },
    'datatables_backend.sqlalchemy': {'SQLTable'},
    'datatables_backend.opensearch': {'SearchTable', 'IndexColumn', 'TextKind', 'NumberKind', 'partial_answer'},
    'datatables_backend.fastapi': {'DataTablesParams', 'parse_datatables_request'},
}


@pytest.mark.parametrize('module', APPROVED)
def test_a_module_offers_exactly_its_approved_names(module: str):
    """A name added or dropped by accident is a change to the public API that nobody decided."""
    assert set(importlib.import_module(module).__all__) == APPROVED[module]


@pytest.mark.parametrize('module', APPROVED)
def test_every_offered_name_exists(module: str):
    """A name in ``__all__`` that the module lacks breaks ``from module import *`` for every adopter."""
    namespace: dict[str, object] = {}
    exec(f'from {module} import *', namespace)  # noqa: S102
    assert set(namespace) - {'__builtins__'} == APPROVED[module]


def test_the_version_is_reachable_but_not_offered():
    package = importlib.import_module('datatables_backend')
    assert isinstance(package.__version__, str)
    assert '__version__' not in package.__all__
