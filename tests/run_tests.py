# SPDX-License-Identifier: GPL-3.0-or-later
"""Blend Master acceptance tests (handover section 8).

Run headless with a Blender install:

    blender -b --factory-startup --python tests/run_tests.py

or with the ``bpy`` module from PyPI (same API, no UI):

    python tests/run_tests.py

Exit code is 0 when every test passes.  Use ``-k name`` to run a subset.
"""

import math
import os
import sys
import tempfile
import time
import traceback

import bpy                                # must come first: provides bmesh/mathutils
import bmesh                              # noqa: E402
from mathutils import Vector              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import blend_master                       # noqa: E402
from blend_master import nodes, operators, utils   # noqa: E402
import scenes                             # noqa: E402

TEST_RES = 64        # keep the suite quick; quality is covered separately


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def reset():
    """Empty the file without touching registration."""
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.node_groups,
                 bpy.data.collections, bpy.data.cameras, bpy.data.lights):
        for item in list(coll):
            coll.remove(item)
    scene = bpy.context.scene
    for child in list(scene.collection.children):
        scene.collection.children.unlink(child)


def build(name, offset=(0, 0, 0)):
    builder, radius = scenes.ALL[name]
    objs = builder(offset)
    bpy.context.view_layer.update()
    return objs, radius


def run_op(op, objects, active, **kwargs):
    """Call an operator as if the user had selected ``objects``."""
    for ob in bpy.context.view_layer.objects:
        ob.select_set(False)
    for ob in objects:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = active
    with bpy.context.temp_override(active_object=active, object=active,
                                   selected_objects=list(objects),
                                   selected_editable_objects=list(objects)):
        return op(**kwargs)


def blend(objects, active=None, radius=None):
    active = active or objects[0]
    assert run_op(bpy.ops.blend_master.blend, objects, active) == {'FINISHED'}
    result = bpy.context.view_layer.objects.active
    assert utils.is_result(result), "Blend did not make the result active"
    mod = utils.blend_modifier(result)
    nodes.set_input(mod, "Viewport Resolution", TEST_RES)
    if radius is not None:
        nodes.set_input(mod, "Radius", radius)
    result.update_tag()
    return result


def evaluated_mesh(ob):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return ob.evaluated_get(depsgraph).data


def mesh_report(ob):
    """(faces, non-manifold edges, shells, bbox_min, bbox_max)."""
    me = evaluated_mesh(ob)
    bm = bmesh.new()
    bm.from_mesh(me)
    nonmanifold = sum(1 for e in bm.edges if not e.is_manifold)
    seen, shells = set(), 0
    for v in bm.verts:
        if v.index in seen:
            continue
        shells += 1
        stack = [v]
        seen.add(v.index)
        while stack:
            x = stack.pop()
            for e in x.link_edges:
                o = e.other_vert(x)
                if o.index not in seen:
                    seen.add(o.index)
                    stack.append(o)
    lo = Vector((min(v.co[i] for v in bm.verts) for i in range(3))) if bm.verts else Vector()
    hi = Vector((max(v.co[i] for v in bm.verts) for i in range(3))) if bm.verts else Vector()
    faces = len(bm.faces)
    bm.free()
    return faces, nonmanifold, shells, ob.matrix_world @ lo, ob.matrix_world @ hi


def idprop_keys(id_or_struct):
    """User custom properties plus (Blender 5+) system properties."""
    keys = set(id_or_struct.keys())
    getter = getattr(id_or_struct, "bl_system_properties_get", None)
    if getter is not None:
        system = getter()
        if system is not None:
            keys |= {"system:" + k for k in system.keys()}
    return keys


def modifier_state(ob):
    out = []
    for m in ob.modifiers:
        props = {}
        for p in m.bl_rna.properties:
            if p.is_readonly or p.identifier in {"rna_type", "is_active"}:
                continue
            value = getattr(m, p.identifier)
            if hasattr(value, "name"):
                value = value.name
            elif not isinstance(value, (int, float, str, bool)) and value is not None:
                try:
                    value = tuple(value)
                except TypeError:
                    value = str(value)
            props[p.identifier] = value
        out.append((m.name, m.type, tuple(sorted(props.items()))))
    return out


