# OpenViking Session 自动 Commit 策略设计文档

Date: 2026-07-15
Status: 已实现

## 背景

记忆库存量客户迁移中，OV 需要对齐记忆库 `StreamingWrite`（`POST /api/memory/session/streaming_write`）的"边对话、边写入、自动抽取"能力。

记忆库的做法是：客户端每轮把 user / assistant 消息写入短期缓存，系统持续跟踪累积的 token 数和消息条数，当任一触发条件满足时自动执行长期记忆抽取，把对话压缩成事件记忆。触发条件包含 `token_count`、`message_count`、`wait_timeout` 三个阈值。

OV 在此之前的方案存在两个问题：

- **易用性**：客户期望"每个 session 每轮对话为单位高频抽取"（记忆库客户 A 场景），但 OV 只有手动 `commit_session(session_id)`，需要接入方自己开发触发逻辑。
- **性能**：非标准 plugin 接入的客户，`add_message` 积攒大量 trace 后一次性 commit，容易因上下文过长导致抽取失败。

因此需要让 OV 支持**按 session 配置的自动抽取策略**，但默认保持关闭，避免对既有租户和既有 session 产生隐式抽取行为。开发者可以在创建 session 时显式开启并定制抽取频率；服务端也可以通过全局 `memory.session_auto_commit.default_enabled` 控制“未显式传策略的新 session 是否默认开启”。

## 目标与非目标

### 目标

- 每个 session 可以携带一份 `auto_commit_policy` 配置；**策略存在即启用，策略为 `None` 即关闭**。
- 服务端提供 `memory.session_auto_commit.default_enabled`，控制创建 session 时请求未显式传 `config.auto_commit_policy` 的默认行为；默认值为 `false`。
- 支持在创建 session 时指定，并通过获取接口查看该配置；创建后配置不可变。
- 消息写入后按"累积 token / 累积消息条数"阈值即时触发自动 commit，并支持节流。
- 支持"空闲超时"强制抽取（服务端后台调度器扫描）。
- 提供 Python SDK 的创建期配置能力；Rust CLI 目前可查看该配置，但不提供运行期修改命令。

### 非目标

- **不提供单独的 `enabled` 字段**。当前启停语义由 `auto_commit_policy` 是否存在表达：创建时未显式传策略且 `default_enabled=false` 时关闭；显式传 `{}` / 部分字段 / 完整字段时开启并按默认值补齐。创建后不提供运行期启停或修改语义。
- 不改变既有手动 `commit` / `commit_session` 的语义。
- 不再支持旧的"逐消息传 `auto_commit_policy`"配置语义；老客户端继续传该字段时会被请求模型静默忽略，策略不会被修改或启用。

## 总体架构

自动 commit 由三层协作完成：

```
                    ┌────────────────────────────────────────────────┐
   HTTP / SDK       │ POST /sessions   GET /sessions/{id}              │  配置面
                    └───────────────┬────────────────────────────────┘
                                    │  auto_commit_policy (dict)
                                    ▼
        ┌──────────────────────────────────────────────────────────┐
        │  AutoCommitPolicy（唯一合法化入口：clamp + 未知键拒绝）    │  策略模型
        └──────────────────────────────────────────────────────────┘
                                    │  持久化到 session .meta.json
                                    ▼
   触发面①  add_message / batch  ─────────────┐
            （inline, reason=message_write）   │
                                               ▼
   触发面②  SessionAutoCommitScheduler ──▶ maybe_schedule_auto_commit
            （后台扫描, reason=idle_timeout）        │ 去重 + 节流复核
                                                     ▼
                                            run_auto_commit
                                                     │
                                                     ▼
                                     Session.commit_async（两阶段归档+抽取）
```

- **配置面**：只在 create / GET 两处读写策略，绝不逐消息传入；`PATCH /api/v1/sessions/{id}` 不支持。
- **策略模型 `AutoCommitPolicy`**：所有取值合法化（clamp、未知键拒绝、默认填充）的唯一实现处。
- **触发面**：两条独立链路（消息写入即时触发、后台空闲超时触发）共用同一套判定/去重/执行逻辑。

## 策略模型 `AutoCommitPolicy`

代码位置：`openviking/session/auto_commit_policy.py`

这是一个 `@dataclass`，5 个 int 字段，配一组模块级常量（默认值、上限、合法键集合 `_POLICY_KEYS`）。它描述的是**已启用时的有效策略**；session meta 里的 `auto_commit_policy=None` 表示未启用自动 commit。

