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
- The options of a list (#167) were run later, on 2026-11-24, with the harness under "The list's options under
  server-side processing": node v26.13.1, jsdom 31.0.1, datatables.net 3.1.5 and 3.1.6 and datatables.net-columncontrol
  2.1.3. The three tarballs came from `npm pack --ignore-scripts` and their integrity equals
  `npm view <package>@<version> dist.integrity`; jsdom came from `npm install jsdom@31.0.1 --ignore-scripts` into an empty scratch directory (no
  lifecycle script ran). The first harness run failed before any case: DataTables asks for the page's base URL, so the
  document needs `url: 'http://localhost/'`, which the listing has [RUN].
- Not run, so READ only: the dates of moment and luxon, `excludeLogic`, and a real browser.
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
- Under server-side processing the options of a list come from a static `options` on the control or from the answer's
  `columnControl`; the run of #167 (below) shows when the buttons appear. An earlier reading here, that without the
  answer's `columnControl` the control stays hidden, was wrong for a list with static `options` and for a list with no
  options at all [RUN].
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

## What 0.1.0 did

The 82 captured requests held 79 distinct bodies. Each was read by `parse_request` of 0.1.0, with `length` set to 10
(the capture ran with paging off, which sends `length=-1`; the library refuses that first, so it hid the ColumnControl
part) [RUN]:

| Shape | Form | JSON |
|---|---|---|
| idle search (any control), idle list `{}` | read | read |
| `starts` or `contains` with an empty value (idle) | read | read |
| a search with a value, any logic | `MalformedRequest: Invalid columns parameter.` | the same |
| `empty` or `notEmpty` (any control, with the text left in the input) | the same | the same |
| a list with a value | the same | the same |
| the `ajax.data` string | `MalformedRequest: The table request holds no DataTables parameter: ...` | (no JSON body) |

The refusal was `ColumnControlSearch._idle` and the `list` validator of `protocol.py`, so the cause was named only as
"columns". The `type` and `mask` fields were ignored: the idle date shape was read [RUN].

## What parsing does now

PRD-F45 reads the shapes above (#163), as JSON and as a form: a search is a value and a logic of the closed set (the
control's eight logics, and the number control's four), a list is the index-keyed object or an array of text and whole
numbers read as text. `type` is read only to refuse the date control; `mask` and any other field are ignored. An idle
search (an empty value with any logic but `empty` and `notEmpty`, or with none) and an empty list add nothing. Five
shapes are refused, each with a message of its own that says what to change: a logic outside the set, a value with no
logic, an active search with `type` `date`, a list entry that is neither text nor a whole number, and a list that is
neither an array nor an object keyed by whole-number indexes. The object's keys are sorted, never used as positions, so
a key of nine digits costs nothing. What the answered shapes do to a query is in #164.

## The list's options under server-side processing (#167)

The question: after the first answer, does a list (`searchList`) show its buttons when the server answers `{draw,
recordsTotal, recordsFiltered, data}` with no `columnControl` key? Each answer below is read from the DOM of the run
(the dropdown button, the list and its buttons, after a real click on the dropdown), after the first answer and again
after a second draw. Every request carried `columnControl: {list: {}}`, so the control was live [RUN].

| Case | `options` | `ajaxOnly` | `hidable` | Answer's `columnControl` | Dropdown button | List | Buttons after the 1st answer | After the 2nd | 3.1.6 the same |
|---|---|---|---|---|---|---|---|---|---|
| 1 | static | default | default | none | shown | shown | Open, Closed | Open, Closed | yes |
| 2 | static | false | default | none | shown | shown | Open, Closed | Open, Closed | yes |
| 3 | static | default | false | none | shown | shown | Open, Closed | Open, Closed | yes |
| 4 (the control) | none | default | default | `{status: [Server A, Server B]}` | shown | shown | Server A, Server B | Server A, Server B | yes |
| C1 | none | default | default | `{}` | hidden | hidden | none | none | yes |
| C2 | none | default | default | none | shown | shown | none, and a "No options" note | the same | yes |
| C3 | static | default | default | `{status: [Server A, Server B]}` | shown | shown | Open, Closed | Open, Closed | yes |

- A list with static `options` shows them after every answer, whether or not the answer has a `columnControl` key, and
  `ajaxOnly: false` and `hidable: false` change nothing for it (cases 1-3) [RUN]. The answer's options are ignored when
  `options` is static (C3) [RUN].
- Options from the answer reach the buttons when the answer's `columnControl` holds an entry for the column (case 4)
  [RUN]; the harness sees buttons, and it sees hiding (C1), so the three static cases are not a blind spot.
- The list hides itself (and its dropdown button) when the answer has a `columnControl` key with no entry for the
  column, as `{}` (C1) [RUN]. With no `columnControl` key and no static options it stays visible and empty, with a "No
  options" note (C2) [RUN].
- Why, from the source [READ, `js/dataTables.columnControl.js` of 2.1.3]: when `config.options` is set the control calls
  `setOptions` once at init, and the `dt.ready` and `xhr` listeners that reload options from the answer are never
  registered (lines 2313-2328). `ajaxOnly` and `hidable` are read only in the reload (lines 2213-2222): a
  `columnControl` in the answer with no entry for the column, `ajaxOnly` and `hidable` hide the list; with no answer key
  under server-side processing the reload sets an empty list (lines 2226 and 2245).
- DataTables' documentation says `columnControl` must be in the Ajax data object, even as an empty object, for `hidable`
  (https://datatables.net/reference/content/searchList, fetched 2026-11-24) [FETCHED]. The answer's options are matched
  by column `name`, then `data`, then position (lines 2184-2203) [READ].
- The library's response model drops keys it does not declare (`extra='ignore'`, `protocol.py:117`), so an application
  that wants the answer's `columnControl` adds the key to the dumped answer itself [READ].

The harness, as run (`node run167.cjs <jsdom package dir> <datatables.net package dir>
<datatables.net-columncontrol package dir>`, from outside every package directory). Its output is one JSON line per case
and the run is void if jsdom printed anything on stderr, if case 4 showed no buttons, if C1 was not hidden, or if a
request lacked `list: {}`; none of these happened:

```js
'use strict';
// usage: node run167.cjs <jsdomPkgDir> <dataTablesPkgDir> <columnControlPkgDir>
const fs = require('node:fs');
const path = require('node:path');
const [jsdomDir, dtDir, ccDir] = process.argv.slice(2);
const { JSDOM } = require(jsdomDir);
const ver = d => JSON.parse(fs.readFileSync(path.join(d, 'package.json'), 'utf8')).version;
const DT_SRC = fs.readFileSync(path.join(dtDir, 'js/dataTables.js'), 'utf8');
const CC_SRC = fs.readFileSync(path.join(ccDir, 'js/dataTables.columnControl.js'), 'utf8');

const ROWS = [{ status: 'open' }, { status: 'closed' }];
const OPTS = [{ label: 'Open', value: 'open' }, { label: 'Closed', value: 'closed' }];
const SERVER = { status: [{ label: 'Server A', value: 'a' }, { label: 'Server B', value: 'b' }] };
const CASES = [
  { id: '1 static', list: { extend: 'searchList', options: OPTS }, answer: null },
  { id: '2 static+ajaxOnly:false', list: { extend: 'searchList', options: OPTS, ajaxOnly: false }, answer: null },
  { id: '3 static+hidable:false', list: { extend: 'searchList', options: OPTS, hidable: false }, answer: null },
  { id: '4 server key', list: { extend: 'searchList' }, answer: SERVER },
  { id: 'C1 key, no entry', list: { extend: 'searchList' }, answer: {} },
  { id: 'C2 no options anywhere', list: { extend: 'searchList' }, answer: null },
  { id: 'C3 static+server key', list: { extend: 'searchList', options: OPTS }, answer: SERVER },
];

const tick = () => new Promise(r => setTimeout(r, 0));

function hidden(el) {
  const win = el.ownerDocument.defaultView;
  for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
    if (win.getComputedStyle(e).display === 'none') return true;
  }
  return false;
}

function snap(win) {
  const doc = win.document;
  const dd = doc.querySelector('thead div.dt-column-header span.dtcc > button.dtcc-button_dropdown');
  if (!dd) return { dropdownButton: 'absent', verdict: 'NO BUTTONS' };
  const dropdownButton = hidden(dd) ? 'hidden' : 'shown';
  dd.click();
  const pop = doc.querySelector('div.dtcc-dropdown');
  const list = pop && pop.querySelector('div.dtcc-list');
  const btns = list ? [...list.querySelectorAll('div.dtcc-list-buttons > button.dtcc-button')] : [];
  const empty = list && list.querySelector('div.dtcc-list-empty');
  const out = {
    dropdownButton,
    opened: !!pop,
    list: !list ? 'absent' : hidden(list) ? 'hidden' : 'shown',
    labels: btns.map(b => b.querySelector('span.dtcc-button-text').textContent),
    visibleButtons: btns.filter(b => !hidden(b)).length,
    noOptionsNote: !!empty && !hidden(empty),
  };
  doc.body.click();
  out.closedAfter = !doc.querySelector('div.dtcc-dropdown');
  out.verdict = dropdownButton === 'shown' && out.list === 'shown' && out.visibleButtons > 0 ? 'BUTTONS SHOWN' : 'NO BUTTONS';
  return out;
}

async function runCase(c) {
  const dom = new JSDOM('<!doctype html><html><body><table id="t"><thead><tr><th>Status</th></tr></thead>'
    + '<tbody></tbody></table></body></html>', { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
  const win = dom.window;
  const requests = [];
  class FakeXHR {
    constructor() { this.readyState = 0; this.status = 0; this.statusText = ''; this.responseText = ''; this.headers = {}; }
    open(method, url) { this.method = method; this.url = url; this.readyState = 1; }
    setRequestHeader(k, v) { this.headers[k] = v; }
    abort() {}
    send(body) {
      const d = JSON.parse(body);
      requests.push(d);
      const answer = { draw: d.draw, recordsTotal: ROWS.length, recordsFiltered: ROWS.length, data: ROWS };
      if (c.answer !== null) answer.columnControl = c.answer;
      setTimeout(() => {
        this.readyState = 4; this.status = 200; this.statusText = 'OK';
        this.responseText = JSON.stringify(answer);
        this.onreadystatechange();
      }, 0);
    }
  }
  win.XMLHttpRequest = FakeXHR;
  win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  win.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
  win.eval(DT_SRC);
  win.eval(CC_SRC);
  // Build the config inside the window realm: DataTables' plainObject() compares prototypes.
  const cfg = win.eval('(' + JSON.stringify({
    serverSide: true,
    ajax: { url: '/data', type: 'POST', submitAs: 'json' },
    columns: [{ data: 'status', name: 'status', title: 'Status', columnControl: [[c.list]] }],
  }) + ')');
  const init = new Promise(r => { cfg.initComplete = () => r(); });
  const dt = new win.DataTable('#t', cfg);
  await init; await tick(); await tick();
  const first = snap(win);
  const drawn = new Promise(r => dt.one('draw', () => r()));
  dt.draw(); await drawn; await tick(); await tick();
  const second = snap(win);
  const sent = requests.map(d => d.columns[0].columnControl);
  win.close();
  return { id: c.id, requests: requests.length, first, second, sent };
}

setTimeout(() => { console.error('TIMEOUT'); process.exit(2); }, 20000).unref();
(async () => {
  console.log(JSON.stringify({ node: process.version, jsdom: ver(jsdomDir), datatables: ver(dtDir), columncontrol: ver(ccDir) }));
  for (const c of CASES) console.log(JSON.stringify(await runCase(c)));
})().catch(e => { console.error(e); process.exit(1); });
```

## What follows

How a value maps to a filter, and the open questions on dates with a zone, NULL and a list option's type, are in #106
and its follow-ups. What the library says about the list's options is in #167.
