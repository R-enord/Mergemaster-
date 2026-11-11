# SPDX-License-Identifier: GPL-3.0-or-later
"""Test scene builders shared by run_tests.py and build_test_scenes.py.

Each builder adds objects at an ``offset`` and returns the list of source
objects; nothing is blended here.  Curved primitives are shaded the way an
artist would (smooth, with sharp edges by angle), because Blend Master
follows the shading.
"""

import math

import bpy
from mathutils import Vector


def _add(kind, offset, location=(0, 0, 0), smooth=True, **kwargs):
    loc = Vector(location) + Vector(offset)
    getattr(bpy.ops.mesh, "primitive_%s_add" % kind)(location=loc, **kwargs)
    ob = bpy.context.active_object
    if smooth:
        ob.data.shade_smooth()
        ob.data.set_sharp_from_angle(angle=math.radians(30))
    return ob


def _material(ob, name, color):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = color
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = color
    ob.data.materials.append(mat)


def cube_cylinder(offset=(0, 0, 0)):
    """Hard-surface junction: a cylinder standing in a cube."""
    a = _add("cube", offset, size=2)
    b = _add("cylinder", offset, location=(0, 0, 1.4), radius=0.4, depth=1.6, vertices=48)
    a.name, b.name = "Cube", "Cylinder"
    _material(a, "BM_Test_Grey", (0.6, 0.6, 0.62, 1))
    _material(b, "BM_Test_Orange", (0.9, 0.35, 0.05, 1))
    return [a, b]


def sphere_torus(offset=(0, 0, 0)):
    """Organic junction: a torus sunk around a sphere."""
    a = _add("uv_sphere", offset, radius=1, segments=48, ring_count=24)
    b = _add("torus", offset, location=(0, 0, 0.55), major_radius=0.9, minor_radius=0.25,
             major_segments=64, minor_segments=24)
    a.name, b.name = "Sphere", "Torus"
    return [a, b]


def three_objects(offset=(0, 0, 0)):
    """Three parts blended together."""
    a = _add("cube", offset, size=2)
    b = _add("cylinder", offset, location=(0.5, 0, 1.3), radius=0.35, depth=1.5)
    c = _add("uv_sphere", offset, location=(-0.6, -0.4, 1.0), radius=0.5)
    a.name, b.name, c.name = "Base", "Post", "Knob"
    return [a, b, c]


def touching(offset=(0, 0, 0)):
    """A sphere resting on a cube: they touch at a single point."""
    a = _add("cube", offset, size=2)
    b = _add("uv_sphere", offset, location=(0, 0, 1.6), radius=0.6, segments=48, ring_count=24)
    a.name, b.name = "Floor", "Ball"
    return [a, b]


def overlapping(offset=(0, 0, 0)):
    """A small cube fully inside a large one."""
    a = _add("cube", offset, size=2)
    b = _add("cube", offset, location=(0.2, 0.1, 0.0), size=1.0)
    a.name, b.name = "Outer", "Inner"
    return [a, b]


def apart(offset=(0, 0, 0)):
    """Objects that do not touch at all (should warn, not crash)."""
    a = _add("cube", offset, size=2)
    b = _add("uv_sphere", offset, location=(0, 0, 3.0), radius=0.6)
    a.name, b.name = "Island", "Moon"
    return [a, b]


def with_modifiers(offset=(0, 0, 0)):
    """Sources with the user's own Bevel, Subdivision and Mirror modifiers."""
    a = _add("cube", offset, size=2, smooth=False)
    bevel = a.modifiers.new("Bevel", 'BEVEL')
    bevel.width = 0.1
    bevel.segments = 3
    b = _add("cylinder", offset, location=(0.6, 0, 1.2), radius=0.3, depth=1.0)
    sub = b.modifiers.new("Subdivision", 'SUBSURF')
    sub.levels = 1
    mirror = b.modifiers.new("Mirror", 'MIRROR')
    mirror.use_axis[0] = True
    mirror.mirror_object = a
    a.name, b.name = "Panel", "Bolt"
    return [a, b]


def scaled_rotated(offset=(0, 0, 0)):
    """Non-uniform scale and rotation on both parts."""
    a = _add("cube", offset, size=2, smooth=False)
    a.scale = (2.0, 1.0, 0.5)
    b = _add("cylinder", offset, location=(0.3, 0.2, 0.6), radius=0.3, depth=1.4,
             rotation=(0.6, 0.3, 0.0))
    b.scale = (1.0, 1.5, 1.0)
    a.name, b.name = "Slab", "Pipe"
    return [a, b]


def mug(offset=(0, 0, 0)):
    """Product-viz example: a handle on a mug body."""
    a = _add("cylinder", offset, radius=1.0, depth=2.0, vertices=64)
    b = _add("torus", offset, location=(1.05, 0, 0), rotation=(math.pi / 2, 0, 0),
             major_radius=0.55, minor_radius=0.12, major_segments=48, minor_segments=24)
    a.name, b.name = "Mug", "Handle"
    return [a, b]


ALL = {
    "cube_cylinder": (cube_cylinder, 0.25),
    "sphere_torus": (sphere_torus, 0.2),
    "three_objects": (three_objects, 0.2),
    "touching": (touching, 0.3),
    "overlapping": (overlapping, 0.15),
    "apart": (apart, 0.2),
    "with_modifiers": (with_modifiers, 0.15),
    "scaled_rotated": (scaled_rotated, 0.15),
    "mug": (mug, 0.12),
}
