# Design decisions

The handover (§12) left four questions open and asked for a decision to be
taken where it was clear what is best for a sellable add-on. This is what
was decided and why. Each one can be revisited. The reasons are here so it's
clear what changing one would cost.

## 1. Blender 4.2 LTS and newer only (no `bl_info`)

* Blend Master ships as a **Blender Extension** (`blender_manifest.toml`).
  It installs through *Get Extensions → Install from Disk* or by dragging
  the zip into Blender. Superhive (formerly Blender Market) and
  extensions.blender.org both accept this format.
* Older versions are not supported. The modern node-group interface API, the
  Repeat zone, multi-item Capture Attribute and Group-ID proximity queries
  that the blend relies on all landed in the 4.x series. Supporting 3.x
  would mean a second implementation and twice the testing for a shrinking
  audience.
* **Tested:** 4.2.23 LTS, 4.5.16 LTS and 5.2.3 LTS. CI runs on all three.

## 2. Organic (volume/SDF) blending for v1.0, built to keep hard surfaces crisp

The handover proposed two approaches: A (volume, robust, manifold) and
B (boolean + local smoothing, keeps topology, fragile). v1.0 implements A,
with one important upgrade that removes most of its drawbacks.

After voxelizing, every vertex is projected onto the *exact* surface of the
sources, and in the blend zone onto the exact smooth union. Away from the
junction the result therefore lies precisely on the original geometry: flat
faces are flat, curved surfaces show no voxel stepping. Hard-surface work
(Salam's main field) looks clean at default settings. Sharp convex edges
are softened only to about one voxel, which shrinks as resolution rises.

A topology-preserving "Hard-surface" mode (approach B) is on the roadmap.
It is only worth building once v1.0 has real users reporting where A falls
short. It cannot guarantee a manifold result, which was a v1.0 acceptance
criterion.

## 3. The result is a separate object, not a modifier on the active object

* The core promise is "nothing is destroyed". A separate result means no
  source object, its data or its modifier stack is ever modified. Only
  viewport display and render visibility change, and both are restored.
* It sidesteps the prototype's modifier-stack problems by construction.
  Blend Master owns exactly one modifier on an object it created, and never
  has to place itself among a user's Bevel / Subdivision / Mirror.
* Sources still show their modifiers, because the blend reads their
  *evaluated* geometry.
* Remove is trivial and exact: delete the result, restore the sources.

## 4. License: GPL-3.0-or-later

Blender add-ons import `bpy`, so they are derivative works and must be GPL
compatible. Selling GPL software is normal and allowed. It is how most
paid add-ons on Superhive work: buyers pay for the download, updates and
support. GPL-3.0-or-later is what Blender itself uses and what
extensions.blender.org expects. The copyright stays with the author.

## Smaller decisions worth knowing

* **Parameters live on the modifier.** Radius, Smoothness and the rest are
  inputs of the `BM_Blend` Geometry Nodes modifier, so they are
  animatable, appear in the Modifier panel and are the single source of
  truth. The N-panel draws the same values.
* **Radius is calibrated.** Where two perpendicular faces meet, the blend
  is as deep as a circular fillet of that radius, whatever the Smoothness.
* **Resolution is relative** (voxels along the longest side). Scaling a
  scene up can never explode memory use.
* **Shading is followed.** Smooth-shaded areas of a source are treated as
  the curved surface they represent. Flat faces and sharp edges (including
  Smooth by Angle) stay exactly as modelled.
* **Apply keeps the originals hidden by default** (`Sources: Hide`), so a
  bake can always be recovered.
* **Node groups are built in code and versioned.** A file saved by another
  Blender or add-on version has its groups rebuilt on load. Socket
  identifiers, and with them every modifier value, are kept.
