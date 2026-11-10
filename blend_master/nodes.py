# SPDX-License-Identifier: GPL-3.0-or-later
"""Build and maintain the Geometry Nodes groups used by Blend Master.

Every node group is generated here in Python (nothing is appended from a
.blend file), so the add-on is self-contained, diff-able, and can rebuild its
groups for whichever Blender version is running.

How a blend is computed (all in the result object's local space)
----------------------------------------------------------------
Each source object contributes a signed distance d_i(x) (negative inside).
The blended surface is the zero level of a *smooth minimum* of those
distances::

    f(x) = smin(d_1, d_2, ..., d_n)

``smin`` equals the plain minimum (a normal union) wherever only one object
is near, so surfaces away from the junction are untouched.  Where two objects
are within ``Radius`` of each other it dips below the minimum, which adds the
fillet material.  We use the polynomial smooth minimum::

    h    = max(k - |a - b|, 0) / k              (k = Radius)
    smin = min(a, b) - k * h**p / (2 p)         (p from Smoothness)

whose gradient is simply a blend of the two input gradients, which makes
projecting vertices onto the surface cheap (see BM_SmoothUnionField).

The mesh is produced in five stages:

1. Gather   - Collection Info of the blend's source collection, realized, each
              source tagged with an integer id (``bm_src``).
2. Coarse   - voxel union of the sources (watertight, sharp creases).
3. Fill     - on the coarse mesh, find the blend zone and grow spheres into
              the region the smooth union adds.  This gives parts that only
              touch, or sit close together, the right topology (a neck or a
              bridge) before the final pass.
4. Fine     - voxel union of sources + fill at full resolution.
5. Project  - Newton steps move every vertex onto f = 0 using the *exact*
              source surfaces, then a light relax in the blend zone.  This
              removes voxel stepping and keeps flat areas flat.

Version safety: groups are tagged with ``BUILD_ID`` and the Blender version
they were built with.  ``ensure_node_groups`` rebuilds them in place
(preserving interface socket identifiers, and therefore every modifier's
input values) whenever either changes.
"""

import bpy

from . import utils

# Bump whenever the generated graph changes. Existing files are rebuilt on load.
BUILD_ID = 1

GROUP_MAIN = utils.PREFIX + "Blend"
GROUP_FIELD = utils.PREFIX + "SmoothUnionField"
GROUP_PROJECT = utils.PREFIX + "Project"
ALL_GROUPS = (GROUP_MAIN, GROUP_FIELD, GROUP_PROJECT)

# Internal attribute names. Removed before output so they never reach the user.
ATTR_SRC = "bm_src"
ATTR_BLEND = "bm_blend"
ATTR_TMP = "bm_tmp"
ATTR_MARK = "bm_mark"
ATTR_GRAD = "bm_grad"
ATTR_FOLD = "bm_fold"

BIG = 1.0e9
FILLET_DEPTH = 1.0 - 0.5 ** 0.5     # depth of a unit circular fillet at 90 deg
BLEND_ZONE = 0.02                   # blend weight above which vertices get refined
# Buried-geometry fade window on the alignment of the two distance gradients:
# 0.6 ~ a 127 deg junction (full blend) .. 1.0 = hidden under a face (none).
BURIED_FROM = 0.6
BURIED_TO = 1.0

# Refinement budget, tuned on the tests/ scenes for crease-free fillets and
# necks (max dihedral angle at the junction) versus viewport update time.
PREVIEW_ITERATIONS = 2      # Newton steps on the blend zone (render: 4)
RELAX_BLUR = 4              # Laplacian iterations per relax round
RELAX_PROJECT = 1           # Newton steps after each relax round
PREVIEW_RELAX_ROUNDS = 4    # relax rounds in the viewport
RELAX_ROUNDS = 6            # relax rounds for render / Apply
REPAIR_ROUNDS = 2           # fold-repair rounds (cheap when nothing is folded)


# --------------------------------------------------------------------------
# Interface definitions (name, socket type, settings).  Order matters: it is
# the order shown in the modifier panel.
# --------------------------------------------------------------------------

MAIN_INPUTS = (
    ("Sources", "NodeSocketCollection", {
        "description": "Collection holding the objects to blend",
    }),
    ("Radius", "NodeSocketFloat", {
        "subtype": "DISTANCE", "default_value": 0.1, "min_value": 0.0,
        "max_value": 10000.0,
        "description": "Size of the blend: how far the fillet reaches along each surface",
    }),
    ("Smoothness", "NodeSocketFloat", {
        "subtype": "FACTOR", "default_value": 0.5, "min_value": 0.0, "max_value": 1.0,
        "description": "Profile of the blend: low is tight and crisp, high is soft and full",
    }),
    ("Viewport Resolution", "NodeSocketInt", {
        "default_value": 96, "min_value": 16, "max_value": 1024,
        "description": "Voxels along the longest side in the viewport (fast preview)",
    }),
    ("Render Resolution", "NodeSocketInt", {
        "default_value": 192, "min_value": 16, "max_value": 2048,
        "description": "Voxels along the longest side for renders and Apply (final quality)",
    }),
    ("Bridge Gaps", "NodeSocketBool", {
        "default_value": True,
        "description": "Grow material between parts that touch or sit within the blend radius",
    }),
    ("Shade Smooth", "NodeSocketBool", {
        "default_value": True,
        "description": "Use smooth shading on the result",
    }),
    # Set by Apply so a bake uses render quality even though it evaluates
    # in the viewport.  Not shown in the modifier panel.
    ("Force Final", "NodeSocketBool", {
        "default_value": False, "hide_in_modifier": True,
        "description": "Compute at render quality in the viewport as well",
    }),
)
MAIN_OUTPUTS = (("Geometry", "NodeSocketGeometry", {}),)

