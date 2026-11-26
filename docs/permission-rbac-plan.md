# 账号分级与权限管理方案（v1）

> 状态：已实现（v1）。权限模型、前端可见性、后台「用户与权限」页与业务接口页面权限校验均已落地，本文档同时作为验收与维护说明。
> 遵循 
>
> [CONTRIBUTING.md](../CONTRIBUTING.md)
>
>  的兼容性、最小改动与测试矩阵要求。

## 1. 目标与非目标

**目标**



* 在现有邮箱账号体系上增加「权限组」这一层：超级管理员 / 普通管理员 / 编辑者三级，每级的权限点可在后台勾选调整，用户挂到某个权限组。

* 管理后台新增「用户与权限」页：用户列表（改权限组、启用 / 禁用）、权限组矩阵（勾选保存）、权限变更审计。

* 页面级可见性：无权限的页面不出现在导航、直接访问路由给出「无权限」态，而不是靠隐藏按钮。

**非目标（v1 不做）**



* 不做自定义权限组（只开放三个内置组改名 / 改权限；表结构预留 `is_builtin=0`）。

* 不做数据行级隔离（"编辑者只能看自己的策略"）。现有租户目录 `tenant_service` 已按用户隔离私有数据，权限组不改变它。

* 不做 API Key / 开放接口通道的权限细分。`api_gateway` 的 Token 通道保持现状（管理员签发、管理员作用域）。

* 不引入第三方 RBAC 库或权限中间件框架。

## 2. 现状盘点（代码事实）



| 位置                                   | 现状                                                                                                                                   |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| `backend/app/services/user_store.py` | SQLite `users.db`, `users` 表有 `role`(admin/pro/free)、`is_active`、配额列。`effective_role()` 同时承担「订阅等级」与「是否管理员」两种语义，`ROLE_ADMIN` 还决定配额不限。 |
| `backend/app/api/accounts.py`        | 只有注册 / 登录 / 登出 /me/ 改密 / 邮箱验证，**没有任何用户管理接口**（无列表、无改角色）。                                                                              |
| `backend/app/api/permissions.py`     | 只有 `is_admin()` 与三个守卫: `guard_settings_access` / `guard_ext_pages_access` / `guard_ext_data_access`。判断依据是 `role == 'admin'`。         |
| `backend/app/main.py:697-719`        | 认证中间件：邮箱账号会话命中后 `request.state.user = current_user` 并注入 tenant 上下文；旧单密码模式已下线。                                                      |
| `backend/app/main.py:890-915`        | `/admin` HTML 也做管理员判断，非管理员 307 回 `/`。                                                                                                |
| `frontend/src/pages/AdminApp.tsx`    | 管理后台只有三块：系统配置 / 数据管理 / 提示词管理。                                                                                                        |
| `frontend/src/lib/navigation.ts`     | `CORE_NAVIGATION` 9 个页面（看板 / 自选 / 策略 / 因子 / 三年五倍 / 个股分析 / 回测 / 市场环境 / 指数）, 无权限字段。                                                    |
| `frontend/src/lib/useAccount.ts`     | `useIsAdmin()` 直接看 `role === 'admin'`; 旧密码模式视为管理员。                                                                                   |
| `frontend/src/router.tsx`            | 页面路由无权限守卫；`/admin` 只有登录态守卫。                                                                                                          |

结论：现在只有「管理员 / 非管理员」一条硬边界，且这条边界与订阅等级共用同一个 `role` 字段。要加三级权限，必须先把这个字段拆成两个概念。

## 3. 核心决策



1. **拆分两个字段，一个不改语义**

* `users.role` 保留原义：账号等级（free /pro/admin）, 继续驱动配额（`QUOTA_DEFAULTS`）与订阅到期降级。**不为权限改造动它。**

* 新增 `users.perm_role`: 权限组 key（`super_admin` / `admin` / `editor`）。

* 对外字段名分开暴露: `role` = 账号等级，`perm_role` = 权限组，`permissions` = 展开后的权限码数组。

* 好处：配额、订阅、计费零回归；权限组之后可自由增删而不影响计费。

