# 自定义信号 / 因子按账号隔离 — 方案

> 状态：待评审（未实施）。前序已完成：告警写边界、监控规则+告警按账号（1a）、批次/模拟盘（1b）、
> 策略池（1c）、策略调参+后台 owner 上下文（1d）。本文件是下一项的设计，不是已实现事实。

## 1. 现状（2026-11-26 核对）

| 环节 | 现状 |
| --- | --- |
| 存储 | `custom_signals._STORE = JsonDefinitionStore("user_data/custom_signals")`；因子 `factors/store.py` 同样固定 `user_data/custom_factors` |
| 读写 | `load_all / load_one / save_one / delete_one` 全部直接吃 `data_dir`，**没有任何 tenant 语义** |
| 生成 | 内置 20 个种子由 `seed_if_empty` 写入同一目录（带 `.seeded_v1` 标记，幂等）；用户新建走 `POST /api/signals` |
| 编译 | `indicators/pipeline.py` 启动期 `custom_signals.load_all(settings.data_dir)` → `build_expressions` → 作为 `csg_<id>`（或显式 `column`）列注入**全站共享的 enriched 表**；今日路径另有 `_custom_signal_exprs_today` |
| 消费 | 策略 `entry_signals/exit_signals/REQUIRED_FEATURES`、override 的同名字段、overlay_filter 与评分、筛选器 SQL、监控规则 `conditions`、前端 `csg_${id}` 拼列名 |
| 缓存 | 表达式缓存模块级单例；`signal_names`、`load_intraday_all` 以 `data_dir` 为键 + 目录 mtime 指纹；`api/signals.py:_invalidate()` 已负责清表达式/盘中/策略磁盘缓存 |
| 生产数据 | `custom_signals` 20 个文件全部是种子；`custom_factors` 为空；`strategies/custom` 为空 |

结论：信号/因子是**共享计算资产**——它们以列的形式长在所有人共用的一张 enriched 表上，
所以"按账号分文件"只是第一步，真正要决策的是**列命名空间与引用解析**。

## 2. 语义边界（三档，必须明确选哪档）

- **A 不可见**：列表只返回自己的 + 内置共享的。
- **B 不可写**：不能覆盖、不能删除别人的定义。
- **C 不可引用**：别人的策略/信号定义不能通过猜 id 算到别人的列。

用户既定原则是"自己的数据自己看，写操作不影响别人"——对应 A + B。C 属于更强的保密要求，
成本高一档（见 §3）。

## 3. 方案对比

| | 方案 1：账号存储 + 全局唯一 id + 并集注入（推荐） | 方案 2：id 前缀 `u<uid>_` + 并集注入 | 方案 3：每账号独立 enriched 表 |
| --- | --- | --- | --- |
| A 不可见 | ✅ | ✅ | ✅ |
| B 不可写 | ✅（保存时全局唯一性校验，冲突 409） | ✅（id 天然不撞） | ✅ |
| C 不可引用 | ❌（猜 id + 手写 `csg_x` 仍能算） | ✅（再叠加"前缀归属校验"可 fail-closed） | ✅ |
| 前端改动 | 几乎 0（列名规则不变） | 中（"信号标识"输入框要展示固定前缀；列表回显真实 id） | 0 |
| 迁移成本 | 0（生产无用户自建） | 需改写既有引用（策略代码、override、规则 conditions） | 高 |
| 计算代价 | 一份 enriched 表，列 = 全账号并集 | 同左 | N 份表，磁盘/重算 N 倍 |
| 用户可理解性 | 高（id 短、和现在一致） | 中（标识变长，但归属一目了然） | 低 |

**方案 3 直接否决**：enriched 历史缓存与回测矩阵缓存是全站共享的大文件（`data/enriched`、`data/.backtest_matrix_cache`），
按账号分片意味着磁盘与重算翻 N 倍，且换账号看同一只票指标口径不同，排查成本极高。

**推荐方案 1**：满足 A + B（用户的原始原则），改动集中在存储与校验两处，前端零改，生产零迁移。
如果之后需要 C（比如账号多、要防"猜 id 看到别人的信号语义"），再上方案 2 的前缀，
它可以在方案 1 之上增量实施，不需要推翻。

## 4. 方案 1 实施清单

### 4.1 存储层（`strategy/custom_signals.py`、`factors/store.py`）

1. 拆两个目录：
   - 共享：`data_dir/user_data/custom_signals`、`user_data/custom_factors`（只放内置种子与"无法定归属的历史文件"）
   - 账号：`data_dir/user_data/users/<uid>/custom_signals|custom_factors`
2. 新增 `iter_owned(data_dir)` → `[(None, shared_def), (uid, def), ...]`（与 `monitor_rules.iter_owned` 同构）。
3. `load_all` 改为**并集**（共享在前、账号在后，各自按文件名排序）；`load_one` 先查自己再查共享；
   `save_one` 写当前账号（无租户时仍写共享）；`delete_one` 只删自己那一份。
4. `seed_if_empty` 固定写共享目录，**不按账号补种**——否则每个账号会各存 20 个同 id 定义，
   并集时列名冲突。
5. `signal_names` / `load_intraday_all` / `fingerprint` 的缓存键改为"并集指纹"：
   `(共享目录指纹, 各账号目录指纹, 账号列表)`；任一账号增删都会让所有进程重载。

### 4.2 API 层（`api/signals.py`、`api/factors.py`）

