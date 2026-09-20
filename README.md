# TDT Favoritos Auto

Genera automáticamente una lista M3U8 para VLC combinando **cuatro listas públicas**,
con un filtro de **62 canales nacionales, autonómicos y locales** y varias fuentes por canal.

## Qué hace

- Descarga TDTChannels, IPTV-org España, Free-TV España y Teleonline, configuradas en `channels.json`.
- Conserva únicamente los canales de `channels.json`.
- Reconoce variantes de calidad como `HD` o `(1080p)` sin confundir canales regionales o internacionales con el nacional.
- Elimina URLs repetidas entre listas y conserva las opciones de reproducción de VLC.
- Si un canal tiene varias fuentes, aparecen como `Canal — Fuente 1`, `Canal — Fuente 2`, etc.
- Comprueba cada enlace HLS: manifiesto, variante, inicialización si existe y parte de un fragmento multimedia. Reintenta los fallos una vez y descarta HTML, enlaces caídos y DRM no compatible.
- Publica los enlaces que superan esa comprobación y conserva los que declaran explícitamente un bloqueo geográfico, marcados como tales. Un `403 Forbidden` genérico sigue descartándose.
- `estado.md` y `estado.json` distinguen fuentes comprobadas, fuentes conservadas por geobloqueo, canales ausentes, procedencia y errores.
- Si una lista falla, continúa con las demás. Si no quedan fuentes comprobadas ni fuentes con geobloqueo explícito, termina con error y conserva la publicación anterior.
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

Para ver la lista: **Ctrl+L** y selecciona **Playlist** en la barra lateral.
Después de una actualización, vuelve a abrir la URL con `Ctrl+N` para cargar los canales nuevos.

### Si la URL devuelve 404 tras una ejecución correcta

Comprueba que `tv_principales.m3u8` aparece en la rama `main`. Una versión anterior
del workflow comprobaba `git diff` antes de `git add`: en la primera ejecución
los archivos eran nuevos y Git no los detectaba como cambios, por lo que no se
publicaban aunque Actions terminase en verde. El workflow corregido añade los
archivos antes de comprobar `git diff --cached`.

Sube la corrección de `.github/workflows/update-playlist.yml` a `main`; se ejecutará
automáticamente. También puedes usar **Actions > Actualizar lista TDT > Run workflow**.
Los avisos sobre Node.js y `ubuntu-latest` no eran la causa del 404.

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

El filtro incluye La 1, La 2, Antena 3, Cuatro, Telecinco, laSexta, 24h, Teledeporte,
Clan, FDF, Energy, Divinity, Be Mad, DMAX, TRECE, Neox, Nova, Mega, Atreseries,
Boing, DKISS, Ten, Squirrel, BOM Cine, GOL Play, Euronews, El Toro TV,
Negocios TV, El País, RNE para todos y Real Madrid TV.

También busca Telemadrid, La Otra, Canal Sur Andalucía, Canal Sur 2, Canal Sur Más
Noticias, TV3, TV3 CAT, 3CatInfo, 33, SX3, Esport3, À Punt, Aragón TV, ETB 1, ETB 2,
Castilla-La Mancha Media, Canal Extremadura, TV Canaria, IB3, TPA, La 7 Murcia,
La 7 Castilla y León, TVG, TVG 2, Telemiño, TeleVigo, betevé, Bon Dia TV,
La 8 Mediterráneo, Distrito TV y Sol Música.

**El filtro no garantiza disponibilidad.** En las comprobaciones del 21/09/2026,
los enlaces encontrados para Antena 3, Cuatro, Telecinco y laSexta no funcionaron.
No se incluyen enlaces web como si fueran vídeo ni se sustituye Antena 3 por
Antena 3 Internacional. Estos canales siguen en el filtro y se incorporarán
automáticamente si una lista ofrece un enlace que supere la comprobación.

La comprobación verifica acceso a datos multimedia, **no una reproducción completa
en VLC**. Un enlace puede caducar o estar limitado geográficamente: GitHub Actions
comprueba desde su servidor, cuya ubicación puede ser diferente a la tuya.
Los enlaces que devuelven un `403` con motivo `Geoblock` o `geofence` se conservan
con la etiqueta `[restricción geográfica]` en VLC. No se cuentan como comprobados;
pueden funcionar desde España. Las fuentes comprobadas aparecen primero.

## Añadir listas de origen

Añade un objeto a `sources` en `channels.json`:

```json
{"name": "Nombre de la lista", "url": "https://ejemplo.com/lista.m3u8"}
```

Se admiten listas M3U/M3U8 con streams HTTP(S) HLS. Las listas se consultan por
orden de prioridad; ante una URL repetida se conserva la primera entrada.

## Ejecutarlo manualmente en un PC (opcional)

No instala dependencias:

```bash
python scripts/build_playlist.py
```

Requiere Python 3.10 o superior.

Pruebas del generador, validación y publicación (requieren Git y Bash; Git Bash en Windows):

```bash
python -m unittest discover -s tests -v
```

Para probar un archivo local sin acceder a sus streams:

```bash
python scripts/build_playlist.py --input-file tests/sample.m3u8 --skip-validation --output prueba.m3u8 --status-md prueba.md --status-json prueba.json
```

`--skip-validation` es solo para diagnóstico. El workflow siempre comprueba los streams.
`--source-url URL` permite sustituir las fuentes configuradas y se puede repetir.

## Fuentes y atribución

- [TDTChannels](https://github.com/LaQuay/TDTChannels)
- [IPTV-org](https://github.com/iptv-org/iptv)
- [Free-TV](https://github.com/Free-TV/IPTV)
- [Teleonline](https://github.com/teleonline/listas)

Este repositorio no redistribuye vídeo; genera una playlist con las URLs publicadas
por esas fuentes en el momento de la actualización. Conserva su atribución y consulta
las condiciones de cada proyecto de origen.
