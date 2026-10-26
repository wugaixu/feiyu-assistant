# 第三方来源、边界与许可

原创编排代码按根目录MIT许可；下列组件保持各自许可，不能把整个依赖栈或声音/图片模型统一标成MIT。

| 组件 | 来源/版本 | 分发方式与许可 |
|---|---|---|
| 抖音聊天SDK | https://github.com/zhinjs/douyin-im ，npm1.1.0；原验证提交f6eee669c39094224e00c6b877e1964d9da4e393 | 外部安装，MIT；保留third_party/douyin-im.LICENSE |
| 独立鲸聊网关/Android | 用户自制dsh-wechat-chat 1.7.1基础上的改造 | MIT，网关原LICENSE保留；不是腾讯微信实现 |
| AstrBot | https://github.com/AstrBotDevs/AstrBot ，4.29.1 | 独立安装，按上游AGPL-3.0；未打包引擎/用户数据 |
| GPT-SoVITS插件 | https://github.com/Zhalslar/astrbot_plugin_GPT_SoVITS ，v3.1.1 | third_party包含修改版可用源码与完整AGPL-3.0 LICENSE；鲸聊守门/冷启动/字幕修改见MEDIA_MODELS |
| 可选GPT Image回归基线 | https://github.com/starmiaoa/astrbot-plugin-gpt-image ，bd7adb7db539c3223b4ad1bdaad9a309ab2cca43 | third_party/gpt_image_upstream含MIT源码/许可，仅用于适配回归，不自动安装、不含API Key |
| GPT-SoVITS引擎 | https://github.com/RVC-Boss/GPT-SoVITS ，48b1a0169a28582a8984402f82cf438d3bfa6aca | 外部安装，遵守上游代码及其依赖条款 |
| OhWhale音色参考 | https://github.com/chauko0423/ohwhale-deploy ，8988070a1102bebb195f622c995d77207b183672 | 仅来源/散列清单；不再分发模型、参考录音、表情包或人物图层；素材权利见上游assets/ASSETS.md |
| 小叽可选音色 | https://huggingface.co/chenxin199305/Chobits-Chii-TTS-GPTSoVITS | 不打包；其CC BY-NC-SA 4.0独立适用 |
| ASR | https://github.com/k2-fsa/sherpa-onnx ，sherpa-onnx-node1.13.8 | 独立安装；模型由manifest指定固定修订，分别按发布源许可 |
| DouK | https://github.com/JoeanAmier/TikTokDownloader ，5.8 Windows | 外部安装；不附二进制或Cookie，遵守其GPL及发布页说明 |
| 生图 | https://github.com/Comfy-Org/ComfyUI ，https://github.com/city96/ComfyUI-GGUF | 外部安装；GPL等各自条款；模型单独下载 |
| 参考适配代码 | vendor/douyin-cli、vendor/dsh-douyin-agent | 保留原LICENSE，仅参考/有限兼容代码；不是替代本项目独立网关 |
| Android依赖 | AndroidX、ZXing、uCrop、OkHttp、Kotlin | Gradle声明外部依赖，按各自Apache等许可；不分发签名私钥 |

本项目的APK图标来自项目维护者提供的AI图像；不包含主人的真人头像、手机截图或聊天资料。用户自己安装的记忆、搜索、点歌、表情包等第三方插件和个人配置没有随公开包导出；可以自行安装，不承诺其与当前AstrBot版本全部兼容。

公开发行包不包含账号、API Key、Cookie、配对凭据、登录profile、私信数据、录音、模型权重或第三方付费额度。API与网页自动化仅在部署者自己的授权范围内使用。
