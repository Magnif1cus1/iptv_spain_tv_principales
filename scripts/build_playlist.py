#!/usr/bin/env python3
"""Combina listas públicas y comprueba sus streams HLS antes de publicarlos.

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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlsplit

DEFAULT_SOURCE = "https://www.tdtchannels.com/lists/tv.m3u8"
DEFAULT_EPG = "https://www.tdtchannels.com/epg/TV.xml.gz"
USER_AGENT = "Mozilla/5.0 (compatible; TDT-Favoritos-Auto/1.0; +https://github.com/)"


@dataclass
class Entry:
    extinf: str
    directives: list[str]
    url: str
    source_name: str
    origin: str = ""
    validation_status: str = ""


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return "".join(ch.lower() for ch in value if ch.isalnum())


def channel_key(value: str) -> str:
    # Quitar solo calidad/estado; conservar regiones e identidades internacionales.
    value = re.sub(r"\[(?:Geo-blocked|Not 24/7)\]", "", value, flags=re.I)
    value = re.sub(r"\(\d{3,4}p\)", "", value, flags=re.I).strip()
    value = re.sub(r"\s+(?:SD|HD|FHD|UHD|4K)$", "", value, flags=re.I)
    return normalize(value.replace("+", " plus "))


def select_entries(config: dict, entries: Iterable[Entry]) -> dict[str, list[Entry]]:
    lookup: dict[str, str] = {}
    grouped = {item["name"]: [] for item in config["channels"]}
    seen = {name: set() for name in grouped}
    for item in config["channels"]:
        for alias in [item["name"], *item.get("aliases", [])]:
            key = channel_key(alias)
            if key in lookup and lookup[key] != item["name"]:
                raise ValueError(f"Alias ambiguo: {alias}")
            lookup[key] = item["name"]
    for entry in entries:
        canonical = lookup.get(channel_key(entry.source_name))
        if canonical and entry.url not in seen[canonical]:
            seen[canonical].add(entry.url)
            grouped[canonical].append(entry)
    return grouped


def load_sources(sources: list[dict]) -> tuple[list[Entry], list[dict]]:
    entries: list[Entry] = []
    reports: list[dict] = []
    for source in sources:
        report = {"name": source["name"], "url": source["url"]}
        try:
            text = download_text(source["url"], retries=2, timeout=20)
            if not text.lstrip().startswith("#EXTM3U"):
                raise ValueError("La respuesta no es una lista M3U")
            parsed = parse_m3u(text)
            if not parsed:
                raise ValueError("Lista sin entradas")
            for entry in parsed:
                entry.origin = source["name"]
            entries.extend(parsed)
            report.update(status="ok", entries=len(parsed))
        except (RuntimeError, ValueError) as exc:
            report.update(status="error", error=str(exc))
        reports.append(report)
        print(f"Lista {source['name']}: {report['status']}", flush=True)
    if not entries:
        raise RuntimeError("Ninguna lista disponible; se conserva la publicación anterior.")
    return entries, reports


def fetch_bytes(url: str, headers: dict, timeout: int, limit: int) -> tuple[bytes, str]:
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("URL no HTTP(S)")
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(limit), response.geturl()


def probe_hls(entry: Entry, timeout: int = 10) -> dict:
    """Verifica manifiesto y bytes de un fragmento; no certifica decodificación."""
    headers = {"User-Agent": USER_AGENT}
    for directive in entry.directives:
        for option, header in (("http-user-agent", "User-Agent"), ("http-referrer", "Referer")):
            prefix = f"#EXTVLCOPT:{option}="
            if directive.startswith(prefix):
                headers[header] = directive[len(prefix):]
    try:
        url = entry.url
        for _ in range(5):
            data, final_url = fetch_bytes(url, headers, timeout, 2_000_000)
            text = data.decode("utf-8-sig", errors="replace").strip()
            if not text.startswith("#EXTM3U"):
                raise ValueError("La respuesta no es un manifiesto HLS")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            encrypted = False
            for line in lines:
                if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
                    method = re.search(r"METHOD=([^,]+)", line)
                    if not method or method[1] not in ("NONE", "AES-128"):
                        raise ValueError("Cifrado DRM/no compatible con la lista VLC")
                    if method[1] == "AES-128":
                        if 'KEYFORMAT=' in line and 'KEYFORMAT="identity"' not in line:
                            raise ValueError("Clave DRM no compatible")
                        key_uri = re.search(r'URI="([^"]+)"', line)
                        if not key_uri:
                            raise ValueError("Falta URI de clave HLS")
                        key, _ = fetch_bytes(urljoin(final_url, key_uri[1]), headers, timeout, 17)
                        if len(key) != 16:
                            raise ValueError("Clave AES-128 no disponible")
                        encrypted = True
            urls = [line for line in lines if not line.startswith("#")]
            if not urls:
                raise ValueError("Manifiesto sin variantes ni fragmentos")
            if any(line.startswith("#EXT-X-STREAM-INF:") for line in lines):
                url = urljoin(final_url, urls[0])
                continue
            if not any(line.startswith("#EXTINF:") for line in lines):
                raise ValueError("No contiene fragmentos HLS")
            for line in lines:
                if line.startswith("#EXT-X-MAP:"):
                    init_uri = re.search(r'URI="([^"]+)"', line)
                    if not init_uri:
                        raise ValueError("Falta URI del fragmento de inicialización")
                    init, _ = fetch_bytes(urljoin(final_url, init_uri[1]), headers, timeout, 4096)
                    if init[4:8] not in (b"ftyp", b"styp", b"moov"):
                        raise ValueError("Inicialización multimedia no disponible")
            # Un fragmento reciente, sin descargar el vídeo completo.
            segment, _ = fetch_bytes(urljoin(final_url, urls[-1]), headers, timeout, 4096)
            if segment.lstrip().lower().startswith((b"<!doctype", b"<html")):
                raise ValueError("El fragmento devuelve HTML")
            is_ts = len(segment) > 376 and segment[0] == segment[188] == segment[376] == 0x47
            is_mp4 = segment[4:8] in (b"ftyp", b"styp", b"moof", b"sidx")
            is_audio = segment.startswith(b"ID3") or (len(segment) > 1 and segment[0] == 255 and segment[1] & 0xF6 == 0xF0)
            if len(segment) < 188 or (not encrypted and not (is_ts or is_mp4 or is_audio)):
                raise ValueError("El fragmento no contiene datos multimedia reconocibles")
            return {"status": "ok", "check": "hls_manifest_and_segment"}
        raise ValueError("Demasiados manifiestos anidados")
    except (OSError, ValueError) as exc:
        geo_restricted = (
            isinstance(exc, urllib.error.HTTPError)
            and exc.code == 403
            and any(marker in str(exc.reason).lower() for marker in ("geoblock", "geo-block", "geofence"))
        )
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        return {"status": "geo_restricted" if geo_restricted else "failed", "reason": str(exc)}


def validate_entries(grouped: dict[str, list[Entry]], workers: int = 8, timeout: int = 10):
    candidates = [(name, entry) for name, entries in grouped.items() for entry in entries]
    def check(candidate):
        name, entry = candidate
        result = probe_hls(entry, timeout)
        if result["status"] == "failed":
            result = probe_hls(entry, timeout)
        return name, entry, result
    accepted, reports = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for name, entry, result in pool.map(check, candidates):
            reports.append({"channel": name, "source": entry.origin, "url": entry.url, **result})
            if result["status"] in ("ok", "geo_restricted"):
                entry.validation_status = result["status"]
                accepted.append(entry)
    return accepted, reports


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
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
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

    canonical_order = [item["name"] for item in wanted]
    grouped = select_entries(config, entries)

    output = [
        f'#EXTM3U url-tvg="{DEFAULT_EPG}"',
        "# Generada automáticamente a partir de listas públicas.",
        *[f"# Fuente: {source['name']} - {source['url']}" for source in config.get("sources", [{"name": "TDTChannels", "url": DEFAULT_SOURCE}])],
        "",
    ]

    found: dict[str, int] = {}
    missing: list[str] = []
    total_sources = 0

    for canonical in canonical_order:
        # Presentar primero las fuentes comprobadas desde el servidor.
        channel_entries = sorted(grouped[canonical], key=lambda entry: entry.validation_status == "geo_restricted")
        if not channel_entries:
            missing.append(canonical)
            continue

        count = len(channel_entries)
        found[canonical] = count
        total_sources += count
        for index, entry in enumerate(channel_entries, start=1):
            display_name = canonical if count == 1 else f"{canonical} — Fuente {index}"
            if entry.validation_status == "geo_restricted":
                display_name += " [restricción geográfica]"
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
        "sources": config.get("sources", [{"name": "TDTChannels", "url": DEFAULT_SOURCE}]),
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

    lines += ["", "## Canales sin fuente publicada", ""]
    if status["missing_channels"]:
        lines += [f"- {name}" for name in status["missing_channels"]]
    else:
        lines.append("- Ninguno")
    lines += [
        "",
        "> La comprobación descarga el manifiesto HLS y parte de un fragmento multimedia. No prueba la decodificación en VLC ni garantiza disponibilidad futura o desde otro país.",
        "> Se conservan los enlaces cuyo servidor declara explícitamente un bloqueo geográfico. Aparecen marcados en VLC y no se cuentan como comprobados: pueden funcionar desde España aunque GitHub no pueda verificarlos.",
        "> Un canal ausente puede requerir web/app oficial, DRM o una sesión, o tener sus enlaces caídos. No se sustituye por su versión internacional.",
        "",
    ]
    lines += ["## Listas consultadas", ""]
    for source in status.get("source_results", []):
        detail = f"{source.get('entries', 0)} entradas" if source["status"] == "ok" else source.get("error", "Error")
        lines.append(f"- {source['name']}: {source['status']} — {detail}")
    validation = status.get("validation", {})
    lines += ["", "## Comprobación de enlaces", "",
              f"- Modo: {validation.get('mode', 'no ejecutada')}",
              f"- Comprobados (manifiesto y fragmento): {validation.get('passed', 0)}",
              f"- Conservados por restricción geográfica, sin comprobar: {validation.get('geo_restricted', 0)}",
              f"- Descartados: {validation.get('failed', 0)}", ""]
    for check in validation.get("checks", []):
        if check["status"] != "ok":
            disposition = "Conservado por geobloqueo" if check["status"] == "geo_restricted" else "Descartado"
            lines.append(f"- {check['channel']} ({check['source']}): {disposition} — {check['reason']}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="channels.json")
    parser.add_argument("--output", default="tv_principales.m3u8")
    parser.add_argument("--status-md", default="estado.md")
    parser.add_argument("--status-json", default="estado.json")
    parser.add_argument("--source-url", action="append", help="Sustituye las listas configuradas; se puede repetir.")
    parser.add_argument("--input-file", help="Usa un M3U8 local en lugar de descargar (útil para pruebas).")
    parser.add_argument("--skip-validation", action="store_true", help="Solo para diagnóstico/pruebas: no comprobar streams.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.input_file:
        source_text = Path(args.input_file).read_text(encoding="utf-8-sig")
        entries = parse_m3u(source_text)
        config["sources"] = [{"name": "Archivo local", "url": args.input_file}]
        source_results = [{**config["sources"][0], "status": "ok", "entries": len(entries)}]
        for entry in entries:
            entry.origin = "Archivo local"
    else:
        if args.source_url:
            config["sources"] = [{"name": url, "url": url} for url in args.source_url]
        sources = config.get("sources", [{"name": "TDTChannels", "url": DEFAULT_SOURCE}])
        entries, source_results = load_sources(sources)
    if not entries:
        raise RuntimeError("La fuente se descargó, pero no contiene entradas #EXTINF reproducibles.")

    grouped = select_entries(config, entries)
    candidate_count = sum(len(group) for group in grouped.values())
    print(f"Comprobando {candidate_count} enlaces de los canales seleccionados...", flush=True)
    checks = []
    if not args.skip_validation:
        entries, checks = validate_entries(grouped, max(1, args.workers), max(1, args.timeout))
    playlist, status = build(config, entries)
    if not status["available_channels"]:
        raise RuntimeError("Ningún canal disponible; no se sobrescribe la lista anterior.")
    status["source_results"] = source_results
    status["validation"] = {
        "mode": "skipped" if args.skip_validation else "hls_manifest_and_segment",
        "candidate_streams": candidate_count,
        "passed": sum(check["status"] == "ok" for check in checks),
        "geo_restricted": sum(check["status"] == "geo_restricted" for check in checks),
        "failed": sum(check["status"] == "failed" for check in checks),
        "checks": checks,
    }
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
