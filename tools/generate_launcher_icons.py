"""
generate_launcher_icons.py
--------------------------
Generates Android launcher icons for all five density buckets.

Icon design: multi-camera capture theme
  - Deep blue-grey background (#1A237E outer, #283593 inner)
  - Three white camera lenses arranged in a triangle (top-left, top-right, bottom-centre)
  - Thin white aperture ring around each lens
  - App initial "M" is NOT used — purely symbolic camera cluster

Output:
  mipmap-mdpi/ic_launcher.png        48x48
  mipmap-hdpi/ic_launcher.png        72x72
  mipmap-xhdpi/ic_launcher.png       96x96
  mipmap-xxhdpi/ic_launcher.png     144x144
  mipmap-xxxhdpi/ic_launcher.png    192x192
  (and *_round.png variants clipped to circle)
"""

import os
import math
from PIL import Image, ImageDraw

RES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "android_app", "app", "src", "main", "res"
)

DENSITIES = {
    "mipmap-mdpi":    48,
    "mipmap-hdpi":    72,
    "mipmap-xhdpi":   96,
    "mipmap-xxhdpi":  144,
    "mipmap-xxxhdpi": 192,
}

# --- Colour palette ---
BG_OUTER   = (26,  35, 126)   # #1A237E  deep indigo
BG_INNER   = (40,  53, 147)   # #283593  lighter indigo
LENS_RING  = (255, 255, 255)  # white ring
LENS_BODY  = (21,  101, 192)  # #1565C0  camera-blue lens body
LENS_GLINT = (187, 222, 251)  # #BBDEFB  light-blue glint
APERTURE   = (255, 255, 255)  # white aperture blades


def draw_camera_lens(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    """Draw a stylised camera lens: outer ring, body, inner aperture, glint."""
    ring_w = max(1, r * 0.12)

    # Outer white ring
    draw.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        fill=LENS_RING
    )
    # Lens body
    ir = r - ring_w
    draw.ellipse(
        [cx - ir, cy - ir, cx + ir, cy + ir],
        fill=LENS_BODY
    )
    # Inner dark aperture
    ar = ir * 0.52
    draw.ellipse(
        [cx - ar, cy - ar, cx + ar, cy + ar],
        fill=(10, 20, 60)
    )
    # Small glint highlight (top-left quadrant)
    gr = ar * 0.38
    gx = cx - ar * 0.38
    gy = cy - ar * 0.38
    draw.ellipse(
        [gx - gr, gy - gr, gx + gr, gy + gr],
        fill=LENS_GLINT
    )


def draw_square_icon(size: int) -> Image.Image:
    """Create the square launcher icon at the given pixel size."""
    img = Image.new("RGBA", (size, size), BG_OUTER)
    draw = ImageDraw.Draw(img)

    # Rounded-rectangle background gradient effect (two concentric rounded rects)
    pad = size * 0.06
    corner = size * 0.22
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=corner,
        fill=BG_INNER
    )

    # Three lenses in a triangle arrangement
    # Lens radius: each lens takes ~28% of icon width
    lr = size * 0.175

    # Vertical centre of the triangle cluster
    cluster_cy = size * 0.52

    # Top-left lens
    lx1 = size * 0.30
    ly1 = cluster_cy - size * 0.165
    draw_camera_lens(draw, lx1, ly1, lr)

    # Top-right lens
    lx2 = size * 0.70
    ly2 = cluster_cy - size * 0.165
    draw_camera_lens(draw, lx2, ly2, lr)

    # Bottom-centre lens (slightly larger — primary camera)
    lx3 = size * 0.50
    ly3 = cluster_cy + size * 0.195
    draw_camera_lens(draw, lx3, ly3, lr * 1.08)

    # Connecting lines between lenses (subtle, semi-transparent)
    line_color = (255, 255, 255, 60)
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    lw = max(1, int(size * 0.018))
    odraw.line([lx1, ly1, lx2, ly2], fill=line_color, width=lw)
    odraw.line([lx1, ly1, lx3, ly3], fill=line_color, width=lw)
    odraw.line([lx2, ly2, lx3, ly3], fill=line_color, width=lw)
    img = Image.alpha_composite(img, overlay)

    return img


def make_round(img: Image.Image) -> Image.Image:
    """Clip a square RGBA image to a circle."""
    size = img.size[0]
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask=mask)
    return result


def main():
    created = []

    for density, size in DENSITIES.items():
        out_dir = os.path.join(RES_DIR, density)
        os.makedirs(out_dir, exist_ok=True)

        # Square icon
        square = draw_square_icon(size)
        square_path = os.path.join(out_dir, "ic_launcher.png")
        square.save(square_path, "PNG", optimize=True)
        created.append(square_path)

        # Round icon
        rounded = make_round(square)
        round_path = os.path.join(out_dir, "ic_launcher_round.png")
        rounded.save(round_path, "PNG", optimize=True)
        created.append(round_path)

        print(f"  [{size:3d}px]  {density}/ic_launcher.png  +  ic_launcher_round.png")

    print(f"\n✅ Created {len(created)} icon files across {len(DENSITIES)} density buckets.")
    return created


if __name__ == "__main__":
    main()
