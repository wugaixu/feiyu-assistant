# Windows 从零部署

## 1. 基础环境

实际维护环境为 Windows 11 x64。准备 Python（核心建议3.12+；原运行环境3.14；GPT-SoVITS单独使用其支持的3.12）、Node.js 22 LTS及npm、Git、Microsoft Edge。Android源码另需 JDK17+、Android SDK35、Gradle8.14.2；直接安装Release APK则不需要Android开发环境。

核心依赖很轻；本地生图需额外GPU/内存和模型空间，GPT-SoVITS可CPU推理。不要为本项目覆盖已有Anaconda或其他AI工具环境。以下路径仅为示例，改成自己的；运行目录与Git源码分开。

```powershell
git clone https://github.com/wugaixu/feiyu-assistant.git
cd feiyu-assistant
py -3.12 -m venv D:\FeiyuData\core-venv
D:\FeiyuData\core-venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item deployment.example.json deployment.local.json
```

在 `deployment.local.json` 填自己的UID/sec_uid、运行目录、Codex工作区；`python` 填 `D:/FeiyuData/core-venv/Scripts/python.exe`。不走Codex也需给一个本地工作区路径，但不要求安装/登录Codex。`dsh_home`按自己安装位置填写，未使用可保留null。

```powershell
D:\FeiyuData\core-venv\Scripts\python.exe configure.py
D:\FeiyuData\core-venv\Scripts\python.exe deploy/install_components.py --runtime-dir D:\FeiyuData --component sdk
```

`configure.py` 只在新的 `<runtime>/installed/0.3.0` 生成运行代码和缺失的初始配置，不启动程序、不迁移历史、不覆盖旧目录。源码保留模板，实际运行生成目录；以后升级先备份并按新版本迁移，不反复强制覆盖代码/配置。

核心 SDK 使用 npm `douyin-im@1.1.0`；原验证源码提交为 `f6eee669c39094224e00c6b877e1964d9da4e393`。发布版统一从 `<runtime>/node_modules/douyin-im/lib/index.js` 加载，不依赖作者研究目录。

## 2. 先接通一个 AI 主体

### AstrBot（聊天人设和插件路线）

```powershell
D:\FeiyuData\core-venv\Scripts\python.exe deploy/install_components.py --runtime-dir D:\FeiyuData --component astrbot
```

辅助脚本创建独立 `<runtime>/astrbot/tools/astrbot` venv，安装 AstrBot4.29.1并运行 `astrbot init`。按初始化提示完成操作，将控制台绑定 `127.0.0.1:6185`。AstrBot自身数据根为 `<runtime>/astrbot/runtime`，之后本项目 `start_astrbot.py` 使用 `ASTRBOT_ROOT` 指向此处。先按AstrBot官方说明初始化并在本机启动完成一次，管理台才能正常保活它。

1. 登录AstrBot，修改初始管理密码。
2. 在“模型提供商”填自己的API Key、Base URL与准确模型ID，选可用默认模型；先在AstrBot内验证文字回复。需要图片理解时，所选提供商还必须支持图片输入。
3. 在人格设置创建人设，再在对应配置中选择该人格。连接器不覆盖人格、不强制流式；语音/图片插件依赖发送前处理时建议先关闭流式验证。
4. 在AstrBot API Key管理创建本地Key（`abk_`），授权聊天相关API；运行以下命令隐藏输入它：

```powershell
D:\FeiyuData\core-venv\Scripts\python.exe deploy/connect_astrbot.py --runtime-dir D:\FeiyuData
```

该Key用于本机连接器，不是模型服务商Key。脚本会调用只读 `/chat/configs` 验证，不打印Key。随后在管理台“会话投递→AstrBot”创建/选择专用会话和人设配置，点击应用。不要直接拿AstrBot浏览器随便开的会话代替本连接器登记的专用会话。

选择AstrBot前必须安装统一素材阅读插件（无需先部署生图或语音）：

```powershell
D:\FeiyuData\core-venv\Scripts\python.exe deploy/install_plugins.py --runtime-dir D:\FeiyuData --reader-only
```

在AstrBot启用 `workflow_local_vision` 2.3.0，正常重载或重启后确认 `workflow_read_text`、`workflow_read_image`、`workflow_comment` 已注册，管理台协议状态为4。它按资料包路径读取实际图片/文本并登记评论草稿，不自动把原图发回用户。已有同名插件时脚本拒绝覆盖；升级先备份，再仅替换生成目录对应的插件代码。

### Codex

安装并登录本机Codex桌面，在 `codex_workspace` 指定的目录创建可读文件和执行 bridge 的会话；管理台仅列这个工作区的非归档会话。选择并应用。桌面内部数据库/CLI会变化，当前适配 `state_5.sqlite` 和已有 `codex queue` 契约，升级后先验证；不使用云端服务器替本地读取文件。