def snapshot():
    """Everything Remove must restore exactly."""
    state = {
        "objects": sorted(o.name for o in bpy.data.objects),
        "meshes": sorted(m.name for m in bpy.data.meshes),
        "collections": sorted(c.name for c in bpy.data.collections),
        "node_groups": sorted(g.name for g in bpy.data.node_groups),
        "materials": sorted(m.name for m in bpy.data.materials),
    }
    for ob in bpy.data.objects:
        state["ob:" + ob.name] = (
            ob.display_type, ob.hide_render, ob.hide_get(), ob.hide_viewport,
            tuple(sorted(c.name for c in ob.users_collection)),
            tuple(round(x, 6) for row in ob.matrix_world for x in row),
            tuple(sorted(idprop_keys(ob))),
            ob.is_property_set("blend_master"),
            ob.data.name if ob.data else None,
            len(ob.data.vertices) if ob.type == 'MESH' else None,
            tuple(modifier_state(ob)),
        )
    return state


def diff(a, b):
    lines = []
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            lines.append("  %s:\n    before %r\n    after  %r" % (key, a.get(key), b.get(key)))
    return "\n".join(lines)


def bm_groups():
    return sorted(g.name for g in bpy.data.node_groups if g.name.startswith(utils.PREFIX))


def bm_collections():
    return [c for c in bpy.data.collections if c.get(utils.TAG_OWNER)]


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_register_cycle():
    blend_master.unregister()
    blend_master.register()
    blend_master.unregister()
    blend_master.register()
    assert hasattr(bpy.types.Object, "blend_master")
    assert hasattr(bpy.ops.blend_master, "blend")


def test_blend_creates_result():
    objs, radius = build("cube_cylinder")
    result = blend(objs, radius=radius)
    assert len([m for m in result.modifiers if m.type == 'NODES']) == 1
    assert bm_groups() == sorted(nodes.ALL_GROUPS), bm_groups()
    faces, nonmanifold, shells, _lo, _hi = mesh_report(result)
    assert faces > 1000, faces
    assert nonmanifold == 0, nonmanifold
    assert shells == 1, shells
    for ob in objs:
        assert utils.is_source(ob)
        assert ob.display_type == 'WIRE' and ob.hide_render


def test_repeat_blend_never_duplicates():
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    groups, colls = bm_groups(), len(bm_collections())
    for _ in range(3):
        # all of it selected, result active
        run_op(bpy.ops.blend_master.blend, objs + [result], result)
        # only the sources selected
        run_op(bpy.ops.blend_master.blend, objs, objs[0])
    results = [o for o in bpy.data.objects if utils.is_result(o)]
    assert results == [result], results
    assert len([m for m in result.modifiers if m.type == 'NODES']) == 1
    assert bm_groups() == groups
    assert len(bm_collections()) == colls
    assert len(utils.sources_of(result)) == 2


def test_remove_restores_scene_exactly():
    objs, _ = build("cube_cylinder")
    before = snapshot()
    result = blend(objs)
    assert snapshot() != before
    assert run_op(bpy.ops.blend_master.remove, [result], result) == {'FINISHED'}
    after = snapshot()
    assert after == before, "scene differs after Remove:\n" + diff(before, after)


def test_remove_from_source():
    objs, _ = build("sphere_torus")
    before = snapshot()
    blend(objs)
    # Active object is a *source*: Remove still finds its blend.
    assert run_op(bpy.ops.blend_master.remove, [objs[1]], objs[1]) == {'FINISHED'}
    assert snapshot() == before, diff(before, snapshot())