1. **权限码是后端集中注册表，前端只渲染**

   权限目录、分组、默认矩阵、路由映射全部在 `backend/app/services/rbac.py` 一处声明，通过接口下发给前端。前端不硬编码权限清单（与现有「能力矩阵不得在前端硬编码」同一原则）。

2. **三层边界，后端全部强制**

* **管理强边界（后端强制）**: 管理后台、系统配置、数据管理、提示词管理、用户与权限。这些接口在 FastAPI 依赖层校验，未登记的一律 403（fail-closed）。

* **页面接口边界（后端强制，anyOf）**: 每个业务路由按「哪些页面会读它」登记页面权限（`services/page_access.py`），持有其中任一页面权限即可访问；一个都没有 → 403。共享行情/策略/数据状态接口列出全部合法消费页面，避免把共用接口锁死；页面专属数据（筛选/因子/回测/自选/成长等）只列真实消费页面。

* **页面可见性（前端 +&#x20;**`/me`**&#x20;下发权限）**: 无权限页面不进菜单、直访渲染「无权限」页；接口层的同一份权限码负责兜底，二者不会再出现「菜单藏了但接口可调」。

* 剩余边界: 扩展包/自定义后端路由需在各自注册处挂同样的页面依赖（`/api/graph`、`/api/info`、`/api/custom/paper-execution` 已挂 `page.analysis`；全局 AI 助手不挂）。页面权限不是数据隔离 — 同一页面内的用户仍共享同一份数据目录，需要按用户隔离数据时走二期 scope。

## 4. 权限模型

### 4.1 权限码目录（v1 全量）

分三组，权限码格式 `域.对象.动作`, 一律小写。



| 分组 | 权限码                     | 名称             | 说明                        |
| -- | ----------------------- | -------------- | ------------------------- |
| 页面 | `page.dashboard`        | 看板             | `/`、`/overview`           |
| 页面 | `page.watchlist`        | 自选             | `/watchlist`              |
| 页面 | `page.screener`         | 策略             | `/screener`               |
| 页面 | `page.factors`          | 因子             | `/factors`（含挖掘）           |
| 页面 | `page.growth`           | 三年五倍           | `/growth`                 |
| 页面 | `page.stock_analysis`   | 个股分析           | `/stock-analysis`         |
| 页面 | `page.backtest`         | 回测             | `/backtest`               |
| 页面 | `page.regime`           | 市场环境           | `/regime`                 |
| 页面 | `page.indices`          | 指数             | `/indices`                |
| 页面 | `page.analysis`         | 分析中心 / 扩展页     | `/analysis/*`、扩展页         |
| 页面 | `page.dev`              | 开发调试           | `/dev`                    |
| 功能 | `feature.strategy.edit` | 新建 / 修改 / 删除策略 | 写操作；不含只读浏览                |
| 功能 | `feature.backtest.run`  | 运行回测 / 优化      | 消耗计算与配额                   |
| 功能 | `feature.ai.use`        | AI 助手 / AI 分析  | 受 `ai_daily` 配额约束         |
| 功能 | `feature.data.export`   | 导出数据 / 配置      | CSV、备份导出                  |
| 管理 | `admin.access`          | 进入管理后台         | `/admin` HTML 与前端路由       |
| 管理 | `admin.settings.write`  | 修改系统配置         | 数据源 / AI / 通知 / 备份 / 开放接口 |
| 管理 | `admin.data.manage`     | 数据管理           | 同步、回填、清理、扩展数据配置           |
| 管理 | `admin.prompts.manage`  | 提示词管理          |                           |
| 管理 | `admin.pages.manage`    | 页面插件管理         |                           |
| 管理 | `admin.users_manage`    | 用户账号管理         | 改权限组、启禁用                  |
| 管理 | `admin.roles_manage`    | 配置权限组          | 勾选权限矩阵                    |
| 管理 | `admin.audit_view`      | 审计日志           | 只读                        |

约定: `page.*` 同时影响可见性、路由与业务接口（路由级 anyOf）；`admin.*`、`feature.*` 由后端强制。

### 4.2 三个内置权限组与默认矩阵



