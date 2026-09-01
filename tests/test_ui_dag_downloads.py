"""Dashboard architecture-graph download buttons (SVG + PNG) — v0.7.1."""

from __future__ import annotations

import base64
import sys

from infra.analyzer.cost import estimate_cost
from infra.analyzer.ui_generator import (
    _dag_download_link,
    _dag_png_download_link,
    generate_ui_html,
)
from infra.parser import parse

SRC = """\
service api {
  image: "nginx:1.25"
  depends_on: [db]
}
database db {
  type: postgres
  storage: 10Gi
}
"""

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TestDashboardPngDownload:
    def test_dashboard_offers_both_downloads(self):
        spec = parse(SRC)
        html = generate_ui_html(spec, estimate_cost(spec))
        assert "Architecture DAG" in html
        assert 'download="infra-dag.svg"' in html
        assert "Download SVG" in html
        assert 'download="infra-dag.png"' in html
        assert "Download PNG" in html
        assert "data:image/png;base64," in html

    def test_dashboard_still_renders_inline_svg(self):
        spec = parse(SRC)
        html = generate_ui_html(spec, estimate_cost(spec))
        assert "<svg" in html
        assert "</svg>" in html

    def test_png_payload_round_trips_to_valid_png(self):
        link = _dag_download_link(parse(SRC))
        payload = link.split("data:image/png;base64,", 1)[1]
        # base64 alphabet never contains '"' — payload ends at the quote
        png = base64.b64decode(payload.split('"', 1)[0])
        assert png.startswith(_PNG_MAGIC)
        assert len(png) > 100

    def test_png_link_helper_inlines_base64_png(self):
        link = _dag_png_download_link(parse(SRC))
        assert 'download="infra-dag.png"' in link
        assert "Download PNG" in link


class TestPngLinkWithoutPillow:
    def test_png_link_omitted_when_pillow_missing(self, monkeypatch):
        # sys.modules[...] = None makes ``from infra.analyzer.graph_png
        # import ...`` raise ImportError, simulating a Pillow-less install.
        monkeypatch.setitem(sys.modules, "infra.analyzer.graph_png", None)
        assert _dag_png_download_link(parse(SRC)) == ""

    def test_svg_link_survives_without_pillow(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "infra.analyzer.graph_png", None)
        link = _dag_download_link(parse(SRC))
        assert "Download SVG" in link
        assert "data:image/svg+xml" in link
        assert "Download PNG" not in link