def test_live_update():
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    _f, _n, _s, _lo, hi = mesh_report(result)
    objs[1].location.z += 0.5
    _f, _n, _s, _lo, hi2 = mesh_report(result)
    assert abs((hi2.z - hi.z) - 0.5) < 0.05, (hi.z, hi2.z)
    # Edit the source mesh data itself.
    for v in objs[1].data.vertices:
        v.co.x *= 1.5
    objs[1].data.update()
    _f, _n, _s, lo3, hi3 = mesh_report(result)
    assert hi3.x - lo3.x > 2.0 - 0.05, (lo3.x, hi3.x)   # cube is 2 wide; still fused


def test_apply_hide():
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    names = [o.name for o in objs]
    assert run_op(bpy.ops.blend_master.apply, [result], result,
                  use_render_quality=False, sources_action='HIDE') == {'FINISHED'}
    assert not result.modifiers, list(result.modifiers)
    assert len(result.data.polygons) > 1000
    assert not result.is_property_set("blend_master")
    assert bm_groups() == [], bm_groups()
    assert bm_collections() == []
    for name in names:
        ob = bpy.data.objects[name]
        assert not ob.is_property_set("blend_master")
        assert ob.hide_get() and ob.hide_render
        assert ob.display_type == 'TEXTURED'      # restored
    faces, nonmanifold, shells, _lo, _hi = mesh_report(result)
    assert nonmanifold == 0 and shells == 1
    # Materials survive the bake.
    assert {m.name for m in result.data.materials if m} >= {"BM_Test_Grey", "BM_Test_Orange"}


def test_apply_delete_and_keep():
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    names = [o.name for o in objs]
    operators.apply_blend(bpy.context, result, use_render_quality=False, sources_action='DELETE')
    assert all(n not in bpy.data.objects for n in names)
    reset()
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    operators.apply_blend(bpy.context, result, use_render_quality=False, sources_action='KEEP')
    for ob in objs:
        assert not ob.hide_get() and not ob.hide_render and ob.display_type == 'TEXTURED'


def test_apply_keeps_user_modifiers_on_result():
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    weld = result.modifiers.new("MyWeld", 'WELD')
    operators.apply_blend(bpy.context, result, use_render_quality=False)
    assert [m.name for m in result.modifiers] == ["MyWeld"]
    assert result.modifiers[0].show_viewport


def test_user_modifiers_untouched():
    objs, _ = build("with_modifiers")
    before = {o.name: modifier_state(o) for o in objs}
    result = blend(objs)
    assert {o.name: modifier_state(o) for o in objs} == before
    # The blend sees the *evaluated* sources (the Mirror copy of the bolt).
    _f, nonmanifold, _s, lo, hi = mesh_report(result)
    assert nonmanifold == 0
    assert lo.x < -0.8 and hi.x > 0.8, (lo.x, hi.x)
    operators.remove_blend(result)
    assert {o.name: modifier_state(o) for o in objs} == before


def test_all_scenes_watertight():
    expected_shells = {"apart": 2}
    for i, name in enumerate(scenes.ALL):
        reset()
        objs, radius = build(name)
        result = blend(objs, radius=radius)
        faces, nonmanifold, shells, _lo, _hi = mesh_report(result)
        assert faces > 0, name
        assert nonmanifold == 0, (name, nonmanifold)
        assert shells == expected_shells.get(name, 1), (name, shells)


def test_isolated_objects_warn_not_crash():
    objs, radius = build("apart")
    isolated = utils.find_isolated(objs, radius, bpy.context.evaluated_depsgraph_get())
    assert sorted(isolated) == sorted(o.name for o in objs), isolated
    result = blend(objs)             # must not raise
    assert mesh_report(result)[1] == 0
    objs, radius = build("touching", offset=(10, 0, 0))
    assert utils.find_isolated(objs, radius, bpy.context.evaluated_depsgraph_get()) == []


