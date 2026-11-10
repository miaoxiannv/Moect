# 留白 · Moect 二开版

基于 Moect 的 Windows 微信信息工作台。连接本机微信后，可选择会话、设置模型接口与日期范围，生成每日简报、群聊重点和由聊天记录提取的待办，并回看来源。自动分析启用后每小时补齐，失败或中断后可从已保存的进度继续。

## 使用

解压完整的便携版目录，运行 `Liubai.exe`；同目录的 `LiubaiCollector.exe` 提供本地采集和分析服务。连接微信、选择分析范围并配置兼容 OpenAI 的模型 API 后，手动分析或开启每小时自动分析。模型分析会将所选会话中日期范围内的部分消息发送到所配置的服务，并可能产生费用。请自行确认服务商的数据处理方式。

Windows 计划任务仅在启用自动分析时创建，关闭时移除。关机、休眠或离线时任务不会即时执行，恢复后尝试补跑。模型可能漏判或误判；请通过来源核对，待办不会自动完成。

## 开发与构建

前端：`cd frontend && npm ci && npm run build`。构建后的 `ui-qce` 需要保留 `icon.ico` 和 `icon.svg`（Vite 会清空输出目录）。

安装根目录及 `liubai_collector/requirements.txt` 的依赖，以及 PyInstaller，然后在 Windows 上运行 `python build_liubai.py`。最终产物位于 `dist/Liubai/`，整个目录应一起分发。源码运行时可另行运行 `python liubai_collector/api.py`。

本版停用 Moect 官方自动更新，避免官方安装包覆盖二开功能。源码继承上游 GPL-3.0 许可，第三方许可见 `THIRD_PARTY_NOTICES.md` 和 `THIRD_PARTY_LICENSES`。本机密钥、快照、分析结果和聊天记录不应提交或打包。
