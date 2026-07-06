"""Compatibility shim: `import llm` resolves to masshine.llm (the real client).

Kept so tools/stream_probe.py (bare `import llm`) and any legacy `import llm` keep working — and so
a test that patches `llm.chat_json` patches the SAME module object the package calls internally.
"""
import sys

from masshine import llm as _llm

sys.modules[__name__] = _llm
