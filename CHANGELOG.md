# Changelog

All notable changes to Blend Master. Versions follow [semver](https://semver.org).

## 1.0.0 (unreleased)

First public version: a rewrite from the ground up (the prototype's code was
not available). It addresses the three problem areas the prototype had:

- **Modifier stack:** the result is a separate object that carries exactly
  one Blend Master modifier. Clicking Blend again updates the blend in place
  and never duplicates modifiers, node groups or collections. Sources and
  their own modifiers are never modified.
- **Blender 4.2 – 5.x compatibility:** every node and API difference between
  4.2 and 5.x is handled in one place (`nodes.py`). Examples: Menu sockets
  that replaced node properties, modifier inputs moving to
  `mod.properties.inputs`, Compare/Capture socket layouts. Tested on
  4.2.23 LTS, 4.5.16 LTS and 5.2.3 LTS.
- **Surface artifacts:** the result is projected onto the exact smooth union
  of the sources. That gives no voxel stepping, no lumps over buried parts,
  no pinching at necks, no folds, and seamless C²-smooth fillets.

### Added
- Blend, Apply (render quality; hide / keep / delete originals), Remove,
  Add Selected, Remove Selected, Select Sources / Result, Repair.
- Radius (calibrated to a circular fillet) and Smoothness (profile) controls,
  viewport and render resolutions, Shade Smooth, Show Sources As.
- Material transfer from the nearest source face.
- Automatic node-group rebuild for files saved by other Blender or add-on
  versions, keeping every modifier value.
- Warning when selected parts don't touch.
- Headless acceptance tests, an install-as-extension test and CI on three
  Blender versions.
