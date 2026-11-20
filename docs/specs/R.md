# Specs: rows and the response (R)

Statements for PRD section 4.7. Form, tags, answers and the `items` fixture: see `README.md`.

- **SPEC-R1.1** [all] Each row in `data` is a dict whose keys are the declared client names. With the stored field
  renamed `colour_code` and the column declared `Column('colour', field='colour_code')`, the rows carry the key
  `colour` and never `colour_code`.
- **SPEC-R2.1** [all] With `RowHooks(shape=...)` mapping each row through a Pydantic model that adds a field
  `label`, every row in `data` carries `label`.
- **SPEC-R3.1** [all] With a row-id hook returning `row_<id>`, row 1 carries `DT_RowId` `row_1`; without the hook no
  row has `DT_RowId`.
- **SPEC-R4.1** [all] With a row-data hook returning `{"pk": id}`, row 1 carries `DT_RowData` `{"pk": 1}`; without
  the hook no row has `DT_RowData`.
- **SPEC-R5.1** [core] `DTResponse.model_fields` holds exactly `draw`, `recordsTotal`, `recordsFiltered`, `data`,
  `error` and `error_code`.
- **SPEC-R5.2** [all] `to_dict()` of a successful answer gives exactly the keys `draw`, `recordsTotal`,
  `recordsFiltered`, `data`; of an answered user error, those plus exactly `error` and `error_code`.
- **SPEC-R6.1** [all] `draw` 41 in the request → `draw` 41 in the answer, for rows and for a user error alike.
- **SPEC-R7.1** [SQL] Given a fifth row whose `note` is `<b>x</b> & "y"`: its `note` comes back as that text
  unchanged (no HTML escaping); row 1's NULL `note` is `None`, `made` a `datetime.date`, `price` an `int`.
- **SPEC-R7.2** [OS] Given a fifth document whose `note` is `<b>x</b> & "y"`: its `note` comes back unchanged; each
  row carries every declared column with its value as `_source` holds it, so `made` is the stored string
  `2024-03-05`, and row 1's missing `note` is the key `note` with `None`.
