"""Offline Qt WebEngine host for renderer-neutral Lytrize ChartModels."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

from modules.chart_model import validate_chart_model

log = logging.getLogger(__name__)

_HOST_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self' file: qrc:; script-src 'self' file: qrc: 'unsafe-inline'; style-src 'unsafe-inline'; img-src file: data: blob: https://services.arcgisonline.com; font-src file: data:;">
<style><!--LYTRIZE_FONT_FACES-->
html,body,#lytrize-chart{width:100%;height:100%;margin:0;overflow:hidden;background:transparent}
body{font-family:Inter,'Noto Sans',sans-serif}
</style>
<script src="echarts.min.js"></script><script src="lytrize_adapter.js"></script><script src="lytrize_map_3d.js"></script>
</head><body><div id="lytrize-chart" role="img" aria-label="Interactive chart"></div>
<script>
(function(){
  let chart=null, model=null, renderer=null, activeThemeMode=null, resizeTimer=0, drawRevision=0;
  let lastW=0, lastH=0;
  let mapLoadFailed=false;
  const pendingMaps={};
  const root=document.getElementById('lytrize-chart');
  function ensureMap(asset){
    const mapInfo={
      'countries-110m':{id:'lytrize_ne_countries_110m',path:'maps/natural-earth/countries-110m.geojson'},
      'admin1-50m':{id:'lytrize_ne_admin1_50m',path:'maps/natural-earth/admin1-50m.geojson'}
    }[asset];
    if(!mapInfo) return Promise.reject(new Error('Unknown offline map asset'));
    if(echarts.getMap(mapInfo.id)) return Promise.resolve(mapInfo.id);
    if(pendingMaps[mapInfo.id]) return pendingMaps[mapInfo.id];
    pendingMaps[mapInfo.id]=fetch(mapInfo.path).then(function(response){
      if(!response.ok) throw new Error('Could not read local boundary data');
      return response.json();
    }).then(function(geojson){
      echarts.registerMap(mapInfo.id,{geoJSON:geojson});
      return mapInfo.id;
    }).finally(function(){delete pendingMaps[mapInfo.id];});
    return pendingMaps[mapInfo.id];
  }
  function drawOption(revision){
    if(revision!==drawRevision || !window.echarts || !window.LytrizeEChartsAdapter || !model) return;
    if(mapLoadFailed){root.textContent='';root.style.padding='';mapLoadFailed=false;}
    const w=root.clientWidth, h=root.clientHeight;
    if(!w || !h) return;
    lastW = w; lastH = h;
    const mapView=String((model.display&&model.display.map_visualization)||'').toLowerCase();
    if(mapView==='globe'||mapView==='extruded'){
      if(chart){chart.dispose();chart=null;}
      renderer='map3d';activeThemeMode=String((model.display&&model.display.theme_mode)||'light');
      const oldCanvas=root.querySelector('canvas.lytrize-map-3d');
      const oldTip=root.querySelector('.lytrize-map-3d-tip');
      if(oldCanvas&&oldCanvas.__lyMode!==mapView){oldCanvas.remove();if(oldTip)oldTip.remove();}
      const map=echarts.getMap(model.data.map.id);
      if(!window.Lytrize3DMap||!map){root.textContent='3D map renderer or local boundary asset is unavailable.';return;}
      window.Lytrize3DMap.render(root,model,map.geoJSON||map.geoJson||map);
      return;
    }
    const previous3d=root.querySelector('canvas.lytrize-map-3d');
    if(previous3d){previous3d.remove();const tip=root.querySelector('.lytrize-map-3d-tip');if(tip)tip.remove();}
    const isMapOr3D = model.chart_type === 'map_plot';
    const requested = (isMapOr3D || (model.display && model.display.renderer === 'canvas')) ? 'canvas' : 'svg';
    const themeMode = String((model.display && model.display.theme_mode) || 'light').toLowerCase();
    if(chart && (renderer!==requested || activeThemeMode!==themeMode)){chart.dispose();chart=null;}
    if(!chart){chart=echarts.init(root,null,{renderer:requested});renderer=requested;activeThemeMode=themeMode;}
    chart.setOption(window.LytrizeEChartsAdapter.buildOption(model,w,h,root),
                    {notMerge:true,lazyUpdate:true});
    chart.resize({width:w,height:h});
    window.LytrizeEChartsAdapter.applySatelliteBasemap(chart,model,root);
    if(!chart.__lytrizeMapRoamBound){
      chart.__lytrizeMapRoamBound=true;
      chart.on('georoam',function(){clearTimeout(chart.__lytrizeMapTimer);
        chart.__lytrizeMapTimer=setTimeout(function(){
          if(model)window.LytrizeEChartsAdapter.applySatelliteBasemap(chart,model,root);
        },100);});
    }
  }
  function draw(){
    if(!window.echarts || !window.LytrizeEChartsAdapter || !model) return;
    const revision=++drawRevision;
    const asset=model.data&&model.data.map&&model.data.map.asset;
    if((model.chart_type==='map_plot') && asset){
      ensureMap(asset).then(function(){drawOption(revision);}).catch(function(error){
        if(revision!==drawRevision) return;
        mapLoadFailed=true;
        root.textContent='Local map boundary data could not be loaded: '+String(error&&error.message||error);
        root.style.color='#fca5a5';
        root.style.padding='16px';
      });
      return;
    }
    drawOption(revision);
  }
  window.lytrizeSetChartModel=function(next){model=next;draw();};
  window.lytrizeDispose=function(){if(chart){chart.dispose();chart=null;}renderer=null;model=null;};
  new ResizeObserver(function(){
    const cw = root.clientWidth, ch = root.clientHeight;
    if (cw === lastW && ch === lastH) return;
    clearTimeout(resizeTimer);resizeTimer=setTimeout(draw,50);
  }).observe(root);
  document.fonts&&document.fonts.ready.then(draw);
  window.addEventListener('beforeunload',window.lytrizeDispose);
})();
</script></body></html>"""