FIELD_INPUTS = (
    ("Sources", "NodeSocketGeometry", {}),
    ("Count", "NodeSocketInt", {"default_value": 0}),
    ("Position", "NodeSocketVector", {}),
    ("Radius", "NodeSocketFloat", {"default_value": 0.1}),
    ("Exponent", "NodeSocketFloat", {"default_value": 3.0}),
    ("Epsilon", "NodeSocketFloat", {"default_value": 0.001}),
)
FIELD_OUTPUTS = (
    ("Distance", "NodeSocketFloat", {}),
    ("Gradient", "NodeSocketVector", {}),
    ("Blend", "NodeSocketFloat", {}),
)

PROJECT_INPUTS = (
    ("Geometry", "NodeSocketGeometry", {}),
    ("Selection", "NodeSocketBool", {"default_value": True}),
    ("Sources", "NodeSocketGeometry", {}),
    ("Count", "NodeSocketInt", {"default_value": 0}),
    ("Radius", "NodeSocketFloat", {"default_value": 0.1}),
    ("Exponent", "NodeSocketFloat", {"default_value": 3.0}),
    ("Epsilon", "NodeSocketFloat", {"default_value": 0.001}),
    ("Max Step", "NodeSocketFloat", {"default_value": 1.0}),
    ("Iterations", "NodeSocketInt", {"default_value": 1, "min_value": 0}),
)
PROJECT_OUTPUTS = (
    ("Geometry", "NodeSocketGeometry", {}),
)


# --------------------------------------------------------------------------
# Small builder that keeps the graph code readable.
# --------------------------------------------------------------------------

class _Builder:
    """Thin helper around a node tree: create nodes, wire values, do math.

    Any "value" argument may be a NodeSocket (linked) or a plain Python
    constant (written to the input's default_value).
    """

    def __init__(self, tree):
        self.tree = tree
        self.nodes = tree.nodes
        self.links = tree.links

    # -- basics -----------------------------------------------------------
    def node(self, idname, **props):
        node = self.nodes.new(idname)
        for key, value in props.items():
            setattr(node, key, value)
        return node

    @staticmethod
    def sock(sockets, key):
        """Find a socket by index, identifier, or (enabled) name."""
        if isinstance(key, int):
            return sockets[key]
        for s in sockets:
            if s.identifier == key and s.enabled:
                return s
        for s in sockets:
            if s.name == key and s.enabled:
                return s
        for s in sockets:
            if s.identifier == key or s.name == key:
                return s
        raise KeyError("socket %r not found on %s" % (key, sockets.data.bl_idname))

    def set(self, node, key, value):
        """Link or assign ``value`` to input ``key`` of ``node``."""
        if value is None:
            return
        target = self.sock(node.inputs, key)
        if isinstance(value, bpy.types.NodeSocket):
            self.links.new(value, target)
        else:
            target.default_value = value

    def out(self, node, key=0):
        return self.sock(node.outputs, key)

    @staticmethod
    def option(node, prop, prop_value, socket, menu_value):
        """Set a node option across versions.

        Blender 4.x stores options such as Resolution Mode as node
        properties; 5.x turned them into Menu input sockets (which take the
        item *name*).  Use whichever this Blender has.
        """
        if prop in node.bl_rna.properties:
            setattr(node, prop, prop_value)
        else:
            node.inputs[socket].default_value = menu_value
        return node

    @staticmethod
    def label(socket, text):
        """Label the node that owns ``socket`` (shown in the node editor)."""
        socket.node.label = text
        return socket

    def make(self, idname, inputs=None, out=0, **props):
        """Create a node, wire ``inputs`` ({key: value}), return output ``out``."""
        node = self.node(idname, **props)
        for key, value in (inputs or {}).items():
            self.set(node, key, value)
        if out is None:
            return node
        return self.out(node, out)

    # -- scalar math ------------------------------------------------------
    def math(self, op, a, b=None, c=None):
        node = self.node("ShaderNodeMath", operation=op)
        for i, v in enumerate((a, b, c)):
            self.set(node, i, v)
        return node.outputs[0]

    def add(self, a, b):
        return self.math('ADD', a, b)

    def sub(self, a, b):
        return self.math('SUBTRACT', a, b)

    def mul(self, a, b):
        return self.math('MULTIPLY', a, b)

    def div(self, a, b):
        return self.math('DIVIDE', a, b)

    def vmin(self, a, b):
        return self.math('MINIMUM', a, b)

    def vmax(self, a, b):
        return self.math('MAXIMUM', a, b)

    # -- vector math ------------------------------------------------------
    def vec(self, op, a, b=None, scale=None):
        node = self.node("ShaderNodeVectorMath", operation=op)
        self.set(node, 0, a)
        if b is not None:
            self.set(node, 1, b)
        if scale is not None:
            self.set(node, "Scale", scale)
        value_ops = {'DOT_PRODUCT', 'LENGTH', 'DISTANCE'}
        return node.outputs["Value" if op in value_ops else "Vector"]

    # -- logic ------------------------------------------------------------
    def compare(self, op, a, b, data_type='FLOAT'):
        node = self.node("FunctionNodeCompare", data_type=data_type, operation=op)
        # By *name*: 4.x has per-type sockets (A, A_INT, ...) of which the
        # matching one is enabled; 5.x rebuilds plain A/B for the type.
        self.set(node, "A", a)
        self.set(node, "B", b)
        return node.outputs["Result"]

    def boolean(self, op, a, b=None):
        node = self.node("FunctionNodeBooleanMath", operation=op)
        self.set(node, 0, a)
        if b is not None:
            self.set(node, 1, b)
        return node.outputs[0]

    def switch(self, input_type, condition, if_false, if_true):
        node = self.node("GeometryNodeSwitch", input_type=input_type)
        self.set(node, "Switch", condition)
        self.set(node, "False", if_false)
        self.set(node, "True", if_true)
        return node.outputs[0]

    # -- common inputs ----------------------------------------------------
    def position(self):
        return self.make("GeometryNodeInputPosition")

    def named(self, name, data_type):
        return self.make("GeometryNodeInputNamedAttribute",
                         {"Name": name}, out="Attribute", data_type=data_type)

    def store(self, geometry, name, value, data_type, domain='POINT', selection=None):
        return self.make("GeometryNodeStoreNamedAttribute", {
            "Geometry": geometry, "Selection": selection, "Name": name, "Value": value,
        }, data_type=data_type, domain=domain)

    def group(self, tree, inputs):
        node = self.node("GeometryNodeGroup")
        node.node_tree = tree
        for key, value in inputs.items():
            self.set(node, key, value)
        return node

    # -- zones ------------------------------------------------------------
    def repeat(self, items):
        """Create a repeat zone. ``items`` is [(socket_type, name), ...].

        Returns (input_node, output_node).
        """
        rin = self.nodes.new("GeometryNodeRepeatInput")
        rout = self.nodes.new("GeometryNodeRepeatOutput")
        rin.pair_with_output(rout)
        rout.repeat_items.clear()
        for socket_type, name in items:
            rout.repeat_items.new(socket_type, name)
        return rin, rout

    # -- layout -----------------------------------------------------------
    def tidy(self):
        """Arrange nodes in columns by link depth so the graph is legible."""
        depth = {n: 0 for n in self.nodes}
        for _ in range(len(self.nodes)):
            changed = False
            for link in self.links:
                a, b = link.from_node, link.to_node
                if depth[b] < depth[a] + 1:
                    depth[b] = depth[a] + 1
                    changed = True
            if not changed:
                break
        columns = {}
        for node, d in depth.items():
            columns.setdefault(d, []).append(node)
        for d, column in columns.items():
            for row, node in enumerate(column):
                node.location = (d * 220.0, -row * 180.0)


