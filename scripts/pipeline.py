#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py — 端到端编排：查询 → 建矩阵 → 求解 → 输出

用法：
    python pipeline.py --city 北京 --days 3 --themes 历史,美食 --pace 标准 --out plan.json
    python pipeline.py --spec trip.json --out plan.json
"""
import argparse
import json
import math
import os
import sys
import time

# Windows 控制台默认 cp1252/GBK，打印中文会崩；强制 utf-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import geo
import sources
import trip_opt
from trip_opt import POI, DayConfig, plan


PACE_MAP = {"轻松": 0.75, "标准": 1.0, "紧凑": 1.25}

# 城市默认中心点 bbox（避免每次地理编码）
CITY_HINTS = {
    "北京": "39.9042,116.4074",
    "上海": "31.2304,121.4737",
    "广州": "23.1291,113.2644",
    "深圳": "22.5431,114.0579",
    "成都": "30.5728,104.0668",
    "杭州": "30.2741,120.1551",
    "西安": "34.3416,108.9398",
    "重庆": "29.5630,106.5516",
    "南京": "32.0603,118.7969",
    "武汉": "30.5928,114.3055",
    "厦门": "24.4798,118.0894",
    "青岛": "36.0671,120.3826",
    "昆明": "25.0389,102.7183",
    "长沙": "28.2282,112.9388",
    "苏州": "31.2989,120.5853",
    "天津": "39.3434,117.3616",
}


def _bbox_around(lat: float, lng: float, span: float = 0.32) -> str:
    return f"{lng-span},{lat-span},{lng+span},{lat+span}"


def resolve_pois(spec: dict) -> list:
    """把 spec 里的地点名解析成带坐标的 POI 列表。"""
    city = spec["city"]
    center = spec.get("center")
    if center:
        clat, clng = [float(x) for x in center.split(",")]
    elif city in CITY_HINTS:
        clat, clng = [float(x) for x in CITY_HINTS[city].split(",")]
    else:
        g = sources.geocode_one(city)
        clat, clng = g["lat"], g["lng"]
    bbox = _bbox_around(clat, clng, spec.get("bbox_span", 0.32))

    pois = []
    for i, item in enumerate(spec["places"]):
        name = item["name"]
        if item.get("lat") and item.get("lng"):
            lat, lng = item["lat"], item["lng"]
        else:
            hits = sources.geocode(name, limit=1, bbox=bbox)
            if not hits:
                # 放宽范围再试一次
                hits = sources.geocode(name, limit=1)
            if not hits:
                print(f"  [跳过] 无法定位: {name}", file=sys.stderr)
                continue
            lat, lng = hits[0]["lat"], hits[0]["lng"]
        pois.append(POI(
            id=item.get("id") or f"p{i}",
            name=name,
            lat=lat, lng=lng,
            visit_min=item.get("visit_min", 90),
            open_time=item.get("open_time", 9 * 60),
            close_time=item.get("close_time", 18 * 60),
            ticket=item.get("ticket", 0.0),
            rating=item.get("rating", 4.0),
            priority=item.get("priority", 3),
            category=item.get("category", ""),
            closed_days=item.get("closed_days", []),
            indoor=item.get("indoor", False),
        ))
    return pois


def run(spec: dict, verbose: bool = True) -> dict:
    t0 = time.time()
    pois = resolve_pois(spec)
    if not pois:
        raise RuntimeError("没有解析到任何地点")

    # 坐标系：POI 是 WGS-84，Valhalla 也是 WGS-84，直接可用
    coords = [(p.lat, p.lng) for p in pois]

    mode = spec.get("travel_mode", "pedestrian")
    if verbose:
        print(f"[1/4] 已定位 {len(pois)} 个地点，模式={mode}", file=sys.stderr)
    T = sources.matrix(coords, mode=mode)
    if verbose:
        print(f"[2/4] 路网矩阵 {len(T)}x{len(T)} 完成 ({time.time()-t0:.1f}s)", file=sys.stderr)

    days = spec.get("days", 3)
    pace = PACE_MAP.get(spec.get("pace", "标准"), 1.0)
    start_min = spec.get("day_start", 9 * 60)
    end_min = spec.get("day_end", 21 * 60)

    cfgs = []
    for d in range(days):
        cfgs.append(DayConfig(
            start_min=start_min, end_min=end_min,
            max_pois=spec.get("max_pois_per_day", 6),
            pace=pace,
        ))

    weights = {
        "travel": spec.get("w_travel", 1.0),
        "penalty": 1.0,
        "value": spec.get("w_value", 60.0),
        "ticket": spec.get("w_ticket", 2.0),
        "insert_threshold": spec.get("insert_threshold", 900.0),
        "balance": spec.get("w_balance", 150.0),
        "long_leg_min": spec.get("long_leg_min", 45.0),
        "stretch_factor": spec.get("stretch_factor", 0.6),
    }

    must = [p.id for p in pois if p.priority == 1]
    opt = [p.id for p in pois if p.priority > 1]

    closed = {}
    for p in pois:
        if p.closed_days:
            closed[p.id] = p.closed_days

    result = plan(pois, T, days, cfgs, weights, must, opt, closed)
    if verbose:
        print(f"[3/4] 求解完成 ({time.time()-t0:.1f}s)", file=sys.stderr)

    # 补充坐标与几何，供渲染
    id2poi = {p.id: p for p in pois}
    for d in result["days"]:
        for s in d["detail"]:
            if s.get("is_meal"):
                s["leg_mode"] = "用餐"
                continue
            p = id2poi[s["poi"]]
            g = geo.wgs84_to_gcj02(p.lng, p.lat)
            s["lat"], s["lng"] = p.lat, p.lng
            s["gcj_lat"], s["gcj_lng"] = g[1], g[0]
            s["ticket"] = p.ticket
            s["rating"] = p.rating
            s["priority"] = p.priority
            s["category"] = p.category
            s["indoor"] = p.indoor
            s["open_time"] = f"{p.open_time//60:02d}:{p.open_time%60:02d}"
            s["close_time"] = f"{p.close_time//60:02d}:{p.close_time%60:02d}"
        # 当天路线几何（跳过用餐，用餐不产生位移）
        seq = [id2poi[s["poi"]] for s in d["detail"] if not s.get("is_meal")]
        if len(seq) >= 2:
            try:
                rt = sources.route([(p.lat, p.lng) for p in seq], mode=mode)
                d["geometry"] = [leg["shape"] for leg in rt["legs"]]
                d["route_km"] = rt["km"]
                # 把每段交通方式写回 detail（按非用餐点顺序对齐）
                stops = [s for s in d["detail"] if not s.get("is_meal")]
                for k, leg in enumerate(rt["legs"]):
                    if k + 1 < len(stops):
                        stops[k + 1]["leg_mode"] = leg["mode"]
                        stops[k + 1]["leg_km"] = leg["km"]
                        stops[k + 1]["leg_maneuvers"] = leg.get("maneuvers", [])[:4]
                if stops:
                    stops[0]["leg_mode"] = "起点"
            except Exception as e:
                if verbose:
                    print(f"  [警告] 第{d['day']}天几何获取失败: {e}", file=sys.stderr)
                d["geometry"] = []
        else:
            d["geometry"] = []

    # 汇总统计
    total_visit = sum(s["visit_min"] for d in result["days"] for s in d["detail"])
    total_travel = sum(d["travel_min"] for d in result["days"])
    result["stats"] = {
        "total_visit_min": total_visit,
        "total_travel_min": total_travel,
        "efficiency": round(total_visit / max(1, total_visit + total_travel), 3),
        "poi_count": len(pois),
        "elapsed_sec": round(time.time() - t0, 1),
        "travel_mode": mode,
        "city": spec["city"],
        "days": days,
    }
    result["pois"] = [{
        "id": p.id, "name": p.name, "lat": p.lat, "lng": p.lng,
        "visit_min": p.visit_min, "ticket": p.ticket, "rating": p.rating,
        "priority": p.priority, "category": p.category,
    } for p in pois]
    if verbose:
        print(f"[4/4] 完成，总耗时 {time.time()-t0:.1f}s", file=sys.stderr)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", help="完整 spec JSON 文件")
    ap.add_argument("--city")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--pace", default="标准", choices=list(PACE_MAP))
    ap.add_argument("--mode", default="pedestrian", choices=["pedestrian", "auto", "bicycle"])
    ap.add_argument("--out", default="plan.json")
    args = ap.parse_args()

    if args.spec:
        with open(args.spec, encoding="utf-8") as f:
            spec = json.load(f)
    else:
        raise SystemExit("需要 --spec（先用 SKILL.md 里的流程生成 spec）")

    result = run(spec)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*64}")
    print(f"{result['stats']['city']} · {result['stats']['days']}天 · "
          f"{result['stats']['poi_count']}个候选 · {result['stats']['travel_mode']}")
    print(f"游览 {result['stats']['total_visit_min']}min / 交通 "
          f"{result['stats']['total_travel_min']}min · 效率 "
          f"{result['stats']['efficiency']*100:.0f}%")
    print(f"{'='*64}")
    for d in result["days"]:
        tag = "✓" if d["feasible"] else f"⚠超时{d['violation_min']}min"
        print(f"\nDay {d['day']}  交通{d['travel_min']}min  ¥{d['ticket']}  结束{d['end_time']}  {tag}")
        print("-" * 64)
        for s in d["detail"]:
            print(f"  {s['arrive']}→{s['leave']}  {s['name'][:18]:<20} "
                  f"🚶{s['travel_min']:>3}分  游{s['visit_min']:>3}分")
    if result["summary"]["dropped"]:
        print(f"\n未安排: {result['summary']['dropped']}")


if __name__ == "__main__":
    main()
