#!/usr/bin/env python3
"""Genera una M3U8 reducida desde la lista oficial de TDTChannels.

- Mantiene solo los canales definidos en channels.json.
- Conserva todas las fuentes publicadas para cada canal.
- Deduplica URLs idénticas.
- Etiqueta fuentes múltiples para que VLC permita escogerlas manualmente.
- Genera estado.md y estado.json con canales encontrados/faltantes.

Solo usa la librería estándar de Python.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_SOURCE = "https://www.tdtchannels.com/lists/tv.m3u8"
DEFAULT_EPG = "https://www.tdtchannels.com/epg/TV.xml.gz"
USER_AGENT = "Mozilla/5.0 (compatible; TDT-Favoritos-Auto/1.0; +https://github.com/)"


@dataclass
class Entry:
    extinf: str
    directives: list[str]
    url: str
    source_name: str


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return "".join(ch.lower() for ch in value if ch.isalnum())


def extinf_attr(extinf: str, attr: str) -> str | None:
    # Admite atributos con comillas dobles en EXTINF.
    match = re.search(rf'\b{re.escape(attr)}="([^"]*)"', extinf, flags=re.IGNORECASE)
    return match.group(1) if match else None


def extinf_display_name(extinf: str) -> str:
    # El nombre visible va después de la última coma de EXTINF.
    return extinf.rsplit(",", 1)[-1].strip() if "," in extinf else ""


def entry_name(extinf: str) -> str:
    return (extinf_attr(extinf, "tvg-name") or extinf_display_name(extinf)).strip()


def parse_m3u(text: str) -> list[Entry]:
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    entries: list[Entry] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("#EXTINF:"):
            i += 1
            continue

        extinf = line
        directives: list[str] = []
        url = ""
        i += 1
        while i < len(lines):
            current = lines[i]
            if not current:
                i += 1
                continue
            if current.startswith("#EXTINF:"):
                # Entrada malformada: no había URL para la anterior.
                break
            if current.startswith("#"):
                directives.append(current)
                i += 1
                continue
            url = current
            i += 1
            break

        if url:
            entries.append(Entry(extinf=extinf, directives=directives, url=url, source_name=entry_name(extinf)))

    return entries


def download_text(url: str, retries: int = 4, timeout: int = 30) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, text/plain, */*",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read()
                return data.decode("utf-8-sig", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"No se pudo descargar {url}: {last_error}")


def set_attr(extinf: str, attr: str, value: str) -> str:
    escaped = value.replace('"', "'")
    pattern = re.compile(rf'\b{re.escape(attr)}="[^"]*"', flags=re.IGNORECASE)
    replacement = f'{attr}="{escaped}"'
    if pattern.search(extinf):
        return pattern.sub(replacement, extinf, count=1)
    # Insertar tras la duración de EXTINF, antes de otros atributos.
    prefix = "#EXTINF:-1"
    if extinf.startswith(prefix):
        return prefix + " " + replacement + extinf[len(prefix):]
    return extinf


def set_display_name(extinf: str, display_name: str) -> str:
    if "," in extinf:
        return extinf.rsplit(",", 1)[0] + "," + display_name
    return extinf + "," + display_name


def build(config: dict, entries: Iterable[Entry]) -> tuple[str, dict]:
    wanted = config["channels"]
    group_title = config.get("group_title", "Favoritos España")

    lookup: dict[str, str] = {}
    canonical_order: list[str] = []
    for item in wanted:
        canonical = item["name"].strip()
        canonical_order.append(canonical)
        for candidate in [canonical, *item.get("aliases", [])]:
            key = normalize(candidate)
            if key:
                lookup[key] = canonical

    grouped: dict[str, list[Entry]] = {name: [] for name in canonical_order}
    seen_urls: dict[str, set[str]] = {name: set() for name in canonical_order}

    for entry in entries:
        canonical = lookup.get(normalize(entry.source_name))
        if not canonical:
            continue
        if entry.url in seen_urls[canonical]:
            continue
        seen_urls[canonical].add(entry.url)
        grouped[canonical].append(entry)

    output = [
        f'#EXTM3U url-tvg="{DEFAULT_EPG}"',
        "# Generada automáticamente a partir de TDTChannels.",
        "# Fuente: https://github.com/LaQuay/TDTChannels",
        "",
    ]

    found: dict[str, int] = {}
    missing: list[str] = []
    total_sources = 0

    for canonical in canonical_order:
        channel_entries = grouped[canonical]
        if not channel_entries:
            missing.append(canonical)
            continue

        count = len(channel_entries)
        found[canonical] = count
        total_sources += count
        for index, entry in enumerate(channel_entries, start=1):
            display_name = canonical if count == 1 else f"{canonical} — Fuente {index}"
            extinf = set_attr(entry.extinf, "group-title", group_title)
            # Mantener tvg-name canónico ayuda a conservar EPG/identidad aunque el nombre visible tenga Fuente N.
            extinf = set_attr(extinf, "tvg-name", canonical)
            extinf = set_display_name(extinf, display_name)
            output.append(extinf)
            output.extend(entry.directives)
            output.append(entry.url)
            output.append("")

    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": DEFAULT_SOURCE,
        "requested_channels": len(canonical_order),
        "available_channels": len(found),
        "missing_channels": missing,
        "sources_per_channel": found,
        "total_stream_sources": total_sources,
    }
    return "\n".join(output).rstrip() + "\n", status


def write_status_md(path: Path, status: dict) -> None:
    lines = [
        "# Estado de la lista",
        "",
        f"Actualizado: `{status['generated_at_utc']}`",
        "",
        f"- Canales solicitados: **{status['requested_channels']}**",
        f"- Canales con al menos una fuente: **{status['available_channels']}**",
        f"- Fuentes totales incluidas: **{status['total_stream_sources']}**",
        "",
        "## Fuentes encontradas",
        "",
    ]
    if status["sources_per_channel"]:
        lines += [f"- {name}: {count}" for name, count in status["sources_per_channel"].items()]
    else:
        lines.append("- Ninguna")

    lines += ["", "## Canales sin stream M3U8 en la fuente actual", ""]
    if status["missing_channels"]:
        lines += [f"- {name}" for name in status["missing_channels"]]
    else:
        lines.append("- Ninguno")
    lines += [
        "",
        "> Que un canal aparezca aquí no significa que haya dejado de emitir: puede estar disponible solo en web/app oficial, con DRM, token de sesión o en otro formato que VLC no pueda usar directamente.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="channels.json")
    parser.add_argument("--output", default="tv_principales.m3u8")
    parser.add_argument("--status-md", default="estado.md")
    parser.add_argument("--status-json", default="estado.json")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE)
    parser.add_argument("--input-file", help="Usa un M3U8 local en lugar de descargar (útil para pruebas).")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.input_file:
        source_text = Path(args.input_file).read_text(encoding="utf-8-sig")
    else:
        source_text = download_text(args.source_url)

    entries = parse_m3u(source_text)
    if not entries:
        raise RuntimeError("La fuente se descargó, pero no contiene entradas #EXTINF reproducibles.")

    playlist, status = build(config, entries)
    Path(args.output).write_text(playlist, encoding="utf-8", newline="\n")
    Path(args.status_json).write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_status_md(Path(args.status_md), status)

    print(
        f"OK: {status['available_channels']}/{status['requested_channels']} canales, "
        f"{status['total_stream_sources']} fuentes -> {args.output}"
    )
    if status["missing_channels"]:
        print("Sin fuente:", ", ".join(status["missing_channels"]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
