# Open source attribution

## WeChat built-in text faces

The preset library covers the 109 built-in faces checked against the user's
WeChat panel and exported text tokens. `ui/wechat-faces.json` records each
source URL and SHA-256; the original PNGs come from Tencent's public
`res.wx.qq.com/t/wx_fed/we-emoji/res/v1.2.8/assets/` resources. Artwork ownership
remains with Tencent and its respective rights holders; see
`THIRD_PARTY_LICENSES/WeChat-built-in-faces-NOTICE.txt`.

The name/address table was cross-checked against the GPL-3.0 4.x adapter's
`app/DataBase/output_pc.py` at `ea4d3f295f767996883958cf88aa5f793cac4c75`.
Moect's renderer uses these preset files locally and copies only the faces
used by an HTML archive, leaving the original message text unchanged.

The announcement reader uses react-markdown and remark-gfm (MIT), with notices
for their added dependency tree in `THIRD_PARTY_LICENSES/frontend-online-dependencies.txt`.

This is a GPL-3.0 derivative application. It is not an official release of the projects below.

## QQ Chat Exporter

Source: https://github.com/shuakami/qq-chat-exporter

Snapshot: `4ae4b700939346b46b05b46346fb5c5c5f08b081`

License: GPL-3.0; copyright belongs to shuakami and the respective contributors.

Directly reused code in `frontend/` includes `components/ui/`, `hooks/use-session-filter.ts`, `lib/session-sort.ts`, `lib/session-dedupe.ts`, related utilities/types, and `styles/globals.css`. The application shell/overview/task row layout in `frontend/src/App.tsx` is adapted from `qce-v4-tool/app/page.tsx`. `window-controls.tsx` preserves the original visuals and replaces Tauri operations with the local Qt bridge. QQ-only resource/roaming actions are removed; message fetches use the local WeChat backend. Blur effects and idle rerenders are reduced for desktop performance. Original comments and author attributions are retained.

`core/streaming.py` ports the bounded buffered-writer algorithm from `qq-chat-export-core/src/stream_utils.rs`. `core/exporting.py` adapts its source/render separation and chunk rotation design. These portions are covered by the same GPL-3.0 license.

## WeChatMsg / WeChatMsg-4x

Original: https://github.com/LC044/WeChatMsg

Historical source: `2622c32b38d595c7b29356f2ed95a1870c9796f4` (GPL-3.0, SiYuan and contributors).

4.x adapter: https://github.com/chenanhan230-del/WeChatMsg-4x

Snapshot: `ea4d3f2`.

`wx4/` retains the adapter code. Local modifications include locale-independent process enumeration, explicit 64-bit Windows process/token API types and error handling (completed in 1.0.1), cooperative scan cancellation, committed-WAL handling, stale snapshot cleanup, and stricter integrity-check failure handling. The old PyQt UI is not part of this application's runtime.

## Runtime dependencies

Python, PySide6/Qt (including Qt WebEngine/Chromium), psutil, PyCryptodome, Zstandard, React, Vite, Tailwind CSS, Radix UI, Framer Motion/Motion, Lucide, date-fns, React Day Picker, Geist and other dependencies retain their respective licenses. The Python runtime is distributed by PyInstaller; third-party JS license details are in `frontend/package-lock.json` and installed package metadata. Qt LGPL/GPL and Python license texts are included with the binary distribution where provided by their packages. Source for this derivative application is supplied alongside the binary archive.

## Additional desktop components

The Windows host uses pywebview (BSD), pythonnet (MIT), clr-loader and the Microsoft WebView2 SDK/runtime. License files are included under THIRD_PARTY_LICENSES. HarmonyOS Sans SC is bundled from the locally installed original font at the user's explicit request; original font copyright/license metadata is retained in THIRD_PARTY_LICENSES/HarmonyOS-Sans-metadata.txt. The Violet Conversation SVG was supplied by the user.

## Media and native-frame corrections (2026-10-31)

