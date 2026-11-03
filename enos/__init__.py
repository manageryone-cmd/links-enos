"""links-enos — a cross-platform MCP memory system.

The package is intentionally dependency-free (standard library only) so that the
exact same code can be launched from:

* a source checkout:      ``python3 -m enos.mcp_server``
* a wheel install:        ``links-enos``
* a PyInstaller binary:   ``./links-enos``
* a container:            ``docker run -i --rm links-enos``

Public surface kept deliberately small — the MCP server module is the entry
point, :class:`enos.store.MemoryStore` is the storage engine.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
