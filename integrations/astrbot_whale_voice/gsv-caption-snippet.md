# GPT-SoVITS 语音原文兼容片段

2026-10-25。只保存合成器实际使用的`GSVRequestResult.text`与音频，不额外识别、不用相邻聊天文字猜原文，不恢复已经删除的Windows女声插件。

安装插件`astrbot_plugin_GPT_SoVITS/main.py`中，将静态方法`_to_record(res)`签名改为`_to_record(res, event=None)`，在方法开头加入：

```python
if event is not None:
    try:
        from app.voice_captions import remember_plugin
        remember_plugin(event, res)
    except Exception as exc:
        logger.warning("鲸聊语音原文未保存（%s），保留音频发送，不编造转写", type(exc).__name__)
```

同文件三处调用统一为`self._to_record(res, event)`（自动结果、说/gsv命令、gsv_tts工具）。其余逻辑不动，尤其保留语音许可检查和冷启动等待。

项目独立模块`app.voice_captions`保存字幕：有效主人鲸聊任务/许可/会话校验→规范PCM→按(task_id, sha256)绑定实际合成文字。不包含语音合成工具，也不调用Windows语音函数。

worker现有`known_text`回填该片音频的原文，reply_outbox→whale_messages→前端语音气泡既有“查看文字/收起文字”。文本通过textContent呈现，默认收起，不额外发一条文字消息。无对应原文的旧语音不猜、不重发、不自动转写。

插件升级可能覆盖片段；升级后核对三处调用，不能为了字幕而恢复被移除插件。验证使用隔离数据库和音频，不能伪造生产主人任务或许可。

## 独立环境修复

AstrBot自有venv没有imageio_ffmpeg，首次上线保存字幕因导入失败，之前项目环境测试未覆盖。`audio_sender.ffmpeg_exe`现优先选择已部署且校验来源的`tools/gpt-sovits/ffmpeg-shared/ffmpeg.exe`，两个环境使用同一二进制，保证规范音频散列一致；仅无此文件时才回退imageio_ffmpeg。已用AstrBot实际python和真实32kHz样音跑隔离许可/解码/原文匹配通过。474项检查（1跳过），插件与管理台已正常重载。

本次明确匹配的两条失败历史（seq58/59），从实际gsv工具参数及对应缓存音频恢复原文，使用原转换器核验散列后仅补字幕元数据；不重发、不改发送状态。没有依据的旧语音仍不补造。重新打开App读取历史后显示。

AstrBot原生Windows重启会创建可见控制台，已将该安装的`core/process_restart.py`替换进程标志改为CREATE_NO_WINDOW并保存备份；升级后复核，避免恢复黑色窗口。
