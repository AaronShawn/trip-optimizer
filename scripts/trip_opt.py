#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trip_opt.py — 行程优化核心引擎

零第三方依赖，纯 Python 标准库。
输入：POI 列表(endpoint A) / 消费者的必去清单(endpoint B)，输出：多日行程方案 + 指标。

设计要点：
- 真实路网耗时矩阵（不是直线距离）
- 带时间窗的多日 VRPTW（每辆车 = 一天）
- 目标函数：可解释的加权多目标（用户可调权重）
- 输出含"为什么这样排"的归因

用法：
    python trip_opt.py --input plan.json --out result.json
"""
import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional


# ============================================================
# 数据模型
# ============================================================

@dataclass
class POI:
    id: str
    name: str
    lat: float
    lng: float
    visit_min: int = 60                 # 游览时长（分钟）
    open_time: int = 9 * 60             # 开放时间（当日分钟数）
    close_time: int = 18 * 60
    ticket: float = 0.0
    rating: float = 4.0                 # 0-5
    priority: int = 3                   # 1=必去 2=重要 3=可选 4=备选
    category: str = ""
    closed_days: List[int] = field(default_factory=list)   # 0=周一
    indoor: bool = False                # 雨天友好


@dataclass
class DayConfig:
    start_min: int = 9 * 60
    end_min: int = 21 * 60
    start_poi: Optional[str] = None     # 当天出发点（酒店）
    end_poi: Optional[str] = None
    max_pois: int = 6
    max_travel_min: int = 180
    pace: float = 1.0                   # 0.7 轻松 / 1.0 标准 / 1.3 紧凑
    # 用餐时间窗（真实行程里不吃午饭的计划是假的）
    lunch_start: int = 11 * 60 + 30
    lunch_end: int = 13 * 60 + 30
    lunch_min: int = 60
    dinner_start: int = 17 * 60 + 30
    dinner_end: int = 19 * 60 + 30
    dinner_min: int = 60
    enable_meals: bool = True


# ============================================================
# 时间窗硬约束求解（VRPTW · 自研，无需 ortools）
# ============================================================

def _fmt(minutes: int) -> str:
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _eval_route(route: List[int], pois: List[POI], T: List[List[float]],
                cfg: DayConfig) -> Tuple[float, float, list]:
    """评估一条路线：返回 (等待+超时惩罚, 完成时刻, 明细)。

    用餐是硬性现实约束：午餐/晚餐窗口内必须留出用餐时间。
    若某个景点把整个午餐窗口占满，则插入用餐块（并把该景点的迟到罚计入）。
    """
    if not route:
        return 0.0, cfg.start_min, []

    cur = cfg.start_min
    penalty = 0.0
    detail = []
    prev = None
    lunch_done = not cfg.enable_meals
    dinner_done = not cfg.enable_meals

    def maybe_meal(t_now: float) -> float:
        """在抵达空档时插入用餐。只在真正合理的窗口内触发。

        关键：午餐只在 [lunch_start, lunch_end] 内触发，且已过窗口就视为"错过"，
        宁可不算这顿饭（用户会在路上解决），也不要排出一个 13:17 的"午餐"。
        """
        nonlocal penalty, lunch_done, dinner_done
        if not cfg.enable_meals:
            return t_now

        if not lunch_done:
            if cfg.lunch_start <= t_now <= cfg.lunch_end:
                detail.append({
                    "poi": "_lunch", "name": "午餐", "category": "用餐",
                    "arrive": _fmt(t_now), "start": _fmt(t_now),
                    "leave": _fmt(t_now + cfg.lunch_min),
                    "travel_min": 0, "visit_min": cfg.lunch_min,
                    "is_meal": True,
                })
                lunch_done = True
                return t_now + cfg.lunch_min
            if t_now > cfg.lunch_end:
                # 已经错过午餐窗口，标记为"已在路上解决"，不再插入
                lunch_done = True

        if not dinner_done:
            if cfg.dinner_start <= t_now <= cfg.dinner_end:
                detail.append({
                    "poi": "_dinner", "name": "晚餐", "category": "用餐",
                    "arrive": _fmt(t_now), "start": _fmt(t_now),
                    "leave": _fmt(t_now + cfg.dinner_min),
                    "travel_min": 0, "visit_min": cfg.dinner_min,
                    "is_meal": True,
                })
                dinner_done = True
                return t_now + cfg.dinner_min
            if t_now > cfg.dinner_end:
                dinner_done = True
        return t_now

    for idx in route:
        p = pois[idx]
        if prev is not None:
            cur += T[prev][idx]
        arrive = cur
        # 早到等待
        if arrive < p.open_time:
            penalty += (p.open_time - arrive) * 0.8
            cur = p.open_time
        # 迟到的超时惩罚（到点已闭馆，强罚）
        if cur > p.close_time - p.visit_min:
            late = cur - (p.close_time - p.visit_min)
            penalty += late * 8.0
        start = cur
        dur = p.visit_min * cfg.pace

        # 用餐插在景点之前的情形：抵达时刻已进午餐窗口，且这个景点会跨过窗口
        # → 先吃完再进馆，否则会"11:15 进馆、15:00 出来"这种事
        if (cfg.enable_meals and not lunch_done
                and arrive >= cfg.lunch_start - 20
                and arrive + dur > cfg.lunch_end):
            cur = maybe_meal(max(arrive, cfg.lunch_start))
            start = cur
            arrive = start
        cur = start + dur

        detail.append({
            "poi": p.id, "name": p.name,
            "arrive": _fmt(arrive), "start": _fmt(start), "leave": _fmt(cur),
            "travel_min": int(round(T[prev][idx])) if prev is not None else 0,
            "visit_min": int(round(dur)),
            "is_meal": False,
        })
        cur = maybe_meal(cur)
        prev = idx

    # 当天结束时若还没吃晚饭且时间已过窗口，不额外惩罚（用户可能回酒店吃）
    if cur > cfg.end_min:
        penalty += (cur - cfg.end_min) * 5.0
    return penalty, cur, detail


def _objective(route: List[int], pois: List[POI], T: List[List[float]],
               cfg: DayConfig, w: Dict[str, float]) -> Tuple[float, dict]:
    """单日多目标代价（越小越好）。

    各项可解释：
      travel   总交通分钟数
      penalty  时间窗违规（早到等待按 0.8 折价、迟到按 8 倍重罚、超时按 5 倍）
      value    低分/低优先级景点的"凑数"惩罚
      ticket   门票加价
      stretch  长距离跳跃额外惩罚（超长交通耗时的平方项，避免"上午圆明园下午天安门"）
    """
    if not route:
        return 0.0, {"travel_min": 0, "penalty": 0, "value": 0.0}

    pen, end, _ = _eval_route(route, pois, T, cfg)
    travel = sum(T[route[i]][route[i + 1]] for i in range(len(route) - 1))
    # "凑数"惩罚：只惩罚"可选但价值不高"的点。
    # 权重按可选程度线性递增：必去(1)=0，重要(2)=1，可选(3)=2，备选(4)=3。
    # 早期版本写成 (5 - priority)，方向反了 —— 必去点反而被罚最重，
    # 求解器会主动把 priority=1 的景点丢掉。
    value = sum((5.0 - pois[i].rating) * max(0, pois[i].priority - 1) for i in route)
    value = max(0.0, value)

    # 长距离跳跃惩罚：任何一段超过 45 分钟的交通，加平方项
    stretch = 0.0
    long_legs = 0
    for i in range(len(route) - 1):
        seg = T[route[i]][route[i + 1]]
        if seg > w.get("long_leg_min", 45.0):
            over = seg - w.get("long_leg_min", 45.0)
            stretch += over * over * w.get("stretch_factor", 0.6)
            long_legs += 1

    cost = (
        w.get("travel", 1.0) * travel
        + w.get("penalty", 1.0) * pen
        + w.get("value", 60.0) * value
        + w.get("ticket", 2.0) * sum(pois[i].ticket for i in route)
        + stretch
    )
    return cost, {"travel_min": int(travel), "penalty": int(pen),
                  "end": _fmt(end), "value_penalty": round(value, 2),
                  "long_legs": long_legs}


# ============================================================
# 求解器：贪心插入 + 2-opt/LNS 局部搜索（真实耗时矩阵）
# ============================================================

def _feasible_insert(route: List[int], pos: int, node: int, pois: List[POI],
                     T: List[List[float]], cfg: DayConfig) -> bool:
    """硬约束校验：插入后当天不得超出结束时间、不得违反闭馆时间。"""
    trial = route[:pos] + [node] + route[pos:]
    pen, end, _ = _eval_route(trial, pois, T, cfg)
    if end > cfg.end_min:
        return False
    # 任何一点到了已经关门，直接不可行
    if pen > 1e-6:
        # 允许少量等待（早到），但迟到超时不允许
        cur = cfg.start_min
        prev = None
        for idx in trial:
            p = pois[idx]
            if prev is not None:
                cur += T[prev][idx]
            if cur > p.close_time - p.visit_min:
                return False
            cur = max(cur, p.open_time) + p.visit_min * cfg.pace
            prev = idx
    return True


def _two_opt(route: List[int], pois: List[POI], T: List[List[float]],
             cfg: DayConfig, w: Dict[str, float], rounds: int = 60) -> List[int]:
    """2-opt + or-opt 局部改进。"""
    if len(route) < 3:
        return route
    best = route[:]
    best_cost, _ = _objective(best, pois, T, cfg, w)

    for _ in range(rounds):
        improved = False
        n = len(best)
        # 2-opt: 反转区间
        for i in range(n - 1):
            for j in range(i + 2, n):
                cand = best[:i + 1] + best[i + 1:j + 1][::-1] + best[j + 1:]
                c, _ = _objective(cand, pois, T, cfg, w)
                if c < best_cost - 1e-6:
                    best, best_cost, improved = cand, c, True
        # or-opt: 单点移位
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                cand = best[:]
                node = cand.pop(i)
                cand.insert(j, node)
                c, _ = _objective(cand, pois, T, cfg, w)
                if c < best_cost - 1e-6:
                    best, best_cost, improved = cand, c, True
        if not improved:
            break
    return best


def _lns_shuffle(route: List[int], pois: List[POI], T: List[List[float]],
                 cfg: DayConfig, w: Dict[str, float], iters: int = 250,
                 seed: int = 42) -> List[int]:
    """破坏-重建（LNS 简化版），跳出局部最优。"""
    rng = random.Random(seed)
    best = route[:]
    best_cost, _ = _objective(best, pois, T, cfg, w)

    for _ in range(iters):
        if len(best) < 2:
            break
        cand = best[:]
        # 随机移除 1~2 个
        k = rng.choice([1, 1, 2])
        removed = []
        for _ in range(min(k, len(cand))):
            pos = rng.randrange(len(cand))
            removed.append(cand.pop(pos))
        # 随机插回
        for node in removed:
            cand.insert(rng.randrange(len(cand) + 1), node)
        cand = _two_opt(cand, pois, T, cfg, w, rounds=8)
        c, _ = _objective(cand, pois, T, cfg, w)
        if c < best_cost - 1e-6:
            best, best_cost = cand, c
    return best


def plan(pois: List[POI], T: List[List[float]], days: int,
         day_cfgs: List[DayConfig], w: Dict[str, float],
         must_go: List[str], optional: List[str],
         closed_days_map: Dict[str, List[int]] = None) -> Dict:
    """主规划：全局贪心分配 → 跨日重平衡 → 单日路线优化 → 汇总结论。

    与"先聚类再逐日求解"相比，全局分配能保证：
      1. 闭馆日硬约束（闭馆当天的点不会被分配到那天）
      2. 每日负载均衡（不会出现 Day1 半天就收工、Day3 塞满的情况）
      3. 必去点优先占位（不会被"代价太高"筛掉）
    """
    closed_days_map = closed_days_map or {}
    id_to_idx = {p.id: i for i, p in enumerate(pois)}
    n = len(pois)

    must_idx = [id_to_idx[m] for m in must_go if m in id_to_idx]
    opt_idx = [id_to_idx[o] for o in optional if o in id_to_idx]
    all_idx = must_idx + opt_idx
    must_set = set(must_idx)

    cfgs = [day_cfgs[d] if d < len(day_cfgs) else day_cfgs[-1] for d in range(days)]

    # ---------- 1. 预计算可分配天（闭馆日硬约束） ----------
    allowed: Dict[int, List[int]] = {}
    for i in all_idx:
        cl = closed_days_map.get(pois[i].id, [])
        allowed[i] = [d for d in range(days) if d not in cl]
    unplaceable = [i for i in all_idx if not allowed[i]]

    # ---------- 2. 全局贪心：按优先级 + 评分排序，逐个选最便宜的一天插入 ----------
    routes: List[List[int]] = [[] for _ in range(days)]
    placed = set()

    order = sorted(all_idx, key=lambda i: (pois[i].priority, -pois[i].rating, -pois[i].visit_min))
    for i in order:
        if i in unplaceable:
            continue
        best = None
        for d in allowed[i]:
            if len(routes[d]) >= cfgs[d].max_pois:
                continue
            for pos in range(len(routes[d]) + 1):
                if not _feasible_insert(routes[d], pos, i, pois, T, cfgs[d]):
                    continue
                trial = routes[d][:pos] + [i] + routes[d][pos:]
                c, _ = _objective(trial, pois, T, cfgs[d], w)
                base, _ = _objective(routes[d], pois, T, cfgs[d], w)
                marginal = c - base
                # 负载均衡：按"这天当前收工时刻"是否已晚于其他天的均值来加价，
                # 而不是按点数——否则"两点的一天"和"六点的一天"会被判为均衡。
                cur_end = _eval_route(routes[d], pois, T, cfgs[d])[1] if routes[d] else cfgs[d].start_min
                others = []
                for od in range(days):
                    if od == d:
                        continue
                    others.append(_eval_route(routes[od], pois, T, cfgs[od])[1]
                                  if routes[od] else cfgs[od].start_min)
                avg_other = sum(others) / len(others) if others else cfgs[d].start_min
                load_pen = w.get("balance", 150.0) * max(0.0, cur_end - avg_other) / 60.0
                marginal += load_pen
                # 必去点打折，备选点加价
                marginal *= {1: 0.2, 2: 0.6, 3: 1.0, 4: 1.5}.get(pois[i].priority, 1.0)
                if best is None or marginal < best[0]:
                    best = (marginal, d, pos, trial)
        if best is not None:
            _, d, pos, trial = best
            routes[d] = trial
            placed.add(i)

    # ---------- 3. 跨日重平衡 + 未分配点补插 ----------
    routes, placed = _global_improve(pois, T, routes, cfgs, w, must_set, closed_days_map, placed, unplaceable)

    # ---------- 4. 单日路线局部优化（2-opt / LNS） ----------
    for d in range(days):
        if len(routes[d]) >= 2:
            r = _two_opt(routes[d], pois, T, cfgs[d], w, rounds=40)
            r = _lns_shuffle(r, pois, T, cfgs[d], w, iters=200, seed=100 + d)
            routes[d] = _two_opt(r, pois, T, cfgs[d], w, rounds=25)

    # ---------- 5. 汇总 ----------
    out_days = []
    total_travel = 0.0
    total_ticket = 0.0
    for d, route in enumerate(routes):
        cfg = cfgs[d]
        pen, end, detail = _eval_route(route, pois, T, cfg)
        travel = sum(T[route[i]][route[i + 1]] for i in range(len(route) - 1))
        ticket = sum(pois[i].ticket for i in route)
        total_travel += travel
        total_ticket += ticket
        # 闭馆日违规检查
        closed_conflict = [p.id for p in (pois[i] for i in route)
                           if d in closed_days_map.get(p.id, [])]
        out_days.append({
            "day": d + 1,
            "pois": [pois[i].id for i in route],
            "detail": detail,
            "travel_min": int(round(travel)),
            "end_time": _fmt(end),
            "ticket": round(ticket, 2),
            "feasible": pen < 1.0 and not closed_conflict,
            "violation_min": int(round(pen)) if pen >= 1.0 else 0,
            "closed_conflict": closed_conflict,
            "poi_count": len(route),
        })

    planned = {i for r in routes for i in r}
    dropped = [pois[i].id for i in all_idx if i not in planned]
    dropped_must = [pois[i].id for i in must_idx if i not in planned]

    return {
        "days": out_days,
        "summary": {
            "total_pois": len(planned),
            "dropped": dropped,
            "dropped_must_go": dropped_must,
            "unplaceable": [pois[i].id for i in unplaceable],
            "total_travel_min": int(round(total_travel)),
            "total_ticket": round(total_ticket, 2),
            "all_must_go_covered": len(dropped_must) == 0,
            "load_balance": [len(r) for r in routes],
            "solver": "builtin-greedy+2opt+lns",
        },
        "config": {"weights": w, "days": days},
    }


def _global_improve(pois, T, routes, cfgs, w, must_set, closed_days_map,
                    placed, unplaceable) -> Tuple[List[List[int]], set]:
    """跨日改进：补插未分配点 → 移动点 → 交换点，直到收敛。

    目标：总代价下降，且每日 POI 数更均衡。
    """
    days = len(routes)

    def day_cost(r, d):
        c, _ = _objective(r, pois, T, cfgs[d], w)
        return c

    def balance_cost(routes_list):
        """负载均衡：以"每天收工时刻"的方差衡量，而不是点数。

        真正让行程难受的是某天下午三点就没事干、另一天排到深夜。
        用点数均衡会导致"两个点的一天"和"六个点的一天"被判为均衡（如果平均是4）。
        """
        ends = []
        for d, r in enumerate(routes_list):
            if not r:
                ends.append(cfgs[d].start_min)
                continue
            _, end, _ = _eval_route(r, pois, T, cfgs[d])
            ends.append(end)
        if not ends:
            return 0.0
        mean = sum(ends) / len(ends)
        # 每偏离 1 小时的成本 = balance 权重
        return w.get("balance", 150.0) * sum(abs(e - mean) for e in ends) / 60.0

    def total_cost(routes_list=None):
        rs = routes_list if routes_list is not None else routes
        return sum(day_cost(r, d) for d, r in enumerate(rs)) + balance_cost(rs)

    for _round in range(4):
        # --- 3a. 补插未分配点 ---
        for i in list(range(len(pois))):
            if i in placed or i in unplaceable:
                continue
            if pois[i].priority > 4:
                continue
            best = None
            for d in range(days):
                if d in closed_days_map.get(pois[i].id, []):
                    continue
                if len(routes[d]) >= cfgs[d].max_pois:
                    continue
                for pos in range(len(routes[d]) + 1):
                    if not _feasible_insert(routes[d], pos, i, pois, T, cfgs[d]):
                        continue
                    trial = routes[d][:pos] + [i] + routes[d][pos:]
                    delta = day_cost(trial, d) - day_cost(routes[d], d)
                    if pois[i].priority == 1:
                        delta *= 0.2
                    if best is None or delta < best[0]:
                        best = (delta, d, pos, trial)
            if best is not None and (best[0] < w.get("insert_threshold", 900.0) or pois[i].priority <= 2):
                _, d, pos, trial = best
                routes[d] = trial
                placed.add(i)

        # --- 3b. 移动点：把过载天的点挪到宽松天 ---
        improved = False
        for d1 in range(days):
            for node in list(routes[d1]):
                if node in must_set:
                    continue
                for d2 in range(days):
                    if d1 == d2 or d2 in closed_days_map.get(pois[node].id, []):
                        continue
                    if len(routes[d2]) >= cfgs[d2].max_pois:
                        continue
                    trial1 = [x for x in routes[d1] if x != node]
                    best_ins, best_c = None, None
                    for pos in range(len(routes[d2]) + 1):
                        trial2 = routes[d2][:pos] + [node] + routes[d2][pos:]
                        if not _feasible_insert(routes[d2], pos, node, pois, T, cfgs[d2]):
                            continue
                        c = day_cost(trial2, d2)
                        if best_c is None or c < best_c:
                            best_c, best_ins = c, trial2
                    if best_ins is None:
                        continue
                    # 用含负载均衡的全局代价判断，否则永远不会往均衡方向移动
                    cand_routes = list(routes)
                    cand_routes[d1] = trial1
                    cand_routes[d2] = best_ins
                    if total_cost(cand_routes) < total_cost(routes) - 5.0:
                        routes[d1], routes[d2] = trial1, best_ins
                        improved = True
                        break
                if improved:
                    break
            if improved:
                break

        # --- 3c. 交换点：两天各出一个点互换，减少总交通 ---
        if not improved:
            for d1 in range(days):
                for d2 in range(d1 + 1, days):
                    for a in list(routes[d1]):
                        for b in list(routes[d2]):
                            if a in must_set and b in must_set:
                                continue
                            if d2 in closed_days_map.get(pois[a].id, []):
                                continue
                            if d1 in closed_days_map.get(pois[b].id, []):
                                continue
                            r1 = [x for x in routes[d1] if x != a]
                            r2 = [x for x in routes[d2] if x != b]
                            best1 = None
                            for pos in range(len(r1) + 1):
                                t1 = r1[:pos] + [b] + r1[pos:]
                                if not _feasible_insert(r1, pos, b, pois, T, cfgs[d1]):
                                    continue
                                c = day_cost(t1, d1)
                                if best1 is None or c < best1[0]:
                                    best1 = (c, t1)
                            if best1 is None:
                                continue
                            best2 = None
                            for pos in range(len(r2) + 1):
                                t2 = r2[:pos] + [a] + r2[pos:]
                                if not _feasible_insert(r2, pos, a, pois, T, cfgs[d2]):
                                    continue
                                c = day_cost(t2, d2)
                                if best2 is None or c < best2[0]:
                                    best2 = (c, t2)
                            if best2 is None:
                                continue
                            cand_routes = list(routes)
                            cand_routes[d1] = best1[1]
                            cand_routes[d2] = best2[1]
                            if total_cost(cand_routes) < total_cost(routes) - 5.0:
                                routes[d1], routes[d2] = best1[1], best2[1]
                                improved = True
                                break
                        if improved:
                            break
                    if improved:
                        break
                if improved:
                    break

        if not improved:
            break

    return routes, placed


def load_problem(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_from_json(data: Dict) -> Tuple[List[POI], List[List[float]], int,
                                         List[DayConfig], Dict[str, float]]:
    pois = [POI(**p) for p in data["pois"]]
    T = data["travel_matrix"]      # 秒 或 分钟，需统一
    unit = data.get("matrix_unit", "min")
    if unit == "sec":
        T = [[v / 60.0 for v in row] for row in T]
    days = data.get("days", 3)
    dcs = [DayConfig(**d) for d in data.get("day_configs", [{}])]
    if not dcs:
        dcs = [DayConfig()]
    while len(dcs) < days:
        dcs.append(dcs[-1])
    w = data.get("weights", {})
    return pois, T, days, dcs, w


def main():
    ap = argparse.ArgumentParser(description="行程优化求解器")
    ap.add_argument("--input", required=True, help="问题 JSON")
    ap.add_argument("--out", default="plan_result.json")
    args = ap.parse_args()

    data = load_problem(args.input)
    pois, T, days, dcs, w = build_from_json(data)
    result = plan(pois, T, days, dcs, w,
                  data.get("must_go", []), data.get("optional", []),
                  data.get("closed_days", {}))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    for d in result["days"]:
        print(f"\nDay {d['day']}  交通 {d['travel_min']}min  门票 ¥{d['ticket']}  "
              f"结束 {d['end_time']}  {'OK' if d['feasible'] else '超时' + str(d['violation_min']) + 'min'}")
        for s in d["detail"]:
            print(f"  {s['arrive']}-{s['leave']}  {s['name']:<16} 移动{s['travel_min']:>3}min  游{s['visit_min']}min")


if __name__ == "__main__":
    main()
