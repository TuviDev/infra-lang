"""Native PNG architecture-DAG export via Pillow — v0.7.1.

Font-agnostic assertions only: PNG signature, canvas geometry, background
pixel and palette diversity (rendered glyphs differ across platforms).
"""

from __future__ import annotations

import io
from pathlib import Path

from typer.testing import CliRunner

from infra.analyzer.graph_png import generate_dag_png, render_dag_png_bytes
from infra.cli.main import app
from infra.parser import parse

runner = CliRunner()

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_BG_RGB = (15, 23, 42)  # #0f172a — dark canvas

SRC = """\
service api {
  image: "nginx:1.25"
  depends_on: [db, session]
}
service worker {
  image: "worker:2"
  depends_on: [jobs]
}
database db {
  type: postgres
  storage: 10Gi
}
cache session {
  type: redis
}
queue jobs {
  type: rabbitmq
}
"""


def _open(png: bytes):  # -> PIL.Image.Image (typed loosely: Pillow is lazy)
    from PIL import Image

    image = Image.open(io.BytesIO(png))
    image.load()
    return image


def _write(tmp_path: Path, content: str = SRC) -> Path:
    f = tmp_path / "main.infra"
    f.write_text(content, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# render_dag_png_bytes (engine)
# --------------------------------------------------------------------------- #


class TestRenderDagPngBytes:
    def test_returns_png_bytes(self):
        data = render_dag_png_bytes(parse(SRC))
        assert isinstance(data, bytes)
        assert data.startswith(_PNG_MAGIC)
        assert len(data) > 100

    def test_canvas_dimensions_match_layout(self):
        from infra.analyzer import ui_generator as ui

        program = parse(SRC)
        nodes, edges = ui._collect_dag(program)
        ui._layout(nodes, edges)
        expected = ui._canvas_size(nodes, edges)
        image = _open(render_dag_png_bytes(program))
        assert image.size == expected
        assert image.format == "PNG"
        assert image.width > 0 and image.height > 0

    def test_canvas_background_is_dark(self):
        image = _open(render_dag_png_bytes(parse(SRC)))
        assert image.getpixel((1, 1)) == _BG_RGB

    def test_palette_has_nodes_edges_and_text(self):
        # bg + edge grey + >= 1 node accent + text anti-aliasing shades
        image = _open(render_dag_png_bytes(parse(SRC)))
        colors = image.getcolors(1 << 24)
        assert colors is not None
        assert len(colors) >= 3

    def test_service_kind_colors_present(self):
        # indigo service (#6366f1) and violet database (#8b5cf6) accents
        image = _open(render_dag_png_bytes(parse(SRC)))
        palette = {rgb for _count, rgb in image.getcolors(1 << 24)}
        assert (99, 102, 241) in palette
        assert (139, 92, 246) in palette

    def test_empty_program_renders_placeholder(self):
        program = parse('environment dev { region: "eu-central-1" }')
        image = _open(render_dag_png_bytes(program))
        assert image.size == (380, 140)
        assert image.getpixel((1, 1)) == _BG_RGB

    def test_unknown_dependency_gets_ghost_node(self):
        src = 'service solo { image: "x:1" depends_on: [external_db] }'
        image = _open(render_dag_png_bytes(parse(src)))
        palette = {rgb for _count, rgb in image.getcolors(1 << 24)}
        assert (100, 116, 139) in palette  # slate ghost node

    def test_cyclic_graph_still_renders(self):
        src = (
            'service a { image: "x:1" depends_on: [b] }\n'
            'service b { image: "y:1" depends_on: [a] }\n'
        )
        data = render_dag_png_bytes(parse(src))
        assert data.startswith(_PNG_MAGIC)

    def test_long_names_are_truncated_not_crashing(self):
        src = (
            "service a-very-long-service-name-that-overflows-the-card-badly "
            '{ image: "registry.example.com/team/image-with-long-tag:1.2.3" }\n'
        )
        data = render_dag_png_bytes(parse(src))
        assert data.startswith(_PNG_MAGIC)

    def test_render_skips_unknown_edge_endpoint(self, monkeypatch):
        # defensive `continue` when an edge endpoint has no node card
        from infra.analyzer import ui_generator as ui

        real = ui._collect_dag

        def fake_collect(spec):
            nodes, edges = real(spec)
            return nodes, [*edges, ("api", "nowhere"), ("nobody", "api")]

        monkeypatch.setattr(ui, "_collect_dag", fake_collect)
        data = render_dag_png_bytes(parse(SRC))
        assert data.startswith(_PNG_MAGIC)


# --------------------------------------------------------------------------- #
# _load_font fallback chain (Windows/macOS/containers without TTFs)
# --------------------------------------------------------------------------- #


class TestLoadFontFallbacks:
    def test_missing_candidates_fall_back_to_default(self, monkeypatch):
        from infra.analyzer import graph_png

        monkeypatch.setattr(graph_png, "_FONT_CANDIDATES", ("/no/such.ttf",))
        font = graph_png._load_font(12)
        assert font is not None
        assert hasattr(font, "getbbox") or hasattr(font, "getsize")

    def test_corrupt_ttf_is_skipped(self, tmp_path: Path, monkeypatch):
        from infra.analyzer import graph_png

        fake = tmp_path / "not-a-font.ttf"
        fake.write_text("junk, not sfnt data", encoding="utf-8")
        monkeypatch.setattr(graph_png, "_FONT_CANDIDATES", (str(fake),))
        font = graph_png._load_font(12)  # OSError inside truetype -> fallback
        assert font is not None

    def test_pillow_100_load_default_without_size(self, monkeypatch):
        from PIL import ImageFont

        from infra.analyzer import graph_png

        real = ImageFont.load_default

        def legacy_load_default(*args, **kwargs):
            if kwargs:  # simulate Pillow 10.0 (no `size` kwarg)
                raise TypeError("unexpected keyword argument 'size'")
            return real(*args, **kwargs)

        monkeypatch.setattr(graph_png, "_FONT_CANDIDATES", ())
        monkeypatch.setattr(ImageFont, "load_default", legacy_load_default)
        assert graph_png._load_font(14) is not None

    def test_full_render_with_default_font(self, monkeypatch):
        from infra.analyzer import graph_png

        monkeypatch.setattr(graph_png, "_FONT_CANDIDATES", ())
        data = render_dag_png_bytes(parse(SRC))
        assert data.startswith(_PNG_MAGIC)


# --------------------------------------------------------------------------- #
# generate_dag_png (file writer)
# --------------------------------------------------------------------------- #


class TestGenerateDagPng:
    def test_writes_png_file(self, tmp_path: Path):
        out = tmp_path / "graf.png"
        generate_dag_png(parse(SRC), str(out))
        assert out.is_file()
        assert out.read_bytes().startswith(_PNG_MAGIC)

    def test_output_bytes_match_renderer(self, tmp_path: Path):
        program = parse(SRC)
        out = tmp_path / "graf.png"
        generate_dag_png(program, str(out))
        assert out.read_bytes() == render_dag_png_bytes(program)


# --------------------------------------------------------------------------- #
# CLI integration (`infra graph --format png` / `-o graf.png`)
# --------------------------------------------------------------------------- #


class TestGraphPngCli:
    def test_format_png_writes_file(self, tmp_path: Path):
        f = _write(tmp_path)
        out = tmp_path / "graf.png"
        r = runner.invoke(app, ["graph", str(f), "--format", "png", "-o", str(out)])
        assert r.exit_code == 0, r.output
        assert "[OK]" in r.output
        assert out.read_bytes().startswith(_PNG_MAGIC)

    def test_output_suffix_infers_png_format(self, tmp_path: Path):
        f = _write(tmp_path)
        out = tmp_path / "graf.png"
        r = runner.invoke(app, ["graph", str(f), "-o", str(out)])
        assert r.exit_code == 0, r.output
        assert out.read_bytes().startswith(_PNG_MAGIC)

    def test_png_without_output_fails(self, tmp_path: Path):
        f = _write(tmp_path)
        r = runner.invoke(app, ["graph", str(f), "--format", "png"])
        assert r.exit_code == 1
        assert "[FAIL] PNG export requires --output/-o" in r.output

    def test_png_requires_exactly_one_file(self, tmp_path: Path):
        f1 = _write(tmp_path)
        f2 = tmp_path / "second.infra"
        f2.write_text(SRC, encoding="utf-8")
        out = tmp_path / "graf.png"
        r = runner.invoke(
            app, ["graph", str(f1), str(f2), "--format", "png", "-o", str(out)]
        )
        assert r.exit_code == 1
        assert "[FAIL] PNG export requires exactly one .infra file" in r.output
        assert not out.exists()

    def test_existing_formats_unaffected(self, tmp_path: Path):
        f = _write(tmp_path)
        for fmt in ("ascii", "dot", "mermaid"):
            r = runner.invoke(app, ["graph", str(f), "--format", fmt])
            assert r.exit_code == 0, r.output
        r = runner.invoke(app, ["graph", str(f), "--format", "svg"])
        assert r.exit_code == 0
        assert r.output.lstrip().startswith("<?xml")
