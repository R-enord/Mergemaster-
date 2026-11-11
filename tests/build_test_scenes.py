# SPDX-License-Identifier: GPL-3.0-or-later
"""Build every handover test scene side by side for manual testing.

Open them in Blender's UI (nothing is blended, so you can try the workflow):

    blender --python tests/build_test_scenes.py

Or save a file, optionally with every scene already blended:

    blender -b --python tests/build_test_scenes.py -- --save test_scenes.blend
    blender -b --python tests/build_test_scenes.py -- --save blended.blend --blend

(--blend needs the add-on installed or the repository importable.)
Each scene gets its own collection, named after it, laid out on a grid.
"""

import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scenes  # noqa: E402

SPACING = 6.0


def main(argv):
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    save = args[args.index("--save") + 1] if "--save" in args else None
    do_blend = "--blend" in args

    bpy.ops.wm.read_homefile(use_empty=True)
    root = bpy.context.scene.collection
    built = []
    for i, (name, (builder, radius)) in enumerate(scenes.ALL.items()):
        coll = bpy.data.collections.new(name)
        root.children.link(coll)
        layer = bpy.context.view_layer.layer_collection.children[coll.name]
        bpy.context.view_layer.active_layer_collection = layer
        offset = ((i % 3) * SPACING, (i // 3) * SPACING, 0.0)
        objs = builder(offset)
        built.append((name, objs, radius))

    if do_blend:
        if not hasattr(bpy.ops, "blend_master") or not hasattr(bpy.ops.blend_master, "blend"):
            sys.path.insert(0, os.path.dirname(HERE))
            import blend_master
            blend_master.register()
        from blend_master import nodes, utils
        for name, objs, radius in built:
            for ob in bpy.context.view_layer.objects:
                ob.select_set(False)
            for ob in objs:
                ob.select_set(True)
            bpy.context.view_layer.objects.active = objs[0]
            with bpy.context.temp_override(active_object=objs[0], object=objs[0],
                                           selected_objects=objs):
                bpy.ops.blend_master.blend()
            result = bpy.context.view_layer.objects.active
            nodes.set_input(utils.blend_modifier(result), "Radius", radius)
            result.update_tag()
            print("blended", name)

    if save:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(save))
        print("saved", save)


main(sys.argv)
