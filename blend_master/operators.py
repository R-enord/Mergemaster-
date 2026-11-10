# SPDX-License-Identifier: GPL-3.0-or-later
"""Blend, Apply, Remove and helper operators.

The actual work lives in plain functions (``create_blend``, ``remove_blend``,
``apply_blend`` ...) so it can be tested headless and reused; the operator
classes only validate the selection, call them and report to the user.

Rules every function here follows:

* Source objects are never modified: only their viewport display, render
  visibility and collection membership change, and all three are restored
  exactly when they leave a blend.
* Only datablocks tagged as Blend Master's own are ever deleted.
* The user's modifiers, on any object, are never touched.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty
from mathutils import Matrix

from . import nodes, properties, utils


# --------------------------------------------------------------------------
# Core functions
# --------------------------------------------------------------------------

_set_input = nodes.set_input
get_input = nodes.get_input


def _tag_result(result):
    """Make the depsgraph re-evaluate a result after its inputs changed."""
    # Writing modifier inputs from Python does not tag the depsgraph.
    result.update_tag()


def _adopt_source(result, ob, display):
    """Make ``ob`` a source of ``result``: remember its state, then link it."""
    coll = utils.source_collection(result)
    s = utils.settings(ob)
    s.role = utils.ROLE_SOURCE
    s.orig_display_type = ob.display_type
    s.orig_hide_render = ob.hide_render
    if ob.name not in coll.objects:
        coll.objects.link(ob)
    ob.hide_render = True
    if display == 'HIDE':
        try:
            ob.hide_set(True)
        except RuntimeError:
            pass
    else:
        ob.display_type = display


def _release_source(ob, coll):
    """Restore a source exactly as it was and forget all Blend Master data."""
    s = utils.settings(ob)
    if s is not None and s.role == utils.ROLE_SOURCE:
        if s.orig_display_type:
            ob.display_type = s.orig_display_type
        ob.hide_render = s.orig_hide_render
    try:
        ob.hide_set(False)
    except RuntimeError:
        pass
    if coll is not None and ob.name in coll.objects:
        coll.objects.unlink(ob)
    ob.property_unset("blend_master")


def _result_matrix(active):
    """Result transform: the active object's location and rotation, no scale.

    Unit scale keeps the result's local space metric, so Radius means the
    same distance everywhere even when sources are scaled non-uniformly.
    """
    loc, rot, _scale = active.matrix_world.decompose()
    return Matrix.Translation(loc) @ rot.to_matrix().to_4x4()


def create_blend(context, active, others):
    """Create a new blend of ``active`` + ``others``. Returns the result object."""
    prefs = properties.preferences(context)
    display = prefs.source_display if prefs else 'WIRE'
    sources = [active] + [ob for ob in others if ob != active]

    group = nodes.ensure_node_groups()

    coll = bpy.data.collections.new(utils.PREFIX + active.name + "_Sources")
    coll[utils.TAG_OWNER] = True

    mesh = bpy.data.meshes.new(active.name + "_Blend")
    result = bpy.data.objects.new(active.name + "_Blend", mesh)
    home = next((c for c in active.users_collection if not c.get(utils.TAG_OWNER)),
                context.scene.collection)
    home.objects.link(result)
    result.matrix_world = _result_matrix(active)

    rs = utils.settings(result)
    rs.role = utils.ROLE_RESULT
    rs.collection = coll
    rs.source_display = display

    mod = result.modifiers.new(utils.MODIFIER_NAME, 'NODES')
    mod.node_group = group
    _set_input(mod, "Sources", coll)
    _set_input(mod, "Radius", utils.nice_radius(sources))
    if prefs:
        _set_input(mod, "Viewport Resolution", prefs.viewport_resolution)
        _set_input(mod, "Render Resolution", prefs.render_resolution)

    for ob in sources:
        _adopt_source(result, ob, display)
    _tag_result(result)
    return result


def add_sources(context, result, objects):
    """Add ``objects`` to an existing blend. Returns how many were added."""
    display = utils.settings(result).source_display
    coll = utils.source_collection(result)
    added = 0
    for ob in objects:
        if ob == result or ob.name in coll.objects:
            continue
        _adopt_source(result, ob, display)
        added += 1
    if added:
        _tag_result(result)
    return added


def _delete_object(ob):
    data = ob.data
    bpy.data.objects.remove(ob)
    if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
        bpy.data.meshes.remove(data)


def _dissolve(result, keep_sources=True):
    """Shared tail of Remove and Apply: release sources, drop the collection.

    If another result object still reads the same collection (the user
    duplicated the result), the sources stay with that one.
    """
    coll = utils.source_collection(result)
    released = []
    if coll is not None:
        others = [ob for ob in utils.results_using(coll) if ob != result]
        if not others:
            released = list(coll.objects)
            for ob in released:
                _release_source(ob, coll)
            if coll.get(utils.TAG_OWNER):
                bpy.data.collections.remove(coll)
    return released


def remove_blend(result):
    """Delete the result and restore the sources. Returns the sources."""
    released = _dissolve(result)
    _delete_object(result)
    nodes.remove_node_groups_if_unused()
    return released


def detach_sources(result, objects):
    """Take ``objects`` out of a blend; removes the blend if none remain."""
    coll = utils.source_collection(result)
    for ob in objects:
        if coll is not None and ob.name in coll.objects:
            _release_source(ob, coll)
    if coll is None or len(coll.objects) == 0:
        remove_blend(result)
        return None
    _tag_result(result)
    return result


def apply_blend(context, result, use_render_quality=True, sources_action='HIDE'):
    """Bake the result to a plain mesh and dissolve the blend.

    sources_action: 'HIDE' (default, recoverable), 'KEEP' or 'DELETE'.
    Returns the (now ordinary) result object.
    """
    mod = utils.blend_modifier(result)
    if mod is None:
        raise RuntimeError("Not a Blend Master result")
    if use_render_quality:
        # Render resolution and full refinement, even though we evaluate in
        # the viewport depsgraph.
        _set_input(mod, "Force Final", True)
    # Bake only Blend Master's output: the user's own modifiers after ours
    # stay on the object (untouched) and keep working on the baked mesh, so
    # they must not be baked in as well.
    later = list(result.modifiers)[list(result.modifiers).index(mod) + 1:]
    muted = [m for m in later if m.show_viewport]
    for m in muted:
        m.show_viewport = False
    _tag_result(result)
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = result.evaluated_get(depsgraph)
    baked = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True,
                                            depsgraph=depsgraph)
    for m in muted:
        m.show_viewport = True
    old = result.data
    result.modifiers.remove(mod)
    result.data = baked
    name = old.name
    if old.users == 0:
        bpy.data.meshes.remove(old)
    baked.name = name

    released = _dissolve(result)
    result.property_unset("blend_master")
    for ob in released:
        if sources_action == 'HIDE':
            ob.hide_render = True
            try:
                ob.hide_set(True)
            except RuntimeError:
                pass
        elif sources_action == 'DELETE':
            _delete_object(ob)
    nodes.remove_node_groups_if_unused()
    return result


def repair(context):
    """Rebuild node groups and restore sources whose result was deleted.

    Returns (rebuilt_groups, restored_sources).
    """
    results = [ob for ob in bpy.data.objects if utils.is_result(ob)]
    rebuilt = False
    if results:
        nodes.ensure_node_groups(force=True)
        rebuilt = True
        for ob in results:
            mod = utils.blend_modifier(ob)
            if mod is None:
                mod = ob.modifiers.new(utils.MODIFIER_NAME, 'NODES')
                mod.node_group = bpy.data.node_groups[nodes.GROUP_MAIN]
                if utils.source_collection(ob) is not None:
                    _set_input(mod, "Sources", utils.source_collection(ob))
            _tag_result(ob)
    restored = 0
    live = {utils.source_collection(ob) for ob in results}
    for ob in list(bpy.data.objects):
        if utils.is_source(ob):
            owned = utils.owned_collections(ob)
            if not any(c in live for c in owned):
                for c in owned:
                    _release_source(ob, c)
                if not owned:
                    _release_source(ob, None)
                restored += 1
    for coll in list(bpy.data.collections):
        if coll.get(utils.TAG_OWNER) and coll not in live:
            for ob in list(coll.objects):
                _release_source(ob, coll)
            bpy.data.collections.remove(coll)
    nodes.remove_node_groups_if_unused()
    return rebuilt, restored


# --------------------------------------------------------------------------
# Selection helpers
# --------------------------------------------------------------------------

def active_result(context):
    """The result the user is working on: the active object, or its blend."""
    ob = context.active_object
    if ob is None:
        return None
    if utils.is_result(ob):
        return ob
    if utils.is_source(ob):
        return utils.result_of(ob)
    return None


def _object_mode(context):
    return context.mode == 'OBJECT'


def _select_only(context, objects, active=None):
    """Select exactly ``objects`` (and make ``active`` active).

    Walks the view layer rather than ``context.selected_objects``: after an
    object has been deleted that list may still point at it when the
    operator runs with an overridden context, and touching it would crash.
    """
    for ob in context.view_layer.objects:
        if ob.select_get():
            ob.select_set(False)
    for ob in objects:
        try:
            ob.select_set(True)
        except RuntimeError:     # not in this view layer
            pass
    if active is not None:
        context.view_layer.objects.active = active


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------

class BLENDMASTER_OT_blend(bpy.types.Operator):
    """Blend the selected meshes into one smooth, live result.
    With a blend selected, adds the other selected meshes to it"""
    bl_idname = "blend_master.blend"
    bl_label = "Blend"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _object_mode(context):
            cls.poll_message_set("Switch to Object Mode")
            return False
        meshes = [ob for ob in context.selected_objects if ob.type == 'MESH']
        if active_result(context) is not None and len(meshes) >= 2:
            return True
        if len([ob for ob in meshes if utils.is_blendable(ob)]) < 2:
            cls.poll_message_set("Select two or more mesh objects")
            return False
        return True

    def execute(self, context):
        selected = [ob for ob in context.selected_objects if ob.type == 'MESH']
        results = set()
        for ob in selected:
            if utils.is_result(ob):
                results.add(ob)
            elif utils.is_source(ob):
                r = utils.result_of(ob)
                if r is not None:
                    results.add(r)
        if len(results) > 1:
            self.report({'ERROR'}, "The selection belongs to %d different blends; "
                        "blend them one at a time" % len(results))
            return {'CANCELLED'}

        free = [ob for ob in selected
                if utils.is_blendable(ob) and not utils.is_source(ob)]

        if results:
            result = results.pop()
            added = add_sources(context, result, free)
            if not added:
                self.report({'INFO'}, "Already blended - nothing to add")
            else:
                self.report({'INFO'}, "Added %d object(s) to %s" % (added, result.name))
        else:
            active = context.active_object
            if active not in free:
                active = free[0]
            if len(free) < 2:
                self.report({'ERROR'}, "Select two or more mesh objects")
                return {'CANCELLED'}
            result = create_blend(context, active, free)
            self.report({'INFO'}, "Blended %d objects into %s" % (len(free), result.name))

        self._warn_isolated(context, result)
        _select_only(context, [result], result)
        return {'FINISHED'}

    def _warn_isolated(self, context, result):
        mod = utils.blend_modifier(result)
        radius = get_input(mod, "Radius") if mod else 0.0
        sources = utils.sources_of(result)
        if len(sources) < 2:
            return
        try:
            isolated = utils.find_isolated(sources, radius, context.evaluated_depsgraph_get())
        except Exception:      # a warning must never break the operator
            return
        if isolated:
            self.report({'WARNING'}, "%s %s not touch the other objects and will stay "
                        "separate (move closer or raise Radius)"
                        % (", ".join(isolated), "does" if len(isolated) == 1 else "do"))


class BLENDMASTER_OT_remove(bpy.types.Operator):
    """Remove the blend: delete the result and restore the source objects exactly"""
    bl_idname = "blend_master.remove"
    bl_label = "Remove Blend"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _object_mode(context):
            cls.poll_message_set("Switch to Object Mode")
            return False
        if active_result(context) is None:
            cls.poll_message_set("Select a blend result or one of its sources")
            return False
        return True

    def execute(self, context):
        result = active_result(context)
        name = result.name
        released = remove_blend(result)
        _select_only(context, released, released[0] if released else None)
        self.report({'INFO'}, "Removed %s" % name)
        return {'FINISHED'}


class BLENDMASTER_OT_apply(bpy.types.Operator):
    """Bake the blend into a regular mesh. The original objects are kept (hidden) by default"""
    bl_idname = "blend_master.apply"
    bl_label = "Apply Blend"
    bl_options = {'REGISTER', 'UNDO'}

    use_render_quality: BoolProperty(
        name="Render Quality",
        description="Bake with the render resolution and full refinement",
        default=True,
    )
    sources_action: EnumProperty(
        name="Sources",
        description="What to do with the original objects",
        items=(
            ('HIDE', "Hide", "Keep the originals, hidden, so they can be recovered"),
            ('KEEP', "Keep", "Keep the originals visible"),
            ('DELETE', "Delete", "Delete the originals"),
        ),
        default='HIDE',
    )

    @classmethod
    def poll(cls, context):
        return BLENDMASTER_OT_remove.poll(context)

    def execute(self, context):
        result = active_result(context)
        if utils.blend_modifier(result) is None:
            self.report({'ERROR'}, "The Blend Master modifier is missing; use Repair first")
            return {'CANCELLED'}
        apply_blend(context, result, self.use_render_quality, self.sources_action)
        _select_only(context, [result], result)
        self.report({'INFO'}, "Applied %s" % result.name)
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class BLENDMASTER_OT_detach(bpy.types.Operator):
    """Take the selected source objects out of their blend and restore them"""
    bl_idname = "blend_master.detach"
    bl_label = "Remove from Blend"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not _object_mode(context):
            return False
        if not any(utils.is_source(ob) for ob in context.selected_objects):
            cls.poll_message_set("Select source objects of a blend")
            return False
        return True

    def execute(self, context):
        by_result = {}
        for ob in context.selected_objects:
            if utils.is_source(ob):
                r = utils.result_of(ob)
                if r is not None:
                    by_result.setdefault(r, []).append(ob)
        count = 0
        for result, objs in by_result.items():
            detach_sources(result, objs)
            count += len(objs)
        self.report({'INFO'}, "Removed %d object(s) from their blend" % count)
        return {'FINISHED'}


class BLENDMASTER_OT_select_sources(bpy.types.Operator):
    """Select the source objects of the active blend"""
    bl_idname = "blend_master.select_sources"
    bl_label = "Select Sources"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _object_mode(context) and active_result(context) is not None

    def execute(self, context):
        result = active_result(context)
        sources = utils.sources_of(result)
        if utils.settings(result).source_display == 'HIDE':
            utils.settings(result).source_display = 'WIRE'
            properties.apply_source_display(result)
        _select_only(context, sources, sources[0] if sources else None)
        return {'FINISHED'}


class BLENDMASTER_OT_select_result(bpy.types.Operator):
    """Select the blend result of the active source object"""
    bl_idname = "blend_master.select_result"
    bl_label = "Select Result"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _object_mode(context) and active_result(context) is not None

    def execute(self, context):
        result = active_result(context)
        _select_only(context, [result], result)
        return {'FINISHED'}


class BLENDMASTER_OT_source_display(bpy.types.Operator):
    """Choose how the source objects of the active blend are drawn"""
    bl_idname = "blend_master.source_display"
    bl_label = "Show Sources As"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(name="Show Sources As", items=properties.SOURCE_DISPLAY_ITEMS)

    @classmethod
    def poll(cls, context):
        return active_result(context) is not None

    def execute(self, context):
        result = active_result(context)
        utils.settings(result).source_display = self.mode
        properties.apply_source_display(result)
        return {'FINISHED'}


class BLENDMASTER_OT_repair(bpy.types.Operator):
    """Rebuild Blend Master's node groups and restore objects left over from deleted blends"""
    bl_idname = "blend_master.repair"
    bl_label = "Repair"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        rebuilt, restored = repair(context)
        parts = []
        if rebuilt:
            parts.append("rebuilt node groups")
        if restored:
            parts.append("restored %d orphaned object(s)" % restored)
        self.report({'INFO'}, "Repair: " + (", ".join(parts) if parts else "nothing to fix"))
        return {'FINISHED'}


classes = (
    BLENDMASTER_OT_blend,
    BLENDMASTER_OT_remove,
    BLENDMASTER_OT_apply,
    BLENDMASTER_OT_detach,
    BLENDMASTER_OT_select_sources,
    BLENDMASTER_OT_select_result,
    BLENDMASTER_OT_source_display,
    BLENDMASTER_OT_repair,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