启用后，策略以 `config.auto_commit_policy` 的形式写入 session meta：

```json
{
  "config": {
    "auto_commit_policy": {
      "pending_token_threshold": 10000,
      "message_count_threshold": 50,
      "idle_timeout_seconds": 86400,
      "keep_recent_count": 2,
      "min_commit_interval_seconds": 0
    }
  }
}
```

| 字段 | 类型 | 默认值 | 上限 | 语义 |
|------|------|--------|------|------|
| `pending_token_threshold` | int | 10000 | 50000 | 未提交 pending token **严格大于**该值时，在消息写入后触发自动 commit |
| `message_count_threshold` | int | 50 | 500 | 未提交 live message 数**严格大于**该值时，在消息写入后触发自动 commit |
| `idle_timeout_seconds` | int | 86400（1 天） | 604800（7 天） | 有未提交内容的 session 空闲超过该秒数后，进入服务端 idle 调度器处理范围 |
| `keep_recent_count` | int | 2 | 500 | 阈值触发的自动 commit 保留的最近 live message 数（作为原始 context）；idle 超时触发忽略该值，提交全部积压 |
| `min_commit_interval_seconds` | int | 0 | 604800 | 两次自动 commit 之间的最小间隔秒数（节流） |

默认值对齐记忆库 `streaming_write` 的推荐值。所有字段最小值为 `0`；`0` 有"关闭该触发维度"的语义（见下文 `get_*` helper）。

### 校验与 clamp 规则（单一来源）

所有取值的合法化只在 `AutoCommitPolicy` 一处发生，保证 HTTP / SDK / 本地嵌入式入口行为一致。核心是 `_coerce_int`：

```python
def _coerce_int(value, *, field, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise InvalidArgumentError(f"auto_commit_policy.{field} must be an integer")
    if parsed < minimum:
        parsed = minimum      # clamp 到下界（恒为 0）
    if parsed > maximum:
        parsed = maximum      # clamp 到上界（PRD 上限）
    return parsed
```

据此得到三条规则：

- **clamp 而非拒绝**：超出上限的值被 clamp 到 `[0, max]`，请求正常返回 200。例如 `pending_token_threshold=10_000_000` 会被 clamp 成 `50000`；负数被 clamp 成 `0`。
- **未知字段拒绝**：`from_dict()` 发现 `_POLICY_KEYS` 之外的键时抛 `InvalidArgumentError`（HTTP 映射为 400）。
- **类型校验**：非整数字符串/对象抛 `InvalidArgumentError`。

> 设计取舍：request 层（`AutoCommitPolicyRequest`）**刻意不加** Pydantic 的 `ge` / `le` 数值边界，只保留 `extra: "forbid"` 用于拒绝未知键。数值边界交给 dataclass clamp，避免"request 层 422 拒绝 vs dataclass clamp 200"两套语义打架。而 request 层保留 `forbid`、dataclass 也再查一次未知键，是因为 SDK 本地直连路径不经过 Pydantic model，仍需 dataclass 兜底。

关键方法：

- `default()` — 返回全默认策略。
- `from_dict(data)` — 从 dict 构建：`None` → 默认；已是 `AutoCommitPolicy` → 原样返回；dict → 逐字段 `_coerce_int`（缺失填默认、越界 clamp）、未知键抛错。注意：这是模型层的解析行为；运行期调用方会先判断 `session.meta.auto_commit_policy is None`，将其视为关闭，不会把缺失策略自动解析成默认开启。
- `merge(patch)` — 内部辅助方法：基于当前策略覆盖传入 dict 里的合法键，再走一遍 `from_dict` 完成 clamp。当前 HTTP / SDK 配置面不暴露运行期 merge 接口。
- `to_dict()` — 序列化为固定 5 字段的 dict，写入 meta。

### 运行期 helper（`session_auto_commit.py`）

触发判定不直接读 dataclass 字段，而是经过一组 helper，把"`None` 表示整个自动 commit 关闭"和"0 表示关闭单个触发维度"的语义收敛在一处：

```python
def get_token_threshold(policy):        # >0 才返回，否则 None（关闭该维度）
    if policy is None:
        return None
    t = resolve_policy(policy).pending_token_threshold
    return t if t > 0 else None
def get_message_count_threshold(policy): ...   # 同上
def get_idle_timeout_seconds(policy): ...      # >0 才返回，否则 None
def get_min_commit_interval_seconds(policy): return max(0, ...)  # 0 表示不节流
def get_keep_recent_count(policy): return max(0, ...)
```