### DSH

配置 `dsh_home` 后，将生成目录 `dsh_bridge/index.mjs` 的绝对file URL注册到自己的DSH `profiles/desktop/cordis.patch.yml`：

```yaml
- insert:
    - id: douyin-local-session-bridge
      name: "file:///D:/FeiyuData/installed/0.3.0/dsh_bridge/index.mjs"
```

先备份原文件，不覆盖其他插件；在DSH正常重载或重启后确认8794在线。管理台选DSH时会检查并修复丢失的注册行，保留原条目。接口以DSH0.2.1-rc系列的SessionController为基线，更新后须确认。DSH消息须按生成目录AGENTS说明读取资料并执行bridge完成任务。

## 3. 启动、扫码与手机连接

从 `<runtime>/installed/0.3.0/管理界面.vbs` 启动；它隐藏控制台，管理台保活SDK、receiver和已启用的鲸聊。页面关闭只是保留到托盘，退出用托盘“关闭程序”。不添加开机自启或Codex定时任务。

SDK首次产生 `<runtime>/sdk-login.png`，用**助手小号**扫码；平台验证由自己完成。管理台检查实际小号UID，不能拿SDK在线代替主站评论登录或DouK Cookie。

鲸聊初始为本机、网络禁用模式。远程手机：安装并登录Tailscale，按其官方条件启用Funnel；在 `<runtime>/whale-channel.json` 把 `network.mode` 改为 `tailscale_funnel`，如果设置 `public_base_url` 必须是自己的HTTPS `.ts.net` 根地址。先设置网关访问密码，按管理台“打开鲸聊配对面板”提示操作；再正常重启管理台。入口只暴露鲸聊及受限管理代理，不手动转发8791/8793/9880等后端端口。

安装Release中的 `自行构建的1.5.3 APK（本次未上传新二进制）`，扫码配对，必要时先输入访问密码。配对成功自动登记设备。手机可选择头像、发文字/录音，点＋选图片或文件；图片10MiB、其他32MiB、每批6个。无新设备授权时不能读取其他设备附件。手机管理默认关闭，在电脑管理台显式开启。

已收到私聊后回复固定回原设备，管理台“主动消息通道”只用于自主联系/作品分享，不会把当前聊天的答复改发给别人。

## 4. 语音识别与作品下载

```powershell
D:\FeiyuData\core-venv\Scripts\python.exe deploy/install_components.py --runtime-dir D:\FeiyuData --component asr
```

安装 `sherpa-onnx-node@1.13.8`，按 `deploy/sensevoice-assets.json` 下载INT8模型和tokens并核验SHA256。目录为 `tools/sensevoice/models/model.int8.onnx` 和 `tokens.txt`；不需要在线ASR Key。手机录音限约60秒，视频音轨累计最多600秒，30秒分块。歌词和旁白可能混杂，不能声称已做声源分离。

