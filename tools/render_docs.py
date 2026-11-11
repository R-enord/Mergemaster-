# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the README images with the real add-on (Cycles, CPU).

    blender -b --factory-startup --python tools/render_docs.py -- docs/images
    python tools/render_docs.py docs/images        # with the bpy module

Every image is produced by calling Blend Master's own operators, so the
pictures always show what the current code does.  Takes a few minutes.
"""

import math
import os
import subprocess
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import blend_master                       # noqa: E402
from blend_master import nodes, utils     # noqa: E402

OUT = os.path.abspath(sys.argv[-1]) if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") \
    else os.path.join(ROOT, "docs", "images")
SAMPLES = int(os.environ.get("BM_DOC_SAMPLES", "64"))
KETTLE_FLOOR = -0.355          # bottom of the kettle's base
PLATE_FLOOR = -1.0


# --------------------------------------------------------------------------
# Studio
# --------------------------------------------------------------------------

def reset(floor_z=-1.0):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = True
    scene.render.film_transparent = False
    scene.view_settings.view_transform = 'AgX' if 'AgX' in [
        i.identifier for i in scene.view_settings.bl_rna.properties['view_transform'].enum_items
    ] else 'Filmic'
    world = bpy.data.worlds.new("Studio")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.045, 0.047, 0.055, 1)
    bg.inputs[1].default_value = 1.0
    for name, loc, energy, size in (("Key", (4, -4, 6), 900, 4), ("Fill", (-5, -2, 3), 250, 5),
                                    ("Rim", (-1, 5, 5), 600, 3)):
        light = bpy.data.lights.new(name, 'AREA')
        light.energy = energy
        light.size = size
        ob = bpy.data.objects.new(name, light)
        ob.location = loc
        ob.rotation_euler = (Vector((0, 0, 0.6)) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
        scene.collection.objects.link(ob)
    bpy.ops.mesh.primitive_plane_add(size=60, location=(0, 0, floor_z - 0.001))
    floor = bpy.context.object
    floor.name = "Floor"
    floor.data.materials.append(material("Floor", (0.08, 0.08, 0.09), 0.6))
    return scene


def material(name, color, roughness, metallic=0.0, coat=0.0):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    for key in ("Coat Weight", "Clearcoat"):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = coat
            break
    return mat


def camera(target, direction, distance, lens=60):
    data = bpy.data.cameras.new("Camera")
    data.lens = lens
    cam = bpy.data.objects.new("Camera", data)
    bpy.context.scene.collection.objects.link(cam)
    target = Vector(target)
    cam.location = target + Vector(direction).normalized() * distance
    cam.rotation_euler = (target - cam.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.camera = cam
    return cam


def render(path, width=960, height=600):
    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    print("rendered", os.path.relpath(path, ROOT))


def add(kind, mat, smooth=True, **kwargs):
    getattr(bpy.ops.mesh, "primitive_%s_add" % kind)(**kwargs)
    ob = bpy.context.object
    if smooth:
        ob.data.shade_smooth()
        ob.data.set_sharp_from_angle(angle=math.radians(30))
    ob.data.materials.append(mat)
    return ob


def blend(objects, radius, smoothness=0.5, resolution=192):
    for ob in bpy.context.view_layer.objects:
        ob.select_set(False)
    for ob in objects:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    with bpy.context.temp_override(active_object=objects[0], object=objects[0],
                                   selected_objects=objects):
        bpy.ops.blend_master.blend()
    result = bpy.context.view_layer.objects.active
    mod = utils.blend_modifier(result)
    nodes.set_input(mod, "Radius", radius)
    nodes.set_input(mod, "Smoothness", smoothness)
    nodes.set_input(mod, "Viewport Resolution", resolution)
    nodes.set_input(mod, "Render Resolution", resolution)
    result.update_tag()
    return result


def strip(paths, out, labels=None):
    """Join renders side by side (with optional captions) using Pillow."""
    from PIL import Image, ImageDraw, ImageFont
    images = [Image.open(p).convert("RGB") for p in paths]
    w, h = images[0].size
    band = 46 if labels else 0
    sheet = Image.new("RGB", (w * len(images), h + band), (14, 15, 18))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    for i, im in enumerate(images):
        sheet.paste(im, (i * w, 0))
        if labels:
            text = labels[i]
            tw = draw.textlength(text, font=font)
            draw.text((i * w + (w - tw) / 2, h + 10), text, fill=(220, 220, 225), font=font)
    sheet.save(out, optimize=True)
    print("assembled", os.path.relpath(out, ROOT))


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------

def kettle():
    """A product-viz style kettle built from five primitives."""
    ceramic = material("Ceramic", (0.82, 0.80, 0.76), 0.18, coat=0.6)
    body = add("uv_sphere", ceramic, radius=1.0, segments=64, ring_count=32, location=(0, 0, 0.55))
    body.scale = (1.0, 1.0, 0.82)
    base = add("cylinder", ceramic, radius=0.72, depth=0.35, vertices=64, location=(0, 0, -0.18))
    spout = add("cone", ceramic, radius1=0.24, radius2=0.08, depth=1.15, vertices=48,
                location=(1.02, 0, 0.78), rotation=(0, math.radians(55), 0))
    handle = add("torus", ceramic, major_radius=0.55, minor_radius=0.09, major_segments=64,
                 minor_segments=24, location=(-0.92, 0, 0.62), rotation=(math.radians(90), 0, 0))
    knob = add("uv_sphere", ceramic, radius=0.16, segments=32, ring_count=16, location=(0, 0, 1.42))
    for ob in (body, base, spout, handle, knob):
        ob.location.z += 0.0
    return [body, base, spout, handle, knob]


def pipe_wall(radius_mat=None):
    steel = material("Steel", (0.62, 0.63, 0.66), 0.32, metallic=1.0)
    wall = add("cube", steel, smooth=False, size=2, location=(0, 0, 0))
    wall.scale = (1.6, 1.2, 0.5)
    wall.location.z = -0.5
    pipe = add("cylinder", steel, radius=0.38, depth=2.0, vertices=48, location=(0, 0, 0.6))
    return [wall, pipe]


def main():
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="bm_docs_")
    blend_master.register()

    # Hero: the kettle, blended.
    reset(KETTLE_FLOOR)
    parts = kettle()
    blend(parts, radius=0.14, smoothness=0.6, resolution=256)
    camera((0.05, 0, 0.45), (0.9, -1.6, 0.55), 6.4, lens=55)
    render(os.path.join(OUT, "hero.png"), 1280, 720)

    # Before / after: raw intersecting parts vs the blend.
    reset(KETTLE_FLOOR)
    parts = kettle()
    camera((0.05, 0, 0.45), (0.9, -1.6, 0.55), 6.4, lens=55)
    render(os.path.join(tmp, "before.png"), 800, 560)
    blend(parts, radius=0.14, smoothness=0.6, resolution=256)
    render(os.path.join(tmp, "after.png"), 800, 560)
    strip([os.path.join(tmp, "before.png"), os.path.join(tmp, "after.png")],
          os.path.join(OUT, "before_after.png"),
          ["Five separate primitives", "Blend Master (radius 0.14)"])

    # Radius and smoothness series on a pipe meeting a plate.
    for label, values, key in (("radius", (0.05, 0.15, 0.3), "Radius"),
                               ("smoothness", (0.0, 0.5, 1.0), "Smoothness")):
        paths = []
        for value in values:
            reset()
            parts = pipe_wall()
            result = blend(parts, radius=0.15, resolution=192)
            nodes.set_input(utils.blend_modifier(result), key, value)
            result.update_tag()
            camera((0, 0, 0.05), (1.0, -1.25, 0.75), 4.6)
            path = os.path.join(tmp, "%s_%s.png" % (label, value))
            render(path, 640, 480)
            paths.append(path)
        strip(paths, os.path.join(OUT, "%s.png" % label),
              ["%s %s" % (key, v) for v in values])

    # Touching and nearly-touching parts fuse with a neck.
    paths = []
    for gap, label in ((0.0, "touching"), (0.06, "gap")):
        reset()
        clay = material("Clay", (0.55, 0.42, 0.36), 0.55)
        block = add("cube", clay, smooth=False, size=2, location=(0, 0, -0.0))
        ball = add("uv_sphere", clay, radius=0.6, segments=48, ring_count=24,
                   location=(0, 0, 1.6 + gap))
        blend([block, ball], radius=0.3, resolution=192)
        camera((0, 0, 1.0), (1.0, -1.2, 0.35), 5.2)
        path = os.path.join(tmp, "bridge_%s.png" % label)
        render(path, 640, 480)
        paths.append(path)
    strip(paths, os.path.join(OUT, "bridge.png"),
          ["Sphere resting on a block", "6 cm apart (radius 0.3)"])

    # Live update GIF: move a source, the blend follows.
    reset()
    parts = pipe_wall()
    result = blend(parts, radius=0.16, resolution=160)
    camera((0, 0, 0.05), (1.0, -1.25, 0.75), 4.6)
    scene = bpy.context.scene
    scene.cycles.samples = max(16, SAMPLES // 3)
    frames = 36
    frame_dir = os.path.join(tmp, "frames")
    os.makedirs(frame_dir, exist_ok=True)
    pipe = parts[1]
    for i in range(frames):
        t = i / frames
        pipe.location.x = 0.75 * math.sin(2 * math.pi * t)
        pipe.rotation_euler.y = 0.45 * math.sin(2 * math.pi * t)
        result.update_tag()
        render(os.path.join(frame_dir, "f_%03d.png" % i), 480, 360)
    gif = os.path.join(OUT, "live_update.gif")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "12",
                    "-i", os.path.join(frame_dir, "f_%03d.png"),
                    "-vf", "split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer",
                    "-loop", "0", gif], check=True)
    print("assembled", os.path.relpath(gif, ROOT))
    blend_master.unregister()


if __name__ == "__main__":
    main()