`resolve_policy` 内部就是 `AutoCommitPolicy.from_dict(...)`，因此 meta 里存的是 dict 时，即便是历史脏数据，也会在读取时被重新 clamp/补齐默认字段。`policy is None` 在 helper / service 层短路为关闭，不进入默认策略解析。

## 配置读写接口

### 创建：`POST /api/v1/sessions`

- `CreateSessionRequest` 新增 `config: Optional[SessionConfigRequest]`。
- 若请求显式传入 `config.auto_commit_policy`，路由用 `model_dump(exclude_none=True)` 提取（只保留用户显式给的字段），交给 `SessionService.create(auto_commit_policy=...)`。
- `create()` 内部按如下顺序决定是否启用：
  - `auto_commit_policy is not None`：启用。`AutoCommitPolicy.from_dict(payload).to_dict()` 写入 `session.meta.auto_commit_policy`，即便用户只给一个字段，落库的也是**填充默认后的完整 5 字段**。
  - 请求未显式传策略，但服务端 `memory.session_auto_commit.default_enabled=true`：启用。用 `AutoCommitPolicy.from_dict(None).to_dict()` 写入完整默认策略。
  - 请求未显式传策略，且 `default_enabled=false`（默认）：关闭。`session.meta.auto_commit_policy = None`。
- 响应 `result.config.auto_commit_policy` 返回当前 session 的有效配置；关闭时为 `null`。

### 查看：`GET /api/v1/sessions/{session_id}`

- 响应在 `session.meta.to_dict()` 之外追加 `result.config = effective_session_config(session)`。
- `effective_session_config` 只有在 `meta.auto_commit_policy is not None` 时才用 `AutoCommitPolicy.from_dict(...).to_dict()` 计算；若 meta 里没有策略或策略为 `None`，返回 `{"auto_commit_policy": null}`。
- 同时单独回填 `result.pending_tokens`，方便调用方观察触发进度。

### 运行期编辑

Session config 创建后不可变；`auto_commit_policy` 只能在 `POST /api/v1/sessions`
时设置，之后通过 `GET /api/v1/sessions/{session_id}` 查看生效配置。

`PATCH /api/v1/sessions/{session_id}` 不支持，HTTP 路径返回 405 Method Not
Allowed。这样避免运行期配置写入与消息追加同时覆盖 `.meta.json` 的
read-modify-write 竞态，也让自动 commit 的启停边界保持在 session 创建期。

### 移除的旧入口

- `AddMessageRequest` / `BatchAddMessageRequest` **移除了** `auto_commit_policy` 入参。这两个 model 不设 `extra: "forbid"`，因此老客户端仍传该字段时会被 Pydantic **静默忽略**（返回 200，策略保持不变），不报 422，降低迁移摩擦。

## SessionMeta 数据模型

`openviking/session/session.py:SessionMeta`（dataclass，序列化进 `.meta.json`）。与本设计相关的字段：

| 字段 | 类型 | 用途 |
|------|------|------|
| `message_count` | int | 当前 live 消息条数（每次 load 从 messages.jsonl 重新校准） |
| `pending_tokens` | int | 累积未提交 token 数，触发判据之一（见下节） |
| `keep_recent_count` | int | **plugin 的 pending-token 记账窗口**，决定滑动窗口哪些消息计入 pending_tokens |
| `auto_commit_policy` | Optional[Dict] | 持久化的策略；`None` 表示自动 commit 关闭 |
| `last_message_at` | str | 最近消息时间（ISO），idle 计时基准 |
| `last_auto_commit_at` | str | 最近一次自动 commit 时间，节流计时基准 |
| `auto_commit_last_error` | str | 最近一次自动触发/一阶段 commit 的错误 |
| `auto_commit_last_error_at` | str | 上述错误的时间 |

> 注意 `keep_recent_count`（meta 上的记账窗口）与 `auto_commit_policy.keep_recent_count`（策略的 commit 预留）是**两个不同的东西**，见后文"关键设计取舍"。

## pending_tokens 记账原理

`pending_tokens` 是"累积 token"触发维度的数据基础，需要在高频 add_message 下保持 O(1) 维护，同时在 load / commit / 回滚时能被正确重建。

