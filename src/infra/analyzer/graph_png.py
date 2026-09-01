"""Native PNG rendering of the architecture DAG (v0.7.1).

Pure-Python rasterizer built on Pillow — no native/system dependencies
(no Cairo, no Graphviz, no headless browser). It reuses the exact same
collector and longest-path layered layout as the SVG/dashboard renderers
(``infra.analyzer.ui_generator``), so the PNG always matches what the
dashboard shows.

Theme: dark slate canvas with blue/violet node accents, mirroring the
dashboard palette. Both Pillow and the layout helpers are imported
lazily inside functions, so importing this module stays cheap and never
creates an import cycle with ``ui_generator`` (which lazily imports this
module back for the dashboard download link).
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Tuple, Union

from infra.parser import ast_nodes as n

if TYPE_CHECKING:  # Pillow stays a lazy import at runtime (see module docstring)
    from PIL import ImageDraw, ImageFont

# ---------------------------------------------------------------------- #
# Theme (dark canvas, blue/violet accents — matches the dashboard)
# ---------------------------------------------------------------------- #

_BG = "#0f172a"
_EDGE = "#94a3b8"
_TEXT_NAME = "#ffffff"
_TEXT_SUB = "#c7d2fe"
_EMPTY_NOTE = "#94a3b8"

_KIND_COLORS: Dict[str, str] = {
    "service": "#6366f1",  # indigo — blue/violet accent
    "database": "#8b5cf6",  # violet
    "cache": "#38bdf8",  # sky
    "queue": "#22d3ee",  # cyan
    "external": "#64748b",  # slate
    "network": "#10b981",  # emerald
    "secret_store": "#f43f5e",  # rose
    "network_policy": "#f59e0b",  # amber
}
_DEFAULT_NODE = "#64748b"

_RADIUS = 8  # rounded-rectangle corner radius
_PAD_X = 12  # inner text padding
_EDGE_WIDTH = 2
_ARROW_LEN = 11.0  # arrowhead length along the edge direction
_ARROW_HALF = 5.0  # arrowhead half-width

#: TTF candidates per OS (Linux → Windows → macOS); first hit wins, then we
#: fall back to Pillow's built-in default font so rendering always works.
_FONT_CANDIDATES: Tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)

#: Pillow's two font classes (truetype → FreeTypeFont, fallback → ImageFont);
#: both support ``textlength`` and drawing. Forward refs resolve only for
#: type checkers — Pillow is imported lazily at runtime.
_FontT = Union["ImageFont.FreeTypeFont", "ImageFont.ImageFont"]


def _load_font(size: int) -> _FontT:
    """Load a legible TTF at *size* or fall back to Pillow's default font."""
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        if not Path(candidate).is_file():
            continue
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:  # corrupt/unreadable font file — try the next one
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:  # Pillow 10.0 has no ``size`` parameter
        return ImageFont.load_default()


def _truncate(
    draw: ImageDraw.ImageDraw, text: str, font: _FontT, max_width: float
) -> str:
    """Ellipsize *text* with ``…`` so it fits *max_width* pixels wide."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return (text + "…") if text else ""


def _arrowhead(
    x1: float, y1: float, x2: float, y2: float
) -> List[Tuple[float, float]]:
    """Triangle polygon whose tip sits on the destination's left edge."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length  # unit vector along the edge
    px, py = -uy, ux  # perpendicular unit vector
    base_x = x2 - _ARROW_LEN * ux
    base_y = y2 - _ARROW_LEN * uy
    return [
        (x2, y2),
        (base_x + _ARROW_HALF * px, base_y + _ARROW_HALF * py),
        (base_x - _ARROW_HALF * px, base_y - _ARROW_HALF * py),
    ]


def _service_images(spec: n.Program) -> Dict[str, str]:
    """Map service name → its container image tag (for the node caption)."""
    images: Dict[str, str] = {}
    for stmt in spec.statements:
        if isinstance(stmt, n.ServiceDef) and stmt.image:
            images[stmt.name] = stmt.image
    return images


def render_dag_png_bytes(spec: n.Program) -> bytes:
    """Render the architecture DAG of *spec* as raw PNG bytes.

    Shares the collector/layout of the SVG renderer, so nodes, edges and
    geometry are identical to the dashboard view — only the rasterization
    (and the dark theme) differ. Service captions show the container image
    tag instead of the bare ``service`` kind when an image is declared.
    """
    from PIL import Image, ImageDraw

    from infra.analyzer import ui_generator as ui

    nodes, edges = ui._collect_dag(spec)
    ui._layout(nodes, edges)
    width, height = ui._canvas_size(nodes, edges)
    images = _service_images(spec)

    image = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(image)
    name_font = _load_font(14)
    sub_font = _load_font(11)

    by_name = {nd.name: nd for nd in nodes}
    for src, dst in edges:  # edges first so node cards paint over line ends
        a, b = by_name.get(src), by_name.get(dst)
        if a is None or b is None:  # defensive: unknown endpoint
            continue
        x1, y1 = a.x + ui._NODE_W, a.y + ui._NODE_H / 2
        x2, y2 = b.x, b.y + ui._NODE_H / 2
        draw.line((x1, y1, x2, y2), fill=_EDGE, width=_EDGE_WIDTH)
        draw.polygon(_arrowhead(x1, y1, x2, y2), fill=_EDGE)

    for nd in nodes:
        color = _KIND_COLORS.get(nd.kind, _DEFAULT_NODE)
        x, y = nd.x, nd.y
        draw.rounded_rectangle(
            (x, y, x + ui._NODE_W, y + ui._NODE_H), radius=_RADIUS, fill=color
        )
        sub_text = nd.kind if nd.sub == nd.kind else f"{nd.kind} · {nd.sub}"
        if nd.kind == "service" and images.get(nd.name):
            sub_text = images[nd.name]  # image tag on service nodes
        name = _truncate(draw, nd.name, name_font, ui._NODE_W - 2 * _PAD_X)
        sub = _truncate(draw, sub_text, sub_font, ui._NODE_W - 2 * _PAD_X)
        draw.text((x + _PAD_X, y + 8), name, font=name_font, fill=_TEXT_NAME)
        draw.text((x + _PAD_X, y + 27), sub, font=sub_font, fill=_TEXT_SUB)

    if not nodes:
        draw.text(
            (ui._MARGIN, ui._MARGIN),
            "No workloads declared.",
            font=name_font,
            fill=_EMPTY_NOTE,
        )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_dag_png(program: n.Program, output_path: str) -> None:
    """Render the architecture DAG of *program* and write PNG file to disk."""
    Path(output_path).write_bytes(render_dag_png_bytes(program))
