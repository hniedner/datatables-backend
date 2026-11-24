# 007: What ColumnControl sends

This is the record behind issue #106, step 1: the request fields ColumnControl adds to a server-side request, and what
the library does with each today. The requirements are in `docs/PRD.md` (PRD-P5 refuses ColumnControl's filters), the
statements in `spec/parsing.md`. Nothing here is a requirement, and what the library should do instead is in the issue.

Every fact carries a label. RUN: a command was run and printed it. READ: it is in the downloaded source and was not
run. Where the two disagreed, the entry says so.

## How it was found

- Versions as of 2026-11-24: datatables.net-columncontrol 2.1.3 and datatables.net 3.1.5, both the registry's latest
  (`npm view <package> version`). Each tarball came from `npm pack --ignore-scripts` into its own empty directory, and
  its integrity equals `npm view <package>@<version> dist.integrity` [RUN].
- The client was run, without a browser: node v26.13.0 with jsdom 31.0.1, loading `js/dataTables.js` and
  `js/dataTables.columnControl.js` (the UMD files of those two releases). The script replaced `XMLHttpRequest` with a
  recorder that keeps the method, the Content-Type and the body, and answers `{draw, recordsTotal: 0,
  recordsFiltered: 0, data: []}`. It drove the controls the way a user does: it set the input's value and dispatched
  `input`, set the logic `<select>` and dispatched `input` (the control listens to `input`, not `change`), and clicked
  the list's buttons. Each shape ran on a fresh table, as a default form POST and as `ajax.submitAs: 'json'`. The only
  stubbed browser globals were `ResizeObserver` and `IntersectionObserver`; jsdom has no layout.
- Not run, so READ only: the dates of moment and luxon, `excludeLogic`, the options of a control, and a real browser.
- Line numbers below are `js/dataTables.columnControl.js` of 2.1.3.

## Which control a column gets

The control is chosen by the `columnControl` option of the table, not by the column's data: `searchText`,
`searchNumber`, `searchDateTime`, `searchList` (the registry at line 2760) [READ]. The run configured one control per
table and a column with none sent no `columnControl` key at all [RUN].

## The search object

Each of the three search controls adds `columns[i].columnControl.search = {value, logic, type}` at the time the request
is made (lines 1790-1805) [READ]. The run printed every row of the table below, in both encodings [RUN].

| Control | `type` | Logics sent | `value` | Extra |
|---|---|---|---|---|
| text | `text` | contains, notContains, equal, notEqual, starts, ends, empty, notEmpty | the input as typed | none |
| number | `num` | equal, notEqual, greater, greaterOrEqual, less, lessOrEqual, empty, notEmpty | as typed | none |
| date | `date` | equal, notEqual, greater, less, empty, notEmpty | `/` becomes `-` | `mask` |

- The first logic of each control is its default: `contains`, `equal`, `equal` [RUN].
- Idle: an empty `value` with a logic that is not `empty` or `notEmpty`. The control's own test of "active" is the same
  (line 1617) [READ]. Every table with a control sends its idle search on every request, the first draw included [RUN].
- `empty` and `notEmpty` send the text left in the input: `ann` typed, then `empty` chosen, goes out as value `ann` with
  logic `empty` [RUN]. A user who chose `starts` and typed nothing sends an empty value with logic `starts` [RUN].
- The `type` and `mask` fields are not a choice of the user; they name the control, and `mask` is `''` [RUN].

## The list

- `columns[i].columnControl.list` is an object with index keys, `{}` when nothing is ticked: `{"0":"a","1":"b"}` in
  JSON, `columns[0][columnControl][list][0]=a&...[list][1]=b` in a form (line 2343) [RUN]. An empty `{}` adds nothing
  to a form [RUN].
- A value is not turned into text: an option whose value is the number 1 arrives as `{"0":1}` in JSON and as `=1` in a
  form [RUN].
- Under server-side processing the control shows only when the answer carries `columnControl: {<column name>: [{label,
  value}]}`; without it the control stays hidden (`ajaxOnly`, lines 2184 and 2254) [READ]. The run's answers carried it
  and the buttons appeared.
- A column with a text control and a list sends `search` and `list` together [RUN].
- The option whose value is the empty text shows a button labelled "Empty". Ticked, it arrives as `{"0":""}` in JSON and
  as `columns[0][columnControl][list][0]=` in a form, which is an empty value at index 0 [RUN]. (A first run looked for
  the button by an empty label and missed it; the capture was run again, with the buttons picked by position.)

## Both encodings

The two carry the same content. The form is the `[a][b][c]` nesting, the JSON the same objects [RUN]:

- Form: `columns[0][columnControl][search][value]=ann&columns[0][columnControl][search][logic]=contains&columns[0]`
  `[columnControl][search][type]=text`.
- JSON: `"columnControl":{"search":{"value":"ann","logic":"contains","type":"text"}}`.

In `submitAs: 'json'` the body is JSON and the Content-Type is `application/x-www-form-urlencoded; charset=UTF-8`
[RUN]. READ said nothing about the header; a server that picks its parser by Content-Type reads the body as a form.

## The `ajax.data` pitfall

With `ajax: {url, type: 'POST', data: d => JSON.stringify(d)}`, ColumnControl's fields never leave the browser: its
hooks run on `preXhr` after `ajax.data`, find a string where they expect `d.columns`, and return (lines 1790 and 2334)
[READ]. The run sent `{"draw":1,"columns":[{"data":"a",...,"search":{...}}],...}` as the body with a form Content-Type,
and no `columnControl` key, on the first draw and on `contains ann` [RUN]. `ajax: {submitAs: 'json'}` is the way that
works with ColumnControl.

## What main does today

The 82 captured requests held 79 distinct bodies. Each was read by `parse_request`, with `length` set to 10 (the
capture ran with paging off, which sends `length=-1`; the library refuses that first, so it hid the ColumnControl part)
[RUN]:

| Shape | Form | JSON |
|---|---|---|
| idle search (any control), idle list `{}` | read | read |
| `starts` or `contains` with an empty value (idle) | read | read |
| a search with a value, any logic | `MalformedRequest: Invalid columns parameter.` | the same |
| `empty` or `notEmpty` (any control, with the text left in the input) | the same | the same |
| a list with a value | the same | the same |
| the `ajax.data` string | `MalformedRequest: The table request holds no DataTables parameter: ...` | (no JSON body) |

The refusal is `ColumnControlSearch._idle` and the `list` validator of `protocol.py` (lines 140-160), so the cause is
named only as "columns". The `type` and `mask` fields are ignored: the idle date shape is read [RUN].

## What follows

Which shapes the library answers, how a value maps to a filter, and the open questions on dates with a zone, NULL and
a list option's type are in #106.