def test_add_and_detach_sources():
    objs, _ = build("three_objects")
    result = blend(objs[:2])
    assert len(utils.sources_of(result)) == 2
    knob = objs[2]
    knob_before = (knob.display_type, knob.hide_render, sorted(idprop_keys(knob)))
    run_op(bpy.ops.blend_master.blend, [result, knob], result)
    assert len(utils.sources_of(result)) == 3 and utils.is_source(knob)
    run_op(bpy.ops.blend_master.detach, [knob], knob)
    assert len(utils.sources_of(result)) == 2
    assert (knob.display_type, knob.hide_render, sorted(idprop_keys(knob))) == knob_before
    assert not knob.is_property_set("blend_master")


def test_repair_after_result_deleted():
    objs, _ = build("cube_cylinder")
    before = snapshot()
    result = blend(objs)
    mesh = result.data
    bpy.data.objects.remove(result)      # user deletes the result with X
    bpy.data.meshes.remove(mesh)
    rebuilt, restored = operators.repair(bpy.context)
    assert restored == 2, restored
    assert snapshot() == before, diff(before, snapshot())


def test_duplicate_result_then_remove():
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    dup = result.copy()
    dup.data = result.data.copy()
    bpy.context.scene.collection.objects.link(dup)
    operators.remove_blend(result)
    # Sources still belong to the duplicate.
    assert all(utils.is_source(o) for o in objs)
    assert mesh_report(dup)[0] > 0
    operators.remove_blend(dup)
    assert not any(utils.is_source(o) for o in objs)
    assert bm_groups() == [] and bm_collections() == []


def test_empty_blend_does_not_crash():
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    for ob in objs:
        bpy.data.objects.remove(ob)
    assert mesh_report(result)[0] == 0


def test_node_group_rebuild_keeps_values():
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    mod = utils.blend_modifier(result)
    nodes.set_input(mod, "Radius", 0.33)
    nodes.set_input(mod, "Smoothness", 0.8)
    nodes.ensure_node_groups(force=True)
    assert abs(nodes.get_input(mod, "Radius") - 0.33) < 1e-6
    assert abs(nodes.get_input(mod, "Smoothness") - 0.8) < 1e-6
    assert mod.node_group == bpy.data.node_groups[nodes.GROUP_MAIN]
    assert bm_groups() == sorted(nodes.ALL_GROUPS)


