# Specs: rows and the response (R)

Statements for PRD section 4.7. Form, backends, answers and the `items` fixture: see `README.md`.

- **SPEC-R1.1** [all] Each row in `data` is a dict whose keys are exactly the declared client names; with `colour`
  declared over field `colour_code`, the key is `colour`.
- **SPEC-R2.1** [all] With `RowHooks(shape=...)` mapping each row through a Pydantic model that adds a field
  `label`, every row in `data` carries `label`, and the hook is called once per row on the page.
- **SPEC-R3.1** [all] With a row-id hook returning `row_<id>`, row 1 carries `DT_RowId` `row_1`; without the hook no
  row has `DT_RowId`.
- **SPEC-R4.1** [all] With a row-data hook returning `{"pk": id}`, row 1 carries `DT_RowData` `{"pk": 1}`; without
  the hook no row has `DT_RowData`.
- **SPEC-R5.1** [all] A successful answer serialises to exactly the keys `draw`, `recordsTotal`, `recordsFiltered`,
  `data`; an answered user error adds exactly `error` and `error_code`.
- **SPEC-R6.1** [all] `draw` 41 in the request → `draw` 41 in the answer, for rows and for a user error alike.
- **SPEC-R7.1** [all] Given a row whose `note` is `<b>x</b> & "y"`, the answer's `note` is that text unchanged (no
  HTML escaping); a NULL is `None`, a date a `date`, a number a number, as the backend reads them.
