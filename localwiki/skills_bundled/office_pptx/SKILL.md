---
name: office-pptx
description: Read PowerPoint (.pptx) when python-pptx is installed; write is best-effort/PARTIAL.
---

# Office PPTX

## Read

```python
from localwiki.office_io import read_pptx
text = read_pptx(path)  # raises if python-pptx missing
```

## Write

Prefer Markdown or DOCX export. PPTX write may be unavailable (`ImportError`).
