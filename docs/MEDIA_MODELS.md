# 本地音频、语音与图片模型

## ASR（接收语音/视频音轨）

来源：[sherpa-onnx SenseVoice](https://github.com/k2-fsa/sherpa-onnx)，INT8模型仓库与固定修订见 `deploy/sensevoice-assets.json`。原环境使用 `sherpa-onnx-node@1.13.8`；安装辅助脚本会核验模型和tokens的SHA256。CPU推理，无需在线API或DSH模型。

录音和视频音轨经FFmpeg转换成16kHz单声道PCM，最多600秒，30秒分块。结果含文本和模型估计标签；分块时间不是词级对齐，也未进行BGM歌词/旁白分离。不要把空转写当成视频完全没有声音。

## GPT-SoVITS（鲸聊与QQ发语音）

与ASR相反，这是把模型给出的**原文**合成为声音。流程：AstrBot调用 `gsv_tts` → 本机9880 → 真WAV附件 → 音频规范化与散列 → 原设备的鲸聊历史；原文随音频元数据提供“查看文字”，不再做一轮ASR猜原文。

已验证的引擎来源：[RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)，提交 `48b1a0169a28582a8984402f82cf438d3bfa6aca`。在 `<runtime>/tools/gpt-sovits/venv` 建独立Python3.12环境，源码在 `app/`，按该修订的官方依赖/预训练模型说明安装。需要Torch、语言处理依赖、中文RoBERTa、中文HuBERT、分词/音素资源、FFmpeg shared及可用OpenJTalk字典；不要只下载两个权重就认为完整。

本项目包装器使用以下目录：

```text
tools/gpt-sovits/
  venv/Scripts/python.exe
  app/api_v2.py
  app/GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/
  app/GPT_SoVITS/pretrained_models/chinese-hubert-base/
  app/voices/dafeiyu/s1v3.ckpt
  app/voices/dafeiyu/dafeiyu_e14_s280.pth
  app/voices/dafeiyu/ref_audio.wav
  app/voices/dafeiyu/ref_text.txt
  ffmpeg-shared/ffmpeg.exe      同版本shared DLL也放此处
  nltk-data/
```

大肥鱼声音来源：[OhWhale部署项目](https://github.com/chauko0423/ohwhale-deploy)，参考修订 `8988070a1102bebb195f622c995d77207b183672`。权重下载地址和SHA256见 `deploy/dafeiyu-assets.json`。参考录音为该修订 `assets/voice/default.wav`，自行下载并改名为 `ref_audio.wav`，将其准确对应的参考文字保存为UTF-8 `ref_text.txt`。不要用一段新文字冒充参考录音内容；原项目参数/配置提供该文字。

**音色素材没有随本仓库再分发。** 上游 `assets/ASSETS.md` 仍区分维护者许可说明与待补齐来源，不应把声音模型简单标成“MIT”。另一个可选小叽模型仓库为 `chenxin199305/Chobits-Chii-TTS-GPTSoVITS`，其CC BY-NC-SA 4.0条款独立适用；不是当前默认音色。

先修改 `<runtime>/gpt-sovits.json`：`enabled=true`、`voice_id=dafeiyu`、`idle_minutes=5`。执行生成目录 `语音试听.vbs`，验证独立服务确实能合成可播放音频，再配置AstrBot插件。系统只在短期需求下唤醒，连续空闲5分钟退出；端口可连不等于模型已经推理成功。

本发布附修改版 `third_party/astrbot_plugin_GPT_SoVITS`（v3.1.1，上游AGPL-3.0），包含鲸聊许可、等待冷启动、原文字幕兼容；市场更新后要复查这些修改。用安装辅助脚本复制后，在AstrBot启用它，并设置：

- `client.base_url=http://127.0.0.1:9880`，超时180秒。
- model的GPT/SoVITS路径指向上述两份文件；default_params参考录音/文字指向同一音色。
- `prompt_lang=zh`、`text_lang=zh`、非流式WAV、`cut5`、`top_k=5`、`top_p=0.9`、`temperature=0.7`、`speed_factor=1.0`、`repetition_penalty=1.35`、`seed=42`、停顿0.3秒。
- `auto.tts_prob=0`，先让AI按需调用工具，不做随机每条转语音。三个情绪槽如果用同一参考，不能声称是三个独立情感模型。

只给核验鲸聊轮次开放真实语音附件，抖音/公开评论/自主任务不支持原生audio。**不安装历史Windows系统女声插件。** 如果AstrBot原生重启在Windows弹出终端，检查其 `core/process_restart.py` 是否使用 `CREATE_NEW_CONSOLE`；改成 `CREATE_NO_WINDOW`（先备份）并按官方流程重启。不要为隐藏终端强行关闭正在生成的模型进程。

## ComfyUI + Qwen 本地生图

后端：[ComfyUI](https://github.com/Comfy-Org/ComfyUI)；GGUF节点：[city96/ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)。代码的workflow使用 `UnetLoaderGGUF`、`CLIPLoader(type=qwen_image)`、`TextEncodeQwenImage21`、`EmptyLatentImage`、`KSampler`、`VAEDecodeTiled` 等节点；先检查安装版本确实提供这些节点，再启用聊天插件。

安装在 `<runtime>/tools/local-image/ComfyUI`，独立venv位于同层 `venv`。Torch/CUDA选择按自己的显卡及ComfyUI官方要求，不把原机器8GB显存参数当成所有设备都适合。模型清单 `deploy/qwen-image-assets.json` 含下载来源、体积与SHA256，约11GB：

| 文件 | 放到ComfyUI下 |
|---|---|
| qwen_image_2.1-Q4_K.gguf | models/diffusion_models/ |
| qwen3vl_8b_w4a8.safetensors | models/text_encoders/ |
| qwen_image_2.1_vae_bf16.safetensors | models/vae/ |

链接失效或模型版本变化时，以仓库内容和散列核验，不能下载别的模型后只改文件名。模型与自定义节点许可按各自发布源。

先启动仅回环8188并验证一张图；再设置 `local-image.json` 的 `enabled=true`、`idle_minutes=10`，启用 `douyin_local_image` 插件。每个授权私聊轮次仅允许一批1–10张，实际串行生成、逐张入队；图片产生后能沿用原设备，不需要让另一个模型再描述文件路径。结果不明不自动再生成一次。

手动入口 `本地生图.vbs` → 8189门户 → 按需拉起8188；不需要聊天模型，也不使用模型API Key。参考图编辑需相应ComfyUI节点和模型工作流支持，不能因为有上传入口就声称所有模型都支持图生图。生图后端满5分钟先卸载模型，达到配置的空闲时间结束本程序持有的进程。

QQ通过OneBot record发真实音频，统一回复批次随后发实际合成原文；鲸聊仅气泡展开文字。抖音仍不支持原生语音外发。插件热加载须刷新共用caption模块，详见integrations/astrbot_whale_voice/gsv-caption-snippet.md。
