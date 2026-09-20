# TDT Favoritos Auto

Genera automáticamente una lista M3U8 pequeña para VLC a partir de la lista pública de **TDTChannels**, conservando varias fuentes cuando TDTChannels publica más de una para el mismo canal.

## Qué hace

- Descarga `https://www.tdtchannels.com/lists/tv.m3u8`.
- Conserva únicamente los canales de `channels.json`.
- Si un canal tiene varias fuentes, aparecen como `Canal — Fuente 1`, `Canal — Fuente 2`, etc.
- No inventa ni rescata enlaces antiguos: si TDTChannels no ofrece un stream reproducible, el canal se marca como ausente en `estado.md`.
- GitHub Actions vuelve a generar la lista todos los días y también al cambiar la configuración.
- No necesitas servidor, PC encendido, Docker ni Threadfin.

## Instalación en GitHub

1. Crea un repositorio **público** nuevo, por ejemplo `tdt-favoritos`. Si es privado, VLC no podrá abrir directamente la URL `raw.githubusercontent.com` sin autenticación.
2. Descomprime este ZIP y sube **todo su contenido**, incluida la carpeta `.github`.
3. Usa la rama `main`.
4. En GitHub abre **Actions > Actualizar lista TDT > Run workflow**. El primer `push` también debería ejecutarlo automáticamente.
5. Cuando termine aparecerán:
   - `tv_principales.m3u8`
   - `estado.md`
   - `estado.json`

Si GitHub impide que el workflow haga `git push`, entra en:

**Settings > Actions > General > Workflow permissions > Read and write permissions**

Normalmente el `permissions: contents: write` del workflow es suficiente, pero esta opción depende de la configuración del repositorio/cuenta.

## URL que debes abrir en VLC

Sustituye `TU_USUARIO` y `TU_REPO`:

```text
https://raw.githubusercontent.com/TU_USUARIO/TU_REPO/main/tv_principales.m3u8
```

En VLC:

**Medio > Abrir ubicación de red** (`Ctrl+N`) y pega esa URL.

Para ver la lista: **Ctrl+L**.

## Cambiar los canales

Edita `channels.json`. El orden de ese archivo es también el orden de la lista generada.

Ejemplo:

```json
{"name": "La 1"}
```

Para aceptar variantes de nombre:

```json
{"name": "laSexta", "aliases": ["La Sexta"]}
```

Al guardar el cambio en `main`, GitHub Actions regenera la lista.

## Canales incluidos como objetivo

La 1, La 2, Antena 3, Cuatro, Telecinco, laSexta, 24h, Teledeporte, Clan, FDF, Energy, Divinity, Be Mad, DMAX, TRECE, Neox, Nova, Mega, Atreseries, Boing, DKISS, Ten, Real Madrid TV, TVG, TVG 2, Telemiño y TeleVigo.

**Importante:** estar en esta lista de objetivos no garantiza que exista un M3U8 directo. `estado.md` muestra cuáles existen en la publicación de TDTChannels de ese día.

## Ejecutarlo manualmente en un PC (opcional)

No instala dependencias:

```bash
python scripts/build_playlist.py
```

Requiere Python 3.10 o superior.

## Fuente y atribución

Los datos de canales/streams proceden de TDTChannels:

https://github.com/LaQuay/TDTChannels

Este repositorio no redistribuye vídeo; genera una playlist con las URLs publicadas por la fuente en el momento de la actualización.
# iptv_spain_tv_principales
