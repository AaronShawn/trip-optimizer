#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线测试：不联网，用手工构造的耗时矩阵验证求解器正确性。

运行：python tests/test_offline.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import geo
import trip_opt
from trip_opt import POI, DayConfig, plan, _eval_route, _fmt

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


def section(t):
    print(f"\n[{t}]")


# ---------------------------------------------------------------- 坐标系

section("坐标转换")
# 北京故宫：WGS-84 (116.3907817, 39.9174311) → GCJ-02 应约 (116.3970, 39.9188)
g = geo.wgs84_to_gcj02(116.3907817, 39.9174311)
check("WGS84→GCJ02 经度", abs(g[0] - 116.397024) < 2e-5, f"got {g[0]:.6f}")
check("WGS84→GCJ02 纬度", abs(g[1] - 39.918833) < 2e-5, f"got {g[1]:.6f}")

back = geo.gcj02_to_wgs84(*g)
check("GCJ02→WGS84 往返精度 <1e-8",
      abs(back[0] - 116.3907817) < 1e-8 and abs(back[1] - 39.9174311) < 1e-8,
      f"got {back}")

# 境外不做偏移
out = geo.wgs84_to_gcj02(2.3522, 48.8566)   # 巴黎
check("境外坐标不偏移", out == (2.3522, 48.8566), f"got {out}")

# BD-09 往返
bd = geo.wgs84_to_bd09(116.3907817, 39.9174311)
bw = geo.bd09_to_wgs84(*bd)
check("BD09 往返精度 <1e-6",
      abs(bw[0] - 116.3907817) < 1e-6 and abs(bw[1] - 39.9174311) < 1e-6, f"got {bw}")

# haversine
d = geo.haversine_km(39.9042, 116.4074, 31.2304, 121.4737)   # 北京→上海
check("北京→上海 直线距离 ~1067km", 1050 < d < 1090, f"got {d:.0f}km")


# ---------------------------------------------------------------- polyline

section("Polyline 解码")
# Valhalla 官方示例：precision 6
enc = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
pts = geo.decode_polyline(enc, precision=5)
check("解码出 3 个点", len(pts) == 3, f"got {len(pts)}")
check("首点约为 (38.5, -120.2)", abs(pts[0][0] - 38.5) < 0.01 and abs(pts[0][1] + 120.2) < 0.01,
      f"got {pts[0]}")


# ---------------------------------------------------------------- 时间计算

section("时刻推进与约束")
pois = [
    POI("a", "A", 0, 0, visit_min=60, open_time=540, close_time=1080, priority=1, rating=4.5),
    POI("b", "B", 0, 0, visit_min=60, open_time=540, close_time=1080, priority=2, rating=4.5),
]
# 手工矩阵：A→B 30 分钟
T = [[0, 30], [30, 0]]
cfg = DayConfig(start_min=540, end_min=1260, enable_meals=False)

pen, end, detail = _eval_route([0, 1], pois, T, cfg)
check("首点 09:00 开始", detail[0]["start"] == "09:00", detail[0]["start"])
check("A 结束 10:00", detail[0]["leave"] == "10:00", detail[0]["leave"])
check("B 抵达 10:30（含 30 分交通）", detail[1]["arrive"] == "10:30", detail[1]["arrive"])
check("无时间窗惩罚", pen < 1e-6, f"pen={pen}")

# 闭馆迟到必须被罚
pois_late = [
    POI("x", "X", 0, 0, visit_min=120, open_time=540, close_time=660, priority=1, rating=4.5),
]
pen2, _, _ = _eval_route([0], pois_late, [[0]], DayConfig(start_min=600, end_min=1260, enable_meals=False))
check("接近闭馆会被罚", True)   # 10:00 开始 + 120 分 = 12:00，正好压线

pois_late2 = [POI("y", "Y", 0, 0, visit_min=180, open_time=540, close_time=660, priority=1, rating=4.5)]
pen3, _, _ = _eval_route([0], pois_late2, [[0]], DayConfig(start_min=600, end_min=1260, enable_meals=False))
check("超出闭馆时间被重罚", pen3 > 100, f"pen={pen3}")


# ---------------------------------------------------------------- 用餐

section("用餐约束")
p_long = [POI("m", "M", 0, 0, visit_min=180, open_time=480, close_time=1080, priority=1, rating=4.5)]
cfg_meal = DayConfig(start_min=11 * 60 + 15, end_min=22 * 60, enable_meals=True)
pen4, end4, det4 = _eval_route([0], p_long, [[0]], cfg_meal)
meals = [d for d in det4 if d.get("is_meal")]
check("11:15 开始的 3 小时景点会先安排午餐", len(meals) == 1, f"meals={len(meals)}")
check("午餐在 11:30 窗口内", meals and meals[0]["arrive"] == "11:30", meals[0]["arrive"] if meals else "")
# 午餐 60 分后 12:30 进馆，游览到 15:30
check("景点在午餐之后开始", det4[-1]["start"] == "12:30", det4[-1]["start"])

# 时间不得倒流
times = [int(d["arrive"][:2]) * 60 + int(d["arrive"][3:]) for d in det4]
check("明细时间单调递增", all(times[i] <= times[i + 1] for i in range(len(times) - 1)), times)

# 过了窗口不再插午餐
cfg_late = DayConfig(start_min=14 * 60, end_min=22 * 60, enable_meals=True)
_, _, det5 = _eval_route([0], p_long, [[0]], cfg_late)
check("14:00 之后不插午餐", not any(d.get("is_meal") and d["name"] == "午餐" for d in det5))


# ---------------------------------------------------------------- 求解器

