---
name: office-xlsx
description: Read and write Excel (.xlsx) via LocalWiki office_io; save under .kw/exports.
---

# Office XLSX

## Export

Write with `localwiki.office_io.write_xlsx(rows)` then `localwiki.exports.save_bytes(workspace, "table.xlsx", data)`.
Reply with `/api/exports/table.xlsx` only (no external links).

## Read

`localwiki.office_io.read_xlsx(path) -> list[list[str]]`
