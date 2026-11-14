# 外部组件分发权审计 / Redistribution audit

2026-11-15，基于上游 `917145d4` 合并树（macOS 移植分支）。依据：
`tools/check_public_tree.py` 实跑（807 文件、0 错误）、`THIRD_PARTY_NOTICES.md`、
`fastboot/SOURCES.md`、`training/assets-manifest.json`、`training/third_party/source-manifest.json`、
`no-fastboot-install/software/dependencies.json`、git 追踪文件扩展名清单。

## A. 已随仓库发布的内容（源码树本身）

| 项 | 状态 | 依据/条件 |
|---|---|---|
| 新增主机代码/文档 | ✅ 可发布 | GPLv3（新代码）+ LICENSE-MIT-LEGACY（历史 MIT）双证框架 |
| `fastboot/assets/fastboot.bin`、`uart_boot.ino.uf2` | ⚠️ 条件发布 | 维护者明确指定公开的**仅此两个**历史启动资产，哈希钉 + `check_public_tree` 门禁；来源 ILCE-7M4-RE 许可非单一明确（MIT 惯例+可能 GPL-2.0 参考），必须保留 `fastboot/SOURCES.md` 溯源声明，不主张统一 MIT |
| `fastboot/source/`（payload、uart_boot 源码资料） | ⚠️ 同上 | 同 ILCE-7M4-RE 来源，保留原作者注释与来源记录 |
| `keys/published/` 两个 PEM | ✅ 可发布 | 所有者明确授权公开实验密钥对（私钥有意公开，无保密性声明） |
| `examples/custom-sounds/` 3 个 WAV | ✅ 可发布 | 用户授权原样公开，含 SHA-256 |
| `methods/07` 两张相机照片 | ✅ 可发布 | 维护者授权，已去元数据 |
| `training/third_party/sonylook/` | ✅ 可发布 | 751K/sony-look-bridge MIT，钉 `6302d69d`，manifest 逐文件 SHA-256 |
| `native/custom-sounds/` | ✅ 可发布 | 原创适配代码；按 COPY 引用原厂区间、仅分发有界改动字节，不含 Sony 库/固件 |
| docs 内生成图表/heatmap | ✅ 可发布 | 项目生成分析产物 |
| `fastboot/driver/` 预设（cfg/ini） | ✅ 可发布 | 原创配置文本，非 Zadig 本体 |

**机器门禁**：`tools/check_public_tree.py` 已通过（807 文件、0 错误）；git 追踪的二进制/密钥类文件恰为上述 4 个获授权项，无其他 `.bin/.exe/.dll/.pem` 等入树。

## B. 不在仓库内、属「发布包输入」的外部组件

这些不进源码树（manifest 钉哈希、用户自备），**决定的是能否随发布包再分发**：

| 组件 | 许可 | 再分发判定 |
|---|---|---|
| CPython embeddable | PSF | ✅ 可，须带 license 文件 |
| pyserial 3.5 | BSD-3 | ✅ 可，带 license |
| pyelftools | 公有领域式 | ✅ 可，带声明 |
| cryptography | Apache-2.0/BSD | ✅ 可，带 notices（含 OpenSSL/Rust 依赖） |
| NumPy / dissect.extfs 等可选包 | BSD/各自 | ✅ 可，逐组件带 license |
| squashfs-tools-ng 1.3.2 | 工具 GPL-3 / 库 LGPL-3 | ⚠️ 可但负担重：须满足 corresponding-source 与依赖声明，仅链接不够 |
| Zadig.exe（pbatard/libwdi 签名版） | GPL-3（libwdi） | ⚠️ 可但须随附许可文本+对应源码归档（上游便携版已按此处理） |
| Google ADB DLL（AdbWinApi.dll 等）/ USB 驱动 | Android SDK 条款，非开源 | ❌ **不可再分发**，保持用户自备（上游便携版已不捆绑 Google USB 驱动） |
| `vendor/sony_pmca`（2 文件） | Sony 专有 | ❌ **不可再分发**，用户自备 |
| Zig 工具链 | MIT 等 | ✅ 可，带 license |
| `M4-LUT-training-assets.zip`（543MB 清单外置包） | 混合：索尼 ARW 图卡钉哈希、无 Sony 二进制/常量 | ⚠️ 由维护者单独决定，不随源码树/发布包自动分发 |

## C. 结论

1. **源码分支可直接推到用户 fork**：树内获授权/可分发项边界清晰且有机器门禁；唯一保留项是两个启动资产的**来源许可模糊**——上游已如实记录而非掩盖，随仓库继续原样发布 + 保留溯源是合理做法。
2. **发布包（ZIP/便携包）可构建**，但打包时 Google ADB DLL 与 `vendor/sony_pmca` **不得入包**；squashfs-tools 与 Zadig 需随附许可文本+源码归档；Python 系逐组件带 license。
3. **不得随仓库/发布包分发**：完整 Sony 固件、个人备份、Sony 库/常量、未授权密钥、训练资产外置包（除非维护者另行决定）。
