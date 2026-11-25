# Example: FastAPI on SQLite with a DataTables page

A small application that answers a DataTables page with this library, on invented data. It uses the public API only
and the patterns of the project README. Nothing in `examples/` ships in the wheel or the sdist.

| File | What it is |
|---|---|
| `example_app.py` | The FastAPI application: 120 seeded rows in an in-memory SQLite database, `GET /` for the page, `POST /items/table` for DataTables |
| `index.html` | The page: DataTables 3.1.6 and ColumnControl 2.1.3 from jsDelivr, server-side processing, the global search, a column filter (the colour select) and a ColumnControl list on `status` |
| `test_example_app.py` | Starts the application with FastAPI's `TestClient` and replays the requests the page sends |
| `page_requests.json` | Three requests as DataTables sent them, captured from `index.html` in jsdom, which the test pins its builder to |

## Run it

From the repository root:

```sh
uv sync
uv run --with uvicorn uvicorn example_app:app --app-dir examples
```

Then open <http://127.0.0.1:8000/>. The page loads its scripts from a CDN, so it needs the network.

`--with uvicorn` installs the server into a temporary environment: no ASGI server is in `pyproject.toml` or the
lockfile, and this changes neither. Running the page is **not** run in CI (there is no browser there); `uv run pytest`
runs `test_example_app.py` with the other tests.

## The CDN files

Pinned, from jsDelivr, each with the SRI hash of the file the npm package holds (`integrity` in `index.html`, FETCHED
and hashed 2026-11-24; each file equals the member of the npm tarball, whose integrity equals the registry's):

| Package | File |
|---|---|
| `datatables.net@3.1.6` | `js/dataTables.js` |
| `datatables.net-columncontrol@2.1.3` | `js/dataTables.columnControl.js` |
| `datatables.net-dt@3.1.6` | `css/dataTables.dataTables.css` |
| `datatables.net-columncontrol-dt@2.1.3` | `css/columnControl.dataTables.css` |

No jQuery is needed. To move to another version, fetch the four files, compute `openssl dgst -sha384 -binary FILE |
openssl base64 -A`, change the URLs and hashes, and renew `page_requests.json` (the test says if the wire form moved).

## Check the page by hand

1. Run it as above, open the page with the browser's developer tools (Console and Network) open.
2. On load: "Showing 1 to 10 of 120 entries", no error in the Console (no integrity, CORS or "DataTable is not defined"
   message), and a `POST /items/table?_=...` with a form payload in the Network tab.
3. Type `amber` in Search: the count drops to 12. Choose the colour `red`: it drops again. Open the Status dropdown, which
   lists Open and Closed; tick Open: only open rows remain; untick it.
4. Type seventeen one-letter words in Search: a message appears under "Items" (`#items-error`) and no `alert()` opens.