### 滑动窗口定义

给定 meta 里的记账窗口 `keep = meta.keep_recent_count`：

- `keep <= 0`：所有 live 消息都算 pending（`pending_tokens = Σ tokens`）。
- `keep > 0`：只有**除最近 keep 条以外**的消息算 pending（`pending_tokens = Σ tokens[: total-keep]`）。
- live 消息数 `<= keep`：`pending_tokens = 0`。

### 增量维护（add 路径，O(1)）

`_append_messages` 每追加一条消息时增量更新，避免每次全量求和：

```python
keep = int(meta.keep_recent_count or 0)
if keep <= 0:
    meta.pending_tokens += msg_tokens          # 全部计入
elif len(messages) > keep:
    pushed_out = messages[-(keep + 1)]          # 新消息把窗口最老的一条挤出 keep 窗
    meta.pending_tokens += pushed_out.tokens    # 被挤出者进入 pending
```

即：新消息进入"最近 keep 条"窗口，同时把窗口里最老的一条挤出成 pending，pending 只累加被挤出者的 token。

### 全量重建（load / 回滚，O(n)）

`_rebuild_pending_tokens()` 按上面的滑动窗口定义整段重算，用于：

- `load()`：从 messages.jsonl 恢复 live 消息后重算，保证跨重启一致，并为 meta 早于本次改动的历史 session 补齐该字段。
- commit 回滚等安全网场景。

commit 归档后，pending 部分已被写入 archive，故 `commit_async` 直接把 `pending_tokens` 重置为 0。

## commit_async 两阶段流程

`Session.commit_async(keep_recent_count=0, *, memory_policy=None, persist_keep_recent_count=True)` 是所有 commit（手动/自动）的统一入口。

### keep_recent_count 与 persist 两个入参

- `keep_recent_count`：本次归档后 live 保留的最近条数。`0` = 全量归档。
- `persist_keep_recent_count`：是否把本次 `keep_recent_count` 回写进 `meta.keep_recent_count`（影响后续 add 的记账窗口）。
  - 默认 `True`：手动 commit / message_write 触发都回写。
  - idle 全量提交传 `False`：一次性全量归档**不应**把存量的 keep 偏好覆盖成 0。

```python
stored_keep_recent_count = (
    keep_recent_count if persist_keep_recent_count
    else max(0, int(meta.keep_recent_count or 0))   # 保留原值
)
```

### Phase 1（归档，路径锁保护）

1. **空消息快速路径**：`self._messages` 为空 → 只重置 `pending_tokens=0`、写 `stored_keep_recent_count`，返回 `status=skipped, reason=no_messages`，不进锁。
2. 取文件系统分布式锁 `LockContext(session_path, "exact")`，跨 worker/进程串行化归档。
3. **锁内二次空检查**：处理两个并发调用都过了预检查、但只应有一个归档的竞态。
4. **全在 keep 窗内**：`keep_recent_count > 0 且 total <= keep_recent_count` → 无需归档，重置 `pending_tokens=0`、写 keep、返回 `reason=all_within_keep_window`。
5. 计算切分点 `split_idx = total - keep_recent_count`（keep=0 时归档全部）：
   - `messages_to_archive = messages[:split_idx]`
   - `retained_messages = messages[split_idx:]`
6. **先写 archive 再裁剪 live**：先把 `messages_to_archive` 写入 `history/archive_NNN/messages.jsonl`，成功后才把 live 替换成 retained tail 并回写 messages.jsonl。任一步失败则回滚 `self._messages` 与 `compression_index` 并抛错——保证归档写失败不会丢失 live 会话。
7. 锁外收尾：更新 `message_count`、`pending_tokens=0`、`keep_recent_count=stored`、`commit_count`、`last_commit_at`，`_save_meta()`。

### Phase 2（记忆抽取，后台）

通过 `asyncio.create_task` 异步做长期记忆抽取，返回 `task_id` 供轮询；二阶段失败通过后台 task 与 `.failed.json` 上报，**不**写 `auto_commit_last_error`。

## 触发链路

### 消息写入触发（inline，reason = `message_write`）

`add_message` / `batch_add_messages` 在写入消息后依次调用：

1. `SessionService.touch_last_message_at(session)` — 写 `meta.last_message_at`（供 idle 计时），并 `_save_meta`。
2. `SessionService.maybe_schedule_auto_commit(session_id, ctx, reason_hint="message_write")`。