- QCE resource indexing and structured reply design: https://github.com/shuakami/qq-chat-exporter/blob/master/qq-chat-export-core/src/resource_index.rs and reply_preview_renderer.rs / reply_render.rs. The Python implementation keeps WeChat resource IDs and credentials separate from QQ/NapCat APIs.
- WeChat resource-schema cross-reference: https://github.com/jackwener/wx-cli-again/blob/main/src/attachment/resolver.rs (ChatName2Id, message_local_id and message_create_time).
- DAT format and WXGF/HEVC format cross-reference: https://github.com/hicccc77/WeFlow/blob/main/electron/services/imageDecryptService.ts. No closed native decryption component is bundled.
- Native draggable regions: https://github.com/MicrosoftEdge/WebView2Feedback/blob/main/specs/IsNonClientRegionSupportEnabled.md.
- Pillow 12.0.0 and imageio-ffmpeg 0.6.0 provide format validation and local codec conversion. Their license notices are included. The FFmpeg binary distributed with imageio-ffmpeg is bundled without modification; upstream build/source information: https://github.com/imageio/imageio-ffmpeg .
- pysilk-mod 1.6.4 provides local Tencent SILK decoding for browser-playable PCM WAV exports. Source: https://github.com/DCZYewen/Python-Silk-Module . Its BSD/MIT notices are retained in THIRD_PARTY_LICENSES/pysilk-mod-LICENSE.txt; the included Skype SILK SDK notice is retained separately. No audio is sent to an online conversion service.

## Account resource format references

`core/sns_codec.py` implements Bob Jenkins' public-domain ISAAC-64 algorithm (1996), using the standard reference at https://burtleburtle.net/bob/c/isaac64.c . The original declaration and attribution are retained in THIRD_PARTY_LICENSES/ISAAC64-NOTICE.txt. SNS uses reverse word order with big-endian bytes. Fixed reference outputs, encrypted synthetic PNGs spanning multiple result blocks, and MP4 header recovery are covered by tests.

The independent account sticker and scoped Moments readers were cross-checked against CN-Grace/Wechat-Emoticon-Parser (`v4.0-plus`, MIT; notice retained in THIRD_PARTY_LICENSES/Wechat-Emoticon-Parser-LICENSE.txt), Rikkkum/wx_emoji_windows, liusheng22/export-wechat-emoji, bojieli/greenbubbles and sgaofen/murmur. These references document database identities, cache layout, AES derivation and TimelineObject fields. The Python implementation uses this application's existing read-only archives and local resource pipeline. WeFlow was consulted for feature feasibility; no WeFlow implementation or native binary is incorporated. Details and links: docs/account-resource-export.md.

## Public release packaging

HarmonyOS Sans SC is distributed unmodified under the HarmonyOS Sans Fonts License Agreement, included in THIRD_PARTY_LICENSES/HarmonyOS-Sans-LICENSE.txt. Official license source: https://github.com/openharmony/global_system_resources/blob/master/LICENSE_Fonts . Its use is disclosed in the About page. The font is bundled with the software and is not offered as a standalone font download.

The installer uses Inno Setup, with its ChineseSimplified.isl translation from https://github.com/jrsoftware/issrc/blob/main/Files/Languages/ChineseSimplified.isl . Original translation credits are retained. The Microsoft-signed WebView2 bootstrapper is obtained from Microsoft and only runs if the runtime is absent; Microsoft license terms apply. Documentation graphics are composed from the modified Moect UI with fictional data and a locally authored SVG landscape.

## Portable archive calendars and themes

The shared calendar/time custom element is implemented in this repository and bundled inline with exported HTML for offline use. Archive colors use the palette selected in Moect when the export task is created. The experimental Moments UI automation has been removed; the application does not include or use a Windows UI Automation library for Moments.


## Export libraries

XlsxWriter 3.2.9 creates streaming XLSX records under its BSD-2-Clause license. pyzipper 0.3.6 creates AES-encrypted ZIP archives under its MIT license. Their original license texts are included in THIRD_PARTY_LICENSES. Archive password memory uses Windows DPAPI for the current Windows user.
