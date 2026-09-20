import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_playlist", ROOT / "scripts" / "build_playlist.py")
mod = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
import sys
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class BuildTests(unittest.TestCase):
    def test_filter_and_multiple_sources(self):
        config = {
            "group_title": "Favoritos España",
            "channels": [
                {"name": "La 1"},
                {"name": "24h", "aliases": ["24 Horas"]},
                {"name": "Telecinco"},
            ],
        }
        text = (ROOT / "tests" / "sample.m3u8").read_text(encoding="utf-8")
        entries = mod.parse_m3u(text)
        playlist, status = mod.build(config, entries)
        self.assertIn("La 1 — Fuente 1", playlist)
        self.assertIn("La 1 — Fuente 2", playlist)
        self.assertIn("24h", playlist)
        self.assertNotIn("Otro canal", playlist)
        self.assertEqual(status["sources_per_channel"]["La 1"], 2)
        self.assertIn("Telecinco", status["missing_channels"])


if __name__ == "__main__":
    unittest.main()