判定见 `_should_run_auto_commit(session, policy, "message_write")`：

1. `policy is None` → 自动 commit 未启用，不触发。
2. 无未提交内容（`_has_uncommitted_content`）→ 不触发。
3. 处于节流窗口内（`_within_min_commit_interval`）→ 不触发。
4. 读 `meta.pending_tokens` / `meta.message_count`（异常转 int 失败则不触发）。
5. `token_threshold is not None 且 pending_tokens > token_threshold` → **触发**。
6. `message_threshold is not None 且 message_count > message_threshold` → **触发**。
7. 否则不触发。

`_has_uncommitted_content`（helper `has_uncommitted_content`）：`pending_tokens > 0` 或 `message_count > meta.keep_recent_count` 即视为有未提交内容。

阈值比较用**严格大于**（`>`），与 PRD "大于该值则触发" 一致。`threshold is not None` 的判断把"阈值=0（关闭该维度）"排除在触发之外。

### 空闲超时触发（后台调度，reason = `idle_timeout`）

`SessionAutoCommitScheduler`（`openviking/service/session_auto_commit.py`）：

- 仅当服务端全局开关 `memory.session_auto_commit.idle_enabled = true` 时才在 `service/core.py` 里被创建并 `start()`；否则 `_session_auto_commit_scheduler = None`。
- `_run_loop`：每 `check_interval_seconds`（默认 60s）`await sleep` 后，再判一次 `idle_enabled` 才 `_scan_once()`；循环内任何异常都被 catch 记 error，不让调度器崩溃。
- `_scan_once` 统计 `scanned/due/scheduled`，仅当 `due>0` 才打 info 日志，避免空扫刷屏。

#### 扫描算法（`_iter_session_meta_path_batches`）

三层遍历 AGFS 树，产出 `.meta.json` 路径批次：

```
/local/{account_id}/user/{user_id}/sessions/{session_id}/.meta.json
```

- 顶层 `ls /local` 得到 account 目录，跳过 `_system` 与非目录项。
- 逐 account `ls /local/{acc}/user`，逐 user `ls .../sessions`。
- 每个 session 目录拼出 meta_path，用 `seen` set 去重后进 `batch`。
- 累计到 `scan_batch_size`（默认 16）就 `yield` 一批，批间可选 `scan_batch_pause_seconds` 暂停降存储压力。
- 各级 `ls` 的 NotFound 记 debug、其他异常记 warning 后跳过，单个目录出错不影响整体扫描。

#### 单条判定（`_read_idle_candidate` → `_is_idle_policy_due`）

- 并发 `asyncio.gather` 读取一批 meta；JSON 解析失败/非 dict/NotFound 都返回 None 跳过。
- `_is_idle_policy_due(meta, now)`：
  1. `auto_commit_policy is None` → 自动 commit 未启用，不到期。
  2. `get_idle_timeout_seconds(policy)` 为 None（idle_timeout<=0）→ 不到期。
  3. `has_uncommitted_content(meta)` 为假 → 不到期。
  4. `next_check_at = compute_next_check_at(last_message_at, idle_timeout)`（= `last_message_at + idle_timeout`）。
  5. `is_next_check_due(next_check_at, now)` 为 True → 命中。
- 命中后从 meta_path 反解 `session_id/account_id/user_id`，构造 `RequestContext(Role.USER)`，调 `maybe_schedule_auto_commit(..., reason_hint="idle_timeout")`。

`is_next_check_due` 做了**时区归一**：`next_check_at` 带 tz 而 `now` 不带（或反之）时，会把两者对齐到同一 tz/naive 再比较，避免混用 aware/naive datetime 抛错。同样的归一逻辑也用在节流判断 `_within_min_commit_interval`。

## 调度、去重与并发

### `maybe_schedule_auto_commit`

```python
session = await self.get(session_id, ctx, auto_create=False)
if not self._should_run_auto_commit(session, policy, reason_hint):
    return False
claim = (account_id, user_id, session_id)
async with self._auto_commit_claims_lock:          # 进程内互斥
    if claim in self._auto_commit_claims:
        return False                               # 已在飞，跳过
    if await tracker.has_running("session_commit", session_id, ...):
        return False                               # 跨进程已有 commit 任务，跳过
    self._auto_commit_claims.add(claim)
asyncio.create_task(self.run_auto_commit(session_id, ctx, reason=reason_hint))
return True
```

