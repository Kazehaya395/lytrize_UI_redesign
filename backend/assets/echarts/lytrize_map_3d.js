/* Offline 3D Globe and extruded polygon renderer for Lytrize map models.
 * Uses only the bundled Natural Earth GeoJSON and Canvas 2D; no network tiles,
 * WebGL extension, or third-party map runtime is required.
 */
(function (global) {
  "use strict";
  function norm(value) { return String(value == null ? "" : value).trim().toLowerCase().replace(/[^a-z0-9]+/g, " "); }
  function rgb(hex) {
    var s = String(hex || "").replace("#", "");
    if (s.length === 3) s = s.split("").map(function (c) { return c + c; }).join("");
    return /^([0-9a-f]{6})$/i.test(s) ? [parseInt(s.slice(0,2),16),parseInt(s.slice(2,4),16),parseInt(s.slice(4,6),16)] : [99,102,241];
  }
  function mix(a,b,t) { return "rgb(" + a.map(function(v,i){return Math.round(v+(b[i]-v)*t);}).join(",") + ")"; }
  function render(root, model, geojson) {
    var mode = String(model.display.map_visualization || "").toLowerCase();
    if (mode !== "globe" && mode !== "extruded") return false;
    var canvas = root.querySelector("canvas.lytrize-map-3d");
    if (canvas && canvas.__lyMode !== mode) {
      canvas.remove();
      var priorTip=root.querySelector(".lytrize-map-3d-tip");if(priorTip)priorTip.remove();
      canvas=null;
    }
    if (!canvas) {
      root.textContent = "";
      canvas = document.createElement("canvas");
      canvas.className = "lytrize-map-3d";
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", model.title || (mode === "globe" ? "Interactive 3D globe" : "Extruded map"));
      canvas.style.cssText = "display:block;width:100%;height:100%;touch-action:none;cursor:"+(mode==="globe"?"grab":"zoom-in");
      canvas.__lyMode = mode;
      root.appendChild(canvas);
    }
    var ctx = canvas.getContext("2d");
    if (!ctx || !geojson || !Array.isArray(geojson.features)) {
      root.textContent = "Map boundaries are unavailable for this 3D view.";
      return true;
    }
    var state = canvas.__lyState || (canvas.__lyState = {lon:0,lat:0,zoom:1,paths:[]});
    state.model=model;state.geojson=geojson;state.mode=mode;
    var dpr = Math.min(2, global.devicePixelRatio || 1), w = Math.max(1, root.clientWidth), h = Math.max(1, root.clientHeight);
    canvas.width = Math.round(w*dpr); canvas.height = Math.round(h*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,w,h);
    var dark = String(model.display.map_theme || model.display.theme_mode || "light").toLowerCase() === "dark";
    var land = model.display.map_land_color || (dark ? "#263449" : "#e8edf4");
    var stroke = model.display.map_border_color || (dark ? "#74849a" : "#9aa8ba");
    var series = (model.data.series || [])[0] || {}, values = series.values || [];
    var byKey = Object.create(null), byName = Object.create(null), nums = [];
    values.forEach(function (r) {
      if (!Array.isArray(r)) return;
      var key = norm(r[0]), name = norm(r[2] || r[0]), v = Number(r[1]);
      byKey[key] = r; byName[name] = r; if (Number.isFinite(v)) nums.push(v);
    });
    var lo = nums.length ? Math.min.apply(null,nums) : 0, hi = nums.length ? Math.max.apply(null,nums) : 1;
    if (hi === lo) hi = lo + 1;
    var palette = model.display.heatmap_colors || ["#eff6ff","#1d4ed8"];
    function featureRow(f) {
      var p=f.properties||{}, keys=[f.id,p.name,p.display_name,p.iso_a3,p.iso_3166_2,p["iso_3166_2"],p.country_iso_a3];
      for(var i=0;i<keys.length;i++){var k=norm(keys[i]);if(byKey[k])return byKey[k];if(byName[k])return byName[k];}
      return null;
    }
    function fillFor(f) {
      var row=featureRow(f), v=row&&Number(row[1]);
      if (!Number.isFinite(v)) return land;
      var t=Math.max(0,Math.min(1,(v-lo)/(hi-lo))), idx=t*(palette.length-1), a=Math.floor(idx), b=Math.min(palette.length-1,a+1);
      return mix(rgb(palette[a]),rgb(palette[b]),idx-a);
    }
    var title = String(model.title || ""), top = title ? 28 : 8;
    if (title) {ctx.fillStyle=dark?"#f8fafc":"#172033";ctx.font="600 15px Inter, sans-serif";ctx.textAlign="center";ctx.fillText(title,w/2,20,Math.max(40,w-24));}
    state.paths=[];
    function polygons(g) {
      if(!g)return [];
      if(g.type==="Polygon")return [g.coordinates||[]];
      if(g.type==="MultiPolygon")return g.coordinates||[];
      return [];
    }
    function eachRing(f, fn) {
      polygons(f.geometry).forEach(function(poly){if(poly[0]&&poly[0].length>2)fn(poly[0]);});
    }
    if (mode === "globe") {
      var r=Math.max(20,Math.min((w-32)/2,(h-top-24)/2)*state.zoom), cx=w/2, cy=top+(h-top)/2;
      var sphere=ctx.createRadialGradient(cx-r*.32,cy-r*.42,r*.05,cx,cy,r);
      sphere.addColorStop(0,dark?"#294967":"#b9e3ff");sphere.addColorStop(1,dark?"#071525":"#23639a");
      ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.fillStyle=sphere;ctx.fill();
      ctx.save();ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.clip();
      ctx.strokeStyle=dark?"rgba(190,215,235,.25)":"rgba(30,70,105,.25)";ctx.lineWidth=.6;
      function project(pt) {
        var lon=(pt[0]+state.lon)*Math.PI/180, lat=pt[1]*Math.PI/180, rot=state.lat*Math.PI/180;
        var x=Math.cos(lat)*Math.sin(lon), y=Math.sin(lat)*Math.cos(rot)-Math.cos(lat)*Math.cos(lon)*Math.sin(rot);
        var z=Math.sin(lat)*Math.sin(rot)+Math.cos(lat)*Math.cos(lon)*Math.cos(rot);
        return {x:cx+r*x,y:cy-r*y,z:z};
      }
      // Geographic graticule makes the spherical projection legible and gives
      // the user a clear, interactive globe instead of an oval map.
      for(var lat=-60;lat<=60;lat+=30){ctx.beginPath();for(var lon=-180;lon<=180;lon+=3){var q=project([lon,lat]);if(q.z>0){if(lon===-180)ctx.moveTo(q.x,q.y);else ctx.lineTo(q.x,q.y);}}ctx.stroke();}
      for(var mer=-150;mer<180;mer+=30){ctx.beginPath();var first=true;for(var la=-87;la<=87;la+=3){var p=project([mer,la]);if(p.z>0){if(first){ctx.moveTo(p.x,p.y);first=false;}else ctx.lineTo(p.x,p.y);}}ctx.stroke();}
      geojson.features.forEach(function(f){
        eachRing(f,function(ring){var path=new Path2D(),begun=false,visible=0;
          ring.forEach(function(pt){var p=project(pt);if(p.z>-.03){if(!begun){path.moveTo(p.x,p.y);begun=true;}else path.lineTo(p.x,p.y);visible++;}});
          if(visible>2){path.closePath();ctx.fillStyle=fillFor(f);ctx.fill(path);ctx.strokeStyle=stroke;ctx.lineWidth=.55;ctx.stroke(path);state.paths.push({path:path,row:featureRow(f),feature:f});}
        });
      });
      ctx.restore();ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.stroke();
    } else {
      var pad=18, depth=Math.max(8,Math.min(28,h*.055)), scale=Math.min((w-pad*2)/360,(h-top-depth-24)/180)*state.zoom, ox=(w-360*scale)/2, oy=top+Math.max(8,(h-top-180*scale-depth)/2);
      function xy(pt){return [ox+(pt[0]+180)*scale,oy+(90-pt[1])*scale];}
      geojson.features.forEach(function(f){
        eachRing(f,function(ring){var path=new Path2D(),first=xy(ring[0]);path.moveTo(first[0],first[1]);
          for(var i=1;i<ring.length;i++){var p=xy(ring[i]);path.lineTo(p[0],p[1]);}path.closePath();
          // Draw the side wall first, then the top polygon for visible depth.
          var side=new Path2D();side.moveTo(first[0],first[1]);for(var j=1;j<ring.length;j++){var a=xy(ring[j]);side.lineTo(a[0],a[1]);}
          side.lineTo(first[0],first[1]+depth);for(var k=ring.length-1;k>=0;k--){var b=xy(ring[k]);side.lineTo(b[0],b[1]+depth);}side.closePath();
          ctx.fillStyle=dark?"#111d2d":"#b9c7d9";ctx.fill(side);ctx.strokeStyle=stroke;ctx.lineWidth=.55;ctx.stroke(side);
          ctx.fillStyle=fillFor(f);ctx.fill(path);ctx.strokeStyle=stroke;ctx.stroke(path);state.paths.push({path:path,row:featureRow(f),feature:f});
        });
      });
    }
    if (model.display.show_colorbar !== false && nums.length) {
      var barW=Math.min(180,w*.34), bx=(w-barW)/2, by=h-17, grad=ctx.createLinearGradient(bx,0,bx+barW,0);
      palette.forEach(function(c,i){grad.addColorStop(i/Math.max(1,palette.length-1),c);});
      ctx.fillStyle=grad;ctx.fillRect(bx,by,barW,7);ctx.fillStyle=dark?"#e2e8f0":"#263247";ctx.font="10px Inter,sans-serif";
      ctx.textAlign="left";ctx.fillText(String(model.display.colorbar_title||model.display.map_value_title||"Value"),bx,by-3);
      ctx.textAlign="right";ctx.fillText(String(hi),bx+barW,by+17);ctx.textAlign="left";ctx.fillText(String(lo),bx,by+17);
    }
    if (!canvas.__lyBound) {
      canvas.__lyBound=true;var drag=null, tip=document.createElement("div");
      tip.className="lytrize-map-3d-tip";
      tip.style.cssText="position:absolute;display:none;z-index:5;padding:6px 9px;border-radius:5px;background:#111827;color:#fff;font:12px Inter,sans-serif;pointer-events:none;white-space:pre-line";
      root.style.position="relative";root.appendChild(tip);
      canvas.addEventListener("pointerdown",function(e){drag={x:e.clientX,y:e.clientY,lon:state.lon,lat:state.lat};canvas.style.cursor="grabbing";canvas.setPointerCapture(e.pointerId);});
      canvas.addEventListener("pointermove",function(e){
        if(drag&&state.mode==="globe"){state.lon=drag.lon+(e.clientX-drag.x)*.45;state.lat=Math.max(-75,Math.min(75,drag.lat+(e.clientY-drag.y)*.35));render(root,state.model,state.geojson);return;}
        var rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top,hit=null;
        for(var i=state.paths.length-1;i>=0;i--)if(ctx.isPointInPath(state.paths[i].path,x,y)){hit=state.paths[i];break;}
        if(hit){var name=(hit.feature.properties||{}).display_name||(hit.feature.properties||{}).name||"Region";
          tip.textContent=name+(hit.row&&hit.row[1]!=null?"\n"+(model.display.map_value_title||"Value")+": "+hit.row[1]:"");
          tip.style.display="block";tip.style.left=Math.min(w-180,x+12)+"px";tip.style.top=Math.max(4,y+10)+"px";
        }else tip.style.display="none";
      });
      canvas.addEventListener("pointerup",function(){drag=null;canvas.style.cursor="grab";});
      canvas.addEventListener("pointercancel",function(){drag=null;canvas.style.cursor="grab";});
      canvas.addEventListener("wheel",function(e){e.preventDefault();state.zoom=Math.max(.75,Math.min(2.5,state.zoom*(e.deltaY<0?1.08:.92)));render(root,state.model,state.geojson);},{passive:false});
    }
    return true;
  }
  global.Lytrize3DMap={render:render};
})(window);
