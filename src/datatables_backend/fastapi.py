"""A FastAPI dependency that reads a DataTables request.

@app.post('/users/table')
async def users_table(params: DataTablesParams, session: SessionDep) -> dict[str, Any]:
    return (await users.fetch(params, session)).to_dict()
"""

from __future__ import annotations

import json
from typing import Annotated
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request

from .protocol import DTParams
from .protocol import MalformedRequest
from .protocol import parse_request

MALFORMED = 'The table request is malformed: send it as a JSON object or as a form.'
#: The most fields Starlette reads from a form, which a form read here without its parser is held to as well
FORM_FIELD_LIMIT = 1000
#: The media types whose body Starlette's own form parser reads
FORM_TYPES = ('application/x-www-form-urlencoded', 'multipart/form-data')


async def parse_datatables_request(request: Request) -> DTParams:
    """The request's DataTables parameters: from the body when there is one, otherwise from the query string; the two
    are never merged (PRD-A9). A body that decodes to a JSON object is read as JSON whatever its Content-Type, since
    DataTables' ``submitAs: 'json'`` keeps the form's; any other body is read as a form.

    A request the library cannot read answers 422 with ``{"detail": {"message": ..., "draw": ...}}``: the message
    names a parameter, never a value, and ``draw`` is the request's own, left out when it could not be read.
    """
    try:
        body = await request.body()
        if not body.strip():
            data: Any = list(request.query_params.multi_items())
        else:
            data = _json_object(body)
            if data is None:
                data = await _form(request, body)
        return parse_request(data)
    except MalformedRequest as exc:
        detail: dict[str, Any] = {'message': str(exc)}
        if exc.draw is not None:
            detail['draw'] = exc.draw
        raise HTTPException(status_code=422, detail=detail) from None
    except (ValueError, RecursionError):  # not UTF-8, or too many fields; the parser's message would quote the body
        raise HTTPException(status_code=422, detail={'message': MALFORMED}) from None


def _json_object(body: bytes) -> dict[str, Any] | None:
    """``body`` as a JSON object, or None for anything else: text, a list, and a body nested too deep to decode."""
    if not body.lstrip().startswith(b'{'):
        return None
    try:
        decoded = json.loads(body)
    except (ValueError, RecursionError):  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        return None
    return decoded if isinstance(decoded, dict) else None


async def _form(request: Request, body: bytes) -> list[tuple[str, Any]]:
    """``body`` as the form it is: through Starlette's parser under a form media type, as urlencoded text under any
    other."""
    media_type = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if media_type in FORM_TYPES:
        return list((await request.form()).multi_items())
    return parse_qsl(body.decode(), keep_blank_values=True, max_num_fields=FORM_FIELD_LIMIT)


#: The parsed DataTables request as a handler parameter (``params: DataTablesParams``)
DataTablesParams = Annotated[DTParams, Depends(parse_datatables_request)]
