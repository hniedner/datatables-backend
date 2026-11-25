# Example: FastAPI on SQLite with a DataTables page

A small application that answers a DataTables page with this library, on invented data. It uses the public API only
and the patterns of the project README. Nothing in `examples/` ships in the wheel or the sdist.

| File | What it is |
|---|---|
| `example_app.py` | The FastAPI application: 120 seeded rows in an in-memory SQLite database, `GET /` for the page, `POST /items/table` for DataTables |
| `index.html` | The page: DataTables 3.1.6 and ColumnControl 2.1.3 from jsDelivr, server-side processing, the global search, a column filter (the colour select) and a ColumnControl list on `status`, and multi-column ordering by Shift+click with each sorted column's place shown beside its arrow and spoken to a screen reader |
| `test_example_app.py` | Starts the application with FastAPI's `TestClient` and replays the requests the page sends |
| `page_requests.json` | Four requests as DataTables sent them, captured from `index.html` in jsdom (the fourth after a click on Colour and two Shift+clicks on Size), which the test pins its builder to |

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
The fourth request comes from a click on Colour and two Shift+clicks on Size.

## Check the page by hand

1. Run it as above, open the page with the browser's developer tools (Console and Network) open.
2. On load: "Showing 1 to 10 of 120 entries", no error in the Console (no integrity, CORS or "DataTable is not defined"
   message), and a `POST /items/table?_=...` with a form payload in the Network tab.
3. Type `amber` in Search: the count drops to 12. Choose the colour `red`: it drops again. Open the Status dropdown, which
   lists Open and Closed; tick Open: only open rows remain; untick it.
4. Type seventeen one-letter words in Search: a message appears under "Items" (`#items-error`) and no `alert()` opens.
5. Sort by several columns. Click Colour: the rows are blue first, with no number by the arrows. Shift+click Size: a 1
   by Colour and a 2 by Size, one accent-coloured arrow each, and the request holds `order[1][column]=3`. Shift+click
   Size again: its arrow points down, the first rows read sizes 99, 95, 91 and the ids 3 and 103 follow each other.
   A third Shift+click on Size removes it, and both numbers go. Tab to Size's arrows and press Shift+Enter: it comes
   back as 2. A click on Label, without Shift, sorts by Label alone.

## Sorting by several columns

DataTables adds a column to the order on Shift+click (or Shift+Enter on its arrows), cycles it ascending, descending,
removed, and sends one `order[i]` entry per column. The page starts sorted by Id, which is unique, so a Shift+click on
top of that initial order changes no row: the rank numbers show why (Id is 1, the column you added is 2). Click a
header without Shift to start again from that column.

The numbers beside the arrows are drawn by the page, for the eyes only; each header also carries a visually hidden
text, as its tooltip and as the `aria-describedby` of its arrows, that says its state and place ("sorted ascending,
priority 2. Shift+click or Shift+Enter adds this column to the sort."). DataTables itself sets `aria-sort` on the first
sorted column only. The page's own script is not run by `uv run pytest` (no browser and no node in CI): the capture was
checked in jsdom, and step 5 above is the check by hand.

ColumnControl's own `order` button needs `ordering: { handler: false }` on the table (its documentation says so):
with DataTables' header click still on, one click on the button orders twice. This page keeps the header click.
