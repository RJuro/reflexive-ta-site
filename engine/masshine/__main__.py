"""`python -m masshine`            → full-pipeline demo (the old `python masshine.py`)
   `python -m masshine import-cache <cache.json> [project name]` → seed a project from a run cache
"""
import sys
from pathlib import Path

if len(sys.argv) > 1 and sys.argv[1] == "import-cache":
    from .seed import import_cache
    cache = Path(sys.argv[2])
    name = sys.argv[3] if len(sys.argv) > 3 else None
    import_cache(cache, name)
else:
    from .runner import demo
    demo()