| 权限                     | 超级管理员 `super_admin` | 普通管理员 `admin` | 编辑者 `editor` |
| ---------------------- | ------------------- | ------------- | ------------ |
| `page.*`（全部）           | ✓                   | ✓             | ✓            |
| `page.dev`             | ✓                   | ✓             | ✗            |
| `feature.*`（全部）        | ✓                   | ✓             | ✓            |
| `admin.access`         | ✓                   | ✓             | ✗            |
| `admin.settings.write` | ✓                   | ✓             | ✗            |
| `admin.data.manage`    | ✓                   | ✓             | ✗            |
| `admin.prompts.manage` | ✓                   | ✓             | ✗            |
| `admin.pages.manage`   | ✓                   | ✓             | ✗            |
| `admin.users_manage`   | ✓                   | ✗             | ✗            |
| `admin.roles_manage`   | ✓                   | ✗             | ✗            |
| `admin.audit_view`     | ✓                   | ✓             | ✗            |

说明：默认值只是初值，超级管理员可在矩阵页调整（含给普通管理员开 `admin.users_manage`）。但**只有超级管理员能改权限组与分配权限组**这一条是代码硬约束，不允许通过矩阵把自己提权。

## 5. 数据模型与迁移

### 5.1 表结构（`data/user_data/users.db`）



```
-- 权限组（三个内置 + 预留自定义）
CREATE TABLE IF NOT EXISTS role_groups (
  key          TEXT PRIMARY KEY,      -- super_admin / admin / editor
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  permissions  TEXT NOT NULL,         -- JSON 数组, 与 rbac.PERMISSIONS 对齐
  is_builtin   INTEGER NOT NULL DEFAULT 1,
  updated_at   REAL NOT NULL,
  updated_by   INTEGER
);

-- 用户挂组
ALTER TABLE users ADD COLUMN perm_role TEXT;   -- NULL 表示历史数据, 读取时按 4.3 规则回填
ALTER TABLE users ADD COLUMN last_login_at REAL;

-- 审计
CREATE TABLE IF NOT EXISTS permission_audit (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id   INTEGER, actor_email TEXT,
  action     TEXT NOT NULL,          -- user.set_perm_role / user.toggle_active / group.update
  target     TEXT NOT NULL,          -- 被改对象（用户 id 或组 key）
  detail     TEXT NOT NULL DEFAULT '', -- JSON: before/after
  created_at REAL NOT NULL
);
```

`permissions` 存 JSON 而不是关联表：权限组数量是个位数，读路径是「登录后一次查询」, 用关联表只增加 join 与迁移成本。写入时用 `rbac.validate_permissions()` 过滤未注册的 key, 防止脏数据。

### 5.2 启动迁移（`_migrate_schema`）



1. 建表 + 按 `rbac.DEFAULT_GROUPS` 幂等播种（存在则只补缺失字段，不覆盖超管改过的矩阵）。

2. `users.perm_role` 回填: `role='admin'` → `super_admin`; 其余 → `editor`。只回填 `perm_role IS NULL` 的行，重复启动无副作用。

3. 迁移完成后 `perm_role` 不再为 NULL（新建用户显式赋 `editor`）。

4. 无账号会话（API Token 通道/内部调用）在权限判定里等价于 `super_admin`，由 Token scope/调用方负责边界。

### 5.3 兼容性



* 不改 cookie、session 表、注册 / 登录 / 改密流程。

* 不改 `effective_role()`、`QUOTA_DEFAULTS`、`grant_pro()`。

* `/api/accounts/me` 只加字段（`perm_role`、`perm_role_name`、`permissions`), 不删不改现有字段；前端按「缺字段则回退 `is_admin`」处理旧缓存。

* 现有 `is_admin` 字段保留一个版本用于兼容，语义改为「拥有 `admin.access`」, 在 PR 里标注后续移除。

* 认证只保留邮箱账号会话：`AUTH_PASSWORD` 环境变量与 `/api/auth/*`（`auth.json` 单密码）已下线，旧 `tf_session` 不再被中间件识别；升级后用户改用邮箱账号登录，未初始化部署在本机/内网或通过 `ADMIN_EMAIL`+`ADMIN_PASSWORD` 创建首个管理员。

## 6. 后端接口与守卫

### 6.1 新增模块

`backend/app/services/rbac.py`（唯一事实源）



