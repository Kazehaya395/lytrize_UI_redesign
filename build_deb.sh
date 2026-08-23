#!/bin/bash
# build_deb.sh — Build Lytrize Qt .deb package
# Usage: bash build_deb.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP=lytrize
VERSION=1.0
ARCH=amd64
PKG_DIR=packaging/deb
BUILD=build/${APP}_qt_${VERSION}_${ARCH}
VENV_TARGET=/opt/$APP/venv
VENV_BUILD=$BUILD$VENV_TARGET

echo "======================================"
echo "  Lytrize Qt .deb builder  v${VERSION}"
echo "  Output: $SCRIPT_DIR/$BUILD.deb"
echo "======================================"
echo ""

# ── Verify build tools ────────────────────────────────────────────────────────
for tool in python3 dpkg-deb; do
    if ! command -v "$tool" &>/dev/null; then
        echo "ERROR: '$tool' not found. Install with: sudo apt install $tool"
        exit 1
    fi
done

# ── [1/7] Clean ───────────────────────────────────────────────────────────────
echo "[1/7] Cleaning old build..."
rm -rf build
mkdir -p "$BUILD/opt/$APP"

# ── [2/7] Copy Qt app files ───────────────────────────────────────────────────
echo "[2/7] Copying Qt app files..."
cp -r backend  "$BUILD/opt/$APP/"
cp -r desktop  "$BUILD/opt/$APP/"
# Note: No systemd service needed for Qt desktop app

find "$BUILD/opt/$APP" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$BUILD/opt/$APP" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
find "$BUILD/opt/$APP" -name "* (copy*).py" -delete 2>/dev/null || true
find "$BUILD/opt/$APP" -name "* (Copy*).py" -delete 2>/dev/null || true

# ── [3/7] Create virtual environment ─────────────────────────────────────────
echo "[3/7] Creating virtual environment..."
python3 -m venv "$VENV_BUILD"

# ── [4/7] Install Qt dependencies ────────────────────────────────────────────
echo "[4/7] Installing Qt dependencies..."
"$VENV_BUILD/bin/pip" install --upgrade pip setuptools wheel --quiet
"$VENV_BUILD/bin/pip" install \
    "pandas>=2.2.0,<3.0.0" \
    "plotly>=5.22.0,<6.0.0" \
    "openpyxl>=3.1.0,<4.0.0" \
    "scipy>=1.12.0,<2.0.0" \
    "statsmodels>=0.14.0,<0.15.0" \
    "pycountry>=23.12.11,<25.0.0" \
    "PySide6>=6.11.0,<7.0.0" \
    "PySide6-Addons>=6.11.0,<7.0.0" \
    "PyWebEngineView>=1.0.0" \
    --quiet
"$VENV_BUILD/bin/pip" install "pyarrow==18.1.0" --quiet --only-binary pyarrow

# ── [5/7] Patch venv shebangs ─────────────────────────────────────────────────
echo "[5/7] Patching venv shebangs..."
VENV_BUILD_ABS="$(realpath "$VENV_BUILD")"
find "$VENV_BUILD/bin" -maxdepth 1 -type f | while read -r f; do
    if head -c 2 "$f" 2>/dev/null | grep -q '#!'; then
        sed -i "1s|#!${VENV_BUILD_ABS}/bin/python[0-9.]*|#!/opt/lytrize/venv/bin/python3|g" "$f"
    fi
done
PYDIR="$(dirname "$(readlink -f "$(which python3)")")"
sed -i "s|^home = .*|home = $PYDIR|" "$VENV_BUILD/pyvenv.cfg" 2>/dev/null || true

# ── [6/7] Slim venv ───────────────────────────────────────────────────────────
echo "[6/7] Slimming venv..."
find "$VENV_BUILD" -type d \( -name '__pycache__' -o -name 'tests' -o -name 'test' -o -name 'docs' \) \
    -exec rm -rf {} + 2>/dev/null || true
find "$VENV_BUILD" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
find "$VENV_BUILD" -type l ! -exec test -e {} \; -delete 2>/dev/null || true

# ── [7/7] Assemble and build .deb ─────────────────────────────────────────────
echo "[7/7] Building .deb package..."

cp -r "$PKG_DIR/DEBIAN" "$BUILD/"
cp -r "$PKG_DIR/usr"    "$BUILD/"

ICON_SRC="backend/assets/lytrize.png"
if [ -f "$ICON_SRC" ]; then
    echo "      Packaging icons..."
    for SIZE in 16 22 24 32 48 64 96 128 256; do
        IDIR="$BUILD/usr/share/icons/hicolor/${SIZE}x${SIZE}/apps"
        mkdir -p "$IDIR"
        cp "$ICON_SRC" "$IDIR/lytrize.png"
    done
    mkdir -p "$BUILD/usr/share/icons/hicolor/scalable/apps" "$BUILD/usr/share/pixmaps"
    cp "$ICON_SRC" "$BUILD/usr/share/icons/hicolor/scalable/apps/lytrize.png"
    cp "$ICON_SRC" "$BUILD/usr/share/pixmaps/lytrize.png"
else
    echo "      WARNING: backend/assets/lytrize.png not found"
fi

find "$BUILD" -type d -exec chmod 755 {} \;
find "$BUILD" -type f -exec chmod 644 {} \;
chmod 755 "$BUILD/usr/local/bin/lytrize"
chmod 755 "$BUILD/DEBIAN/postinst"
chmod 755 "$BUILD/DEBIAN/postrm"
chmod 755 "$BUILD/opt/$APP/desktop/app.py"
chmod 755 "$BUILD/opt/$APP/desktop/launcher.py"
find "$VENV_BUILD/bin" -type f -exec chmod 755 {} \;

dpkg-deb --build --root-owner-group "$BUILD"

echo ""
echo "======================================"
echo "  Build complete!"
echo "  Package : $SCRIPT_DIR/$BUILD.deb"
echo "  Size    : $(du -sh "$BUILD.deb" | cut -f1)"
echo ""
echo "  Install : sudo dpkg -i $BUILD.deb"
echo "            sudo apt-get install -f"
echo "  Launch  : lytrize"
echo "======================================"