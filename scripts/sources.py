#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources.py — 数据源适配层

实测可用性（2026-10，本机网络）：
  ✅ Valhalla (valhalla1.openstreetmap.de)  真实路网 route / matrix / 多模式
  ✅ OSRM (router.project-osrm.org)        真实路网 route / table（但 foot/bike 实际是汽车速度，不可用于步行）
  ✅ Photon (photon.komoot.io)             地理编码 + 类别检索，支持中文，无需 key
  ✅ 高德瓦片 (webrd01.is.autonavi.com)     中文底图，可直接 <img> 引用
  ❌ Overpass (全部镜像)                    nginx 406 / 超时，本机不可用
  ❌ 高德/百度 REST API                    需 key，demo key 返回 INVALID_USER_KEY
  ❌ OSM 官方瓦片 / CartoDB                 超时 / 连接重置

所有请求默认绕过系统代理（本机沙箱代理会导致 502）。
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

VALHALLA = "https://valhalla1.openstreetmap.de"
OSRM = "https://router.project-osrm.org"
PHOTON = "https://photon.komoot.io"

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 矩阵缓存：路网矩阵是最贵的开销（13x13 需 ~25s），同一批坐标重跑时直接复用
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache")
_CACHE_TTL = 7 * 24 * 3600


def _cache_key(coords: list, mode: str) -> str:
    raw = json.dumps([[round(a, 5), round(b, 5)] for a, b in coords] + [mode], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _cache_get(key: str):
    p = os.path.join(_CACHE_DIR, key + ".json")
    if not os.path.exists(p):
        return None
    if time.time() - os.path.getmtime(p) > _CACHE_TTL:
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_put(key: str, value) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(os.path.join(_CACHE_DIR, key + ".json"), "w", encoding="utf-8") as f:
        json.dump(value, f)


def _get(url: str, timeout: int = 40) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9"})
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _retry(fn, tries: int = 3, delay: float = 2.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    raise last


# ---------------------------------------------------------------- 地理编码

def geocode(query: str, limit: int = 5, bbox: str = None,
            osm_tag: str = None, lang: str = None) -> list:
    """地点名 → 坐标（返回 WGS-84）。支持中文。

    bbox 格式 "minLon,minLat,maxLon,maxLat"
    osm_tag 例 "tourism:museum"
    """
    params = {"q": query, "limit": str(limit)}
    if bbox:
        params["bbox"] = bbox
    if osm_tag:
        params["osm_tag"] = osm_tag
    if lang:
        params["lang"] = lang
    url = f"{PHOTON}/api/?" + urllib.parse.urlencode(params)

    def _do():
        return _get(url, timeout=25)
    data = _retry(_do)
    out = []
    for f in data.get("features", []):
        pr = f.get("properties", {})
        lng, lat = f["geometry"]["coordinates"][:2]
        out.append({
            "name": pr.get("name") or pr.get("street") or query,
            "lat": lat, "lng": lng,
            "city": pr.get("city", ""), "district": pr.get("district", ""),
            "osm_key": pr.get("osm_key", ""), "osm_value": pr.get("osm_value", ""),
            "country": pr.get("country", ""),
            "extent": pr.get("extent"),
        })
    return out


def geocode_one(query: str, bbox: str = None) -> dict:
    r = geocode(query, limit=1, bbox=bbox)
    if not r:
        raise LookupError(f"找不到地点：{query}")
    return r[0]


# ---------------------------------------------------------------- 路网

def matrix(coords: list, mode: str = "pedestrian") -> list:
    """真实路网耗时矩阵（分钟）。coords=[(lat,lng),...]

    mode: pedestrian | auto | bicycle | hybrid
    返回 T[i][j] = 从 i 到 j 的分钟数。
    单次上限 100 个点对（Valhalla 限制），本函数自动分块。

    hybrid（推荐）：模拟真实游客行为 —— 短距离步行，长距离打车。
      对每个点对取 步行时间 与 (驾车时间 + 打车附加 8 分钟) 的较小值，
      并以 walk_max_min 为分界（超过就必然改打车）。
      这比单一步行模式真实得多：北京故宫→颐和园步行 182 分钟，打车约 35 分钟。
    """
    if mode == "hybrid":
        return _hybrid_matrix(coords)
    return _raw_matrix(coords, mode)


def _raw_matrix(coords: list, mode: str) -> list:
    n = len(coords)
    T = [[0.0] * n for _ in range(n)]
    if n <= 1:
        return T

    key = _cache_key(coords, mode)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    block = 10
    for si in range(0, n, block):
        for ti in range(0, n, block):
            src = coords[si:si + block]
            tgt = coords[ti:ti + block]
            payload = {
                "sources": [{"lat": a, "lon": b} for a, b in src],
                "targets": [{"lat": a, "lon": b} for a, b in tgt],
                "costing": mode,
            }
            url = f"{VALHALLA}/sources_to_targets?json=" + urllib.parse.quote(json.dumps(payload))
            data = _retry(lambda: _get(url, timeout=60))
            for i, row in enumerate(data["sources_to_targets"]):
                for cell in row:
                    T[si + i][ti + cell["to_index"]] = cell.get("time", 0) / 60.0
    _cache_put(key, T)
    return T


def _hybrid_matrix(coords: list, walk_max_min: float = 30.0,
                   walk_max_km: float = 2.5, taxi_overhead_min: float = 8.0) -> list:
    """步行 + 打车混合矩阵。

    判定规则（贴近真实游客行为）：
      - 距离 ≤ walk_max_km 且步行时间 ≤ walk_max_min  → 步行
      - 否则比较 步行时间 vs (打车时间 + 找车等待)，取小者
    注：不含地铁。国内大城市 3km 以上更可能坐地铁，地铁通常比打车略快，
        因此打车估算是偏保守（耗时略长）的上界。
    """
    Tw = _raw_matrix(coords, "pedestrian")
    Tc = _raw_matrix(coords, "auto")
    Td = _raw_distance(coords)
    n = len(coords)
    T = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            walk = Tw[i][j]
            taxi = Tc[i][j] + taxi_overhead_min
            if Td[i][j] <= walk_max_km and walk <= walk_max_min:
                T[i][j] = walk
            else:
                T[i][j] = min(walk, taxi)
    return T


def _raw_distance(coords: list) -> list:
    """步行路网的距离矩阵（km），用于判断"是否近到该走路"。"""
    import geo
    n = len(coords)
    D = [[0.0] * n for _ in range(n)]
    key = _cache_key(coords, "walk_dist")
    cached = _cache_get(key)
    if cached is not None:
        return cached
    block = 10
    for si in range(0, n, block):
        for ti in range(0, n, block):
            payload = {
                "sources": [{"lat": a, "lon": b} for a, b in coords[si:si + block]],
                "targets": [{"lat": a, "lon": b} for a, b in coords[ti:ti + block]],
                "costing": "pedestrian",
            }
            url = f"{VALHALLA}/sources_to_targets?json=" + urllib.parse.quote(json.dumps(payload))
            data = _retry(lambda: _get(url, timeout=60))
            for i, row in enumerate(data["sources_to_targets"]):
                for cell in row:
                    D[si + i][ti + cell["to_index"]] = cell.get("distance", 0.0)
    _cache_put(key, D)
    return D


def hybrid_legs(coords: list, walk_max_min: float = 25.0) -> list:
    """返回每段实际采用的方式，用于展示。"""
    Tw = _raw_matrix(coords, "pedestrian")
    Tc = _raw_matrix(coords, "auto")
    out = []
    for i in range(len(coords) - 1):
        walk = Tw[i][i + 1]
        taxi = Tc[i][i + 1]
        if walk > walk_max_min and taxi + 8.0 < walk:
            out.append({"mode": "taxi", "min": round(taxi + 8.0, 1), "walk_alt": round(walk, 1)})
        else:
            out.append({"mode": "walk", "min": round(walk, 1), "taxi_alt": round(taxi, 1)})
    return out


def hybrid_route(coords: list, walk_max_min: float = 30.0,
                 walk_max_km: float = 2.5, taxi_overhead_min: float = 8.0) -> dict:
    """混合交通的完整路线：逐段判断步行/打车，分别取真实几何。

    返回 {minutes, km, legs:[{mode, shape, minutes, km, maneuvers}]}
    shape 已转成 GCJ-02，可直接叠在高德底图上。
    """
    import geo
    if len(coords) < 2:
        return {"minutes": 0, "km": 0, "legs": []}

    Tw = _raw_matrix(coords, "pedestrian")
    Tc = _raw_matrix(coords, "auto")
    Td = _raw_distance(coords)
    legs = []
    total_min = 0.0
    total_km = 0.0

    for i in range(len(coords) - 1):
        walk = Tw[i][i + 1]
        taxi = Tc[i][i + 1]
        near = Td[i][i + 1] <= walk_max_km and walk <= walk_max_min
        use_taxi = (not near) and (taxi + taxi_overhead_min) < walk
        mode = "auto" if use_taxi else "pedestrian"
        adjust = taxi_overhead_min if use_taxi else 0.0

        try:
            r = route([coords[i], coords[i + 1]], mode=mode)
        except Exception:
            r = {"minutes": round(walk, 1), "km": Td[i][i + 1], "legs": []}

        leg = {
            "mode": "打车/驾车" if use_taxi else "步行",
            "minutes": round(r["minutes"] + adjust, 1),
            "km": r["km"] or round(Td[i][i + 1], 2),
            "shape": r["legs"][0]["shape"] if r.get("legs") else [],
            "maneuvers": r["legs"][0].get("maneuvers", []) if r.get("legs") else [],
        }
        legs.append(leg)
        total_min += leg["minutes"]
        total_km += leg["km"]

    return {"minutes": round(total_min, 1), "km": round(total_km, 2), "legs": legs}


def route(coords: list, mode: str = "pedestrian") -> dict:
    """完整路线（含几何，用于地图画线）。返回 {minutes, km, legs:[{shape:[latlng...]}]}"""
    if len(coords) < 2:
        return {"minutes": 0, "km": 0, "legs": []}
    if mode == "hybrid":
        return hybrid_route(coords)
    payload = {
        "locations": [{"lat": a, "lon": b, "type": "break"} for a, b in coords],
        "costing": mode,
        "directions_options": {"units": "kilometers", "language": "zh-CN"},
    }
    url = f"{VALHALLA}/route?json=" + urllib.parse.quote(json.dumps(payload))
    data = _retry(lambda: _get(url, timeout=60))

    import geo
    legs = []
    for leg in data["trip"]["legs"]:
        pts = geo.decode_polyline(leg["shape"])
        # 转换为 GCJ-02 以便叠加在高德底图上
        legs.append({
            "shape": [geo.wgs84_to_gcj02(p[1], p[0])[::-1] for p in pts],
            "minutes": round(leg["summary"]["time"] / 60.0, 1),
            "km": round(leg["summary"]["length"], 2),
            "maneuvers": [
                {"instruction": m.get("instruction", ""),
                 "km": m.get("length", 0),
                 "min": round(m.get("time", 0) / 60.0, 1)}
                for m in leg.get("maneuvers", []) if m.get("instruction")
            ][:12],
        })
    s = data["trip"]["summary"]
    return {"minutes": round(s["time"] / 60.0, 1), "km": round(s["length"], 2), "legs": legs}


def osrm_route(coords: list, mode: str = "driving") -> dict:
    """OSRM 兜底（仅 driving 可靠）。coords=[(lat,lng)]"""
    if len(coords) < 2:
        return {"minutes": 0, "km": 0, "legs": []}
    pts = ";".join(f"{lng},{lat}" for lat, lng in coords)
    url = f"{OSRM}/route/v1/{mode}/{pts}?overview=full&geometries=polyline"
    data = _retry(lambda: _get(url, timeout=45))
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM: {data.get('code')}")
    r = data["routes"][0]
    import geo
    pts2 = geo.decode_polyline(r["geometry"], precision=5)
    return {
        "minutes": round(r["duration"] / 60.0, 1),
        "km": round(r["distance"] / 1000.0, 2),
        "legs": [{"shape": [geo.wgs84_to_gcj02(p[1], p[0])[::-1] for p in pts2]}],
    }


# ---------------------------------------------------------------- 边界

def city_bbox(city: str, pad: float = 0.15) -> str:
    """按城市名取 bbox，用于限定搜索范围"""
    r = geocode_one(city)
    span = 0.35 + pad
    return f"{r['lng']-span},{r['lat']-span},{r['lng']+span},{r['lat']+span}"


TILE_TEMPLATES = {
    "amap_vec": "https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
    "amap_img": "https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
}
