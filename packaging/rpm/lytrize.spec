Name:           lytrize
Version:        1.0
Release:        1%{?dist}
Summary:        Offline desktop analytics — CSV and Excel to interactive dashboards
BuildArch:      x86_64

License:        See /opt/lytrize/LICENSE
URL:            https://github.com/lytrize/lytrize-desktop

# ── QA overrides ─────────────────────────────────────────────────────────────
%global __brp_check_rpaths %{nil}
%global __requires_exclude_from ^/opt/lytrize/venv/.*$
%global __provides_exclude_from ^/opt/lytrize/venv/.*$

# ── Runtime dependencies ──────────────────────────────────────────────────────
Requires:       python3 >= 3.11
Requires:       mesa-libGL
Requires:       mesa-libEGL
Requires:       glib2
Requires:       xcb-util-cursor
Requires:       dbus-libs
Recommends:     chromium-browser
Recommends:     firefox
Recommends:     google-noto-sans-fonts google-noto-serif-fonts dejavu-fonts
Recommends:     source-sans-pro lato oswald barlow eb-garamond fira-code-fonts
Recommends:     jetbrains-mono-fonts

%description
Lytrize is a local-first Linux desktop analytics app.

Analyse CSV and Excel files locally with interactive Plotly charts,
saved dashboards, KPI cards, and export to PDF/image.

Fully offline — no internet, no cloud account, no telemetry.


%install
cp -rp %{staging_dir}/. %{buildroot}/
find %{buildroot}/opt/lytrize/venv/bin -type f -exec chmod 755 {} \;
chmod 755 %{buildroot}/opt/lytrize/desktop/gui.py
chmod 755 %{buildroot}/opt/lytrize/desktop/launcher.py
chmod 755 %{buildroot}/usr/local/bin/lytrize


%files
%defattr(-,root,root,-)
%attr(755,root,root) /usr/local/bin/lytrize
/usr/share/applications/lytrize.desktop
/usr/share/pixmaps/lytrize.png
/usr/share/icons/
%dir /opt/lytrize
/opt/lytrize/backend
/opt/lytrize/backend/assets/fonts
/opt/lytrize/desktop
/opt/lytrize/venv
/opt/lytrize/lytrize.service


%post
VENV=/opt/lytrize/venv
ICON_SRC=/opt/lytrize/backend/assets/lytrize.png

# Clear stale bytecode
find /opt/lytrize/backend -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find /opt/lytrize/backend -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true

# Re-link venv Python symlinks to this machine's python3
PYTHON_BIN="$(command -v python3 2>/dev/null || true)"
if [ -n "$PYTHON_BIN" ]; then
    echo "Linking Lytrize to system Python ($PYTHON_BIN)..."
    PYTHON_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
    PYTHON_DIR="$(dirname "$(readlink -f "$PYTHON_BIN")" 2>/dev/null || true)"

    ln -sf "$PYTHON_BIN" "$VENV/bin/python"              2>/dev/null || true
    ln -sf "$PYTHON_BIN" "$VENV/bin/python3"             2>/dev/null || true
    [ -n "$PYTHON_VER" ] && \
        ln -sf "$PYTHON_BIN" "$VENV/bin/python${PYTHON_VER}" 2>/dev/null || true

    if [ -f "$VENV/pyvenv.cfg" ] && [ -n "$PYTHON_DIR" ]; then
        FULL_VER="$(python3 --version 2>&1 | awk '{print $2}')"
        sed -i "s|^home = .*|home = $PYTHON_DIR|"     "$VENV/pyvenv.cfg" 2>/dev/null || true
        sed -i "s|^version = .*|version = $FULL_VER|" "$VENV/pyvenv.cfg" 2>/dev/null || true
    fi

    if [ -n "$PYTHON_VER" ]; then
        VENV_LIB="$VENV/lib"
        BUILD_PYVER="$(ls "$VENV_LIB" 2>/dev/null | grep -E '^python[0-9]+\.[0-9]+$' | head -1 | sed 's/^python//')"
        if [ -n "$BUILD_PYVER" ] && [ "$BUILD_PYVER" != "$PYTHON_VER" ]; then
            echo "  Note: built with Python $BUILD_PYVER, target is $PYTHON_VER — creating compat link."
            ln -sfn "$VENV_LIB/python${BUILD_PYVER}" "$VENV_LIB/python${PYTHON_VER}" 2>/dev/null || true
        fi
    fi
