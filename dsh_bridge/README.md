# DSH 本地会话连接插件

在已运行的 DSH desktop 宿主内使用公开的 `sessionController` 服务。只列出现有会话和向用户选定会话 `prompt(mode=queue)`，不创建Agent、会话或模型子进程，不改变会话工具授权。

## 使用

管理台 → 会话投递 → DSH会话 → 使用此DSH会话。切回时点“使用Codex”。切换只处理尚未投递的任务，原有Codex候选/钉住配置、数据库、下载目录、回复与评论队列都保留。

DSH必须保持运行，目标会话应能读取项目 `AGENTS.md`、本地资料并执行 `bridge.py`。当前版本为DSH 0.2.1-rc.2，使用已安装 `@deepseek-ai/dsh-api-session-controller` 的 list/prompt 契约；升级后须再次验证。

## 注册与协议

安装时在 `__DSH_HOME__\profiles\desktop\cordis.patch.yml` 追加 `insert` 行，插件名为本目录 `index.mjs` 的绝对file URL。原配置备份在运行目录 `backups/dsh-switch-20261024/desktop-cordis.patch.yml`。当前宿主HMR支持配置热加载，本次未重启DSH。其他机器若没有HMR，则不能声称配置已加载。

**DSH 更新会重置该文件（0.2.1-rc.3 更新后变成 `[]` 或只剩 UI 设置项），插件注册行会丢失。** 恢复方法：在该文件末尾追加下面的 insert 块，保存后宿主 HMR 会在数秒内加载，`127.0.0.1:8794` 重新监听，管理台即可检测到 DSH：

```yaml
- insert:
    - id: douyin-local-session-bridge
      name: "__PROJECT_URI__/dsh_bridge/index.mjs"
```

2026-10-24 更新后已按此恢复并验证：8794 监听、`/sessions` 返回25个会话、管理台 `GET /api/agents/dsh` 返回 `online=true, sessions=25`，未重启 DSH。更新前先备份 `cordis.patch.yml`，更新后立即检查本行是否还在。

连接只绑定 `127.0.0.1:8794`，不开放公网。凭据单独放在运行目录 `dsh-bridge.json`，只由Python和宿主读取，不通过管理台返回，不复用或读取DSH自身登录令牌。所有请求验证Bearer、Host并拒绝浏览器Origin。管理台同源JSON接口 `POST /api/agent` 保存主体和DSH会话，`GET /api/agents/dsh` 只返回标题、ID、最近活动、工作目录、预设及运行状态，无会话历史。

- `GET /sessions`：调用 `list({})`，过滤子Agent和需迁移的会话，最多100项。
- `POST /prompt`：`session_id`、32位任务UUID、简短信封。只接受当前管理台选中的会话。Python先核对真实数据库中的主人身份、pending状态、attempted投递记录和目的地快照。
- 插件以任务UUID独占创建 `dsh-dispatch-receipts/<UUID>.json`，先记录attempted再调用prompt；稳定requestId为 `douyin-<UUID>`。只在 `accepted=true` 后记录accepted。
- 连接离线/会话缺失时，尚未投递的任务继续pending。投递开始后失败或结果不明记outcome_uncertain，不自动重试或改投其他主体。
- `queued`／`accepted`仅表示DSH接受消息；真正处理成功仍由DSH执行bridge claim/finish决定。发送平台接受不等于主人确认收到。

DSH的模型回复正文不会被抓取后直接转发。DSH照项目协议把UTF-8回复写到原运行目录，再调用bridge finish进入原回复队列；图片、媒体与公开评论同样复用现有工具。

## 验证

`python -B -X utf8 -m unittest test_dsh_dispatch test_event_dispatch test_codex_locate test_sdk_receiver test_local_runtime test_task_context test_console_storage` 与 `node --test dsh_bridge/test.mjs`。所有新增测试使用隔离数据库/目录或mock，不能向真实会话投递测试fixture。真实验收须由主人从主账号发新私信。
