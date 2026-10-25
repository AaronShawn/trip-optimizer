#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲染器离线测试：用伪造 plan 验证 HTML 生成，不联网。

运行：python tests/test_render.py
"""
import json
import os
import sys
import tempfile

# Windows 控制台默认 cp1252/GBK，直接 print 中文会 UnicodeEncodeError。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import render  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {detail}")


# 构造一个最小但完整的 plan（含一个用餐块，验证渲染路径）
FAKE_PLAN = {
    "days": [
        {
            "day": 1,
            "pois": ["p1", "p2"],
            "detail": [
                {"poi": "p1", "name": "测试景点A", "arrive": "09:00", "start": "09:00",
                 "leave": "10:30", "travel_min": 0, "visit_min": 90, "leg_mode": "起点",
                 "lat": 39.9174, "lng": 116.3908, "gcj_lat": 39.918833, "gcj_lng": 116.397024,
                 "ticket": 60, "rating": 4.9, "priority": 1, "category": "历史",
                 "open_time": "08:30", "close_time": "17:00"},
                {"poi": "_lunch", "name": "午餐", "category": "用餐", "arrive": "10:30",
                 "start": "10:30", "leave": "11:30", "travel_min": 0, "visit_min": 60,
                 "is_meal": True, "leg_mode": "用餐"},
                {"poi": "p2", "name": "测试景点B", "arrive": "11:50", "start": "11:50",
                 "leave": "13:20", "travel_min": 20, "visit_min": 90, "leg_mode": "步行",
                 "leg_km": 1.4, "lat": 39.9245, "lng": 116.39, "gcj_lat": 39.9259,
                 "gcj_lng": 116.3962, "ticket": 0, "rating": 4.5, "priority": 3,
                 "category": "园林", "open_time": "06:30", "close_time": "21:00"},
            ],
            "travel_min": 20, "end_time": "13:20", "ticket": 60, "feasible": True,
            "violation_min": 0, "closed_conflict": [], "poi_count": 2,
            "geometry": [
                [[39.9188, 116.3970], [39.9210, 116.3965], [39.9259, 116.3962]],
            ],
            "route_km": 1.4,
        }
    ],
    "summary": {
        "total_pois": 2, "dropped": [], "dropped_must_go": [], "unplaceable": [],
        "total_travel_min": 20, "total_ticket": 60.0,
        "all_must_go_covered": True, "load_balance": [2],
        "solver": "builtin-greedy+2opt+lns",
    },
    "config": {"weights": {}, "days": 1},
    "stats": {
        "total_visit_min": 180, "total_travel_min": 20, "efficiency": 0.9,
        "poi_count": 2, "elapsed_sec": 1.0, "travel_mode": "hybrid",
        "city": "测试城", "days": 1,
    },
    "pois": [
        {"id": "p1", "name": "测试景点A", "lat": 39.9174, "lng": 116.3908,
         "visit_min": 90, "ticket": 60, "rating": 4.9, "priority": 1, "category": "历史"},
        {"id": "p2", "name": "测试景点B", "lat": 39.9245, "lng": 116.39,
         "visit_min": 90, "ticket": 0, "rating": 4.5, "priority": 3, "category": "园林"},
    ],
}

print("[渲染器]")
tmp = os.path.join(tempfile.gettempdir(), "_render_test.html")
try:
    out = render.render(FAKE_PLAN, tmp)
    html = open(out, encoding="utf-8").read()

    check("生成文件存在", os.path.exists(out))
    # Leaflet 的 JS(147KB)+CSS(15KB) 内联后，文件必然 >160KB。
    # 这个下限就是在验证"确实内联了"，而不是只留了个 CDN 链接。
    check("体积 >160KB（Leaflet 已内联）", len(html) > 160_000, f"{len(html)}B")
    check("Leaflet 已内联（不含 CDN script src）",
          "cdn.jsdelivr.net/npm/leaflet" not in html)
    check("包含 Leaflet 核心代码", "L.Map" in html or "leaflet" in html.lower())
    check("含标题", "测试城" in html)
    check("含景点名", "测试景点A" in html and "测试景点B" in html)
    check("含用餐块", "午餐" in html)
    check("含轨迹坐标", "39.9188" in html or "39.9259" in html)
    check("含统计数字", "90%".replace("%", "") in html or "efficiency" in html.lower())
    check("无未替换的占位符",
          "@@LEAFLET_CSS@@" not in html and "@@LEAFLET_JS@@" not in html)
    check("含高德瓦片地址", "autonavi.com" in html)

    # JSON 数据必须能正确回读
    start = html.index("const DATA = ") + len("const DATA = ")
    end = html.index(";\nconst COLORS", start)
    parsed = json.loads(html[start:end])
    check("内嵌 JSON 可解析", parsed["stats"]["city"] == "测试城")
    check("detail 含用餐标记", any(d.get("is_meal") for d in parsed["days"][0]["detail"]))
finally:
    if os.path.exists(tmp):
        os.remove(tmp)

print(f"\n{'=' * 50}")
print(f"  通过 {PASS}  失败 {FAIL}")
print(f"{'=' * 50}")
sys.exit(0 if FAIL == 0 else 1)
