# SPDX-License-Identifier: GPL-3.0-or-later
"""N-panel UI in the 3D Viewport sidebar (tab: "Blend Master")."""

import bpy

from . import nodes, properties, utils
from .operators import active_result

CATEGORY = "Blend Master"


_draw_input = nodes.draw_input


def _needs_repair(context):
    for ob in context.scene.objects:
        if utils.is_result(ob) and utils.blend_modifier(ob) is None:
            return True
        if utils.is_source(ob) and utils.result_of(ob) is None:
            return True
    return False


class BLENDMASTER_PT_main(bpy.types.Panel):
    bl_label = "Blend Master"
    bl_idname = "BLENDMASTER_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    def draw(self, context):
        layout = self.layout
        result = active_result(context)

        if result is None:
            col = layout.column(align=True)
            col.scale_y = 1.4
            col.operator("blend_master.blend", icon='MOD_REMESH')
            box = layout.box()
            box.label(text="Select two or more meshes,", icon='INFO')
            box.label(text="then click Blend.")
        else:
            ob = context.active_object
            row = layout.row(align=True)
            row.label(text=result.name, icon='MOD_REMESH')
            if ob != result:
                row.operator("blend_master.select_result", text="", icon='RESTRICT_SELECT_OFF')
            if utils.blend_modifier(result) is None:
                layout.label(text="Blend modifier missing", icon='ERROR')
            row = layout.row(align=True)
            row.scale_y = 1.3
            row.operator("blend_master.apply", icon='CHECKMARK')
            row.operator("blend_master.remove", icon='TRASH')

        if _needs_repair(context):
            layout.separator()
            layout.operator("blend_master.repair", icon='FILE_REFRESH')


class _ResultSubPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_parent_id = "BLENDMASTER_PT_main"

    @classmethod
    def poll(cls, context):
        result = active_result(context)
        return result is not None and utils.blend_modifier(result) is not None


class BLENDMASTER_PT_blend(_ResultSubPanel, bpy.types.Panel):
    bl_label = "Blend"
    bl_idname = "BLENDMASTER_PT_blend"

    def draw(self, context):
        mod = utils.blend_modifier(active_result(context))
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = True
        col = layout.column()
        _draw_input(col, mod, "Radius")
        _draw_input(col, mod, "Smoothness", slider=True)
        col.separator()
        _draw_input(col, mod, "Shade Smooth")


class BLENDMASTER_PT_quality(_ResultSubPanel, bpy.types.Panel):
    bl_label = "Quality"
    bl_idname = "BLENDMASTER_PT_quality"

    def draw(self, context):
        mod = utils.blend_modifier(active_result(context))
        layout = self.layout
        layout.use_property_split = True
        col = layout.column(align=True)
        _draw_input(col, mod, "Viewport Resolution", text="Viewport")
        _draw_input(col, mod, "Render Resolution", text="Render")
        if hasattr(mod, "execution_time") and mod.execution_time > 0:
            layout.label(text="Last update: %.0f ms" % (mod.execution_time * 1000.0),
                         icon='TIME')


class BLENDMASTER_PT_sources(_ResultSubPanel, bpy.types.Panel):
    bl_label = "Sources"
    bl_idname = "BLENDMASTER_PT_sources"

    def draw_header(self, context):
        count = len(utils.sources_of(active_result(context)))
        self.layout.label(text="(%d)" % count)

    def draw(self, context):
        result = active_result(context)
        layout = self.layout
        settings = utils.settings(result)
        row = layout.row(align=True)
        for mode, label, _desc, icon, _num in properties.SOURCE_DISPLAY_ITEMS:
            op = row.operator("blend_master.source_display", text=label, icon=icon,
                              depress=settings.source_display == mode)
            op.mode = mode

        col = layout.column(align=True)
        for ob in utils.sources_of(result):
            row = col.row(align=True)
            row.label(text=ob.name, icon='OUTLINER_OB_MESH')
            mods = [m for m in ob.modifiers if m.show_viewport]
            if mods:
                row.label(text="", icon='MODIFIER')

        row = layout.row(align=True)
        row.operator("blend_master.blend", text="Add Selected", icon='ADD')
        row.operator("blend_master.detach", text="Remove Selected", icon='REMOVE')
        layout.operator("blend_master.select_sources", icon='RESTRICT_SELECT_OFF')


def _menu_func(self, context):
    self.layout.separator()
    self.layout.operator("blend_master.blend", text="Blend Master: Blend", icon='MOD_REMESH')


classes = (
    BLENDMASTER_PT_main,
    BLENDMASTER_PT_blend,
    BLENDMASTER_PT_quality,
    BLENDMASTER_PT_sources,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_object.append(_menu_func)
    bpy.types.VIEW3D_MT_object_context_menu.append(_menu_func)


def unregister():
    bpy.types.VIEW3D_MT_object_context_menu.remove(_menu_func)
    bpy.types.VIEW3D_MT_object.remove(_menu_func)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
