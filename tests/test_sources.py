import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from test_build import mod


def entry(name="La 1", url="https://example.invalid/master.m3u8", origin="List A"):
    return mod.Entry(f'#EXTINF:-1 tvg-name="{name}",{name}', [], url, name, origin)


TS = bytes([0x47]) + bytes(187)
TS *= 4


class SourceTests(unittest.TestCase):
    def test_merge_deduplicates_urls_preserves_alternatives_and_directives(self):
        config = {"channels": [{"name": "La 1"}]}
        primary = entry()
        primary.directives = ["#EXTVLCOPT:http-referrer=https://example.invalid/"]
        duplicate = entry(origin="List B")
        alternative = entry("La 1 (1080p) [Geo-blocked]", "https://example.invalid/alt.m3u8")
        playlist, status = mod.build(config, [primary, duplicate, alternative])
        self.assertEqual(status["sources_per_channel"], {"La 1": 2})
        self.assertEqual(playlist.count(primary.url), 1)
        self.assertIn(primary.directives[0], playlist)
        self.assertIn("La 1 — Fuente 2", playlist)

    def test_quality_aliases_do_not_merge_different_channels(self):
        config = {"channels": [{"name": "Antena 3"}, {"name": "La 1"}, {"name": "TV3"}, {"name": "Teledeporte", "aliases": ["tdp"]}]}
        grouped = mod.select_entries(config, [entry("Antena 3 HD"), entry("Antena 3 Internacional"),
                                              entry("La 1 Canarias"), entry("TV3CAT"), entry("+tdp")])
        self.assertEqual(len(grouped["Antena 3"]), 1)
        self.assertEqual(grouped["La 1"], [])
        self.assertEqual(grouped["TV3"], [])
        self.assertEqual(grouped["Teledeporte"], [])

    def test_partial_source_outage_keeps_other_lists(self):
        sources = [{"name": "A", "url": "https://a.invalid"}, {"name": "B", "url": "https://b.invalid"}]
        with patch.object(mod, "download_text", side_effect=[RuntimeError("offline"),
                          "#EXTM3U\n#EXTINF:-1,La 1\nhttps://example.invalid/live.m3u8\n"]):
            entries, reports = mod.load_sources(sources)
        self.assertEqual(entries[0].origin, "B")
        self.assertEqual([r["status"] for r in reports], ["error", "ok"])

    def test_all_sources_invalid_aborts(self):
        with patch.object(mod, "download_text", return_value="<html>not a playlist</html>"):
            with self.assertRaises(RuntimeError):
                mod.load_sources([{"name": "A", "url": "https://a.invalid"}])

    def test_no_valid_stream_preserves_previous_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "channels.json"
            config.write_text(json.dumps({"channels": [{"name": "La 1"}]}), encoding="utf-8")
            source = root / "input.m3u8"
            source.write_text("#EXTM3U\n#EXTINF:-1,La 1\nhttps://example.invalid/live.m3u8\n", encoding="utf-8")
            argv = ["build", "--config", str(config), "--input-file", str(source)]
            outputs = []
            for flag, name in [("--output", "tv.m3u8"), ("--status-md", "estado.md"), ("--status-json", "estado.json")]:
                path = root / name
                path.write_text("previous publication", encoding="utf-8")
                outputs.append(path)
                argv.extend([flag, str(path)])
            with patch.object(sys, "argv", argv), patch.object(mod, "probe_hls", return_value={"status": "failed", "reason": "offline"}):
                with self.assertRaises(RuntimeError):
                    mod.main()
            self.assertTrue(all(path.read_text() == "previous publication" for path in outputs))


