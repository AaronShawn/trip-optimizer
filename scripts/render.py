#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render.py — 把 plan.json 渲染成自包含 HTML 行程手册

特性：
- 高德中文底图（GCJ-02 对齐，坐标已在 pipeline 转好）
- 每日真实路网轨迹（步行/打车分色分线型）
- 时间轴 + 交通方式 + 门票 + 开放时间
- 多日切换、地图与卡片联动、打印友好
"""
import argparse
import json
import os
import sys

DAY_COLORS = ["#c0392b", "#2471a3", "#1e8449", "#8e44ad", "#d68910", "#117a8b"]

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>@@LEAFLET_CSS@@</style>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#f7f7f5; --card:#fff; --ink:#1a1a1a; --muted:#6b6b6b;
  --line:#e3e3df; --accent:{accent};
}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg); color:var(--ink); line-height:1.65; -webkit-font-smoothing:antialiased;
}}
.wrap{{max-width:1280px;margin:0 auto;padding:32px 24px 80px}}

/* 头部 */
header{{margin-bottom:28px;padding-bottom:22px;border-bottom:2px solid var(--ink)}}
h1{{font-size:30px;font-weight:700;letter-spacing:-.5px;margin-bottom:6px}}
.sub{{color:var(--muted);font-size:14px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);margin-top:20px}}
.kpi{{background:var(--card);padding:14px 16px}}
.kpi .v{{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}}
.kpi .l{{font-size:11px;color:var(--muted);letter-spacing:.4px;text-transform:uppercase;margin-top:2px}}

/* 布局 */
.grid{{display:grid;grid-template-columns:1fr 420px;gap:24px;align-items:start}}
@media(max-width:1000px){{.grid{{grid-template-columns:1fr}}}}

/* 日切换 */
.tabs{{display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap}}
.tab{{padding:8px 18px;border:1.5px solid var(--line);background:var(--card);
  border-radius:100px;cursor:pointer;font-size:14px;font-weight:600;transition:.15s;
  font-family:inherit;color:var(--muted)}}
.tab:hover{{border-color:#bbb}}
.tab.on{{color:#fff;border-color:transparent}}

/* 时间轴 */
.day{{display:none}}
.day.on{{display:block}}
.dayhead{{display:flex;justify-content:space-between;align-items:baseline;
  padding:14px 18px;background:var(--card);border:1px solid var(--line);
  border-left:4px solid var(--accent);margin-bottom:14px}}
.dayhead .t{{font-weight:700;font-size:16px}}
.dayhead .m{{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}}

.stop{{background:var(--card);border:1px solid var(--line);margin-bottom:1px;
  display:grid;grid-template-columns:88px 1fr;overflow:hidden}}
.stop:first-child{{border-radius:2px 2px 0 0}}
.stop .time{{padding:16px 8px;text-align:center;font-variant-numeric:tabular-nums;
  border-right:1px solid var(--line);background:#fbfbfa}}
.stop .time b{{display:block;font-size:14px;font-weight:700;white-space:nowrap}}
.stop .time span{{font-size:11px;color:var(--muted);white-space:nowrap;display:block;margin-top:2px}}
.stop .body{{padding:14px 16px;min-width:0}}
.stop h3{{font-size:15px;font-weight:650;margin-bottom:5px;display:flex;
  align-items:center;gap:8px;flex-wrap:wrap}}
.stop h3 .idx{{display:inline-flex;width:20px;height:20px;border-radius:50%;
  background:var(--ink);color:#fff;font-size:11px;align-items:center;justify-content:center;flex:none}}
.meta{{font-size:12px;color:var(--muted);display:flex;gap:14px;flex-wrap:wrap}}
.meta b{{color:var(--ink);font-weight:600}}
.tag{{font-size:10px;padding:1px 7px;border-radius:100px;font-weight:700;letter-spacing:.3px}}
.p1{{background:#fdecea;color:#c0392b}}
.p2{{background:#eaf2f8;color:#2471a3}}
.p3{{background:#f0f0ee;color:#6b6b6b}}

/* 段间交通 */
.leg{{display:flex;align-items:center;gap:10px;padding:9px 16px 9px 92px;
  font-size:12px;color:var(--muted);background:transparent}}
.leg .dot{{width:6px;height:6px;border-radius:50%;background:var(--line);flex:none}}
.leg .mode{{font-weight:700;color:var(--ink)}}

/* 地图 */
.mapbox{{position:sticky;top:24px;background:var(--card);border:1px solid var(--line);
  padding:1px;overflow:hidden}}
#map{{height:560px;width:100%;position:relative;overflow:hidden;z-index:0;background:#eee}}
/* Leaflet 布局兜底：CDN 样式若加载慢/失败，瓦片会溢出容器 */
#map .leaflet-pane,#map .leaflet-tile,#map .leaflet-marker-icon,
#map .leaflet-marker-shadow,#map .leaflet-tile-container,#map .leaflet-zoom-box,
#map .leaflet-image-layer,#map .leaflet-layer{{position:absolute;left:0;top:0}}
#map .leaflet-container{{overflow:hidden}}
#map .leaflet-tile{{width:256px;height:256px;user-select:none}}
#map .leaflet-marker-icon{{display:block}}
/* 若 Leaflet CSS 从 CDN 加载失败，上面的兜底规则保证瓦片仍被正确裁剪定位 */
#map .leaflet-control-container .leaflet-top{{position:absolute;top:0;left:0;z-index:1000}}
#map .leaflet-control-container .leaflet-bottom{{position:absolute;bottom:0;right:0;z-index:1000}}
#map .leaflet-control-zoom a{{display:block;width:30px;height:30px;line-height:30px;
  text-align:center;background:#fff;border-bottom:1px solid #ccc;color:#333;
  text-decoration:none;font-size:18px;font-weight:600}}
#map .leaflet-popup{{position:absolute;z-index:700}}
.legend{{padding:12px 14px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);
  display:flex;gap:16px;flex-wrap:wrap}}
.legend i{{display:inline-block;width:22px;height:0;border-top:3px solid;vertical-align:middle;
  margin-right:6px;border-radius:2px}}

/* 用餐块 */
.stop.meal{{background:#fdfaf3;border-style:dashed}}
.stop.meal .time{{background:#faf5e8}}
.stop.meal h3{{font-weight:600;color:#8a6d1f}}

/* 未安排 */
.dropped{{margin-top:24px;padding:14px 18px;background:#fffbea;border:1px solid #f0e0a0;
  font-size:13px;color:#7a6a1f}}
.dropped b{{color:#5a4d10}}

footer{{margin-top:44px;padding-top:18px;border-top:1px solid var(--line);
  font-size:12px;color:var(--muted);line-height:1.9}}
footer code{{background:#eee;padding:1px 5px;border-radius:3px;font-size:11px}}

@media print{{
  body{{background:#fff}}
  .tab,.mapbox,.dropped{{display:none!important}}
  .day{{display:block!important;page-break-after:always}}
  .wrap{{max-width:none;padding:0}}
}}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>{title}</h1>
  <div class="sub">{subtitle}</div>
  <div class="kpis">
    <div class="kpi"><div class="v">{days}</div><div class="l">天数</div></div>
    <div class="kpi"><div class="v">{poi_count}</div><div class="l">景点</div></div>
    <div class="kpi"><div class="v">{travel_h}h</div><div class="l">交通</div></div>
    <div class="kpi"><div class="v">{visit_h}h</div><div class="l">游览</div></div>
    <div class="kpi"><div class="v">{efficiency}%</div><div class="l">时间利用率</div></div>
    <div class="kpi"><div class="v">¥{ticket}</div><div class="l">门票</div></div>
  </div>
</header>

<div class="tabs" id="tabs"></div>

<div class="grid">
  <div id="days"></div>
  <div class="mapbox">
    <div id="map"></div>
    <div class="legend">
      <span><i style="border-color:#c0392b"></i>步行</span>
      <span><i style="border-color:#2471a3;border-top-style:dashed"></i>打车/驾车</span>
      <span style="color:#999">底图 高德 · 轨迹 真实路网</span>
    </div>
  </div>
</div>

{dropped_html}

<footer>
  <div><b>数据来源</b>：路网耗时与轨迹来自 Valhalla / OSRM（OpenStreetMap 真实路网），
  地理编码来自 Photon。坐标已从 WGS-84 转换为 GCJ-02 以对齐高德底图。</div>
  <div><b>算法</b>：多日带时间窗的路径规划（VRPTW 变体）——全局贪心插入 + 跨日重平衡
  + 2-opt/or-opt 局部搜索 + 破坏重建（LNS）。目标函数加权：交通时长、时间窗违规、
  景点质量、门票、长距离跳跃。</div>
  <div><b>注意</b>：开放时间与门票为静态设定，出发前请以官方为准。</div>
</footer>

</div>

<script>@@LEAFLET_JS@@</script>
<script>
const DATA = {data_json};
const COLORS = {colors_json};

// ---------- 时间轴渲染 ----------
const tabs = document.getElementById('tabs');
const daysEl = document.getElementById('days');

DATA.days.forEach((d, i) => {{
  const btn = document.createElement('button');
  btn.className = 'tab' + (i === 0 ? ' on' : '');
  btn.textContent = 'Day ' + d.day;
  if (i === 0) {{ btn.style.background = COLORS[0]; }}
  btn.onclick = () => {{
    document.querySelectorAll('.tab').forEach((b, j) => {{
      b.classList.toggle('on', j === i);
      b.style.background = j === i ? COLORS[i] : '';
    }});
    document.querySelectorAll('.day').forEach((el, j) => el.classList.toggle('on', j === i));
    focusDay(i);
  }};
  tabs.appendChild(btn);

  const wrap = document.createElement('div');
  wrap.className = 'day' + (i === 0 ? ' on' : '');

  const h = document.createElement('div');
  h.className = 'dayhead';
  h.style.borderLeftColor = COLORS[i];
  const flag = d.feasible ? '' : ' ⚠ 超时' + d.violation_min + 'min';
  h.innerHTML = `<div class="t">Day ${{d.day}}${{flag}}</div>
    <div class="m">交通 ${{d.travel_min}}min · ¥${{d.ticket}} · ${{d.poi_count}} 个点 · 结束 ${{d.end_time}}</div>`;
  wrap.appendChild(h);

  d.detail.forEach((s, k) => {{
    // 用餐块：视觉上区分，不占序号
    if (s.is_meal) {{
      const m = document.createElement('div');
      m.className = 'stop meal';
      m.innerHTML = `<div class="time"><b>${{s.arrive}}</b><span>${{s.leave}} 结束</span></div>
        <div class="body"><h3>🍜 ${{s.name}}<span class="tag p3">${{s.visit_min}} 分钟</span></h3>
        <div class="meta"><span>就近用餐 · 建议提前查好周边餐厅</span></div></div>`;
      wrap.appendChild(m);
      return;
    }}
    if (k > 0 && s.leg_mode) {{
      const leg = document.createElement('div');
      leg.className = 'leg';
      const mode = s.leg_mode === '起点' ? null : s.leg_mode;
      if (mode) {{
        leg.innerHTML = `<span class="dot"></span>
          <span class="mode">${{mode}}</span>
          <span>${{s.travel_min}} 分钟</span>
          ${{s.leg_km ? `<span>· ${{s.leg_km}} km</span>` : ''}}`;
        wrap.appendChild(leg);
      }}
    }}
    const st = document.createElement('div');
    st.className = 'stop';
    const pr = s.priority === 1 ? ['p1', '必去'] : s.priority === 2 ? ['p2', '重要'] : ['p3', '选去'];
    const num = d.detail.slice(0, k + 1).filter(x => !x.is_meal).length;
    st.innerHTML = `
      <div class="time"><b>${{s.arrive}}</b><span>${{s.leave}} 离开</span></div>
      <div class="body">
        <h3><span class="idx">${{num}}</span>${{s.name}}
          <span class="tag ${{pr[0]}}">${{pr[1]}}</span></h3>
        <div class="meta">
          <span>游览 <b>${{s.visit_min}}</b> 分</span>
          ${{s.ticket ? `<span>门票 <b>¥${{s.ticket}}</b></span>` : '<span>免费</span>'}}
          <span>★ ${{s.rating}}</span>
          <span>开放 ${{s.open_time}}–${{s.close_time}}</span>
          ${{s.category ? `<span>${{s.category}}</span>` : ''}}
        </div>
      </div>`;
    wrap.appendChild(st);
  }});

  daysEl.appendChild(wrap);
}});

// ---------- 地图 ----------
const map = L.map('map', {{zoomControl: true, scrollWheelZoom: true}});
// 高德中文矢量底图（GCJ-02）
L.tileLayer('https://webrd0{{s}}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={{x}}&y={{y}}&z={{z}}', {{
  subdomains: ['1','2','3','4'], maxZoom: 18, attribution: '© 高德地图'
}}).addTo(map);

const layers = [];

DATA.days.forEach((d, i) => {{
  const g = L.layerGroup();
  // 轨迹：先画白色描边垫底（否则在密集底图上几乎看不见），再画彩色线
  (d.geometry || []).forEach((seg, si) => {{
    const pts = seg.map(p => [p[0], p[1]]);
    if (pts.length < 2) return;
    const isTaxi = (d.detail[si + 1] || {{}}).leg_mode === '打车/驾车';
    L.polyline(pts, {{color: '#ffffff', weight: 9, opacity: .95,
      lineCap: 'round', lineJoin: 'round'}}).addTo(g);
    L.polyline(pts, {{
      color: isTaxi ? '#1565c0' : '#d32f2f',
      weight: 5, opacity: 1,
      dashArray: isTaxi ? '10 7' : null,
      lineCap: 'round', lineJoin: 'round'
    }}).addTo(g);
    // 起终点小圆点，让首尾更清楚
    [pts[0], pts[pts.length - 1]].forEach(p => {{
      L.circleMarker(p, {{radius: 3, color: '#fff', weight: 1.5,
        fillColor: isTaxi ? '#1565c0' : '#d32f2f', fillOpacity: 1}}).addTo(g);
    }});
  }});
  // 点（用餐块不上图）
  let poiNo = 0;
  d.detail.forEach((s, k) => {{
    if (s.is_meal) return;
    poiNo += 1;
    if (!s.gcj_lat) return;
    const icon = L.divIcon({{
      html: `<div style="background:${{COLORS[i]}};color:#fff;width:30px;height:30px;
        border-radius:50%;display:flex;align-items:center;justify-content:center;
        font-size:14px;font-weight:800;border:3px solid #fff;
        box-shadow:0 2px 6px rgba(0,0,0,.45);font-family:-apple-system,sans-serif">${{poiNo}}</div>`,
      className: '', iconSize: [30, 30], iconAnchor: [15, 15]
    }});
    L.marker([s.gcj_lat, s.gcj_lng], {{icon, zIndexOffset: 1000}})
      .bindPopup(`<b>${{s.name}}</b><br>${{s.arrive}} – ${{s.leave}}<br>
        游览 ${{s.visit_min}} 分${{s.ticket ? ' · ¥' + s.ticket : ''}}`)
      .addTo(g);
  }});
  layers.push(g);
}});

function focusDay(i) {{
  layers.forEach((g, j) => {{ if (j === i) {{ g.addTo(map); }} else {{ map.removeLayer(g); }} }});
  const pts = [];
  (DATA.days[i].detail || []).forEach(s => {{ if (s.gcj_lat) pts.push([s.gcj_lat, s.gcj_lng]); }});
  if (pts.length === 1) {{
    map.setView(pts[0], 14);
  }} else if (pts.length > 1) {{
    map.fitBounds(L.latLngBounds(pts).pad(0.22), {{animate: false}});
  }}
}}

layers[0].addTo(map);
// 等容器完成布局后再定位，否则尺寸为 0 会导致缩放错乱
setTimeout(() => {{ map.invalidateSize(); focusDay(0); }}, 60);
window.addEventListener('resize', () => {{ map.invalidateSize(); }});
</script>
</body>
</html>
"""


