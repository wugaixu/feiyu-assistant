# 本地 GPT-SoVITS 语音

## 当前音色：大肥鱼（2026-10-25 晚更新）

已按主人要求接入 OhWhale 的声音模型：原版 `s1v3.ckpt` + `dafeiyu_e14_s280.pth` + 9.5秒中文参考音频。文件在运行目录 `tools/gpt-sovits/app/voices/dafeiyu`，`source.json`记录固定来源及权重散列；未执行参考项目安装脚本，也未安装QQ通话模块。

`gpt-sovits.json` 的 `voice_id=dafeiyu` 决定新服务启动加载的音色；仅支持登记的 `chii` / `dafeiyu`，不能从聊天传入任意权重路径。当前插件模型、默认参考及三个情绪条目均已同步为中文大肥鱼。原小叽文件和配置备份保留在运行目录 `backups/dafeiyu-20261025`。切回音色必须同步服务配置和插件配置，在服务空闲时加载，不能只改显示名字或对生产服务随意切权重。

采用语音消息参数：`prompt_lang=zh`、`text_lang=zh`、`temperature=0.7`、`top_p=0.9`、`top_k=5`、`speed_factor=1.0`、`repetition_penalty=1.35`、`cut5`、`batch_size=1`、`parallel_infer=false`、`streaming_mode=false`、WAV、`seed=42`、停顿0.3秒。temperature/top_p参考作者离线验收，top_k=5沿用本地稳定设置；不是声称完整照搬所有参数。三个情绪条目使用同一参考录音和正常语速，不把调速称为不同情感模型。自动转语音概率仍为用户配置的0；AI可在有效鲸聊许可下使用`gsv_tts`，不额外增加随机语音调度或模型调用。

架构保持：AstrBot决定说什么并调用工具 → 独立本机GPT-SoVITS服务 → 插件返回真实Record → 原音频校验/队列 → 原鲸聊设备。核心模型服务已独立于AstrBot；插件只是工具与附件适配，当前无需另造发送通道。无需实时通话、虚拟声卡或QQ依赖。

本机隔离测试3句均生成非静音、可解码32kHz单声道WAV，3.26/6.84/6.98秒，CPU生成约8–10秒；SenseVoice复核核心文字完整，第二句开头“哼”未识别。正式9880接口在插件重载后也生成样音，通过API生成不等于手机已收到。样音和记录：运行目录`research/gpt-sovits/dafeiyu`；471项Python检查（1跳过）、16项UI检查通过。旧管理台未响应本次wake，最初用WMI隐藏启动绑定管理台PID完成接口验证；随后依主人既有授权正常重启管理台，配置散列不变，已确认新管理台收到wake后自动启动服务、消耗唤醒文件，监听进程所属链为管理台→venv启动器→推理进程，重启后再次合成通过。当前可用性返回`gsv_available=true`与大肥鱼名称。保留原5分钟空闲退出机制，本次未重新等待完整5分钟退出周期。

手动试听仍用`语音试听.vbs`（桌面旧快捷方式名“小叽语音试听”），页面按实际加载音色显示名称。按需/5分钟空闲释放保留。模型素材使用范围见[参考项目资源说明](https://github.com/chauko0423/ohwhale-deploy/blob/8988070a1102bebb195f622c995d77207b183672/assets/ASSETS.md)；原声音来源仍待作者补充，不默认套用代码MIT许可。

以下小叽部署记录为保留音色的历史说明，当前默认参考语言以本节为准。

原目录 `D:\Anaconda\Githubfiles\So-VITS-SVC` 是歌声/录音音色转换程序，不是 GPT-SoVITS；本次未修改它。

引擎、独立 Python 3.12 环境、基础模型和缓存：
`__RUNTIME_ROOT__\tools\gpt-sovits`

- 官方源码：https://github.com/RVC-Boss/GPT-SoVITS ，固定提交 `48b1a0169a28582a8984402f82cf438d3bfa6aca`（音色作者的验证版本）。
- 音色：https://huggingface.co/chenxin199305/Chobits-Chii-TTS-GPTSoVITS 。小叽 / Chii，日语训练、v2Pro 全量微调，CC BY-NC-SA 4.0；权利与使用范围以音色仓库声明为准。模型内附 README 与参考录音。
- 下载固定到仓库提交，LFS 文件校验 SHA256；清单 `tools/gpt-sovits/download-manifest.json`。
- Torch 2.11.0+cu128、独立推理依赖；没有改动原 Anaconda/SVC 或 ComfyUI 环境。
- 当前使用 CPU，避免与已有 8GB 显卡上的生图任务争用显存。语音服务配置为 `gpt-sovits.json`，保留当前按需唤醒和空闲5分钟释放设置。

## 使用

管理台运行时，桌面“小叽语音试听”或项目 `语音试听.vbs` 会写入短期唤醒信号，等服务就绪再打开页面。无需 AstrBot 或其他聊天模型生成文本。后端仅监听 `127.0.0.1:9880`，页面 `/voice/`；不会通过鲸聊网关公开。

AstrBot 的 GPT-SoVITS 插件已配置小叽的两份权重、日语参考音频/文字、中文输出、非流式 WAV 和180秒请求超时。自动转语音概率保持用户页面值（部署时最新为0），可通过 `gsv_tts` 工具主动使用语音。语音只走鲸聊，抖音原生语音发送仍未实现；公开评论/自主决策保持文字格式。没有恢复已经移除的系统女声备用插件。

管理台调用 `app.gpt_sovits.ensure` 按需求启动服务，自己启动的服务由它结束；手动加载进程也绑定管理台PID，管理台退出或更换后自动结束，不在系统开机自启。

## 验证和兼容

API 已生成可解码32kHz单声道WAV，中文样音约3秒，首次实测合成约11秒；耗时随CPU负载变化。现成日语音色跨语言说中文的发音、相似度需用户试听，不承诺每字识别准确。手动Gradio页面也已完成实际生成验证。

TorchCodec 使用独立下载的 FFmpeg 8.1 shared DLL，来源和散列见 `ffmpeg-source.json`。OpenJTalk 使用相对词典路径，避免 Windows 中文路径导致 Mecab 初始化失败。没有修改系统 PATH 或执行策略。

回归日志：运行目录 `tests/gpt-sovits-deploy-20261025.log`；样音、转写与安装日志：`research/gpt-sovits`。没有伪造主人任务或为验收主动发送私信。