```
PERMISSIONS: tuple[Permission, ...]      # key/label/group/description
DEFAULT_GROUPS: dict[str, GroupDefault]  # 三个内置组 + 默认矩阵
ROUTE_PERMISSIONS: tuple[RouteRule, ...] # (method 集合, 路径前缀) -> 权限码

def validate_permissions(codes: Iterable[str]) -> list[str]
def list_groups() -> list[dict]
def update_group(key, *, name, description, permissions, actor) -> dict
def permissions_for(perm_role: str) -> frozenset[str]   # 未知组 -> 空集（fail-closed）
def resolve_user_permissions(user) -> frozenset[str]    # 无账号会话 -> 全量
```

`backend/app/api/permissions.py` 改造（保持文件名与调用方，避免大面积改动的入口）



```
def has_permission(request, key) -> bool
def require_permission(key): ...        # FastAPI 依赖工厂
def guard_settings_access(request)      # 改为按 ROUTE_PERMISSIONS 判定, 逻辑不变
def guard_ext_pages_access / guard_ext_data_access   # 同上, 映射到 admin.pages.manage / admin.data.manage
```

关键点：现有三个守卫的**行为契约不变**（管理员全通、GET 白名单、个人偏好白名单）, 只把「是不是 admin」换成「有没有对应权限码」。这样 `settings.py`、`ext_*` 的调用点无需改动。

### 6.2 新增管理接口（`backend/app/api/admin_users.py`, 前缀 `/api/admin`）



| 方法    | 路径                              | 权限                   | 说明                                                                      |
| ----- | ------------------------------- | -------------------- | ----------------------------------------------------------------------- |
| GET   | `/api/admin/permission-catalog` | `admin.access`       | 权限目录 + 权限组（管理后台渲染矩阵）                                                    |
| GET   | `/api/admin/users`              | `admin.users_manage` | 列表: id/email/ 账号等级 / 权限组 / 是否启用 / 创建时间 / 最后登录；支持 `q` 邮箱模糊、分页            |
| PATCH | `/api/admin/users/{id}`         | `admin.users_manage` | 改 `perm_role` / `is_active`；禁止改自己的组与状态；禁止移除最后一个超级管理员                    |
| GET   | `/api/admin/role-groups`        | `admin.access`       | 三个组 + 权限矩阵                                                              |
| PUT   | `/api/admin/role-groups/{key}`  | `admin.roles_manage` | 改 name/description/permissions；`super_admin` 组的 `admin.*` 权限不可被剥夺（防止自锁） |
| GET   | `/api/admin/permission-audit`   | `admin.audit_view`   | 最近 N 条变更                                                                |

约束（fail-closed）



* 路由级依赖是硬校验；同时加一个启动期测试：遍历 `/api/admin` 下全部路由，断言每条都有权限依赖，漏挂即测试失败。

* `UPDATE role_groups` 前校验 JSON 里每个 key 都在目录内；不在目录内的整批拒绝（400), 不静默丢弃。

* 改权限组后递增版本号 `updated_at`, `/me` 每次实时查库（SQLite 单行读，不引入缓存，避免失效 bug）。

### 6.3 业务接口页面权限（`services/page_access.py`）

路由级依赖: `require_page_access(*pages)`（`api/permissions.py`），在 `main.py` 的
`include_router(..., dependencies=_page_deps("..."))` 处统一挂载。

| 路由 | 允许的页面权限（持有任一即可） |
| --- | --- |
| `kline` / `intraday` / `pipeline` | 全部行情页面 + `admin.data_manage` |
| `screener` | `page.screener` |
| `factors` | `page.factors` / `page.backtest` |
| `backtest` / `mining` / `guardian` / `lab` / `position_policies` | `page.backtest` |
| `watchlist` | `page.watchlist` / `page.screener` / `page.backtest` / `page.stock_analysis` / `page.dev` |
| `strategy` / `strategy_pool` / `signals` | `page.screener` / `page.backtest`（`strategy` 另含 `page.stock_analysis`） |
| `regime` | `page.regime` / `page.backtest` / `admin.data_manage` |
| `indices` | `page.indices` / `page.dashboard` / `page.analysis` / `admin.data_manage` |
| `stock_analysis` | `page.stock_analysis` |
| `stock_growth` | `page.growth` |
| `financials` | `page.stock_analysis` / `page.growth` / `page.analysis` |
| `data` | `page.dashboard` / `page.screener` / `page.backtest` / `admin.data_manage` |
| `ext_data` | `page.analysis` / `page.watchlist` / `page.screener` / `admin.data_manage` |
| `paper` / `lots` / `market_recap` | `page.analysis`（`market_recap` 另含 `page.dashboard`） |

