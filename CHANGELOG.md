# 1.2.8（2026-11-10）

- 预置微信 109 种内置表情，覆盖会话预览、引用、转发及朋友圈正文；会话和朋友圈 HTML 导出保存对应图标，朋友圈评论也支持，离线查看时保留原始文字与搜索。
- 改善大量历史附件下的图片采样，目录扫描超时后保留已经找到的可用样本。
- 新增当前账号配置派生图片密钥的路径，继续使用完整 AES 校验并保留原有搜索回退。
- 修复首个图片目录为空或没有 V2 图片时的采样遗漏，为配置搜索的跨内存块回查预留时间。
- 感谢 [Irk2wd](https://github.com/Irk2wd) 提交的 [PR #9](https://github.com/mofan6/Moect/pull/9)。

# 1.2.7（2026-11-07）

- 会话与朋友圈新增可取消的后台“恢复图片读取”，使用当前账号近期缓存交叉验证，恢复后自动刷新预览。
- 会话手动重试补查同账号缓存与附件，聊天表情复用表情包已有的本地缓存和解码逻辑。
- 朋友圈图片导出补充近期样本，区分未缓存、缺少图片密钥、文件损坏及格式限制，并在资源明细中记录具体原因。
- 恢复任务与退出登录、账号切换、存储迁移及软件更新互相协调，未通过完整验证的图片与表情密钥不会保存。

# 1.2.6（2026-11-07）

- 修复补查本地保留会话时，部分消息缺少类型名称导致连接微信失败的问题。
- 修复图片预览“重试读取”未重新获取解密材料的问题；结合近期缓存样本严格校验图片密钥，改善新旧图片共存时的识别。
- 补齐多种原图、高清图和缩略图命名的查找；原图不可用时显示、导出已缓存的可用缩略图，并保留原图状态说明。

# 1.2.5（2026-11-06）

- 会话增加好友、群聊、公众号 / 服务号和其他会话分类，支持按分类选择和批量导出。
- 会话预览按需读取本机可解码的图片、表情与语音，图片支持放大查看。
- HTML 图片支持原页放大、缩放与切换，补充任意页码输入跳转，表情优先显示主图。
- 新增 Excel 导出，保留大整数消息 ID，支持长消息分段与大记录量分表。
- 新增可选 ZIP 和 AES-256 加密 ZIP，首次默认关闭，偏好与受 Windows 保护的密码可记忆。
- 增加导出体积、媒体分类占用与明确范围的源数据库体积统计。
- 增加缓存迁移、占用统计、过期快照与可再生成缓存清理，以及缓存位置失效后的恢复入口。
- 连接失败细分阶段和原因，可导出脱敏诊断信息；手动选择支持数据根目录、账号目录及 db_storage。
- 支持不限会话数量的排队导出、已删除好友的本地保留记录、打印与 PDF 保存、中文及特殊字符路径。
- HTML 图片打开时自动适应窗口，支持鼠标位置锚定缩放、拖动及图片切换。
- 新增圆形扩散亮暗切换，统一更新、公告和主题按钮的尺寸、间距及悬浮动效。
- 设置页采用独立分区卡片，优化窗口默认尺寸、最小宽度、弹窗和导出设置布局。
- 修复部分文本消息识别错误及引用正文预览重复，改善 SILK 语音转码、任务生命周期、缓存读写与异常恢复。

[完整更新说明](docs/release-1.2.5.md)

# 1.2.0（2026-11-02）

- **朋友圈导出**：支持我的朋友圈、公共朋友圈和指定账号，按昵称、微信号或 UID 选择账号，可按时间范围预览与导出 HTML、JSON。
- **朋友圈离线相册**：图片采用九宫格排布，支持本地视频；点赞、评论可展开或收起，展示可用的昵称和头像，点击点赞头像可查看姓名。导出后仍可按时间、作者和关键词筛选。
- **批量导出表情包**：按收藏、自定义表情与已安装专辑选择分类，保存图片及动画，并生成离线索引和资源明细；支持尝试缓存已有下载地址对应的表情资源。
- **语音离线播放**：导出时在本机将 SILK 语音转换为可直接播放的 WAV，随聊天归档一同保存。
- **公告与在线更新**：启动联网检查公告和新版本。公告支持 Markdown、图片、链接，阅读两秒后确认并记住已读状态；新版本通过下载图标右上角的主题色圆点提示，点击后查看详情、下载并更新重启。安装版与便携版分别使用对应的更新包。

## 使用体验

- **八种主题色**：鸢尾紫、晴空蓝、青瓷青、松叶绿、暖杏橙、蔷薇粉、朱砂红、雾山灰，搭配浅色与深色模式，选择自动记忆。新导出的聊天、转发、朋友圈和表情包 HTML 跟随导出时的主题。
- **动画新手教程**：首次启动展示入门教程，朋友圈和表情包也有独立的首次使用教程；设置和对应页面保留重播入口。
- **账号状态管理**：首次启动手动连接，之后可自动重新连接并整理上次账号。新增退出登录入口，会话和概览回到初始状态，任务与导出文件保留；演示模式可直接返回先前账号。
- **任务分类与批量管理**：支持全部、会话、朋友圈、表情包分类，多选删除记录或清空当前分类；导出文件与进行中的任务保留。
- **更顺手的时间筛选**：软件和新导出的离线 HTML 使用一致的日历、小时及分钟选择方式，确认后应用。
- **更方便的 HTML 阅读**：默认定位最新聊天记录，底部数字页码可直接跳转，每组最多十页；系统提示以居中小字展示，图片、动画表情和语音采用更简洁的消息样式。
- **更清晰的归档命名**：新导出文件夹按昵称、UID 和时间命名。
- **窗口与浏览偏好记忆**：记住窗口尺寸、位置、最大化状态及会话排序；侧栏更加紧凑，滚动条跟随主题色。

# 1.1.5 (2026-10-31)

- 修复微信数据库中 TEXT 类型字段包含非 UTF-8 二进制摘要时，联系人及头像读取失败的问题。二进制内容保留原始字节，正常文字仍按 UTF-8 解码。
- 接通导出内容设置：系统消息、快速导出、图片/表情、视频、语音、文件元数据及头像。
- 跳过操作在复制、下载、解密和转码前执行，并覆盖引用与合并转发；六种导出格式均保留消息及已有元数据。
- 在聊天归档、资源明细与任务中分别显示主动跳过状态，不将其计作资源错误。
- 短消息气泡随内容自适应，修复预览长链接溢出；HTML 顶栏支持折叠/展开并保留筛选条件。
- 便携包移除使用说明、修复说明文件，相关文档保留在项目源码中。

# Moect 1.1.0 · 首次公开发布（2026-10-31）

- HTML 归档入口直接打开聊天记录，批量导出在聊天页顶部切换会话。
- 新增中文一键安装器：按当前用户安装、创建桌面/开始菜单入口、检测 WebView2，缺失时通过 Microsoft 官方引导程序安装。
- 发布紫色主题项目首页、四张原创宣传图、使用与构建指南。
- 保留媒体、合并转发、时间筛选、任务管理、鸿蒙字体和已有数据兼容逻辑。
- 发布前完成静态检查、前端构建及两级打包；未执行真实微信导出或安装流程测试。

# Moect 1.1.0 · branding and cards (2026-10-31)

- Rename application/UI/export identities and Windows executable metadata to Moect; use the user-owned GitHub project in the sidebar/About buttons.
- Preserve existing account state/media keys and historical file paths; use the Moect default export folder for new exports.
- Add compact native-style link/article/mini-program, file and location cards with local thumbnails/source icons; add amount/status transfer cards without payment-action credentials.
- Keep static-only post-change verification at the user's request.

# 1.1.0 (2026-10-31)

- Hide duplicate image/emoji attachment filenames; preserve click-to-open media and on-disk originals.
- Parse bounded recordinfo/dataitem forwards with source names, timestamps, nested records and available attachments; create linked offline HTML pages plus complete JSON/TXT copies. Preserve forward details in all supported formats and expand them in the desktop preview.
- Add offline HTML time-range filtering/reset and bounded chunk caching/time indexes.
- Open session previews by row click/keyboard, preserving independent export actions and batch selection.
- Centralize version 1.1.0 for state/export metadata and the About view; update package versions and Windows EXE version resources.
- Static Python/JS/TS checks and builds only after implementation, per the user's request.

# 1.0.1

- Fix `argument 1: OverflowError: int too long to convert` at `OpenProcessToken`: all process/token APIs now use explicit pointer-width signatures and thread-local Windows error handling.
- Keep missing debug privilege as a normal boolean result; ordinary same-user process reads can continue without it.
- Add four native ABI regression tests using only this process and synthetic data.
- Restore DWM native rounded corners/shadow by preserving the native frame capabilities while handling `WM_NCCALCSIZE` for the custom QCE title bar.
- Preserve native edge resizing and the monitor work area when maximized. Windows uses square corners while maximized and rounded corners after restoring.

# 1.0.0

Initial desktop integration of the QCE interface and WeChatMsg 4.x backend.

## 1.0.1 same-version correction (2026-10-31)

- WebView2 per-monitor rendering, embedded HarmonyOS Sans SC, 14px sidebar.
- Supplied Violet Conversation icon and matching purple themes.
- Contact/profile avatars, portable local media, link and mini-program metadata.
- QCE task/overview rows, conversation names, file/folder actions and record deletion.

## 1.0.1 native window and media correction (2026-10-31)

Native WebView2 drag regions and resize gutter; ChatName2Id/MessageResourceInfo joins; PKCS#7 AES and independent XOR; WXGF image decoding, GIF/CDN handling, H.264/AAC normalization and structured replies. Static-only review/build at user request; re-export is required.

## 1.0.1 strict image-key and XOR correction (2026-10-31)

Validate the full AES segment, PKCS#7 and declared plaintext length before accepting cached/scanned keys. Prioritize mutable private memory and share scan time across processes. Recover and validate independent XOR tails; bound WXGF HEVC candidate decoding; validate JPEG pixels and GIF frames. Prefer HD originals, preserve decrypted WXGF with a PNG preview, expose credential-free failure diagnostics and export-stage progress. Static checks/build only; no live or synthetic decryption tests were run.

## 1.0.1 JPEG trailer and emoticon recovery correction (2026-10-31)

Handle valid JPEGs with trailing WeChat cache metadata by walking raster boundaries before full image verification. Support the observed emoticon CDN host, retry failed inherited proxy connections directly, and keep TLS validation. Add same-account emoticon.db metadata, business/emoticon caches and alternate emoji URL fields. Reuse strictly validated CDN AES decoding for local/remote encrypted resources and preserve GIF animation. Add credential-free network diagnostics. Existing failed resources were diagnosed read-only; the changed application receives static checks/build, without an end-to-end export/UI test at the user's request.