从[DouK官方项目](https://github.com/JoeanAmier/TikTokDownloader)取得Windows5.8版本，解压到 `<runtime>/tools/douk-5.8`，确保该目录直接有 `DouK-Downloader.exe` 和 `Volume/`。人工运行DouK，完成自己的Cookie导入/登录与一次正常下载，产生 `Volume/settings.json` 和 `DouK-Downloader.db`。

视频/实况文件上限256MB，图集总计64MB，DouK进程超时180秒、外层190秒；保留原片，不自动压缩。

工作线程会从这份配置建立 `tools/douk-service-5.8` 专用副本，不使用主界面的下载历史代替任务记录。图集按实际数量、实况按分段校验；超时保留有效落盘文件并标记partial，不能假装所有作品都成功。普通手机上传的视频不需要DouK，也不自动发布作品评论。

## 5. 发语音（可选）

阅读[语音与生图](MEDIA_MODELS.md)。GPT-SoVITS是**独立模型服务**，市场插件只是调用接口；只安装插件不会自动下载模型。使用独立Python3.12环境和官方支持依赖，不把它安装进核心venv。模型、参考录音和依赖就绪后，再启用 `gpt-sovits.json` 与AstrBot插件。

```powershell
D:\FeiyuData\core-venv\Scripts\python.exe deploy/install_plugins.py --runtime-dir D:\FeiyuData --with-gpt-sovits
```

脚本复制的是配置后路径的统一素材阅读插件、本项目生图插件和修改版 GPT-SoVITS 插件。已有同名目录会拒绝覆盖；不自动重启、不恢复已废弃的Windows系统女声插件。配置音色参数和工具后正常重启AstrBot验证。合成原文与真实音频一起进入鲸聊语音历史，可展开查看文字；没有原文的旧音频不猜。

## 6. 生图、评论与自主活动（可选）

本地生图先独立安装ComfyUI和相应模型，确认其API可以实际出图，再启用 `local-image.json` 和 `douyin_local_image` 插件。人设不应伪造“图发了”；真文件通过队列外发。手动生图不需要聊天模型，入口为生成目录 `本地生图.vbs`。

评论草稿由当前主会话顺手生成并自查不含私人资料，无需单独评论会话；只有私信成功完成后才允许发布。

评论：安装Edge/Playwright依赖；管理台→评论→登录检查，在独立profile登录与SDK相同小号，然后手动开启公开评论和可选@。主站账号校验、提及候选、提交及回读走真实网页，和私信SDK无关。平台验证时需自己接管；不要通过无限重试绕过。

自主刷视频：默认关闭，在管理台配置浏览时段、分类和分享额度。旧工作流主动联系调度已移除，Codex/DSH不主动联系。

主动联系（可选）：在主AstrBot安装情绪日记插件，按 integrations/astrbot_diary_proactive/README.md 备份并贴补丁，然后重载插件。主人名单填 connect_astrbot.py 保存的 username（douyin-owner-你的UID），不要填QQ号。插件管理频率、额度、免打扰和素材触发；“每天最多”不是保底发送次数。只绑定管理台当前主AstrBot会话，由统一出站队列回原定通道。没有素材不发，不要通过强制测试制造日记。第三方版本不匹配时安装器可能拒绝，应人工核对，不能绕过检查。

## QQ私聊与抖音群聊（可选）

QQ：自行安装NapCatQQ并用机器人小号扫码，不能用主人号登录。OneBot11 HTTP设为127.0.0.1:8796，WebUI仅127.0.0.1:6099。管理台→会话投递→QQ登记主人号和小号，核验get_login_info后启用；默认配置关闭。配套 qq-channel.json、qq-onebot-secret.txt、qq-webui-secret.txt 仅存运行目录。OneBot回调为 http://127.0.0.1:8793/api/qq-events，HTTP令牌与签名secret应与本地设置一致；开启媒体返回字节选项。不要把这些令牌放进公开仓库。详细当前协议见 app/qq_channel.py；本仓库不捆绑QQ客户端或NapCat安装包。QQ语音自动转写；AstrBot音频先发送record，再发送实际合成原文。

抖音群聊：管理台“群聊”刷新SDK群列表和成员，只启用希望自动回复的群与成员。默认无启用群；不自动加群、邀请或接收陌生人私信。群消息标明群和发言人，使用同一所选宿主，不另建人格或会话；不能将主人私聊内容发进群。

## 独立本地模型

主工作流仍可选择Codex/DSH/AstrBot。原本地Gemma实验实例已脱离工作流，不应恢复旧local8k登记；需要独立小模型聊天时自行启动另一个AstrBot数据目录和端口，人设、插件、记忆与配置单独保存。它不是本版基本部署的必需组件。

## 7. 升级、备份与卸载

备份运行目录的数据库、配置、sdk-data、whale-data及模型来源清单；它们包含私人资料，备份不发到公开仓库。先正常退出托盘，再更新生成目录或部署新版本；不要用本仓库示例覆盖你的真实配置。第三方插件升级可能覆盖守门/原文展开兼容代码，须按测试清单复核。

卸载时只删除自己新建且已确认的目录，先保留凭据和聊天备份；不要递归删除整个用户目录或其他AI工具目录。没有配套“一键删所有数据”脚本。


### QQ首次安装补充

configure.py生成独立随机 qq-onebot-secret.txt 与 qq-webui-secret.txt，已有文件保留。请在运行目录 qq-channel.json 补上 napcat_dir（包含NapCatWinBootMain.exe、NapCatWinBootHook.dll、napcat.mjs、config的独立目录）及 qq_exe（已安装QQNT的QQ.exe绝对路径）。不要填写手机QQ目录或直接改QQ安装文件。先按NapCat官方说明完成兼容QQNT版本的安装与启动，再设置其 config/webui.json 的token与本地qq-webui-secret.txt一致，OneBot HTTP token和回调签名secret与qq-onebot-secret.txt一致；WebUI、OneBot均绑定127.0.0.1。管理台“绑定已登录小号”会再次读取get_login_info及核验主人/小号不是同一个账号，并设置媒体字节转换选项。

NapCat、抖音SDK、DouK、主站评论登录是不同登录面：QQ小号扫码不替代抖音登录，SDK登录不替代主站评论或DouK Cookie。AstrBot的模型API Key在其模型提供商页面填写；本地连接abk_ Key通过connect_astrbot.py保存，两者不能混用。Tailscale需要用户自己的登录和Funnel授权，本项目不提供公网服务器。