# --------------------------------------------------------------------------
# Interface management
# --------------------------------------------------------------------------

def _sync_interface(tree, inputs, outputs):
    """Make the tree's interface match the spec, keeping existing sockets.

    Existing sockets (matched by name, direction and type) keep their
    identifier, so values stored on modifiers survive a rebuild.
    """
    iface = tree.interface
    wanted = [(name, 'INPUT', stype, opts) for name, stype, opts in inputs]
    wanted += [(name, 'OUTPUT', stype, opts) for name, stype, opts in outputs]

    existing = {}
    for item in list(iface.items_tree):
        if item.item_type != 'SOCKET':
            iface.remove(item)
            continue
        key = (item.name, item.in_out, item.socket_type)
        if key in existing or key not in {(w[0], w[1], w[2]) for w in wanted}:
            iface.remove(item)
        else:
            existing[key] = item

    for name, in_out, stype, opts in wanted:
        item = existing.get((name, in_out, stype))
        if item is None:
            item = iface.new_socket(name, in_out=in_out, socket_type=stype)
            # Only set defaults on new sockets so we never clobber user values.
            for key, value in opts.items():
                if hasattr(item, key):
                    try:
                        setattr(item, key, value)
                    except (TypeError, AttributeError, ValueError):
                        pass
        else:
            for key in ("subtype", "min_value", "max_value", "description"):
                if key in opts and hasattr(item, key):
                    try:
                        setattr(item, key, opts[key])
                    except (TypeError, AttributeError, ValueError):
                        pass

    # Enforce order: outputs first then inputs is Blender's own convention for
    # display, but positions are per-direction so moving by index is safe.
    for index, (name, in_out, stype, _opts) in enumerate(wanted):
        item = next(i for i in iface.items_tree
                    if i.item_type == 'SOCKET' and i.name == name
                    and i.in_out == in_out and i.socket_type == stype)
        if item.position != index:
            iface.move(item, index)


def _io(b):
    gin = b.node("NodeGroupInput")
    gout = b.node("NodeGroupOutput")
    return gin, gout


# --------------------------------------------------------------------------
# BM_SmoothUnionField: f(x), grad f(x) and blend weight over all sources.
# --------------------------------------------------------------------------

