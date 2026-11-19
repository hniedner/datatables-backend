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

    A request the library cannot read answers 422; the detail names a parameter, never a value.
    """
    data: Any
    content_type = request.headers.get('content-type', '')
    try:
        if content_type.startswith('application/json'):
            data = await request.json()
            if not isinstance(data, dict):
                raise MalformedRequest(MALFORMED)
        elif request.method in ('POST', 'PUT', 'PATCH'):
            data = list((await request.form()).multi_items())
        else:
            data = list(request.query_params.multi_items())
        return parse_request(data)
    except MalformedRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except (ValueError, RecursionError):  # not JSON or not UTF-8; the parser's message would quote the body
        raise HTTPException(status_code=422, detail=MALFORMED) from None


#: The parsed DataTables request as a handler parameter (``params: DataTablesParams``)
DataTablesParams = Annotated[DTParams, Depends(parse_datatables_request)]
