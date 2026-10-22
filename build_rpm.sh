#!/bin/bash
# build_rpm.sh -- Build Lytrize Qt .rpm package
# Usage: bash build_rpm.sh
#
# Requires: rpm-build  (sudo dnf install rpm-build  /  sudo zypper install rpm-build)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP=lytrize
VERSION=1.0
RELEASE=1
ARCH=x86_64
PKG_DIR=packaging/rpm
SPEC="$SCRIPT_DIR/rpm_packaging/lytrize.spec"

STAGING="$SCRIPT_DIR/build/rpm_staging"
RPMBUILD_ROOT="$SCRIPT_DIR/build/rpmbuild"
VENV="$STAGING/opt/$APP/venv"
FINAL_RPM="build/${APP}-${VERSION}-${RELEASE}.${ARCH}.rpm"

echo "======================================"
echo "  Lytrize Qt .rpm builder  v${VERSION}"
echo "  Arch    : $ARCH"
echo "  Output  : $SCRIPT_DIR/$FINAL_RPM"
echo "======================================"
echo ""

for tool in python3 rpmbuild; do
    if ! command -v "$tool" &>/dev/null; then
        case "$tool" in
            rpmbuild)
                echo "ERROR: 'rpmbuild' not found."
                echo "  Fedora/RHEL : sudo dnf install rpm-build"
                echo "  openSUSE    : sudo zypper install rpm-build"
                ;;
            *) echo "ERROR: '$tool' not found." ;;
        esac
        exit 1
    fi
done

# ── [1/7] Clean ───────────────────────────────────────────────────────────────
echo "[1/7] Cleaning old build..."
rm -rf build
mkdir -p "$STAGING/opt/$APP" \
         "$STAGING/usr/local/bin" \
         "$STAGING/usr/share/applications" \
         "$RPMBUILD_ROOT/"{BUILD,RPMS,SOURCES,SPECS,SRPMS,BUILDROOT}

# ── [2/7] Copy Qt app files ───────────────────────────────────────────────────
echo "[2/7] Copying Qt app files..."
cp -r backend  "$STAGING/opt/$APP/"
cp -r desktop  "$STAGING/opt/$APP/"
# Note: No systemd service needed for Qt desktop app

# Copy RPM-specific launcher stub and desktop entry
cp "$PKG_DIR/usr/local/bin/lytrize"             "$STAGING/usr/local/bin/lytrize"
cp "$PKG_DIR/usr/share/applications/lytrize.desktop" \
                                                 "$STAGING/usr/share/applications/lytrize.desktop"

find "$STAGING/opt/$APP" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$STAGING/opt/$APP" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
find "$STAGING/opt/$APP" -name "* (copy*).py" -delete 2>/dev/null || true
find "$STAGING/opt/$APP" -name "* (Copy*).py" -delete 2>/dev/null || true

# Bake icon into staging tree
ICON_SRC="backend/assets/lytrize.png"
if [ -f "$ICON_SRC" ]; then
    echo "      Packaging icons..."
    for SIZE in 16 22 24 32 48 64 96 128 256; do
        IDIR="$STAGING/usr/share/icons/hicolor/${SIZE}x${SIZE}/apps"
        mkdir -p "$IDIR"
        cp "$ICON_SRC" "$IDIR/lytrize.png"
    done
    mkdir -p "$STAGING/usr/share/icons/hicolor/scalable/apps" \
             "$STAGING/usr/share/pixmaps"
    cp "$ICON_SRC" "$STAGING/usr/share/icons/hicolor/scalable/apps/lytrize.png"
    cp "$ICON_SRC" "$STAGING/usr/share/pixmaps/lytrize.png"
else
    echo "      WARNING: backend/assets/lytrize.png not found"
fi

# ── [3/7] Create virtual environment ─────────────────────────────────────────
echo "[3/7] Creating virtual environment..."
python3 -m venv "$VENV"

# ── [4/7] Install Qt dependencies ────────────────────────────────────────────
echo "[4/7] Installing Qt dependencies..."
"$VENV/bin/pip" install --upgrade pip setuptools wheel --quiet
"$VENV/bin/pip" install --only-binary=pyarrow -r "$SCRIPT_DIR/requirements.txt" --quiet

# ── [5/7] Patch venv shebangs ─────────────────────────────────────────────────
echo "[5/7] Patching venv shebangs..."
VENV_ABS="$(realpath "$VENV")"
find "$VENV/bin" -maxdepth 1 -type f | while read -r f; do
    if head -c 2 "$f" 2>/dev/null | grep -q '#!'; then
        sed -i "1s|#!${VENV_ABS}/bin/python[0-9.]*|#!/opt/lytrize/venv/bin/python3|g" "$f"
    fi
done
PYDIR="$(dirname "$(readlink -f "$(which python3)")")"
sed -i "s|^home = .*|home = $PYDIR|" "$VENV/pyvenv.cfg" 2>/dev/null || true

# ── [6/7] Slim venv ───────────────────────────────────────────────────────────
echo "[6/7] Slimming venv..."
find "$VENV" -type d \( -name '__pycache__' -o -name 'tests' -o -name 'test' -o -name 'docs' \) \
    -exec rm -rf {} + 2>/dev/null || true
find "$VENV" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
find "$VENV" -type l ! -exec test -e {} \; -delete 2>/dev/null || true

# Set permissions
find "$STAGING" -type d -exec chmod 755 {} \;
find "$STAGING" -type f -exec chmod 644 {} \;
chmod 755 "$STAGING/usr/local/bin/lytrize"
chmod 755 "$STAGING/usr/share/applications/lytrize.desktop"
chmod 755 "$STAGING/opt/$APP/desktop/app.py"
chmod 755 "$STAGING/opt/$APP/desktop/launcher.py"
find "$VENV/bin" -type f -exec chmod 755 {} \;

# ── [7/7] Build RPM ────────────────────────────────────────────────────────────
echo "[7/7] Building .rpm package..."
cp "$SPEC" "$RPMBUILD_ROOT/SPECS/lytrize.spec"

# Suppresses rpaths check for bundled venv .so files
export QA_RPATHS=$(( 0x0001|0x0002|0x0010 ))

rpmbuild -bb \
    --define "_topdir        $RPMBUILD_ROOT" \
    --define "_rpmdir        $RPMBUILD_ROOT/RPMS" \
    --define "staging_dir    $STAGING" \
    --define "__brp_check_rpaths %{nil}" \
    --define "_build_name_fmt %%{NAME}-%%{VERSION}-%%{RELEASE}.%%{ARCH}.rpm" \
    "$RPMBUILD_ROOT/SPECS/lytrize.spec"

BUILT=$(find "$RPMBUILD_ROOT/RPMS" -name "*.rpm" | head -1)
[ -z "$BUILT" ] && { echo "ERROR: rpmbuild produced no .rpm file."; exit 1; }
mv "$BUILT" "$SCRIPT_DIR/$FINAL_RPM"

echo ""
echo "======================================"
echo "  Build complete!"
echo "  Package : $SCRIPT_DIR/$FINAL_RPM"
echo "  Size    : $(du -sh "$FINAL_RPM" | cut -f1)"
echo ""
echo "  Install : sudo rpm -i $FINAL_RPM"
echo "     (or)   sudo dnf install $FINAL_RPM"
echo "  Launch  : lytrize"
echo "======================================"
