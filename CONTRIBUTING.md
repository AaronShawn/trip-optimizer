# 贡献指南

## 开发环境

只需要 Python 3.9+，**不需要安装任何第三方包**。

```bash
git clone https://github.com/<your-name>/trip-optimizer.git
cd trip-optimizer
python tests/test_offline.py
```

## 提交前必须跑

```bash
# 1. 全部离线测试（48 项断言，不联网）
python tests/test_offline.py     # 求解器 / 坐标系 / 用餐 / 目标函数
python tests/test_render.py      # HTML 渲染 / Leaflet 内联
python tests/check_no_deps.py    # 零第三方依赖

# 2. 语法检查
python -m compileall -q scripts tests

# 3. 端到端冒烟（需要联网，会调用 Valhalla/Photon）
python scripts/pipeline.py --spec examples/chengdu_2days.json --out /tmp/smoke.json
python scripts/render.py --plan /tmp/smoke.json --out /tmp/smoke.html
```

前三步 CI 会自动跑（Linux/macOS/Windows × Python 3.9/3.11/3.13）。第三步涉及外部服务，CI 不跑。

## 硬性约束

1. **零第三方依赖。** `scripts/` 下只允许 import 标准库和本项目模块。CI 会做 AST 检查，引入 `requests`/`numpy` 之类会被拒。
2. **离线测试必须保持离线。** `tests/test_offline.py` 里不许发网络请求。要测联网逻辑，另建 `tests/test_online.py` 并在 CI 中标记为不阻塞。
3. **改算法必须补测试。** 涉及目标函数、约束、求解流程的改动，都要在离线测试里加断言。

## 修改算法的注意事项

几个已经踩过、写进测试的坑，改动时别踩回去：

| 约束 | 测试名 | 说明 |
|---|---|---|
| 负载均衡用"收工时刻" | `两天各 2 个点（均衡）` | 用点数均衡会排出「Day1 只 2 个点 14:58 收工、Day3 塞 6 个点」 |
| 跨日改进用全局总代价 | 间接覆盖 | 用局部两天代价时均衡项不参与，永远不会为均衡而移动 |
| 凑数惩罚方向 | `低分低优先点代价更高` | `(5 - priority)` 是反的，会让必去点被罚最重 |
| 用餐只在窗口内插 | `14:00 之后不插午餐` | 否则会排出 13:17 的荒谬"午餐" |
| 景点跨餐窗先吃再逛 | `明细时间单调递增` | 否则时刻会倒流（11:30 午餐后出现 11:15 的景点）|
| 闭馆日是硬约束 | `闭馆点不在闭馆日` | 用惩罚项处理会漏 |

## 提交信息格式

```
<type>: <简短描述>

<可选：为什么这么改，特别是有取舍时>
```

`type` 用：`feat` / `fix` / `perf` / `docs` / `test` / `refactor` / `chore`。

例：
```
fix: 修正凑数惩罚方向，必去点不再被主动丢弃

原来写 (5 - priority)，priority=1 的必去点被罚 4 倍、priority=4 的
备选点只罚 1 倍，方向反了。改为 max(0, priority - 1)，必去点零惩罚。
```

## 新增数据源

新数据源请加在 `scripts/sources.py`，并遵守：

1. **默认绕过系统代理**（用 `_OPENER`，即 `ProxyHandler({})`）。沙箱环境里的注入代理会让地图类域名返回 502。
2. **带重试**（`_retry`），公共实例不稳定。
3. **在文件顶部的可用性表格里注明实测状态和日期**，包括失败的。这个表是后续维护者的重要参考。
4. **坐标系要明确**。如果源是 GCJ-02 或 BD-09，转成 WGS-84 后再进入矩阵计算，渲染时再转回去。

## 关于公开服务的礼貌使用

默认配置用的是免费公共实例（Valhalla、OSRM、Photon）。这些服务由第三方无偿提供：

- 矩阵有 7 天本地缓存，避免重复请求
- 单次矩阵请求会自动按 10×10 分块（Valhalla 上限 100 点对）
- 请勿用于大批量爬取。生产环境请自建或使用商业服务

## 许可

贡献即表示同意以 MIT 协议发布。
