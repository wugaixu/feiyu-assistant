# 统一素材阅读适配器（协议4 / 插件2.3.0）

将本目录安装到 AstrBot 的 `data/plugins/workflow_local_vision/`，保留 `main.py` 和 `metadata.yaml`。使用本项目 Python 模块与运行目录；迁移机器时更新 main.py 中 PROJECT，与其他本地集成一致。AstrBot >=4.29.0，使用其已有 MCP 工具结果和视觉上下文接口，不修改 AstrBot 核心。

私聊 worker 在真实主人 processing 任务下签发十分钟素材许可，引用公共资料包及冻结的 `materials.json`。`workflow_read_text(path,offset,limit)` 分页读取与CLI相同的登记文本，`workflow_read_image(path)`返回实际图片；均核对同一份路径、文件散列与大小。Raw `data.json`只作本地排错，不通过模型工具读取，不开放任意文件浏览或Shell。

这意味着输入消息可以只给路径，但底层看图仍需读取图片字节并交给支持视觉的模型；文本模型不会因为有文件路径就具备视觉能力。API 模型的实际图片分析仍发生在其模型服务端，不是纯本地推理。

`workflow_comment(video_id,text,evidence)`在当前主会话登记评论草稿，共用`shared_comments.enqueue`。只有公共`reply_batch.finish`完成私信，草稿才从waiting_for_reply变pending；失败/needs_input则取消草稿。可含私聊梗，仅通过公共提示词要求提交前自查隐私；不增加审查模型或自建隐私过滤模块。

插件每8秒写协议心跳供管理台只读检查；未加载协议4时，新任务留队，不改投其他主体。Codex/DSH使用现有会话工具和bridge适配同一公共契约；DSH协议4不再注入原生图片附件。

验证：`python -B -X utf8 -m unittest test_local_vision test_unified_workflow test_visual_delivery -q`。只用隔离数据库/素材；真实验收用主人新的正常消息，不重放旧任务。安装后需要重载本插件或重启AstrBot；本轮主人选择自行重启，复制文件不表示内存已加载。

2026-10-27：移除旧历史可见性提示钩子，改为经许可核验的当前来源提示；workflow_read_text同样可读任务登记的instructions.md共用规则快照。不改变语音/生图插件或人设。