fi

# Fix permissions — world-readable so non-root users can run the app
find /opt/lytrize -type d -exec chmod 755 {} \;      2>/dev/null || true
find /opt/lytrize -type f -exec chmod 644 {} \;      2>/dev/null || true
find "$VENV/bin" -type f -exec chmod 755 {} \;       2>/dev/null || true
chmod 755 /opt/lytrize/desktop/launcher.py           2>/dev/null || true
chmod 755 /opt/lytrize/desktop/gui.py                2>/dev/null || true

# Per-user data directory
TARGET_USER="${SUDO_USER:-}"
if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
    USER_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)"
    if [ -n "$USER_HOME" ] && [ -d "$USER_HOME" ]; then
        mkdir -p "$USER_HOME/.local/share/lytrize"
        chown "$TARGET_USER" "$USER_HOME/.local/share/lytrize" 2>/dev/null || true
    fi
fi

# Refresh icon and desktop caches
gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database /usr/share/applications      2>/dev/null || true

echo "Lytrize installed successfully."


%preun
if [ "$1" -eq 0 ]; then
    # ── 1. Terminate any active running instances to prevent file locks ──
    pkill -f "/opt/lytrize" 2>/dev/null || true

    # ── 2. Stop and disable systemd user services for the active launcher ──
    TARGET_USER="${SUDO_USER:-$USER}"
    if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
        _UID=$(id -u "$TARGET_USER" 2>/dev/null || echo "")
        _RUNTIME="/run/user/${_UID}"
        if [ -n "$_UID" ] && [ -d "$_RUNTIME" ]; then
            su "$TARGET_USER" -c "
                export XDG_RUNTIME_DIR='${_RUNTIME}'
                export DBUS_SESSION_BUS_ADDRESS='unix:path=${_RUNTIME}/bus'
                systemctl --user stop    lytrize 2>/dev/null || true
                systemctl --user disable lytrize 2>/dev/null || true
                systemctl --user daemon-reload   2>/dev/null || true
            " 2>/dev/null || true
        fi
    fi

    # ── 3. Remove user service configuration files across all users ──────
    for homedir in /home/* /root; do
        [ -d "$homedir" ] || continue
        [ "$homedir" = "/home/*" ] && continue
        rm -f "$homedir/.config/systemd/user/lytrize.service" 2>/dev/null || true
    done
fi


%postun
if [ "$1" -eq 0 ]; then
    # ── 1. Clean residual system binaries, icon maps, and directory trees ──
    rm -rf /opt/lytrize 2>/dev/null || true
    for SIZE in 16 22 24 32 48 64 96 128 256 scalable; do
        rm -f "/usr/share/icons/hicolor/${SIZE}x${SIZE}/apps/lytrize.png" 2>/dev/null || true
        rm -f "/usr/share/icons/hicolor/${SIZE}/apps/lytrize.png"         2>/dev/null || true
    done
    rm -f /usr/share/icons/hicolor/scalable/apps/lytrize.png 2>/dev/null || true
    rm -f /usr/share/pixmaps/lytrize.png                     2>/dev/null || true

    # ── 2. Delete dynamic configs, database states, and caches for all users ──
    for homedir in /home/* /root; do
        [ -d "$homedir" ] || continue
        [ "$homedir" = "/home/*" ] && continue

        rm -rf "$homedir/.local/share/lytrize" 2>/dev/null || true
        rm -rf "$homedir/.config/lytrize"      2>/dev/null || true
        rm -rf "$homedir/.cache/lytrize"       2>/dev/null || true
    done

    # ── 3. Rebuild desktop application and icon system caches ────────────
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database /usr/share/applications      2>/dev/null || true
    echo "Lytrize fully removed, including all user data."
fi