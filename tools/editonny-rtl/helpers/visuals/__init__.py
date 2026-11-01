"""Visual design for editonny-engine: animated texts and numbers drawn in Python.

    from visuals import engine, registry, render
    item = render.plan_item("counter", {"value": 1904152.87, "prefix": "R$ "}, fps=30, duration=3.0)
    clip = render.render_clip(item, "edit/renders/motion")

Templates live in helpers/visuals/templates/<id>.py (one file each, found automatically).
"""

from . import engine, registry, render  # noqa: F401
from .engine import VisualError  # noqa: F401

__all__ = ["engine", "registry", "render", "VisualError"]
