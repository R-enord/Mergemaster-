# Testing Blend Master

There are two layers: an automated suite that runs headless on every push
(CI), and a short manual pass in the real UI before each release. The
manual pass covers what a headless Blender cannot: undo, drawing, and how
it feels.

## Automated

```bash
# Any Blender install (4.2 or newer):
blender -b --factory-startup --python tests/run_tests.py

# Or with Blender's Python module from PyPI (what CI uses):
pip install bpy==4.2.23          # Python 3.11   (or bpy==5.2.3 on Python 3.13)
python tests/run_tests.py
python tests/run_tests.py -k remove      # run a subset

# Build the zip and install it as a real extension, use it, uninstall it:
python tools/build.py
python tests/test_install.py dist/blend_master-1.0.0.zip
```

What the suite checks, mapped to the v1.0 acceptance criteria (handover §8):

| Criterion | Test(s) |
|---|---|
| Installs cleanly as an Extension, no console errors | `test_install.py`, `test_register_cycle` |
| Blend, Apply, Remove work | `test_blend_creates_result`, `test_apply_*`, `test_remove_*` |
| Repeated Blend never duplicates modifiers or groups | `test_repeat_blend_never_duplicates` |
| Remove leaves the scene exactly as before | `test_remove_restores_scene_exactly` compares a full snapshot: objects, data, collections, node groups, display state, transforms, custom *and* system properties, modifier settings |
| Editing a source updates the result live | `test_live_update` (moving and editing mesh data) |
| Manifold result | `test_all_scenes_watertight`, every scene in `tests/scenes.py` |
| User's modifiers are never touched | `test_user_modifiers_untouched`, `test_apply_keeps_user_modifiers_on_result` |
| Objects that don't touch warn, not crash | `test_isolated_objects_warn_not_crash` |
| Files from other versions | `test_file_from_other_version_is_rebuilt`, `test_node_group_rebuild_keeps_values` |
| UI draws | `test_ui_panels_draw` (every panel, every property path, every button's operator) |

Undo cannot be tested headless, because Blender disables the undo stack
without a UI. `test_undo` reports itself as skipped. It is covered by the
manual pass below.

## Manual pass (about 10 minutes)

Build the scenes, then open the file in Blender:

```bash
blender -b --python tests/build_test_scenes.py -- --save test_scenes.blend
```

Every scene is in its own collection, on a grid.

1. **Install.** Drag `dist/blend_master-1.0.0.zip` into a fresh Blender
   (or *Edit → Preferences → Get Extensions → ⌄ → Install from Disk*).
   Open *Window → Toggle System Console* on Windows, or start Blender from a
   terminal. There must be no errors.
2. **Blend.** In `cube_cylinder`, click the Cube, Shift-click the Cylinder,
   then *N-panel → Blend Master → Blend*. A smooth fillet appears and the
   two sources turn into wireframes.
3. **Live.** Select the Cylinder wireframe and move it (`G`), rotate it, and
   scale it non-uniformly. The blend follows. Tab into Edit Mode on the Cube,
   move a face, and Tab out. The blend follows.
4. **Settings.** Drag Radius and Smoothness. Toggle Shade Smooth. Values also appear on the `BM_Blend` modifier in the Properties
   editor. Right-click Radius → *Insert Keyframe* works.
5. **Click Blend repeatedly** with everything selected. Check the Outliner
   (*Blender File* view) and the Modifier panel: still one result, one
   modifier, three `BM_` node groups.
6. **Undo.** After each of Blend, Apply and Remove, press `Ctrl+Z` once.
   The scene returns to exactly the state before that click. `Ctrl+Shift+Z`
   redoes it.
7. **Remove.** With the result selected, click *Remove*. The sources come
   back exactly as they were (solid, renderable). The Outliner's *Blender
   File* view shows no `BM_` node groups or collections.
8. **Apply.** Blend again and click *Apply*. Try each *Sources* option:
   Hide, Keep, Delete. The result is a plain mesh. *Select → Select All by
   Trait → Non Manifold* in Edit Mode selects nothing.
9. **Other scenes.** Blend each remaining collection:
   - `touching` and `overlapping` give one shell with no lumps on the faces.
   - `apart` shows a warning and stays two shells.
   - `with_modifiers`: the Bevel, Subdivision and Mirror settings are unchanged
     afterwards, and the mirrored copy is blended too.
   - `scaled_rotated` behaves normally.
10. **Save and reload** the file, with the add-on enabled, then with it
    disabled. The result keeps working in both cases (it is a plain Geometry
    Nodes modifier). Open the file in the other supported Blender version
    (4.2 ↔ 5.x) too.
11. **Render** a frame (F12). Only the result renders, not the sources.

Report anything odd with the scene name, Blender version, the settings, and a
screenshot. That is usually enough to reproduce it with `tests/scenes.py`.