def _build_field(tree):
    b = _Builder(tree)
    gin, gout = _io(b)
    sources = b.out(gin, "Sources")
    position = b.out(gin, "Position")
    radius = b.vmax(b.out(gin, "Radius"), 1.0e-8)
    exponent = b.out(gin, "Exponent")
    epsilon = b.out(gin, "Epsilon")

    rin, rout = b.repeat([('FLOAT', "Distance"), ('VECTOR', "Gradient"),
                          ('FLOAT', "Blend"), ('INT', "Index")])
    b.set(rin, "Iterations", b.out(gin, "Count"))
    b.set(rin, "Distance", BIG)
    b.set(rin, "Gradient", (0.0, 0.0, 0.0))
    b.set(rin, "Blend", 0.0)
    b.set(rin, "Index", 0)

    f_acc = b.out(rin, "Distance")
    g_acc = b.out(rin, "Gradient")
    w_acc = b.out(rin, "Blend")
    index = b.out(rin, "Index")

    # Distance to source #index (unsigned) and the surface normal there.
    group_id = b.named(ATTR_SRC, 'INT')
    prox = b.node("GeometryNodeProximity", target_element='FACES')
    b.set(prox, 0, sources)                     # Target / Geometry
    b.set(prox, "Group ID", group_id)
    b.set(prox, 2, position)                    # Source / Sample Position
    b.set(prox, "Sample Group ID", index)
    closest = b.out(prox, "Position")
    dist = b.out(prox, "Distance")
    valid = b.out(prox, "Is Valid")

    # Inside/outside comes from dot(x - closest, normal).  The normal must be
    # a *pseudo-normal*: when the closest point lies on an edge (a cylinder
    # rim, a cube edge) the face normal can be perpendicular to x - closest
    # and the sign flips at random.  Vertex normals (angle weighted) averaged
    # across the edge never do, so evaluate Normal on the point domain and
    # let the sampler interpolate those.
    vertex_normal = b.make("GeometryNodeFieldOnDomain", {
        "Value": b.make("GeometryNodeInputNormal"),
    }, domain='POINT', data_type='FLOAT_VECTOR')
    nearest = b.node("GeometryNodeSampleNearestSurface", data_type='FLOAT_VECTOR')
    b.set(nearest, "Mesh", sources)
    b.set(nearest, "Value", vertex_normal)
    b.set(nearest, "Group ID", group_id)
    b.set(nearest, "Sample Position", position)
    b.set(nearest, "Sample Group ID", index)
    normal = b.out(nearest, "Value")

    # Sign from the side of the surface we are on.
    offset = b.vec('SUBTRACT', position, closest)
    side = b.vec('DOT_PRODUCT', offset, normal)
    sign = b.switch('FLOAT', b.compare('LESS_THAN', side, 0.0), 1.0, -1.0)
    d = b.switch('FLOAT', valid, BIG, b.mul(dist, sign))

    # Gradient of this source's distance: away from the surface, blended
    # into the surface normal right at the surface where offset ~ 0.
    grad = b.vec('NORMALIZE', b.vec('ADD', b.vec('SCALE', offset, scale=sign),
                                    b.vec('SCALE', normal, scale=epsilon)))

    # Buried geometry must not bulge the surface above it.  The two distance
    # gradients tell the cases apart: at a real junction they meet at the
    # crease angle (dot = cos of the angle between the faces' normals), in
    # a gap they oppose (dot = -1), but over a part hidden just under a
    # face they point the same way (dot -> 1).  Fade the blend out as they
    # align; only junctions flatter than ~127 deg are touched at all, and
    # those barely need a fillet.  Smooth, and needs no extra queries.
    alignment = b.vec('DOT_PRODUCT', g_acc, grad)
    fade = b.make("ShaderNodeMapRange", {
        "Value": alignment, "From Min": BURIED_FROM, "From Max": BURIED_TO,
        "To Min": 1.0, "To Max": 0.0,
    }, interpolation_type='SMOOTHSTEP', clamp=True)

    # Polynomial smooth minimum of (f_acc, d) and its gradient.
    diff = b.math('ABSOLUTE', b.sub(f_acc, d))
    h = b.div(b.vmax(b.sub(radius, diff), 0.0), radius)
    h = b.mul(h, b.switch('FLOAT', valid, 0.0, 1.0))
    # Scale the bulge (and its gradient weight) linearly by the fade, so it
    # is as gentle as the window above - scaling h would make it h^p steep.
    h_pow = b.mul(b.math('POWER', h, b.sub(exponent, 1.0)), fade)   # h^(p-1)
    bulge = b.div(b.mul(b.mul(h_pow, h), radius), b.mul(exponent, 2.0))
    f_new = b.sub(b.vmin(f_acc, d), bulge)

    w_large = b.mul(h_pow, 0.5)          # weight of the larger distance
    acc_smaller = b.compare('LESS_THAN', f_acc, d)
    g_small = b.switch('VECTOR', acc_smaller, grad, g_acc)
    g_large = b.switch('VECTOR', acc_smaller, g_acc, grad)
    g_new = b.vec('ADD', g_small,
                  b.vec('SCALE', b.vec('SUBTRACT', g_large, g_small), scale=w_large))
    w_new = b.vmax(w_acc, h_pow)

    b.set(rout, "Distance", f_new)
    b.set(rout, "Gradient", g_new)
    b.set(rout, "Blend", w_new)
    b.set(rout, "Index", b.math('ADD', index, 1.0))

    b.set(gout, "Distance", b.out(rout, "Distance"))
    b.set(gout, "Gradient", b.out(rout, "Gradient"))
    b.set(gout, "Blend", b.out(rout, "Blend"))
    b.tidy()


# --------------------------------------------------------------------------
# BM_Project: Newton steps onto f = 0, storing the blend weight.
# --------------------------------------------------------------------------

