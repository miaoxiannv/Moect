[![Moect 会话、朋友圈与表情包，本地珍藏](docs/images/banner.png?v=5394f334b97e)](https://github.com/mofan6/Moect/releases/latest)

**Moect 是一个在 Windows 上读取、整理与导出微信聊天记录的桌面工具。**

把好友和群聊、朋友圈与表情包留在自己的电脑里，随可用的头像、图片、视频和表情一起归档。导出的 HTML 可以离线阅读、搜索、按时间筛选，也可以展开合并转发。

八种可记忆的主题色、深浅色外观与鸿蒙字体，以及熟悉的会话与任务布局。一次选择一个会话，或把多个会话一起保存下来。

**[下载一键安装包](https://github.com/mofan6/Moect/releases/latest)** · **[使用指南](docs/usage.md)** · **[更新记录](CHANGELOG.md)** · **[反馈问题](https://github.com/mofan6/Moect/issues)**

[![最新版本](https://img.shields.io/github/v/release/mofan6/Moect?color=8771cf&labelColor=282333&style=flat-square)](https://github.com/mofan6/Moect/releases/latest)
[![下载量](https://img.shields.io/github/downloads/mofan6/Moect/total?color=8771cf&labelColor=282333&style=flat-square)](https://github.com/mofan6/Moect/releases)
[![系统](https://img.shields.io/badge/Windows-10%20%2F%2011%20x64-8771cf?labelColor=282333&style=flat-square)](docs/usage.md)
[![许可证](https://img.shields.io/badge/license-GPL--3.0-8771cf?labelColor=282333&style=flat-square)](LICENSE)

## 1.2.8 更新

- 内置 109 种微信表情，支持会话预览、引用、转发，以及朋友圈正文与评论的 HTML 导出。
- 导出的内置表情可离线显示，保留原始文字和按表情名称搜索的能力。
- 改善近期图片采样与当前账号图片密钥查找，继续保留完整校验、超时和取消功能。

[查看完整更新说明](docs/release-1.2.8.md)

## 特性

![朋友圈与表情包：九宫格、互动与分类归档](docs/images/features-1.png?v=1.2.0)

![可搜索、按时间筛选的离线聊天归档](docs/images/features-2.png)

![多种导出格式与本地处理](docs/images/features-3.png)

| 你想做的事 | Moect 的方式 |
| --- | --- |
| 保存好友或群聊 | 点击会话预览，单独或批量导出 |
| 保存朋友圈 | 我的朋友圈、公共朋友圈、指定账号；时间筛选、九宫格、视频及可用的点赞评论 |
| 整理收藏表情 | 按收藏、自定义表情或已安装专辑批量导出，保留动画与离线索引 |
| 离线阅读 | HTML 对话气泡、头像、图片、GIF、音视频与分享卡片 |
| 找到某段记录 | 按发送者或正文搜索，按北京时间选择起止时间 |
| 保留转发和引用 | 引用回复、可展开的合并转发、独立转发记录页 |
| 整理和分析 | HTML、JSON、JSONL、TXT、CSV、Markdown、Excel 七种格式 |
| 保存压缩归档 | 可选 ZIP 与 AES-256 加密，选择和密码可记忆 |
| 管理缓存 | 自选缓存位置、查看占用和清理过期快照 |
| 管理导出任务 | 分类查看会话、朋友圈和表情包任务，多选删除或清空记录 |
| 跟随个人偏好 | 八种主题色、窗口位置与尺寸、会话排序自动记忆，HTML 随主题导出 |
| 接收软件消息 | Markdown 公告、已读记忆，以及软件内检查、下载和安装新版本 |

媒体能否保存取决于本地缓存、可用凭据及原始文件。没有取得的附件会显示具体状态；链接、小程序与转账卡片保留可用信息，不保证还原远端已失效的内容。卡片样式更新需要重新导出。

导出时可单独跳过图片/动画表情、视频、语音和文件内容，也可使用快速导出跳过所有资源及头像。消息和已有元数据仍会保留；这些设置同样作用于引用回复和合并转发。系统消息与头像可独立选择。

朋友圈使用本机已缓存内容。先在微信手动浏览目标动态，再回到 Moect 刷新资源快照；软件不会替用户操作微信或自动翻阅朋友圈。语音在本机转换为可直接播放的 WAV，无需在线转码。

## 快速开始

1. 下载 [Moect 一键安装包](https://github.com/mofan6/Moect/releases/latest)，双击后选择安装位置及是否创建桌面快捷方式，再点击“安装”。开始菜单入口会自动创建；缺少 WebView2 时会自动准备运行环境，此时需要联网。
2. 打开并登录电脑微信，然后启动 Moect，点击“连接微信”。也可以打开已有解密备份，或先体验虚构的演示数据。
3. 点击会话查看记录，选择导出格式、时间范围及可选的压缩加密设置，创建任务。
4. 导出完成后，在任务页面直接打开文件。HTML 打开即进入对话，批量导出可在顶部切换会话，顶栏可收起或展开。短消息气泡随内容收紧，长链接自动换行。

完整保留导出文件夹里的 HTML、消息分片和资源目录，才能在离线电脑上继续查看媒体。

## 运行环境与数据

- Windows 10 1809 或更新版本、Windows 11，x64；需要 Microsoft Edge WebView2 Runtime。
- 面向 Windows 微信 4.x。微信版本更新可能影响适配，当前不承诺覆盖所有 4.x 版本。
- 聊天数据库与消息解析在本机完成。按需访问媒体及头像来源，项目没有接收聊天记录的服务器。
- 默认导出目录：`文档\Moect\exports`，可在设置中修改。升级沿用原有配置、任务、快照与媒体密钥缓存，卸载程序保留这些用户数据。

图片中的账号、会话、消息和山野插画均为虚构演示内容。

## 开发与构建

代码采用 Python 3.11、pywebview / WebView2、React / TypeScript。安装版已经包含 Python 和前端，无需另装开发环境。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
Copy-Item ui/icon.svg ui-qce/icon.svg
python build.py
# 安装 Inno Setup 6.5+ 后生成一键安装包
.\installer\build.ps1 -OutputDirectory .\release
```

详细说明见 [构建指南](docs/build.md)，宣传图的可重复生成脚本在 `docs/tools/render_assets.py`。

## 许可证

本项目按 [GPL-3.0](LICENSE) 发布。原始著作权与依赖许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES)。Moect 是独立的社区项目，与微信、腾讯及下列项目不存在官方隶属关系。

## 致谢

感谢以下开源项目及其作者和贡献者，为 Moect 提供了重要基础：

- **[QQ Chat Exporter](https://github.com/shuakami/qq-chat-exporter)**：Moect 直接复用和改造了其界面组件，并参考了会话管理、导出流程与聊天记录展示的设计。
- **[WeChatMsg](https://github.com/LC044/WeChatMsg)**：为微信聊天记录读取、解析与导出提供了重要基础和参考。
