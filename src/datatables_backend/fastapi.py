"""A FastAPI dependency that reads a DataTables request.

@app.post('/users/table')
async def users_table(params: DataTablesParams, session: SessionDep) -> dict[str, Any]:
    return (await users.fetch(params, session)).to_dict()
"""

from __future__ import annotations

from typing import Annotated
from typing import Any

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request

from .protocol import DTParams
from .protocol import MalformedRequest
from .protocol import parse_request

MALFORMED = 'The table request is malformed.'


async def parse_datatables_request(request: Request) -> DTParams:
    """The request's DataTables parameters: a JSON body, a form, or the query string (a GET).

    A request the library cannot read answers 422 with ``{"detail": {"message": ..., "draw": ...}}``: the message
    names a parameter, never a value, and ``draw`` is the request's own, left out when it could not be read.
    """
    data: Any
    content_type = request.headers.get('content-type', '')
    try:
        if content_type.startswith('application/json'):
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError  # answered below, like a body that is not JSON at all
        elif request.method in ('POST', 'PUT', 'PATCH'):
            data = list((await request.form()).multi_items())
        else:
            data = list(request.query_params.multi_items())
        return parse_request(data)
    except MalformedRequest as exc:
        detail: dict[str, Any] = {'message': str(exc)}
        if exc.draw is not None:
            detail['draw'] = exc.draw
        raise HTTPException(status_code=422, detail=detail) from None
    except (ValueError, RecursionError):  # not JSON or not UTF-8; the parser's message would quote the body
        raise HTTPException(status_code=422, detail={'message': MALFORMED}) from None


#: The parsed DataTables request as a handler parameter (``params: DataTablesParams``)
DataTablesParams = Annotated[DTParams, Depends(parse_datatables_request)]