def _build_project(tree, field_tree):
    b = _Builder(tree)
    gin, gout = _io(b)

    rin, rout = b.repeat([('GEOMETRY', "Geometry")])
    b.set(rin, "Iterations", b.out(gin, "Iterations"))
    b.set(rin, "Geometry", b.out(gin, "Geometry"))
    geo = b.out(rin, "Geometry")

    field = b.group(field_tree, {
        "Sources": b.out(gin, "Sources"),
        "Count": b.out(gin, "Count"),
        "Position": b.position(),
        "Radius": b.out(gin, "Radius"),
        "Exponent": b.out(gin, "Exponent"),
        "Epsilon": b.out(gin, "Epsilon"),
    })
    f = b.out(field, "Distance")
    g = b.out(field, "Gradient")
    w = b.out(field, "Blend")

    # Newton step  x -= f * g / |g|^2, with |g|^2 floored and length clamped.
    g_len2 = b.vmax(b.vec('DOT_PRODUCT', g, g), 0.25)
    step = b.vec('SCALE', g, scale=b.div(f, g_len2))
    step_len = b.vmax(b.vec('LENGTH', step), 1.0e-12)
    clamp = b.vmin(b.div(b.out(gin, "Max Step"), step_len), 1.0)
    step = b.vec('SCALE', step, scale=b.mul(clamp, -1.0))

    # Evaluate the field once per iteration: store the blend weight and the
    # step together, then apply the step.
    stored = b.store(geo, ATTR_BLEND, w, 'FLOAT', selection=b.out(gin, "Selection"))
    stored = b.store(stored, ATTR_GRAD, g, 'FLOAT_VECTOR', selection=b.out(gin, "Selection"))
    stored = b.store(stored, ATTR_TMP, step, 'FLOAT_VECTOR',
                     selection=b.out(gin, "Selection"))
    moved = b.make("GeometryNodeSetPosition", {
        "Geometry": stored,
        "Selection": b.out(gin, "Selection"),
        "Offset": b.named(ATTR_TMP, 'FLOAT_VECTOR'),
    })
    b.set(rout, "Geometry", moved)
    b.set(gout, "Geometry", b.out(rout, "Geometry"))
    b.tidy()


# --------------------------------------------------------------------------
# BM_Blend: the modifier group.
# --------------------------------------------------------------------------