class ProbeTests(unittest.TestCase):
    def test_redirect_relative_variant_and_segment_with_headers(self):
        stream = entry()
        stream.directives = ["#EXTVLCOPT:http-referrer=https://player.invalid/"]
        with patch.object(mod, "fetch_bytes", side_effect=[
            (b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100000\nlow/index.m3u8", "https://cdn.invalid/live/master.m3u8"),
            (b"#EXTM3U\n#EXTINF:4,\nsegment.ts", "https://cdn.invalid/live/low/index.m3u8"),
            (TS, "https://cdn.invalid/live/low/segment.ts"),
        ]) as fetch:
            self.assertEqual(mod.probe_hls(stream)["status"], "ok")
        self.assertEqual(fetch.call_args_list[1].args[0], "https://cdn.invalid/live/low/index.m3u8")
        self.assertEqual(fetch.call_args_list[2].args[0], "https://cdn.invalid/live/low/segment.ts")
        self.assertEqual(fetch.call_args_list[2].args[1]["Referer"], "https://player.invalid/")

    def test_http_200_html_is_not_a_stream(self):
        with patch.object(mod, "fetch_bytes", return_value=(b"<html>Forbidden</html>", "https://example.invalid")):
            self.assertEqual(mod.probe_hls(entry())["status"], "failed")

    def test_manifest_alone_is_not_enough(self):
        for response in [(b"<html>error</html>" * 50, "https://example.invalid"),
                         HTTPError("https://example.invalid/segment.ts", 404, "Not Found", {}, None)]:
            with self.subTest(response=type(response).__name__), patch.object(mod, "fetch_bytes", side_effect=[
                (b"#EXTM3U\n#EXTINF:4,\nsegment.ts", "https://example.invalid/live.m3u8"), response,
            ]):
                self.assertEqual(mod.probe_hls(entry())["status"], "failed")

    def test_drm_is_not_published_as_vlc_compatible(self):
        manifest = b'#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key"\n#EXTINF:4,\nsegment.ts'
        with patch.object(mod, "fetch_bytes", return_value=(manifest, "https://example.invalid/live.m3u8")):
            self.assertEqual(mod.probe_hls(entry())["status"], "failed")

    def test_fmp4_checks_initialization(self):
        manifest = b'#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4,\nsegment.m4s'
        with patch.object(mod, "fetch_bytes", side_effect=[
            (manifest, "https://example.invalid/live.m3u8"),
            (b'\x00\x00\x00\x20ftyp' + bytes(100), "https://example.invalid/init.mp4"),
            (b'\x00\x00\x00\x20moof' + bytes(300), "https://example.invalid/segment.m4s"),
        ]):
            self.assertEqual(mod.probe_hls(entry())["status"], "ok")

    def test_retries_transient_failure(self):
        with patch.object(mod, "probe_hls", side_effect=[{"status": "failed", "reason": "timeout"}, {"status": "ok"}]):
            accepted, reports = mod.validate_entries({"La 1": [entry()]}, workers=1)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(reports[0]["status"], "ok")

    def test_explicit_geoblock_is_distinct_from_generic_forbidden(self):
        for reason, expected in [("Geoblock", "geo_restricted"), ("geofence:blocked", "geo_restricted"),
                                 ("Forbidden", "failed"), ("Not Authorized jwt", "failed")]:
            with self.subTest(reason=reason), patch.object(mod, "fetch_bytes", side_effect=HTTPError(
                "https://example.invalid/master.m3u8", 403, reason, {}, None,
            )):
                self.assertEqual(mod.probe_hls(entry())["status"], expected)

    def test_geo_stream_is_kept_and_labeled_after_verified_alternative(self):
        restricted = entry("TV3", "https://example.invalid/geo.m3u8")
        verified = entry("TV3", "https://example.invalid/ok.m3u8")
        with patch.object(mod, "probe_hls", side_effect=[
            {"status": "geo_restricted", "reason": "HTTP 403: Geoblock"}, {"status": "ok"},
        ]) as probe:
            accepted, reports = mod.validate_entries({"TV3": [restricted, verified]}, workers=1)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual([r["status"] for r in reports], ["geo_restricted", "ok"])
        playlist, status = mod.build({"channels": [{"name": "TV3"}]}, accepted)
        self.assertLess(playlist.index(verified.url), playlist.index(restricted.url))
        self.assertIn("TV3 — Fuente 2 [restricción geográfica]", playlist)
        self.assertEqual(status["available_channels"], 1)


if __name__ == "__main__":
    unittest.main()
