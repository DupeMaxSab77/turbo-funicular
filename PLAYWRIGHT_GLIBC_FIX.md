# Playwright Chromium Fix — glibc 2.35 (Jammy) sin root

## Problema

Playwright 1.60.0 descarga **Chromium 148** (revisión 1223, `headless_shell`)
compilado contra **GLIBC 2.38+**. El container HidenCloud tiene
**glibc 2.35** (Ubuntu 22.04 Jammy) y sin acceso root no se puede
instalar dependencias del sistema.

```
headless_shell: /lib/aarch64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found
```

## Diagnóstico

### 1. ¿Qué necesita `headless_shell`?

```bash
readelf -d headless_shell | grep NEEDED
```

```
libXcomposite.so.1, libXdamage.so.1, libXfixes.so.3, libXrandr.so.2,
libasound.so.2, libatk-1.0.so.0, libatspi.so.0, libc.so.6,
libdbus-1.so.3, libdl.so.2, libexpat.so.1, libgbm.so.1,
libgcc_s.so.1, libgio-2.0.so.0, libglib-2.0.so.0,
libgobject-2.0.so.0, libm.so.6, libnspr4.so, libnss3.so,
libnssutil3.so, libpthread.so.0, libudev.so.1, libxcb.so.1,
libxkbcommon.so.0
```

**NO necesita** `libatk-bridge-2.0.so.0`.

### 2. ¿Qué necesita `headless_shell` de glibc?

```bash
readelf -V headless_shell | grep GLIBC
```

Solo **GLIBC_2.17** para `libdl.so.2` y `libpthread.so.0`. El binario
en sí es compatible con glibc 2.17.

### 3. ¿Por qué falla?

Las libs del sistema que `headless_shell` carga (libnspr4, libdbus, etc.)
requieren símbolos GLIBC que no existen en glibc 2.35.

## Solución

### Paquetes `.deb` originales de Jammy (NO rebuilds)

Ubuntu tiene dos versiones de cada paquete:
- **Originales** (`-0ubuntu0.22.04.x`): compilados contra glibc 2.35
- **Rebuilds** (`-1.1build1`, `-1ubuntu1`): recompilados, a veces con glibc 2.38+

**Solo los originales son compatibles.**

### Librerías necesarias (21 archivos .so)

| Librería | Paquete Jammy | GLIBC max | Archivo .so |
|---|---|---|---|
| NSPR | `libnspr4_4.35-0ubuntu0.22.04.1` | 2.34 | `libnspr4.so` |
| NSS | `libnss3_3.98-0ubuntu0.22.04.3` | 2.17 | `libnss3.so` |
| NSS util | (mismo paquete NSS) | 2.33 | `libnssutil3.so` |
| NSS PLC | (mismo paquete NSS) | 2.17 | `libplc4.so` |
| NSS PLDS | (mismo paquete NSS) | 2.17 | `libplds4.so` |
| NSS SMIME | (mismo paquete NSS) | 2.17 | `libsmime3.so` |
| NSS SSL | (mismo paquete NSS) | 2.17 | `libssl3.so` |
| ATK | `libatk1.0-0_2.35.1-1ubuntu2` | 2.17 | `libatk-1.0.so.0` |
| AT-SPI | `libatspi2.0-0_2.36.0-2` | 2.17 | `libatspi.so.0` |
| CUPS | `libcups2_2.3.1-9ubuntu1` | 2.17 | `libcups.so.2` |
| DRM | `libdrm2_2.4.113-2~ubuntu0.22.04.1` | 2.33 | `libdrm.so.2` |
| XComposite | `libxcomposite1_0.4.5-1` | 2.17 | `libXcomposite.so.1` |
| XDamage | `libxdamage1_1.1.5-2` | 2.17 | `libXdamage.so.1` |
| XFixes | `libxfixes3_5.0.3-1` | 2.17 | `libXfixes.so.3` |
| XRandr | `libxrandr2_1.5.2-0ubuntu1` | 2.17 | `libXrandr.so.2` |
| GBM | `libgbm1_22.0.1-1ubuntu2` | 2.34 | `libgbm.so.1` |
| Pango | `libpango-1.0-0_1.50.6+ds-2ubuntu1` | 2.17 | `libpango-1.0.so.0` |
| Cairo | `libcairo2_1.16.0-5ubuntu2` | 2.34 | `libcairo.so.2` |
| ALSA | `libasound2_1.2.2-2.1` | 2.29 | `libasound.so.2` |
| Wayland | `libwayland-client0_1.20.0-1ubuntu0.1` | 2.28 | `libwayland-client.so.0` |
| D-Bus | `libdbus-1-3_1.12.20-2ubuntu4` | 2.34 | `libdbus-1.so.3` |

### Librería EXCLUIDA (causa el error GLIBC_2.38)