def _build_main(tree, field_tree, project_tree):
    b = _Builder(tree)
    gin, gout = _io(b)

    # 1. Gather -------------------------------------------------------------
    info = b.node("GeometryNodeCollectionInfo", transform_space='RELATIVE')
    b.set(info, "Collection", b.out(gin, "Sources"))
    b.set(info, "Separate Children", True)
    b.set(info, "Reset Children", False)
    instances = b.out(info, "Instances")

    count = b.make("GeometryNodeAttributeDomainSize", {"Geometry": instances},
                   out="Instance Count", component='INSTANCES')
    tagged = b.store(instances, ATTR_SRC, b.make("GeometryNodeInputIndex"),
                     'INT', domain='INSTANCE')
    realized = b.make("GeometryNodeRealizeInstances", {"Geometry": tagged})
    src = b.make("GeometryNodeSeparateComponents", {"Geometry": realized}, out="Mesh")
    b.label(src, "1 Sources")

    # Derived parameters ------------------------------------------------------
    radius = b.vmax(b.out(gin, "Radius"), 0.0)
    smooth = b.out(gin, "Smoothness")
    # Kernel exponent p: 2.5 (soft, full) .. 6 (tight, crisp).
    exponent = b.add(2.5, b.mul(3.5, b.sub(1.0, smooth)))
    # Kernel width k, calibrated so that where two perpendicular faces meet
    # the blend is exactly as deep as a circular fillet of radius Radius:
    # depth k/(2p) == Radius * (1 - 1/sqrt(2)).
    kernel = b.mul(radius, b.mul(exponent, FILLET_DEPTH * 2.0))
    is_view = b.boolean('AND', b.make("GeometryNodeIsViewport"),
                        b.boolean('NOT', b.out(gin, "Force Final")))
    res = b.switch('INT', is_view, b.out(gin, "Render Resolution"),
                   b.out(gin, "Viewport Resolution"))
    res = b.vmax(res, 4.0)

    bbox = b.node("GeometryNodeBoundBox")
    b.set(bbox, "Geometry", src)
    size = b.vec('SUBTRACT', b.out(bbox, "Max"), b.out(bbox, "Min"))
    sep = b.make("ShaderNodeSeparateXYZ", {0: size}, out=None)
    longest = b.vmax(b.vmax(sep.outputs[0], sep.outputs[1]), sep.outputs[2])
    span = b.add(longest, b.mul(radius, 2.0))
    voxel = b.vmax(b.div(span, res), b.vmax(b.div(span, 4096.0), 1.0e-5))
    epsilon = b.mul(voxel, 0.25)
    reach = b.div(kernel, exponent)              # widest gap the blend bridges

    src = b.label(_smooth_sources(b, src, count, voxel), "1b Smoothed Sources")

    common = {
        "Sources": src, "Count": count, "Radius": kernel,
        "Exponent": exponent, "Epsilon": epsilon,
    }

    def voxel_union(mesh, size):
        vol = b.option(b.node("GeometryNodeMeshToVolume"),
                       "resolution_mode", 'VOXEL_SIZE', "Resolution Mode", "Size")
        b.set(vol, "Mesh", mesh)
        b.set(vol, "Density", 1.0)
        b.set(vol, "Voxel Size", size)
        b.set(vol, "Interior Band Width", b.mul(size, 2.0))
        surf = b.option(b.node("GeometryNodeVolumeToMesh"),
                        "resolution_mode", 'GRID', "Resolution Mode", "Grid")
        b.set(surf, "Volume", b.out(vol))
        b.set(surf, "Threshold", 0.1)
        b.set(surf, "Adaptivity", 0.0)
        return b.out(surf)

    # 2. Coarse union ----------------------------------------------------------
    coarse_voxel = b.mul(voxel, 2.0)
    coarse = b.label(voxel_union(src, coarse_voxel), "2 Coarse Union")
    normal_cap = b.node("GeometryNodeCaptureAttribute", domain='POINT')
    _capture_items(normal_cap, [('VECTOR', "Normal")])
    b.set(normal_cap, "Geometry", coarse)
    b.set(normal_cap, 1, b.make("GeometryNodeInputNormal"))
    coarse = b.out(normal_cap, "Geometry")
    coarse_normal = normal_cap.outputs[1]

    # 3. Fill: seeds in the blend zone, spheres of radius -f -----------------------
    seed_field = b.group(field_tree, dict(common, Position=b.position()))
    in_zone = b.boolean('AND',
                        b.compare('GREATER_THAN', b.out(seed_field, "Blend"), 0.0),
                        b.compare('LESS_THAN', b.out(seed_field, "Distance"),
                                  b.mul(voxel, -0.5)))
    in_zone = b.boolean('AND', in_zone, b.out(gin, "Bridge Gaps"))
    seeds = b.make("GeometryNodeMeshToPoints", {
        "Mesh": coarse, "Selection": in_zone,
    }, mode='VERTICES')
    seeds = b.store(seeds, ATTR_TMP, coarse_normal, 'FLOAT_VECTOR')
    # Seeds go both outward (into fillets and gaps) and inward (into
    # regions the coarse voxels already merged, where there is no coarse
    # surface to seed from).  Any seed is safe: f is 1-Lipschitz, so a
    # sphere of radius -f(c) around c never leaves the blended solid.
    samples = 5
    dup = b.node("GeometryNodeDuplicateElements", domain='POINT')
    b.set(dup, "Geometry", seeds)
    b.set(dup, "Amount", samples)
    # duplicate k of N sits at t = (k / (N-1) * 2 - 1) * reach along the normal
    t = b.mul(b.sub(b.mul(b.div(b.out(dup, "Duplicate Index"), samples - 1.0), 2.0), 1.0),
              reach)
    pushed = b.make("GeometryNodeSetPosition", {
        "Geometry": b.out(dup, "Geometry"),
        "Offset": b.vec('SCALE', b.named(ATTR_TMP, 'FLOAT_VECTOR'), scale=t),
    })
    ball_field = b.group(field_tree, dict(common, Position=b.position()))
    ball_r = b.vmin(b.mul(b.out(ball_field, "Distance"), -1.0), reach)
    balls = b.store(pushed, ATTR_BLEND, ball_r, 'FLOAT')
    ball_r = b.named(ATTR_BLEND, 'FLOAT')
    balls = b.make("GeometryNodeDeleteGeometry", {
        "Geometry": balls,
        "Selection": b.compare('LESS_THAN', ball_r, b.mul(voxel, 0.75)),
    }, domain='POINT')
    ball_vol = b.option(b.node("GeometryNodePointsToVolume"),
                        "resolution_mode", 'VOXEL_SIZE', "Resolution Mode", "Size")
    b.set(ball_vol, "Points", balls)
    b.set(ball_vol, "Density", 1.0)
    b.set(ball_vol, "Voxel Size", voxel)
    b.set(ball_vol, "Radius", ball_r)
    fill_node = b.option(b.node("GeometryNodeVolumeToMesh"),
                         "resolution_mode", 'GRID', "Resolution Mode", "Grid")
    b.set(fill_node, "Volume", b.out(ball_vol))
    b.set(fill_node, "Threshold", 0.1)
    fill = b.out(fill_node)
    b.label(fill, "3 Gap Fill")

    # 4. Fine union ------------------------------------------------------------
    joined = b.node("GeometryNodeJoinGeometry")
    b.set(joined, 0, fill)
    b.set(joined, 0, src)
    fine = b.label(voxel_union(b.out(joined), voxel), "4 Fine Union")

    # 5. Project ---------------------------------------------------------------
    max_step = b.vmax(kernel, b.mul(voxel, 2.0))
    proj_common = dict(common)
    proj_common["Max Step"] = max_step

    def project(geometry, selection, iterations):
        return b.out(b.group(project_tree, dict(
            proj_common, Geometry=geometry, Selection=selection,
            Iterations=iterations)), "Geometry")

    def final_only(viewport_geometry, final_geometry):
        # Switch is lazy: the branch that is not chosen is never computed,
        # so render-only refinement costs nothing in the viewport.
        return b.switch('GEOMETRY', is_view, final_geometry, viewport_geometry)

    # One step everywhere: an exact snap onto the source surfaces away from
    # the blend, and it records each vertex's blend weight.
    snapped = b.label(project(fine, True, 1), "5a Snap")
    # Only vertices that the blend moves noticeably need more Newton steps;
    # the kernel's long tail moves vertices by far less than a voxel.
    in_blend = b.compare('GREATER_THAN', b.named(ATTR_BLEND, 'FLOAT'), BLEND_ZONE)
    projected = project(snapped, in_blend, b.switch('INT', is_view, 4, PREVIEW_ITERATIONS))
    b.label(projected, "5b Project Blend")

    def relax(geometry, label):
        # Even out the fillet mesh, then put it back on the surface.
        blur = b.node("GeometryNodeBlurAttribute", data_type='FLOAT_VECTOR')
        b.set(blur, "Value", b.position())
        b.set(blur, "Iterations", RELAX_BLUR)
        moved = b.make("GeometryNodeSetPosition", {
            "Geometry": geometry, "Selection": in_blend, "Position": b.out(blur, "Value"),
        })
        return b.label(project(moved, in_blend, RELAX_PROJECT), label)

    relaxed = projected
    for round_index in range(RELAX_ROUNDS):
        stage = relax(relaxed, "5c Relax %d" % (round_index + 1))
        relaxed = stage if round_index < PREVIEW_RELAX_ROUNDS else final_only(relaxed, stage)

    def repair(geometry, label):
        # Fold repair: a face whose normal points against the field gradient
        # is flipped (it can happen in tight, acute fillets).  Untangle just
        # those patches with a strong local smooth, then re-project them.
        # When nothing is folded the selection is empty and this is cheap.
        face_normal = b.make("GeometryNodeInputNormal")
        grad_on_face = b.make("GeometryNodeFieldOnDomain", {
            "Value": b.named(ATTR_GRAD, 'FLOAT_VECTOR'),
        }, domain='FACE', data_type='FLOAT_VECTOR')
        flipped = b.compare('LESS_THAN', b.vec('DOT_PRODUCT', face_normal, grad_on_face), 0.0)
        marked = b.store(geometry, ATTR_FOLD, b.switch('FLOAT', flipped, 0.0, 1.0),
                         'FLOAT', domain='FACE')
        grow = b.node("GeometryNodeBlurAttribute", data_type='FLOAT')
        b.set(grow, "Value", b.named(ATTR_FOLD, 'FLOAT'))     # face -> point: average
        b.set(grow, "Iterations", 2)
        folded = b.compare('GREATER_THAN', b.out(grow, "Value"), 0.0)
        smooth = b.node("GeometryNodeBlurAttribute", data_type='FLOAT_VECTOR')
        b.set(smooth, "Value", b.position())
        b.set(smooth, "Iterations", 6)
        untangled = b.make("GeometryNodeSetPosition", {
            "Geometry": marked, "Selection": folded, "Position": b.out(smooth, "Value"),
        })
        return b.label(project(untangled, folded, 3), label)

    repaired = relaxed
    for round_index in range(REPAIR_ROUNDS):
        repaired = repair(repaired, "5d Fold Repair %d" % (round_index + 1))

    # 6. Finish ------------------------------------------------------------------
    result = _transfer_materials(b, repaired, src)
    result = b.make("GeometryNodeSetShadeSmooth", {
        "Geometry": result, "Shade Smooth": b.out(gin, "Shade Smooth"),
    })
    for name in (ATTR_SRC, ATTR_BLEND, ATTR_TMP, ATTR_MARK, ATTR_GRAD, ATTR_FOLD):
        result = b.make("GeometryNodeRemoveAttribute", {"Geometry": result, "Name": name})

    # Nothing to blend (empty collection): output nothing, cleanly.
    has_sources = b.compare('GREATER_THAN', count, 0, data_type='INT')
    result = b.switch('GEOMETRY', has_sources, None, result)
    b.set(gout, "Geometry", result)
    b.tidy()


