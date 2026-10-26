# 哪些地方必须由部署者提供

## Key 与登录清单

| 用途 | 人工提供/操作 | 在哪里设置 | 是否必须 |
|---|---|---|---|
| 主人、小号身份 | 主人数字 UID、小号数字 UID、主人 `/user/<sec_uid>`；评论提及时补准确owner_nickname | `deployment.local.json` | 抖音通道必须；用于严格身份核验；昵称不代替UID |
| 抖音私信 | 小号扫码，按平台提示手动完成验证 | 启动SDK后 `<runtime>/sdk-login.png`；状态见管理台 | 使用抖音通道必须 |
| DouK下载 | 在自己的 DouK 中登录/导入自己的小号 Cookie | `<runtime>/tools/douk-5.8/Volume/settings.json`，由DouK界面保存 | 分享作品下载必须 |
| 作品评论 | 独立浏览器 profile 登录同一个小号 | 管理台→评论→登录检查 | 仅公开评论需要；SDK登录不能替代 |
| AstrBot模型 | 服务商 API Key、Base URL、准确模型 ID | AstrBot→模型提供商→配置/默认模型 | 选择AstrBot必须；不捆绑额度 |
| AstrBot管理 | 初始页面登录、修改默认密码 | 本机 `http://127.0.0.1:6185` | 选择AstrBot必须 |
| AstrBot本地桥接 | 单独创建 `abk_` 本地 API Key | AstrBot API Key管理；`deploy/connect_astrbot.py`隐藏输入 | 与模型服务商Key不同 |
| Codex | 自己的Codex客户端登录、可用额度、工作区会话 | Codex桌面和管理台会话投递 | 只在选择Codex时需要 |
| DSH | 自己的DSH登录、模型配置、可用会话 | DSH和管理台会话投递 | 只在选择DSH时需要 |
| 鲸聊网络 | Tailscale登录、允许Funnel或配置可访问网络 | Tailscale客户端，`whale-channel.json` | 手机远程连接需要 |
| 鲸聊设备 | 手机安装APK，扫码配对，必要时输入访问密码 | 管理台→会话投递→打开鲸聊配对面板 | 鲸聊必须；不复用作者设备 |
| GPT-SoVITS | 音色权重、参考录音与对应文字 | 本地模型目录与插件配置 | 仅发语音需要；无在线TTS Key |
| SenseVoice | 下载INT8模型与tokens | `deploy/install_components.py --component asr` | 语音输入/视频音轨分析需要；无Key |
| 本地生图 | ComfyUI、适配节点、模型文件 | 本机模型目录 | 仅本地生图需要；无生图Key |
| 外部生图/搜索/点歌插件 | 依具体插件决定，有些需要额外凭据 | AstrBot插件页面 | 本发布不预装、不保证免Key |

模型服务商提供的 Base URL 和模型名必须按该服务的控制台填写；不要照抄他人的模型名或假设某项网页会员等于 API 额度。ChatGPT Plus 不自动提供 AstrBot 可直接使用的 GPT Image API Key。

## 身份字段如何获得

抖音昵称、抖音号、数字 UID、sec_uid 是不同字段。使用自己账号的 SDK 登录状态、本人主页链接和 SDK 返回的账号/联系人身份信息确认。主人主页路径应为 `/user/<sec_uid>`，不是主页昵称。初次可单独运行已配置的 `node sdk_service.mjs`，在本机查看 `/health` 的小号 uid；主人 UID 需从自己可见的联系人/消息信息确认。配置身份不对时应拒绝收信，不要为“方便”删掉校验。

若先只跑鲸聊，也应在本地配置自己的身份标识作为统一队列 owner。未登录抖音 SDK 时管理台可显示其离线，不能把这种状态描述成抖音已通。

## 哪些密钥由程序生成

`configure.py` 为新部署生成 `whale-bridge-secret.txt` 和 `dsh-bridge.json` 的随机本机密钥；手机配对令牌由网关生成。它们不需要去第三方申请，也不应从示例库复制。旧运行目录已有文件不会被覆盖。

## 凭据与资料的保存范围

登录、Cookie、AstrBot桥接Key与模型配置只保存在自己的运行目录。`deployment.local.json` 也不提交；不要把整个运行目录复制进 GitHub。私信、录音、图片、人物记忆、模型、配对设备和浏览器 profile 均不随 Release 分发。

公开仓库里的 `__OWNER_UID__` 等是源码模板占位符；示例JSON中的数字也不是可用账号。APK不含作者的配对凭据，需要连接自己的电脑。