不挂页面依赖（有意）: 认证/账号/管理后台/`/api/settings`/页面插件管理/`alerts`/`events`/
`task_registry`/`analysis-menus`（导航本身需要）/全局 AI 助手。无账号会话
（API Token/内部调用）由 `has_permission` 视为全权限。

新增业务路由必须在此表登记，否则 `tests/test_page_access.py` 的映射校验不会发现，
但接口也不会被页面权限保护 — code review 需检查 `main.py` 是否带上 `_page_deps`。

破坏性/诊断数据接口单独叠加 `admin.data_manage`（`app/api/data.py` 路由级依赖）：
`GET /api/data/integrity`、`POST /api/data/integrity/scan`、`POST /api/data/integrity/repair`、
`POST /api/data/clear`。页面权限只代表「能看该页数据」，不能清库或修补全库共享数据。

### 6.4 `/api/accounts/me` 扩展



```
{
  "user": {
    "id": 3, "email": "...", "role": "free",
    "perm_role": "editor", "perm_role_name": "编辑者",
    "permissions": ["page.dashboard", "page.watchlist", "feature.ai.use", "..."],
    "is_admin": false
  }
}
```

### 6.5 `/admin` HTML 闸门

`main.py:_admin_session_state` 改为返回 `(authenticated, can_admin)`, 判定条件为 `admin.access`。非管理员渲染无权限态；未登录一律先去 `/login`。

## 7. 前端改造



| 文件                                                | 改动                                                                                                                         |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `frontend/src/lib/api.ts`                         | `AccountUser` 增加 `perm_role` / `perm_role_name` / `permissions`; 新增 5 个管理接口封装                                              |
| `frontend/src/lib/useAccount.ts`                  | 新增 `usePermissions()`: 返回 `{ codes, can(key), canAny(keys), loading, isSuperAdmin, canAdmin }`; 后端未下发 `permissions` 时退化为 `role === admin`（兼容旧缓存） |
| `frontend/src/lib/navigation.ts`                  | `NavigationEntry` 增加 `perm?: PermissionKey`; 页面条目映射到 `page.*`（`/analysis/*` 与扩展页映射 `page.analysis`）                        |
| `frontend/src/components/Layout.tsx`              | 先按权限裁剪条目，再套 `nav_layout` 偏好；入口全部被过滤时侧栏显示「无可用页面，请联系管理员」，不自动跳转                                                       |
| `frontend/src/router.tsx`                         | 新增 `<PermissionGate perm \| anyOf>` 包装页面路由：登录态由外层守卫负责，无权限渲染独立「无权限」页（不静默重定向）；`AdminGuard` 对无 `admin.access` 的账号同样给无权限态 |
| `frontend/src/components/shell/GlobalSearch.tsx`  | 该入口唯一动作是跳个股分析；无 `page.stock_analysis` 时整个入口不渲染                                                                                    |
| `frontend/src/lib/navLayout.ts`                   | 权限过滤发生在 `resolveNavLayout()` **之前**：先裁剪条目，再套用户排序/分组/隐藏偏好，否则本地存的导航布局会把已隐藏的页面重新带出来                                  |
| `frontend/src/pages/AdminApp.tsx`                 | 新增「用户与权限」section（`/admin/permissions`），`admin.users_manage` 或 `admin.roles_manage` 才显示；`admin.users_manage` 才渲染用户管理 tab, `admin.roles_manage` 才允许保存矩阵 |
| `frontend/src/pages/admin/PermissionAdmin.tsx`（新） | 三个 tab：用户账号（搜索/分页 + 权限组下拉 + 启禁用）、权限组矩阵（三组勾选 + 未保存提示 + 保存）、变更记录（审计）；无对应权限的 tab 不渲染 |
| `frontend/src/pages/Settings.tsx` 等               | 写操作按钮在无 `admin.settings.write` 时禁用并给 tooltip; 不整页隐藏（避免误以为功能不存在）                                                            |

