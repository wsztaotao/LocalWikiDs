---
name: office-docx
description: Read and write Microsoft Word (.docx) via LocalWiki office_io; save exports under .kw/exports for download.
---

# Office DOCX

## When to use

User asks to create, edit, or export a Word document.

## How

1. Prefer Python in the workspace venv:
   ```text
   python -c "from localwiki.office_io import write_docx; from localwiki.exports import save_bytes, exports_dir; ..."
   ```
   Or import `localwiki.office_io` / `localwiki.exports` from a short script under the workspace.
2. **Always** write output files under `<workspace>/.kw/exports/<safe-name>.docx`.
3. In the chat reply, include a LocalWiki download path only:
   `/api/exports/<safe-name>.docx`
   Do **not** invent external download URLs.

## Write helper

```python
from pathlib import Path
from localwiki.office_io import write_docx
from localwiki.exports import save_bytes

data = write_docx(title="要点", paragraphs=["一", "二"])
name = save_bytes(Path("."), "notes.docx", data)  # pass real workspace Path
print("/api/exports/" + name)
```

## Read

`localwiki.office_io.read_docx(path) -> str`