两级去重：

- **进程内**：`_auto_commit_claims`（`set[(account,user,session)]`）+ `asyncio.Lock`，防止同一进程内高频 add 反复排队 commit。claim 的增删都在锁内 / `run_auto_commit` 的 `finally` 里。
- **跨进程/worker**：`task_tracker.has_running("session_commit", ...)`，已有运行中的 commit 任务则跳过。

执行体用 `create_task` 异步跑，**不阻塞** add_message 的响应。

### `run_auto_commit`

```python
try:
    if await tracker.has_running("session_commit", ...): return   # 再查一次防竞态
    session = await self.get(session_id, ctx, auto_create=False)
    if not self._should_run_auto_commit(session, policy, reason): return  # 状态复核
    if reason == "idle_timeout":
        result = await session.commit_async(keep_recent_count=0,
                                             persist_keep_recent_count=False)
    else:
        result = await session.commit_async(keep_recent_count=get_keep_recent_count(policy))
    if result.get("archived"):
        meta.auto_commit_last_error = ""
        meta.auto_commit_last_error_at = ""
        meta.last_auto_commit_at = get_current_timestamp()   # 节流计时起点
    await session._save_meta()
except Exception as exc:
    # best-effort 记录错误，不影响主流程
    meta.auto_commit_last_error = str(exc); meta.auto_commit_last_error_at = now
finally:
    async with self._auto_commit_claims_lock:
        self._auto_commit_claims.discard(claim)   # 无论成败释放进程内 claim
```

要点：

- 调度时判过一次、执行前再复核一次 `has_running` + `_should_run_auto_commit`，覆盖"排队期间状态已变"的竞态。
- **idle_timeout** 走全量提交（`keep_recent_count=0` 且不持久化 keep），对应 PRD "超时将 session 内未被抽取的所有 message 都提交"。
- **message_write** 按策略 `keep_recent_count` 保留最近若干条。
- 只有真正 `archived` 才更新 `last_auto_commit_at`（节流基准）并清错误；`skipped`（无消息/全在 keep 窗内）不刷新节流时钟。
- 错误记账是 best-effort，二次 `get` 也可能失败，失败只记 debug。

### 节流 `_within_min_commit_interval`

`interval = get_min_commit_interval_seconds(policy)`；`interval<=0` 或无 `last_auto_commit_at` → 不节流。否则 `now - last_auto_commit_at < interval` 即处于节流窗口，`_should_run_auto_commit` 直接返回 False。节流只对 `message_write` 生效（idle 分支不查节流，但 idle 本身有 `idle_timeout` 天然间隔）。

## 关键设计取舍

### `keep_recent_count` 与 `meta.keep_recent_count` 解耦

这是本次实现中最重要的一处修正。

- `meta.keep_recent_count` 是 **plugin 管理的 pending-token 记账窗口**：决定 `_rebuild_pending_tokens()` / `_append_messages()` 时哪些消息计入 `pending_tokens`。
- 策略里的 `auto_commit_policy.keep_recent_count` 是 **commit 时的预留条数**（PRD "每次自动抽取预留出最新的消息条数"）。

早期实现曾在 `load()` / `create()` 时把 `policy.keep_recent_count`（默认 2）**镜像**进 `meta.keep_recent_count`，导致：session 加载后 pending 记账窗口被改成 2，≤2 条消息的 session 直接 `pending_tokens=0`，破坏了既有语义（`test_get_session_pending_tokens_counts_tool_only_messages` 失败）。

**最终方案**：两者完全解耦。

- 策略的 `keep_recent_count` **只在自动 commit 触发那一刻**作为 `commit_async(keep_recent_count=...)` 的实参使用。
- `load()` 不会给缺失策略的 session 回填默认 `auto_commit_policy`；缺失或 `None` 保持关闭。只有当 meta 里已经存在策略 dict 时，运行期读取才会通过 `AutoCommitPolicy.from_dict(...).to_dict()` 做 clamp / 默认字段补齐。无论哪种情况，`load()` 都**不再写** `meta.keep_recent_count`。
- create 也不再镜像。
- `commit_async` 的 `persist_keep_recent_count=False`（idle 全量路径）确保一次性全量归档不会把存量 keep 偏好写成 0。

这样 `pending_tokens` 保持"累积未提交 token 总数"语义（正好对应 PRD 的累积 token 触发口径），既有 plugin 行为不受影响。