def _load_assets() -> tuple:
    """读入本地 Leaflet 资源，用于内联成真正的单文件（离线可用）。"""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
    css_path = os.path.join(base, "leaflet.css")
    js_path = os.path.join(base, "leaflet.js")
    css = js = ""
    if os.path.exists(css_path):
        with open(css_path, encoding="utf-8") as f:
            css = f.read()
    else:
        css = '@import url("https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css");'
    if os.path.exists(js_path):
        with open(js_path, encoding="utf-8") as f:
            js = f.read()
    else:
        js = 'document.write(\'<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"><\\/script>\');'
    return css, js


def render(plan: dict, out_path: str, title: str = None) -> str:
    st = plan.get("stats", {})
    city = st.get("city", "")
    days = st.get("days", len(plan.get("days", [])))
    title = title or f"{city} · {days} 天行程"

    mode_label = {"hybrid": "步行 + 打车混合", "pedestrian": "全程步行",
                  "auto": "全程驾车", "bicycle": "骑行"}.get(
        st.get("travel_mode", ""), st.get("travel_mode", ""))

    subtitle = (f"{st.get('poi_count', 0)} 个候选点 · 真实路网耗时优化 · "
                f"{mode_label} · 求解 {st.get('elapsed_sec', 0)}s")

    dropped = plan.get("summary", {}).get("dropped", [])
    id2name = {p["id"]: p["name"] for p in plan.get("pois", [])}
    dropped_names = [id2name.get(x, x) for x in dropped]
    dropped_html = ""
    if dropped_names:
        dropped_html = (f'<div class="dropped"><b>未安排（{len(dropped_names)} 个）</b>：'
                        f'{"、".join(dropped_names)}<br>'
                        f'<span style="opacity:.8">原因：加进去会超出当天时间窗，或与已选路线'
                        f'交通成本过高。可增加天数、调整节奏，或把它们标为"必去"后重算。</span></div>')

    leaflet_css, leaflet_js = _load_assets()

    # 注意：Leaflet 的 CSS/JS 里含大量花括号，绝不能走 str.format。
    # 用不可见分隔符占位，format 之后再替换，避免转义地狱。
    html = HTML.format(
        title=title,
        subtitle=subtitle,
        leaflet_css="@@LEAFLET_CSS@@",
        leaflet_js="@@LEAFLET_JS@@",
        days=days,
        poi_count=st.get("poi_count", 0),
        travel_h=round(st.get("total_travel_min", 0) / 60, 1),
        visit_h=round(st.get("total_visit_min", 0) / 60, 1),
        efficiency=int(round(st.get("efficiency", 0) * 100)),
        ticket=round(plan.get("summary", {}).get("total_ticket", 0)),
        accent=DAY_COLORS[0],
        dropped_html=dropped_html,
        data_json=json.dumps(plan, ensure_ascii=False),
        colors_json=json.dumps(DAY_COLORS),
    )
    html = html.replace("@@LEAFLET_CSS@@", leaflet_css).replace("@@LEAFLET_JS@@", leaflet_js)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", default="itinerary.html")
    ap.add_argument("--title")
    args = ap.parse_args()

    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    p = render(plan, args.out, args.title)
    print(f"已生成: {p}  ({os.path.getsize(p)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