交互状态（CONTRIBUTING §7 要求）: 每个新界面必须覆盖加载 / 空列表 / 请求失败 / 无权限 / 保存中 / 保存成功回显。用户列表为空时说「还没有其他账号」并给注册入口提示，不是空白表格。

### 7.1 菜单可见性规则（硬要求）

「没有权限的页面 = 菜单里根本不存在」。不是灰掉、不是点了才报错、不是弹个提示。

1. **入口全链路一致**：侧栏（含 rail 图标模式）、移动端抽屉、全局搜索、管理后台分区导航、扩展页菜单，五处按同一份 `permissions` 过滤，不允许某一处漏掉。
2. **用户偏好不能复活被隐藏的页面**：`nav_layout` / `nav_order` / `nav_hidden` 是排序偏好，只在已授权条目集合内生效；授权变化后重新裁剪，而不是把旧布局原样应用。
3. **直访 URL**：`PermissionGate` 在路由层兜底，渲染独立的「无权限」页并给返回入口；不做静默重定向到首页——静默跳转会让人以为页面不存在，排查成本更高。
4. **深链与子路由**：`/analysis/:menuId` 继承 `page.analysis`（扩展页同），`/factors?tab=mining` 继承 `page.factors`，`/dev` 走独立 `page.dev`，不在每个子组件里各写一份。
5. **权限变化即时生效**：保存矩阵或切换账号后 `invalidateQueries(['account-me'])`，导航在同一会话内立即重算，不要求刷新页面或重新登录。
6. **回退页**：若账号所有页面权限都被关掉（只留管理权限或什么都没有），登录后进「无可用页面」提示页并给联系管理员文案，不能白屏或死循环重定向。

## 8. 安全要点



1. **服务端才是边界**。前端隐藏导航只是体验；所有 `admin.*`、`feature.*` 判定必须在 FastAPI 依赖里做一次。

2. **不可自提权**: 只有 `super_admin` 能改 `role_groups.super_admin` 与分配 `super_admin`; 普通管理员即使被授予 `admin.roles_manage`, 也不能把任何组置为超管或把自己的组换成超管。

3. **最后超管保护**: 不允许把最后一个 `super_admin` 降级 / 禁用；返回明确 409 文案。

4. **fail-closed**: 未知权限组 → 空权限集；未登记管理路由 → 403; 权限码非法 → 整体拒绝。

5. **审计留痕**: 改组、改人、启禁用都写 `permission_audit`, 记录 actor 与 before/after; 不含密码与 token。

6. **越权错误信息不泄露结构**: 403 统一文案「无权限执行该操作」, 不回传所需权限码以外的内部信息（审计日志接口除外，仅管理员可见）。

7. **会话不失效**: 改权限组后用户的现有 session 继续有效，但下一次请求即按新权限判定（实时查库）; 前端 `account-me` 查询需在矩阵保存后 `invalidateQueries`。

## 9. 实施步骤（建议按 PR 拆三个）

**PR-1 后端模型与强制边界（可独立合并）**



1. 新建 `services/rbac.py`（目录 + 默认矩阵 + 路由映射 + 校验函数）。

2. `user_store.py`: 建 `role_groups` / 补 `users.perm_role` / 迁移回填 / `update_user` 支持 `perm_role`; 新增 `list_users_paged`、`set_perm_role`、`count_super_admins`、审计写入。

3. `api/permissions.py` 改为权限码判定，保持三个守卫签名不变；`main.py` 的 `/admin` 闸门改用 `admin.access`。

4. 新增 `api/admin_users.py` 并注册；`/api/accounts/me` 下发权限。

5. 测试：见 §10。

验证: `backend/.venv/bin/python -m pytest tests/test_rbac.py tests/test_admin_guard.py tests/test_accounts.py -q`; `backend/.venv/bin/python -m ruff check app/services/rbac.py app/api/admin_users.py app/api/permissions.py`; 手工验证旧密码模式与 admin 账号行为不变。

**PR-2 前端权限可见性**