### 幂等与"以最新一次调用为准"

PRD 要求触发抽取时"以最新一次调用为准"。OV 的实现是：策略在创建 session 时持久化到 session meta，创建后不可变；每次触发时（`maybe_schedule_auto_commit` / `run_auto_commit`）都会重新 `get` session 读取权威 meta，基于最新的消息计数、pending token、节流时间和任务状态做复核。而两级去重 + `archived` 才刷新节流时钟，保证同一时刻只有一次归档在飞，具备幂等性。

### clamp 而非 422 拒绝

把越界值 clamp 成上限、返回 200，而不是拒绝请求，是为了对齐记忆库"设了个超大值也能用（按上限生效）"的宽松语义，降低迁移摩擦；同时把"什么是非法（未知字段/非整数）"与"什么只是越界"区分开——前者报 400，后者静默 clamp。

## 分布式部署的局限与扩展预留

当前实现主要面向**单实例（或单调度器实例）**部署，两条触发链路的分布式特性不同：

- **消息写入触发（message_write）**：天然随请求分布到各实例，配合 `commit_async` 内的文件系统分布式锁 + `task_tracker.has_running` 跨进程去重，多实例并发写入同一 session 时不会重复归档，无额外假设。
- **空闲超时触发（idle_timeout）**：由 `SessionAutoCommitScheduler` 后台全量扫描 AGFS 触发。即使 `idle_enabled=true`，也只会处理自身 `auto_commit_policy` 已存在且 `idle_timeout_seconds>0` 的 session。当前设计假定**至多一个实例开启 `idle_enabled` 并承担扫描**。若多实例同时开启 idle 调度器，会出现：
  - 每个实例都对全量 session 树做重复扫描，扫描成本随实例数线性放大；
  - 虽然 `has_running` + 进程内 claim 能兜住"重复归档"，但重复扫描/重复排队仍是浪费，且缺乏跨实例的分片与故障接管机制。

**扩展预留**：本设计有意把 idle 触发与执行解耦——调度器只负责"发现到期的 session 并调用 `maybe_schedule_auto_commit`"，真正的去重、节流、归档都收敛在 `session_service` 一侧且已具备跨进程语义。因此未来面向分布式的演进（例如：调度选主 / 租约、session 分片扫描、扫描任务下发到队列、独立的 idle-scan 服务等）可以只替换"谁来扫描、如何分片"这一层，而不必改动策略模型、触发判定与 commit 执行路径。具体分布式方案不在本文档范围内，此处仅保留扩展点。

## 服务端全局配置

`openviking_cli/utils/config/memory_config.py:SessionAutoCommitConfig`（`memory.session_auto_commit`），这是**服务端全局控制面**，不是 per-session 业务策略：

| 参数 | 默认 | 约束 | 说明 |
|------|------|------|------|
| `default_enabled` | `false` | — | 创建 session 时，如果请求未显式传 `config.auto_commit_policy`，是否默认写入完整默认策略并启用自动 commit |
| `idle_enabled` | `false` | — | 是否启动 idle 调度器。关闭时，已启用策略的 session 仍可通过 token / message-count 做**即时触发** |
| `check_interval_seconds` | `60.0` | `>0` | idle 扫描周期 |
| `scan_batch_size` | `16` | `>0` | 每批并发读取的 meta 文件数 |
| `scan_batch_pause_seconds` | `0.0` | `>=0` | 批间暂停，降低扫描存储压力 |

（`model_config = {"extra": "forbid"}`，未知配置项报错。）

`service/core.py` 启动流程：`set_session_auto_commit_config(...)` → 仅当 `idle_enabled` 才 `SessionAutoCommitScheduler(...).start()`；关停时对应 `stop()`（cancel 循环 task 并等待）。`default_enabled` 只影响新 session 创建时的默认策略落库，不会启动后台调度器，也不会改写已有 session。

## SDK 与 CLI

### Python SDK

- `create_session(session_id=None, memory_policy=None, config=None)` — `config` 形如 `{"auto_commit_policy": {...}}`。
- 本地 client（`client/local.py`）从 `config` 里取 `auto_commit_policy` 直接下沉到 `SessionService.create`，不经过 HTTP/Pydantic，因此依赖 dataclass 兜底校验。
- 当前嵌入式 / 本地 SDK 与 HTTP SDK 都支持 create 时传 `config`；不再提供 `update_session_config` / `Session.update_config` 这类运行期配置更新入口。