1. 列表返回自己在先、共享在后，每条带 `owner`（`null` = 内置/共享）与 `builtin` 标记，前端据此分组与禁用编辑。
2. 保存时做**全局唯一性**：若 id 已被他人持有 → `409`，文案"该标识已被其他账号使用，请换一个"。
   同一账号内修改自己的同 id 定义仍然允许。
3. 已有的"显式 column 冲突闸"继续扫描并集（列名仍全局唯一）。
4. 删除只作用于自己的定义；共享/内置定义在前端禁用删除按钮，后端也拒绝（403/400）。

### 4.3 编译与消费

`load_all` 聚合后，`build_expressions`、`prev_day_fields`、`build_expressions_prev`、
`allowed_fields`、`definition_dependencies` 全部自动包含两侧——**这是方案 1 的关键优势：
编译层不需要任何改动**。

需要留意的只有两处收口：
- `_get_custom_signal_exprs` / `_get_custom_signal_exprs_today` 的模块级缓存是单例，
  写任一账号后必须 `invalidate_custom_signals()`（现有入口已做到）。
- 因子注册表是进程全局的，`register_definition` 按 id 注册：并集加载时 id 不会撞（唯一性校验保证），
  但**同一 id 出现两个来源**时要以"账号优先、共享兜底"的顺序加载并告警，避免静默覆盖。

### 4.4 失效与重算（必须向用户讲清）

信号增删会改变全站 enriched 表的列集合 → 一次改动让**所有账号**的指标缓存重建。
这是共享计算表的固有代价，不是 bug。要在 UI 上给一句提示（"修改自定义信号会触发全站指标重建"），
并把重建进度沿用现有 enriched refresh 的状态。

### 4.5 备份 / 资源包

- `config_backup`：`users/<uid>/custom_signals|custom_factors` 加入分类与收集；租户导出不带全局旧文件；
  旧备份条目（`user_data/custom_signals/x.json`）在租户模式映射回当前账号（与 1c/1d 同样处理）。
- `resource_bundle`：导出闭包里读 `load_all` 已天然聚合；但"我的信号"打包要过滤掉别人的定义
  （按 owner 判定），否则分享包会带出他人内容。

### 4.6 前端

- 信号/因子列表分"我的"与"内置"两组；内置条目只读（编辑、归档、删除按钮禁用）。
- 新建/编辑只针对自己的定义，`csg_${id}` 拼列名逻辑不变（`SignalPicker`、`SignalTriggerActions`、
  `useCustomSignalNames` 无需改动）。
- 删改信号后的提示补一句全站重建。

## 5. id 冲突与命名规则

- 全局唯一、先到先得；冲突在保存时 409，不静默覆盖。
- 建议（可选，不改后端也成立的约定）：账号内新建用业务语义前缀（`low_touches_ma5` 这类），
  避免 A 和 B 都想叫 `ma20_break` 时互相抢名。这个约定写进表单提示即可，不强制。
- 保留 `column` 显式字段：内置种子继续用 `signal_*` 原名，列名与历史数据零漂移。

## 6. 种子与历史数据

- 种子只进共享目录（`seed_if_empty` 固定路径），所有账号可见、只读。
- 旧全局非种子文件：**无法确定 owner**。处理方式是留在共享目录、全员可见只读，并在文档与日志里标注；
  生产目前为 0 个，实际不触发。
- 不做"搬给第一个管理员"的猜测式迁移——与前面几项的口径一致。

## 7. 测试矩阵

后端新增 `tests/test_custom_signals_tenant.py`、`tests/test_custom_factors_tenant.py`：

1. 双账号各建同结构不同 id 信号 → 彼此列表不可见、`load_all` 并集含两条。
2. B 用 A 的 id 保存 → 409；A 自己的同 id 修改 → 通过。
3. B 删除 A 的 id → 不生效（自己的删除返回 False / 404）。
4. 种子只出现在共享段，账号目录无种子文件。
5. `signal_names` / 指纹跨账号：A 新增后 B 进程侧缓存失效。
6. 编译层：并集后 `build_expressions` 同时含两侧列；策略 REQUIRED_FEATURES 引用自己账号的列可跑。
7. 备份收集与资源包过滤（不含他人定义）。
8. 因子同构 6 条（注册表并集、id 冲突、worker 子进程可见）。

回归要求：现有信号/因子测试全部在**全局模式**（无租户）下运行，必须保持全绿；
新增租户测试不得依赖真实 enriched 数据，用 `build_expressions` 级别的纯函数断言。

## 8. 验收（上线后手工）

1. A 建信号 `a_only` → B 的信号列表里没有；A 的策略引用 `csg_a_only` 可回测出信号。
2. B 用 `a_only` 保存 → 409 提示被占用。
3. A 改自己信号条件 → 全站出现"指标重建"，重建完成后两账号都能看到 `csg_a_only` 列，数值一致。
4. A 归档自己的信号 → B 列表/筛选器不受影响；A 恢复后立即可用。

## 9. 明确排除在本方案外

- **策略文件本体归属**（`data/strategies/{custom,ai,composite}`）：跨账号聚合 + 策略 id 命名空间，
  且被规则/模拟盘/策略池引用，是独立一轮。
- **通知通道 / 偏好个人段**：与计算表无关，单独排期。
- **每账号独立 enriched 表**：已论证不经济，不做。