def _smooth_sources(b, src, count, voxel):
    """Follow the shape the user *sees*, not the raw polygons.

    A 32-sided cylinder with smooth shading looks round in Blender; if we
    projected onto its flat polygons the result would show every facet.  So
    each source with smooth-shaded faces gets Catmull-Clark subdivision,
    with edges creased wherever the shading is sharp (flat faces, edges
    marked sharp, Smooth by Angle), so flat and hard-surface areas stay
    exactly as they are.  The level (0-2) is chosen per source from its
    facet size relative to the voxel size, so dense meshes are left alone.
    """
    rin, rout = b.repeat([('GEOMETRY', "Result"), ('INT', "Index")])
    b.set(rin, "Iterations", count)
    b.set(rin, "Index", 0)
    index = b.out(rin, "Index")

    part = b.make("GeometryNodeSeparateGeometry", {
        "Geometry": src,
        "Selection": b.compare('EQUAL', b.named(ATTR_SRC, 'INT'), index, data_type='INT'),
    }, out="Selection", domain='FACE')

    def face_stat(value, output):
        stat = b.node("GeometryNodeAttributeStatistic", data_type='FLOAT', domain='FACE')
        b.set(stat, "Geometry", part)
        b.set(stat, "Attribute", value)
        return b.out(stat, output)

    face_smooth = b.out(b.node("GeometryNodeInputShadeSmooth"), "Smooth")
    any_smooth = face_stat(face_smooth, "Max")
    edge = b.math('SQRT', face_stat(b.make("GeometryNodeInputMeshFaceArea"), "Mean"))
    level = b.math('CEIL', b.math('LOGARITHM', b.vmax(b.div(edge, b.mul(voxel, 2.0)), 1.0), 2.0))
    level = b.mul(b.vmin(b.vmax(level, 0.0), 2.0), any_smooth)

    # Crease = 1 on edges that render sharp.
    edge_sharp = b.sub(1.0, b.out(b.node("GeometryNodeInputEdgeSmooth"), "Smooth"))
    face_flat = b.make("GeometryNodeFieldOnDomain", {"Value": b.sub(1.0, face_smooth)},
                       domain='FACE', data_type='FLOAT')
    crease = b.vmax(edge_sharp, b.math('CEIL', face_flat))

    sub = b.node("GeometryNodeSubdivisionSurface")
    b.option(sub, "boundary_smooth", 'PRESERVE_CORNERS', "Boundary Smooth", "Keep Corners")
    b.option(sub, "uv_smooth", 'NONE', "UV Smooth", "None")
    b.set(sub, "Mesh", part)
    b.set(sub, "Level", level)
    b.set(sub, "Edge Crease", crease)
    smoothed = b.store(b.out(sub), ATTR_SRC, index, 'INT')

    joined = b.node("GeometryNodeJoinGeometry")
    b.set(joined, 0, smoothed)
    b.set(joined, 0, b.out(rin, "Result"))
    b.set(rout, "Result", b.out(joined))
    b.set(rout, "Index", b.add(index, 1.0))
    return b.out(rout, "Result")


def _capture_items(node, items):
    """Configure Capture Attribute items (multi-item API, Blender 4.2+)."""
    node.capture_items.clear()
    for socket_type, name in items:
        node.capture_items.new(socket_type, name)