### Rust CLI

Rust CLI 当前不提供 `auto_commit_policy set` 或运行期更新命令。`ov session get`
会展示服务端返回的 session config，用户需要通过 HTTP / SDK 在创建 session
时设置策略。

## 测试

- `tests/unit/session/test_auto_commit_policy.py`（13）— dataclass 默认值、clamp（越界/负数）、未知键拒绝、非整数拒绝、merge 部分覆盖、to_dict。
- `tests/unit/service/test_session_auto_commit.py`（17）— 触发判定（token/message 严格大于）、`policy is None` 跳过 message_write / idle_timeout、节流窗口、idle 全量提交、时区归一、`commit_calls=(keep_recent_count, persist_keep_recent_count)` 断言、去重。
- `tests/server/test_api_sessions.py`（新增/改写多条）— 默认关闭时 create / GET 返回 `auto_commit_policy=null`、`default_enabled=true` 时 create 写入默认策略、显式 `{}` / 部分字段启用并填充默认、clamp 返回 200、未知字段 400、`PATCH /sessions/{id}` 返回 405、`keep_recent_count` 与 `pending_tokens` 解耦、移除的逐消息入参被静默忽略。
- Rust：`cargo test -p ov_cli` 覆盖 session 命令仍不会把移除的 message-level `auto_commit_policy` 写入消息请求体。

已知无关失败：`test_tool_result_externalization_respects_server_config_disabled`、`test_commit_endpoint_returns_accepted_with_task_id` 等在 HEAD 基线上也失败（这些用例自建 app 未接 dev auth plugin，返回 401），与本设计无关。

## 端到端时序

### 消息写入触发

```
client ── POST /sessions/{id}/messages ──▶ add_message()
                                              │ session.add_messages([...])   # O(1) 维护 pending_tokens
                                              │ touch_last_message_at()        # 写 last_message_at
                                              │ maybe_schedule_auto_commit(reason="message_write")
                                              │     ├─ _should_run_auto_commit? (policy 存在 & uncommitted & 未节流 & >阈值)
                                              │     ├─ 进程内 claim 去重 + task_tracker.has_running 去重
                                              │     └─ create_task(run_auto_commit)   # 异步，不阻塞响应
                                              ▼
                                        200 {session_id, message_count}

（后台）run_auto_commit ──▶ 复核 has_running + _should_run_auto_commit
                              └─▶ commit_async(keep_recent_count=policy.keep_recent_count)
                                    ├─ Phase 1：路径锁内归档 pending，保留最近 keep 条
                                    ├─ archived → 清 error，写 last_auto_commit_at（节流起点）
                                    └─ Phase 2：后台抽取记忆（task + .failed.json 上报）
                              finally: 释放进程内 claim
```

### 空闲超时触发

```
每 check_interval 秒（仅 idle_enabled=true）:
  SessionAutoCommitScheduler._scan_once()
    └─ ls /local/*/user/*/sessions/*  分批(scan_batch_size)读 .meta.json
         └─ _is_idle_policy_due? (policy 存在 & 有未提交内容 & last_message_at + idle_timeout <= now)
              └─ maybe_schedule_auto_commit(reason="idle_timeout")
                   └─ run_auto_commit ──▶ commit_async(keep_recent_count=0, persist=False)  # 全量提交
```

## 相关代码入口

- `openviking/session/auto_commit_policy.py` — 策略 dataclass、clamp、默认字段补齐。
- `openviking/service/session_auto_commit.py` — 运行期 helper + idle 调度器（扫描/判定/时区归一）。
- `openviking/service/session_service.py` — `create` / `touch_last_message_at` / `effective_session_config` / `maybe_schedule_auto_commit` / `run_auto_commit` / `_should_run_auto_commit` / `_within_min_commit_interval`。
- `openviking/session/session.py` — `SessionMeta` 字段、`load()` 保持缺失策略为关闭、`_append_messages()` / `_rebuild_pending_tokens()` 记账、`commit_async(persist_keep_recent_count=...)` 两阶段。
- `openviking/server/routers/sessions.py` — request models、create / get / messages 路由。
- `openviking_cli/utils/config/memory_config.py` — `SessionAutoCommitConfig`。
- `openviking/service/core.py` — 调度器 bootstrap / 关停。
- `crates/ov_cli/src/{main,commands/session}.rs` — CLI 展示与消息请求体兼容逻辑。
