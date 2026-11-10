# SPDX-License-Identifier: GPL-3.0-or-later
"""Naming, tagging, version checks and cleanup helpers.

Blend Master never relies on object or datablock *names* to find its own
data (users rename things).  Everything it creates is tagged:

* objects   -> ``Object.blend_master.role`` ('RESULT' or 'SOURCE')
* modifiers -> name prefix ``BM_`` *and* the node group it points at
* groups    -> ID custom property ``bm_owner`` plus a build/version stamp
* collections -> ID custom property ``bm_owner``

Names are only used for display, prefixed with ``BM_`` so users can tell
what belongs to the add-on.
"""

import bpy

PREFIX = "BM_"
MODIFIER_NAME = PREFIX + "Blend"

# ID custom-property keys (only ever set on datablocks the add-on created).
TAG_OWNER = "bm_owner"
TAG_BUILD = "bm_build"
TAG_BLENDER = "bm_blender"

ROLE_NONE = 'NONE'
ROLE_RESULT = 'RESULT'
ROLE_SOURCE = 'SOURCE'


def blender_version_tag():
    """Major.minor of the running Blender, e.g. '4.2'."""
    return "%d.%d" % bpy.app.version[:2]


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------

def settings(ob):
    return getattr(ob, "blend_master", None)


def role(ob):
    s = settings(ob)
    return s.role if s is not None else ROLE_NONE


def is_result(ob):
    return ob is not None and role(ob) == ROLE_RESULT


def is_source(ob):
    return ob is not None and role(ob) == ROLE_SOURCE


def blend_modifier(ob):
    """The Blend Master modifier on a result object, or None.

    Matched by node group (tagged), not by name, so a renamed modifier is
    still found and a user's own Geometry Nodes modifiers are never touched.
    """
    if ob is None:
        return None
    from . import nodes
    for mod in ob.modifiers:
        if (mod.type == 'NODES' and mod.node_group is not None
                and mod.node_group.name == nodes.GROUP_MAIN
                and mod.node_group.get(TAG_OWNER)):
            return mod
    return None


def source_collection(result):
    """The collection a result object blends, or None."""
    s = settings(result)
    if s is None:
        return None
    return s.collection


def sources_of(result):
    coll = source_collection(result)
    if coll is None:
        return []
    return list(coll.objects)


def results_using(collection):
    """All result objects whose blend reads ``collection``."""
    return [ob for ob in bpy.data.objects
            if is_result(ob) and settings(ob).collection == collection]


def result_of(source):
    """The result object a source belongs to (first match), or None."""
    for coll in source.users_collection:
        if coll.get(TAG_OWNER):
            users = results_using(coll)
            if users:
                return users[0]
    return None


def owned_collections(ob):
    return [c for c in ob.users_collection if c.get(TAG_OWNER)]


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------

def is_blendable(ob):
    """Objects Blend Master can use as sources."""
    return (ob is not None and ob.type == 'MESH' and not is_result(ob))


def world_bounds(ob, pad=0.0):
    """Axis-aligned world-space bounding box (min, max) of an object."""
    from mathutils import Vector
    corners = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    lo = Vector((min(c[i] for c in corners) - pad for i in range(3)))
    hi = Vector((max(c[i] for c in corners) + pad for i in range(3)))
    return lo, hi


def bounds_overlap(a, b):
    return all(a[0][i] <= b[1][i] and b[0][i] <= a[1][i] for i in range(3))


def nice_radius(objects):
    """A sensible starting blend radius: ~8% of the smallest part's size."""
    sizes = []
    for ob in objects:
        lo, hi = world_bounds(ob)
        dims = sorted(hi[i] - lo[i] for i in range(3))
        size = dims[1] if dims[1] > 0 else dims[2]   # middle dimension
        if size > 0:
            sizes.append(size)
    if not sizes:
        return 0.1
    raw = min(sizes) * 0.08
    # Round to 1, 2 or 5 x 10^n so the UI shows a tidy number.
    import math
    exp = math.floor(math.log10(raw))
    base = raw / (10 ** exp)
    step = 1 if base < 1.5 else 2 if base < 3.5 else 5
    return step * (10 ** exp)


def _touching(a, b, radius):
    """True if two (tree, verts) meshes intersect, nest, or are within radius."""
    tree_a, verts_a = a
    tree_b, verts_b = b
    if tree_a.overlap(tree_b):
        return True
    # Check both directions: a small part resting on a big face is only
    # "near" when seen from the small part's vertices.
    for verts, other in ((verts_a, tree_b), (verts_b, tree_a)):
        step = max(1, len(verts) // 4000)
        for co in verts[::step]:
            loc, normal, _index, dist = other.find_nearest(co)
            if loc is None:
                continue
            if dist <= radius + 1e-6:
                return True
            if (co - loc).dot(normal) < 0.0:
                return True          # this vertex is inside the other object
    return False


def find_isolated(objects, radius, depsgraph):
    """Names of objects that neither touch, overlap, contain each other, nor
    come within ``radius`` of any other object.  Used to warn, never to block."""
    from mathutils.bvhtree import BVHTree
    meshes = {}
    for ob in objects:
        ev = ob.evaluated_get(depsgraph)
        try:
            mesh = ev.to_mesh()
        except RuntimeError:
            continue
        mw = ev.matrix_world
        verts = [mw @ v.co for v in mesh.vertices]
        polys = [tuple(p.vertices) for p in mesh.polygons]
        ev.to_mesh_clear()
        if polys:
            meshes[ob.name] = (BVHTree.FromPolygons(verts, polys), verts)
    names = list(meshes)
    near = {name: False for name in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if (not near[a] or not near[b]) and _touching(meshes[a], meshes[b], radius):
                near[a] = near[b] = True
    return [name for name in names if not near[name]]
