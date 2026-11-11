# SPDX-License-Identifier: GPL-3.0-or-later
"""Install the built extension zip the way a user does, use it, uninstall it.

    python tools/build.py
    blender -b --factory-startup --python tests/test_install.py -- dist/blend_master-1.0.0.zip

(or ``python tests/test_install.py dist/blend_master-1.0.0.zip`` with the bpy
module).  Uses a throwaway user directory, so your own Blender setup is
never touched.  Exit code 0 means every step worked and nothing was left
behind.
"""

import os
import sys
import tempfile

ZIP = os.path.abspath(sys.argv[-1])
os.environ["BLENDER_USER_RESOURCES"] = tempfile.mkdtemp(prefix="bm_user_")

import bpy          # noqa: E402  (after the env var; must precede addon_utils)
import addon_utils  # noqa: E402

MODULE = "bl_ext.user_default.blend_master"


def check(label, condition):
    print("  %-48s %s" % (label, "ok" if condition else "FAIL"))
    if not condition:
        raise SystemExit(1)


def main():
    print("Install test on Blender %s with %s" % (bpy.app.version_string, ZIP))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = bpy.ops.extensions.package_install_files(
        filepath=ZIP, repo="user_default", enable_on_install=True)
    check("installs from disk", result == {'FINISHED'})
    check("is enabled", addon_utils.check(MODULE) == (True, True))
    prefs = bpy.context.preferences.addons.get(MODULE)
    check("preferences available", prefs is not None and prefs.preferences.source_display == 'WIRE')

    bpy.ops.mesh.primitive_cube_add(size=2)
    a = bpy.context.object
    bpy.ops.mesh.primitive_cylinder_add(radius=0.4, depth=1.6, location=(0, 0, 1.4))
    b = bpy.context.object
    with bpy.context.temp_override(active_object=a, object=a, selected_objects=[a, b]):
        check("Blend runs", bpy.ops.blend_master.blend() == {'FINISHED'})
    blended = bpy.context.view_layer.objects.active
    mesh = blended.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    check("result has geometry", len(mesh.polygons) > 1000)
    with bpy.context.temp_override(active_object=blended, object=blended,
                                   selected_objects=[blended]):
        check("Remove runs", bpy.ops.blend_master.remove() == {'FINISHED'})
    check("no node groups left", len(bpy.data.node_groups) == 0)
    check("only the user's objects left",
          sorted(o.name for o in bpy.data.objects) == ["Cube", "Cylinder"])

    bpy.ops.preferences.addon_disable(module=MODULE)
    check("disables", addon_utils.check(MODULE) == (False, False))
    repos = [r.module for r in bpy.context.preferences.extensions.repos]
    result = bpy.ops.extensions.package_uninstall(
        repo_index=repos.index("user_default"), pkg_id="blend_master")
    check("uninstalls", result == {'FINISHED'})
    print("install test passed")


if __name__ == "__main__":
    main()