| Librería | Paquete | GLIBC max | Razón |
|---|---|---|---|
| **ATK-Bridge** | `libatk-bridge2.0-0t64` | **2.38** | `headless_shell` NO la necesita |

### URLs de descarga

Base: `https://ports.ubuntu.com/pool/main/`

```
n/nspr/libnspr4_4.35-0ubuntu0.22.04.1_arm64.deb
n/nss/libnss3_3.98-0ubuntu0.22.04.3_arm64.deb
a/atk1.0/libatk1.0-0_2.35.1-1ubuntu2_arm64.deb
a/at-spi2-core/libatspi2.0-0_2.36.0-2_arm64.deb
c/cups/libcups2_2.3.1-9ubuntu1_arm64.deb
libd/libdrm/libdrm2_2.4.113-2~ubuntu0.22.04.1_arm64.deb
libx/libxcomposite/libxcomposite1_0.4.5-1_arm64.deb
libx/libxdamage/libxdamage1_1.1.5-2_arm64.deb
libx/libxfixes/libxfixes3_5.0.3-1_arm64.deb
libx/libxrandr/libxrandr2_1.5.2-0ubuntu1_arm64.deb
m/mesa/libgbm1_22.0.1-1ubuntu2_arm64.deb
p/pango1.0/libpango-1.0-0_1.50.6+ds-2ubuntu1_arm64.deb
c/cairo/libcairo2_1.16.0-5ubuntu2_arm64.deb
a/alsa-lib/libasound2_1.2.2-2.1_arm64.deb
w/wayland/libwayland-client0_1.20.0-1ubuntu0.1_arm64.deb
d/dbus/libdbus-1-3_1.12.20-2ubuntu4_arm64.deb
```

### Extracción e instalación

```bash
# Descargar y extraer cada .deb
for deb in *.deb; do
  dpkg-deb -x "$deb" extract/
done

# Copiar todos los .so a lib/ (flat, sin subdirectorios excepto nss/)
find extract/ -name "*.so*" -type f -exec cp {} lib/ \;

# Crear symlinks versionados
cd lib
ln -sf libXcomposite.so.1.0.0 libXcomposite.so.1
ln -sf libXdamage.so.1.1.0 libXdamage.so.1
ln -sf libXfixes.so.3.1.0 libXfixes.so.3
ln -sf libXrandr.so.2.2.0 libXrandr.so.2
ln -sf libasound.so.2.0.0 libasound.so.2
ln -sf libatk-1.0.so.0.23510.1 libatk-1.0.so.0
ln -sf libatspi.so.0.0.1 libatspi.so.0
ln -sf libcairo.so.2.11600.0 libcairo.so.2
ln -sf libdbus-1.so.3.19.13 libdbus-1.so.3
ln -sf libdrm.so.2.4.0 libdrm.so.2
ln -sf libgbm.so.1.0.0 libgbm.so.1
ln -sf libpango-1.0.so.0.5000.6 libpango-1.0.so.0
ln -sf libwayland-client.so.0.20.0 libwayland-client.so.0

# Archivos NSS van en subdirectorio nss/
mkdir nss
mv libfreebl3.so libfreeblpriv3.so libnssckbi.so \
   libnssdbm3.so libsoftokn3.so nss/
```

### En server.py

```python
LIB_DIR = Path(os.environ.get("LIB_DIR", "lib"))
os.environ["LD_LIBRARY_PATH"] = str(LIB_DIR)
```

## Verificación

```bash
# Test 1: Import playwright
python3 -c "from playwright.sync_api import sync_playwright; print('ok')"

# Test 2: Launch chromium
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=['--no-sandbox'])
    print('chromium ok:', b.version)
    b.close()
"

# Test 3: Diagnóstico endpoint
curl https://vibeapi.hidenfree.com/api/diag
```

Salida esperada:
```json
{
  "chromium": true,
  "error": null,
  "test": "ok"
}
```

## Comandos de verificación de GLIBC

```bash
# Verificar glibc de un .so
readelf -V library.so | grep GLIBC | awk '{print $NF}' | sort -V | tail -1

# Verificar dependencias de headless_shell
readelf -d headless_shell | grep NEEDED

# Verificar versión de glibc del sistema
ldd --version
```

## Notas importantes

- **ATK-Bridge no es necesaria** — `headless_shell` no la vincula directamente
- **No todos los paquetes Jammy son compatibles** — los `-1.1build1` y
  `-1ubuntu1` a veces recompilan contra glibc más nueva
- **El sufijo `-0ubuntu0.XX.XX.X`** indica el build original de la release
- **NSS requiere subdirectorio `nss/`** para los módulos internos
- **No se necesita `libxkbcommon`** — Chromium lo carga dinámicamente si está disponible
