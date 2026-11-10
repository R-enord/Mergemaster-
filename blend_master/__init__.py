# SPDX-License-Identifier: GPL-3.0-or-later
"""Blend Master: non-destructive mesh blending for Blender 4.2+.

Select meshes, click Blend, adjust the radius.  Nothing is destroyed and
everything stays editable.

This module only registers the add-on.  See:
  nodes.py      - the Geometry Nodes groups (built in code)
  operators.py  - Blend / Apply / Remove / ...
  ui.py         - the N-panel
  properties.py - bookkeeping property group and preferences
  utils.py      - tagging, lookup and helper functions
"""

import bpy
from bpy.app.handlers import persistent

from . import nodes, operators, properties, ui, utils

_modules = (properties, operators, ui)


def _refresh_groups():
    """Rebuild node groups that were built by another version, if in use.

    Files that contain no blends are left untouched (we never add data to a
    file just because the add-on is enabled).
    """
    try:
        if any(utils.is_result(ob) for ob in bpy.data.objects):
            nodes.ensure_node_groups()
            for ob in bpy.data.objects:
                if utils.is_result(ob):
                    ob.update_tag()
    except (AttributeError, RuntimeError, ReferenceError):
        # Restricted context (e.g. during startup); the next load retries.
        pass
    return None     # one-shot when used as a timer


@persistent
def _on_load_post(*_args):
    _refresh_groups()


def register():
    for module in _modules:
        module.register()
    bpy.app.handlers.load_post.append(_on_load_post)
    # Enabling the add-on in an already open file: refresh once it is safe.
    bpy.app.timers.register(_refresh_groups, first_interval=0.1)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if bpy.app.timers.is_registered(_refresh_groups):
        bpy.app.timers.unregister(_refresh_groups)
    for module in reversed(_modules):
        module.unregister()
