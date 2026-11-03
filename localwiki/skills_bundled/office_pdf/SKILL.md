---
name: office-pdf
description: Read and write PDF via LocalWiki office_io (pypdf read / reportlab write). Prefer tool export_pdf for downloads.
---

# Office PDF

## Read

`localwiki.office_io.read_pdf(path) -> str`

## Write (download)

Prefer OpenCode tool **export_pdf**.

Or CLI:

```text
python -m localwiki.tools.oc_export_cli pdf --title "标题" --body-file body.txt --stem export
```

Python API: `localwiki.office_io.write_pdf(title=..., paragraphs=[...]) -> bytes` then save under `.kw/exports` with a unique name and reply `/api/exports/<name>.pdf`.

Do not tell the user to install Python; use the work venv / `LOCALWIKI_PYTHON`.
