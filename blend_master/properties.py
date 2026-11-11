# SPDX-License-Identifier: GPL-3.0-or-later
"""Property groups and add-on preferences.

Where things live
-----------------
* The blend *parameters* (radius, smoothness, resolution...) are inputs of
  the BM_Blend Geometry Nodes modifier on the result object.  That is the
  single source of truth: they are animatable, show up in the Modifier
  panel, and survive without the add-on installed.
* ``Object.blend_master`` only records *bookkeeping*: whether an object is a
  result or a source, which collection a result blends, and the display
  settings a source had before it was blended (restored on Remove/Apply).
  It is unset completely when an object leaves a blend, so no trace is left.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty

from . import utils

SOURCE_DISPLAY_ITEMS = (
    ('WIRE', "Wire", "Show sources as wireframes over the result", 'SHADING_WIRE', 0),
    ('BOUNDS', "Bounds", "Show sources as bounding boxes", 'SHADING_BBOX', 1),
    ('HIDE', "Hidden", "Hide sources in the viewport", 'HIDE_ON', 2),
)


def apply_source_display(result):
    """Apply the result's 'Show Sources As' setting to all of its sources."""
    s = utils.settings(result)
    mode = s.source_display
    for ob in utils.sources_of(result):
        try:
            if mode == 'HIDE':
                ob.hide_set(True)
            else:
                ob.hide_set(False)
                ob.display_type = mode
        except RuntimeError:
            # Object is not in the current view layer; hide_set is per layer.
            if mode != 'HIDE':
                ob.display_type = mode


class BLENDMASTER_PG_object(bpy.types.PropertyGroup):
    """Per-object Blend Master bookkeeping (never holds user-facing values)."""

    role: EnumProperty(
        name="Role",
        items=(
            (utils.ROLE_NONE, "None", "Not part of a blend"),
            (utils.ROLE_RESULT, "Result", "Generated blend result"),
            (utils.ROLE_SOURCE, "Source", "Source object of a blend"),
        ),
        default=utils.ROLE_NONE,
        options=set(),
    )
    # RESULT: the collection of source objects this result blends.
    collection: PointerProperty(type=bpy.types.Collection, options=set())
    # Changed through BLENDMASTER_OT_source_display, never with an update=
    # callback: Blender 4.2 crashes on exit after an extension whose property
    # groups have update callbacks is uninstalled.
    source_display: EnumProperty(
        name="Show Sources As",
        description="How the source objects are drawn in the viewport",
        items=SOURCE_DISPLAY_ITEMS,
        default='WIRE',
        options=set(),
    )
    # SOURCE: what to restore when the object leaves the blend.
    orig_display_type: StringProperty(options=set())
    orig_hide_render: BoolProperty(options=set())


class BLENDMASTER_AP_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    source_display: EnumProperty(
        name="Show Sources As",
        description="How source objects are drawn after blending",
        items=SOURCE_DISPLAY_ITEMS,
        default='WIRE',
    )
    viewport_resolution: bpy.props.IntProperty(
        name="Viewport Resolution",
        description="Default voxel count along the longest side for new blends (viewport)",
        default=96, min=16, max=1024,
    )
    render_resolution: bpy.props.IntProperty(
        name="Render Resolution",
        description="Default voxel count along the longest side for new blends (render and Apply)",
        default=192, min=16, max=2048,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        col = layout.column(heading="New Blends")
        col.prop(self, "source_display")
        col.prop(self, "viewport_resolution")
        col.prop(self, "render_resolution")


def preferences(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


classes = (
    BLENDMASTER_PG_object,
    BLENDMASTER_AP_preferences,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.blend_master = PointerProperty(type=BLENDMASTER_PG_object)


def unregister():
    del bpy.types.Object.blend_master
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