1. `api.ts` / `useAccount.ts` / `navigation.ts` / `Layout.tsx` / `router.tsx` 改造。

2. 无权限页组件 + 导航过滤 + 首屏回退逻辑。

3. 测试: `npm run build`; 手工核对 9 个页面 × 3 个权限组共 27 组可见性；窄屏导航折叠状态。

**PR-3 管理后台「用户与权限」**



1. `PermissionAdmin.tsx` + `AdminApp` section + `api.ts` 管理接口。

2. 列表空态、保存态、错误态、404/403 分支。

3. 测试: `npm run build` + 后端管理接口联调；截图（含深 / 浅色与窄屏）。

## 10. 验证矩阵（最低要求）



| 类型 | 用例                                                                                        |
| -- | ----------------------------------------------------------------------------------------- |
| 迁移 | 空库建表播种三个组；老库（有 admin/free/pro 用户）回填正确；重复启动幂等；`permissions` JSON 损坏时 fail-closed 而非 500    |
| 模型 | `resolve_user_permissions` 对三个组、未知组、禁用用户、旧密码模式的返回                                         |
| 守卫 | 每个权限码至少一条 allow + 一条 deny; `/api/admin` 全路由挂载断言；`settings` 个人偏好白名单仍放行                     |
| 越权 | 普通管理员改超管组 → 403; 超管自降 → 409; 移除最后超管 → 409; 非法权限码 → 400                                    |
| 接口 | `GET /api/admin/users` 成功 / 空 / 分页；`PATCH` 成功 / 404/403; `PUT role-groups` 成功 / 非法码 / 无权限 |
| 兼容 | 旧密码模式登录后 `/admin`、`/api/settings` 写操作、`/api/accounts/me` 均与改造前一致                          |
| 前端 | `npm run build`; 三组 × 页面导航可见性；无权限路由；矩阵保存后当前用户导航即时变化                                          |
| 其他 | `git diff --check`; Ruff 无新增告警                                                            |

## 11. 风险与回滚



| 风险                                               | 处理                                                                                                   |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `role` 与 `perm_role` 概念被混用（后续贡献者又拿 `role` 做权限判断） | 在 `user_store.py` 顶部注释与 `rbac.py` 文档写明；`permissions.py` 不再出现 `ROLE_ADMIN` 字符串；加静态测试禁止 `is_admin` 新用法 |
| 页面隐藏但接口可调                                         | 已收敛: 业务路由在 `main.py` 统一挂 `require_page_access(anyOf)`；新增路由须登记 `page_access.PAGE_ACCESS`，review 检查 `_page_deps`。扩展包路由需各自挂载 |
| 矩阵改错导致管理员进不去后台（自锁）                               | `super_admin` 组强制保留 `admin.access` / `admin.roles_manage`; 改矩阵时若目标是当前用户所属组，保存后立即重取 `/me` 并提示         |
| 多实例部署时 SQLite 并发写                                | 沿用现有 `threading.Lock` + 短事务；审计写与用户更新同事务，不新增长事务                                                       |
| 权限判断进入热路径                                        | 每次请求一次单行 SQLite 查询（命中 page cache); 若压测出现瓶颈，再加按 `(user_id, group updated_at)` 的进程内短 TTL 缓存，不做全局缓存     |
| 前端旧缓存缺 `permissions` 字段                          | `usePermissions()` 缺字段时回退 `is_admin` 全量，并在登录 / 切换账号时 `invalidateQueries(['account-me'])`             |

回滚：本次改动不改 `role`、配额、会话，回滚只需下线前端入口与 `/api/admin` 路由；`perm_role` 列与 `role_groups` 表保留不删（无害）, 不必回滚数据。

## 12. 后续（二期，不在本次范围）



* 自定义权限组（`is_builtin=0`）+ 组复制。

* 数据 scope: 策略 / 自选 / 告警按 owner 或组共享；需要先把 `tenant_service` 从「按用户」升级为「按用户 + 组」。

* 页面级 API 强制：把 `ROUTE_PERMISSIONS` 扩展到业务 API, 前提是先拆开 `page.watchlist` 与 `page.screener` 共用的 `/api/kline`。

* 邀请注册（管理员建号）与「禁止公开注册」开关。