def _transfer_materials(b, mesh, src):
    """Give each result face the material of the nearest source face.

    The result mesh carries no material list of its own, so we join the
    (marked) sources in first - which brings their merged material list
    along - then delete them again and set indices by nearest source face.
    """
    marked = b.store(src, ATTR_MARK, True, 'BOOLEAN')
    joined = b.node("GeometryNodeJoinGeometry")
    b.set(joined, 0, mesh)
    b.set(joined, 0, marked)       # links stack: marked ends up first
    trimmed = b.make("GeometryNodeDeleteGeometry", {
        "Geometry": b.out(joined),
        "Selection": b.named(ATTR_MARK, 'BOOLEAN'),
    }, domain='POINT')
    # Drop every attribute the join dragged in from the sources (UV maps
    # and custom data, zero-filled on the result).  Built-ins that cannot be
    # removed are skipped by the node; material_index is re-set just below.
    wipe = b.option(b.node("GeometryNodeRemoveAttribute"),
                    "pattern_mode", 'WILDCARD', "Pattern Mode", "Wildcard")
    b.set(wipe, "Geometry", trimmed)
    b.set(wipe, "Name", "*")
    trimmed = b.out(wipe)
    sample = b.node("GeometryNodeSampleNearestSurface", data_type='INT')
    b.set(sample, "Mesh", src)
    b.set(sample, "Value", b.make("GeometryNodeInputMaterialIndex"))
    b.set(sample, "Sample Position", b.position())
    return b.make("GeometryNodeSetMaterialIndex", {
        "Geometry": trimmed, "Material Index": b.out(sample, "Value"),
    })


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def _get_or_new(name):
    tree = bpy.data.node_groups.get(name)
    if tree is not None and tree.bl_idname != 'GeometryNodeTree':
        tree = None
    if tree is None:
        tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    return tree


def _is_current(tree):
    return (tree is not None
            and tree.get(utils.TAG_BUILD) == BUILD_ID
            and tree.get(utils.TAG_BLENDER) == utils.blender_version_tag()
            and len(tree.nodes) > 0)


def _rebuild(tree, inputs, outputs, build):
    tree.nodes.clear()
    _sync_interface(tree, inputs, outputs)
    build()
    tree[utils.TAG_BUILD] = BUILD_ID
    tree[utils.TAG_BLENDER] = utils.blender_version_tag()
    tree[utils.TAG_OWNER] = True


def ensure_node_groups(force=False):
    """Return the main group, (re)building any group that is stale.

    Safe to call repeatedly: an up-to-date group is returned untouched, so
    clicking Blend many times never creates duplicates.
    """
    field = _get_or_new(GROUP_FIELD)
    project = _get_or_new(GROUP_PROJECT)
    main = _get_or_new(GROUP_MAIN)
    stale = force or not all(_is_current(t) for t in (field, project, main))
    if stale:
        _rebuild(field, FIELD_INPUTS, FIELD_OUTPUTS, lambda: _build_field(field))
        _rebuild(project, PROJECT_INPUTS, PROJECT_OUTPUTS,
                 lambda: _build_project(project, field))
        _rebuild(main, MAIN_INPUTS, MAIN_OUTPUTS,
                 lambda: _build_main(main, field, project))
        # Helper groups are only used inside BM_Blend; hide them from the
        # Add > Group menu clutter by marking them as non-modifier assets.
        for helper in (field, project):
            if hasattr(helper, "is_modifier"):
                helper.is_modifier = False
        if hasattr(main, "is_modifier"):
            main.is_modifier = True
    return main


def remove_node_groups_if_unused():
    """Delete Blend Master's node groups once no modifier uses them."""
    main = bpy.data.node_groups.get(GROUP_MAIN)
    if main is not None:
        in_use = any(
            m.type == 'NODES' and m.node_group == main
            for ob in bpy.data.objects for m in ob.modifiers
        )
        if in_use:
            return False
    for name in ALL_GROUPS:
        tree = bpy.data.node_groups.get(name)
        if tree is not None and tree.get(utils.TAG_OWNER):
            bpy.data.node_groups.remove(tree)
    return True


def input_identifier(tree, name):
    """Identifier of the main group's input socket called ``name``."""
    for item in tree.interface.items_tree:
        if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == name:
            return item.identifier
    raise KeyError(name)


# --------------------------------------------------------------------------
# Modifier input values, across Blender versions.
#
#   4.2 - 4.x : ID properties on the modifier          mod["Socket_3"]
#   5.0+      : RNA structs on mod.properties.inputs   ...inputs.Socket_3.value
#
# Everything else in the add-on goes through these three functions.
# --------------------------------------------------------------------------

def _rna_inputs(mod):
    props = getattr(mod, "properties", None)
    return getattr(props, "inputs", None) if props is not None else None


def get_input(mod, name):
    ident = input_identifier(mod.node_group, name)
    inputs = _rna_inputs(mod)
    if inputs is not None:
        return getattr(inputs, ident).value
    return mod[ident]


def set_input(mod, name, value):
    ident = input_identifier(mod.node_group, name)
    inputs = _rna_inputs(mod)
    if inputs is not None:
        getattr(inputs, ident).value = value
    else:
        mod[ident] = value


def draw_input(layout, mod, name, text=None, **kwargs):
    try:
        ident = input_identifier(mod.node_group, name)
    except KeyError:
        return
    text = name if text is None else text
    inputs = _rna_inputs(mod)
    if inputs is not None:
        layout.prop(getattr(inputs, ident), "value", text=text, **kwargs)
    else:
        layout.prop(mod, '["%s"]' % ident, text=text, **kwargs)
