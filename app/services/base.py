from __future__ import annotations


class Service:
    """Base class. To add a feature:
      1. app/services/<name>.py  -> subclass Service (pure logic, no Flask imports)
      2. app/routes/<name>.py    -> expose a Flask Blueprint called `bp`
    Both are discovered automatically (see app/registry.py)."""

    name: str = ""            # slug; must equal the module file name
    title: str = ""
    description: str = ""
    home_endpoint: str | None = None   # e.g. "diagnostics.page" -> link on the home page

    def __init__(self, ctx):
        self.ctx = ctx

    @property
    def http(self):
        return self.ctx.http

    @property
    def cache(self):
        return self.ctx.cache

    @property
    def config(self):
        return self.ctx.config