def test_file_from_other_version_is_rebuilt():
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    mod = utils.blend_modifier(result)
    nodes.set_input(mod, "Radius", 0.27)
    group = mod.node_group
    group[utils.TAG_BLENDER] = "0.0"     # pretend another Blender built it
    group[utils.TAG_BUILD] = -1          # ... with another add-on version
    path = os.path.join(tempfile.mkdtemp(), "bm_roundtrip.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)      # load_post handler runs
    group = bpy.data.node_groups[nodes.GROUP_MAIN]
    assert group[utils.TAG_BUILD] == nodes.BUILD_ID
    assert group[utils.TAG_BLENDER] == utils.blender_version_tag()
    result = next(o for o in bpy.data.objects if utils.is_result(o))
    mod = utils.blend_modifier(result)
    assert abs(nodes.get_input(mod, "Radius") - 0.27) < 1e-6
    assert mesh_report(result)[1] == 0
    assert len(utils.sources_of(result)) == 2


def test_materials_transferred():
    objs, _ = build("cube_cylinder")
    result = blend(objs)
    me = evaluated_mesh(result)
    names = [m.name if m else None for m in me.materials]
    used = {names[p.material_index] for p in me.polygons}
    assert {"BM_Test_Grey", "BM_Test_Orange"} <= used, used


def test_undo():
    """Blend, Apply and Remove are each a single undo step."""
    windows = bpy.context.window_manager.windows
    if not windows:
        return "skipped (no window; run inside Blender, see docs/TESTING.md)"
    with bpy.context.temp_override(window=windows[0]):
        if not bpy.ops.ed.undo.poll():
            return "skipped (undo unavailable in this background session)"
    objs, _ = build("cube_cylinder")
    names = [o.name for o in objs]
    with bpy.context.temp_override(window=windows[0]):
        bpy.ops.ed.undo_push(message="scene built")
        before = snapshot()
        for op in ("blend", "apply", "remove"):
            sources = [bpy.data.objects[n] for n in names]
            if op != "blend":
                blend(sources)
                bpy.ops.ed.undo_push(message="blended")
                mid = snapshot()
                result = bpy.context.view_layer.objects.active
                getattr(bpy.ops.blend_master, op)(**({"use_render_quality": False}
                                                    if op == "apply" else {}))
                bpy.ops.ed.undo_push(message=op)
                bpy.ops.ed.undo()
                assert snapshot() == mid, "undo of %s:\n%s" % (op, diff(mid, snapshot()))
                bpy.ops.ed.undo()
            else:
                blend(sources)
                bpy.ops.ed.undo_push(message="blended")
                bpy.ops.ed.undo()
            assert snapshot() == before, "undo back to start after %s:\n%s" % (
                op, diff(before, snapshot()))


class _MockLayout:
    """Stands in for UILayout: records calls and validates prop() paths."""

    def __init__(self, log):
        self.log = log

    def __getattr__(self, name):
        if name in ("row", "column", "box", "split", "column_flow", "grid_flow"):
            return lambda *a, **k: _MockLayout(self.log)
        if name == "prop":
            return self._prop
        if name == "operator":
            return self._operator
        if name in ("label", "separator"):
            return lambda *a, **k: None
        return None

    def __setattr__(self, name, value):
        if name == "log":
            object.__setattr__(self, name, value)

    def _prop(self, data, path, **kwargs):
        data.path_resolve(path)          # raises if the path is wrong
        self.log.append(path)

    def _operator(self, idname, **kwargs):
        module, name = idname.split(".")
        assert hasattr(getattr(bpy.ops, module), name), idname
        self.log.append(idname)
        return type("Props", (), {})()     # accepts any property assignment


def test_ui_panels_draw():
    from blend_master import ui
    objs, _ = build("cube_cylinder")

    def draw_all(active):
        log = []
        bpy.context.view_layer.objects.active = active
        for cls in ui.classes:
            if hasattr(cls, "poll") and not cls.poll(bpy.context):
                continue
            mock = _MockLayout(log)
            # Panel instances can't be created outside Blender's UI; call the
            # unbound draw functions with a stand-in "self".
            fake = type("P", (), {"layout": mock})()
            cls.draw(fake, bpy.context)
            if hasattr(cls, "draw_header"):
                cls.draw_header(fake, bpy.context)
        return log

    assert "blend_master.blend" in draw_all(objs[0])       # nothing blended yet
    result = blend(objs)
    log = draw_all(result)
    for expected in ("blend_master.apply", "blend_master.remove",
                     "blend_master.source_display"):
        assert expected in log, (expected, log)
    assert len([p for p in log if "Socket" in p or p == "value"]) >= 6, log
    draw_all(objs[1])                                      # a source is active


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
# Registration first, it re-registers the add-on.
TESTS.sort(key=lambda f: f.__name__ != "test_register_cycle")


def main(argv):
    args = argv[argv.index("--") + 1:] if "--" in argv else argv[1:]
    pattern = args[args.index("-k") + 1] if "-k" in args else ""
    print("Blend Master tests on Blender %s" % bpy.app.version_string)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    blend_master.register()
    failures, t0 = [], time.time()
    for test in TESTS:
        if pattern and pattern not in test.__name__:
            continue
        reset()
        start = time.time()
        try:
            note = test()
            status = "ok" if not note else note
        except Exception:
            failures.append(test.__name__)
            status = "FAIL\n" + traceback.format_exc()
        print("  %-42s %6.2fs  %s" % (test.__name__, time.time() - start, status))
    blend_master.unregister()
    print("%d failed, %.1fs total" % (len(failures), time.time() - t0))
    return 1 if failures else 0


if __name__ == "__main__":
    code = main(sys.argv)
    if bpy.app.background:
        sys.stdout.flush()
        os._exit(code)
    sys.exit(code)