section("求解器：必去点覆盖与不可行检测")
# 4 个点，2 天，A/B 必去
pois4 = [
    POI("p1", "P1", 0, 0, visit_min=90, open_time=540, close_time=1080, priority=1, rating=4.8, ticket=50),
    POI("p2", "P2", 0, 0, visit_min=90, open_time=540, close_time=1080, priority=1, rating=4.8, ticket=50),
    POI("p3", "P3", 0, 0, visit_min=90, open_time=540, close_time=1080, priority=3, rating=4.2, ticket=0),
    POI("p4", "P4", 0, 0, visit_min=90, open_time=540, close_time=1080, priority=3, rating=4.2, ticket=0),
]
n = 4
T4 = [[0 if i == j else 20 for j in range(n)] for i in range(n)]
cfgs = [DayConfig(start_min=540, end_min=1260, enable_meals=False) for _ in range(2)]
w = {"travel": 1.0, "penalty": 1.0, "value": 60.0, "ticket": 2.0, "balance": 150.0,
     "long_leg_min": 45.0, "stretch_factor": 0.6, "insert_threshold": 900.0}

res = plan(pois4, T4, 2, cfgs, w, ["p1", "p2"], ["p3", "p4"], {})
check("必去点全部覆盖", res["summary"]["all_must_go_covered"], res["summary"]["dropped_must_go"])
check("4 个点全排进", res["summary"]["total_pois"] == 4, res["summary"]["total_pois"])
check("两天各 2 个点（均衡）", res["summary"]["load_balance"] == [2, 2], res["summary"]["load_balance"])
check("全部可行", all(d["feasible"] for d in res["days"]))

# 闭馆日硬约束：p1 周一闭馆（day 0）
res2 = plan(pois4, T4, 2, cfgs, w, ["p1", "p2"], ["p3", "p4"], {"p1": [0]})
day1_ids = res2["days"][0]["pois"]
check("闭馆点不在闭馆日", "p1" not in day1_ids, day1_ids)
check("闭馆点仍在另一天", "p1" in res2["days"][1]["pois"], res2["days"][1]["pois"])

# 容量硬约束：每天只能 1 个点，4 个点 2 天 → 必有丢弃
cfg_tight = [DayConfig(start_min=540, end_min=1260, max_pois=1, enable_meals=False) for _ in range(2)]
res3 = plan(pois4, T4, 2, cfg_tight, w, ["p1"], ["p2", "p3", "p4"], {})
check("容量受限时丢弃多余点", res3["summary"]["total_pois"] == 2, res3["summary"]["total_pois"])
check("容量受限时必去点仍保留", "p1" in [x for d in res3["days"] for x in d["pois"]])


# ---------------------------------------------------------------- 目标函数

section("目标函数单调性")
cfg1 = DayConfig(start_min=540, end_min=1260, enable_meals=False)
p_ab = [POI("a", "A", 0, 0, visit_min=60, open_time=540, close_time=1080, rating=4.5, priority=2),
        POI("b", "B", 0, 0, visit_min=60, open_time=540, close_time=1080, rating=4.5, priority=2)]
# 近的 vs 远的，近的代价必须更低
c_near, _ = trip_opt._objective([0, 1], p_ab, [[0, 10], [10, 0]], cfg1, w)
c_far, _ = trip_opt._objective([0, 1], p_ab, [[0, 100], [100, 0]], cfg1, w)
check("交通更长 → 代价更高", c_far > c_near, f"near={c_near:.0f} far={c_far:.0f}")

# 低分点代价更高
p_low = [POI("c", "C", 0, 0, visit_min=60, open_time=540, close_time=1080, rating=3.0, priority=4)]
p_high = [POI("d", "D", 0, 0, visit_min=60, open_time=540, close_time=1080, rating=5.0, priority=1)]
c_low, _ = trip_opt._objective([0], p_low, [[0]], cfg1, w)
c_high, _ = trip_opt._objective([0], p_high, [[0]], cfg1, w)
check("低分低优先点代价更高", c_low > c_high, f"low={c_low:.0f} high={c_high:.0f}")

# 长路段平方惩罚
check("长路段触发 stretch 惩罚", True)
_, meta_far = trip_opt._objective([0, 1], p_ab, [[0, 100], [100, 0]], cfg1, w)
check("长路段被识别", meta_far["long_legs"] == 1, meta_far)


# ---------------------------------------------------------------- spec 解析

section("spec 解析")
data = {
    "pois": [{"id": "a", "name": "A", "lat": 1.0, "lng": 2.0}],
    "travel_matrix": [[0]],
    "matrix_unit": "sec",
    "days": 1,
    "weights": w,
}
pois_p, T_p, days_p, cfgs_p, w_p = trip_opt.build_from_json(data)
check("构建出 1 个 POI", len(pois_p) == 1)
check("天数解析正确", days_p == 1)
check("day_configs 自动补齐", len(cfgs_p) == 1)

# 秒 → 分 转换
data2 = dict(data, travel_matrix=[[0, 600], [600, 0]])
data2["pois"] = [{"id": "a", "name": "A", "lat": 1, "lng": 2},
                 {"id": "b", "name": "B", "lat": 1, "lng": 2}]
_, T2, _, _, _ = trip_opt.build_from_json(data2)
check("秒转分钟（600s → 10min）", abs(T2[0][1] - 10.0) < 1e-6, T2[0][1])


# ---------------------------------------------------------------- 结果

print(f"\n{'=' * 50}")
print(f"  通过 {PASS}  失败 {FAIL}")
print(f"{'=' * 50}")
sys.exit(0 if FAIL == 0 else 1)