def echarts_asset_dir() -> Path:
    """Return the shared vendored runtime path.

    Frozen builds resolve the extracted ``<bundle>/backend/assets/echarts``;
    the source tree keeps the repo-relative path. Never raises: a missing
    asset shows up as a failed chart, not as a broken import at startup.
    """
    try:
        from modules.runtime_paths import assets_root
        return assets_root() / "echarts"
    except Exception:  # backend not importable yet: keep the source-tree answer
        project_root = Path(__file__).resolve().parents[2]
        return project_root / "backend" / "assets" / "echarts"


class EChartsWebView(QWidget):
    """Render a ChartModel using the bundled ECharts runtime, fully offline.

    The host accepts semantic models only. It does not accept arbitrary HTML,
    JavaScript, or raw ECharts options from saved sessions.
    """

    render_error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model: Optional[dict] = None
        self._loaded = False
        self._view = None
        self._web_view = None  # compatible size probe used by live chart settings
        self._fallback = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        asset_dir = echarts_asset_dir()
        required = (asset_dir / "echarts.min.js", asset_dir / "lytrize_adapter.js",
                    asset_dir / "lytrize_map_3d.js")
        missing = [path.name for path in required if not path.is_file()]
        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except Exception as exc:
            missing.append(f"Qt WebEngine unavailable ({exc})")
        else:
            if not missing:
                try:
                    from font_bundle import font_face_css
                    font_css = font_face_css()
                except Exception as exc:
                    log.debug("Bundled font CSS unavailable: %s", exc)
                    font_css = ""
                self._view = QWebEngineView(self)
                self._web_view = self._view
                self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
                self._view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                self._view.page().setBackgroundColor(Qt.GlobalColor.transparent)
                settings = self._view.settings()
                settings.setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
                    True,
                )
                # Remote access is enabled only for a chart that explicitly
                # selects the online satellite imagery basemap.
                settings.setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                    False,
                )
                self._view.loadFinished.connect(self._on_loaded)
                layout.addWidget(self._view)
                base = QUrl.fromLocalFile(str(asset_dir.resolve()) + "/")
                self._view.setHtml(_HOST_HTML.replace("<!--LYTRIZE_FONT_FACES-->",
                                                       font_css), base)

        if self._view is None:
            self._fallback = QLabel(
                "ECharts renderer unavailable: " + ", ".join(missing), self
            )
            self._fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._fallback.setWordWrap(True)
            layout.addWidget(self._fallback)

    def set_chart_model(self, model: dict) -> None:
        """Validate and render a new model without reloading the WebEngine page."""
        try:
            self._model = validate_chart_model(model)
            self._sync_theme_mode()
        except Exception as exc:
            self.render_error.emit(str(exc))
            return
        if self._view is not None:
            online_imagery = (
                self._model.get("chart_type") == "map_plot" and
                str((self._model.get("display") or {}).get("map_basemap", "")) == "satellite")
            try:
                from PySide6.QtWebEngineCore import QWebEngineSettings
                self._view.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                    bool(online_imagery),
                )
            except Exception:
                log.debug("could not update online map permission", exc_info=True)
        self._push_model()

    def clear(self) -> None:
        self._model = None
        if self._view is not None and self._loaded:
            self._view.page().runJavaScript("window.lytrizeDispose && window.lytrizeDispose()")

    def apply_theme(self) -> None:
        """Re-apply the semantic model after the application theme changes."""
        self._sync_theme_mode()
        self._push_model()

    def _sync_theme_mode(self) -> None:
        if not self._model:
            return
        try:
            from desktop.theme import current_mode
            mode = current_mode()
        except Exception:
            mode = "dark"
        display = dict(self._model.get("display") or {})
        display["theme_mode"] = mode
        self._model["display"] = display

    def _on_loaded(self, ok: bool) -> None:
        self._loaded = bool(ok)
        if not ok:
            self.render_error.emit("The local ECharts page could not be loaded")
            return
        self._push_model()

    def _push_model(self) -> None:
        if self._view is None or not self._loaded or self._model is None:
            return
        payload = json.dumps(self._model, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False)
        # A model title or label may contain markup-like text. Escaping the
        # script-closing sequence keeps the runJavaScript argument data-only.
        payload = (payload.replace("<", "\\u003c")
                   .replace("\u2028", "\\u2028")
                   .replace("\u2029", "\\u2029"))
        script = "window.lytrizeSetChartModel && window.lytrizeSetChartModel(" + payload + ")"
        self._view.page().runJavaScript(script, self._on_render_result)

    def _on_render_result(self, result) -> None:
        if isinstance(result, str) and result:
            self.render_error.emit(result)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.clear()
        super().closeEvent(event)
