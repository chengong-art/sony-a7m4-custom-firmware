# 验证范围与集成注意

[总览](README.md)

下列为既有 ILCE-7M4 实机报告的覆盖范围。本次文档核对仅查阅记录。

| 方法 | 已验证 | 未覆盖 |
|---|---|---|
| ULOGIO 网络终端 | 作者 r2，2026-11-16，正常启动后经 Wi-Fi 完成 TCP 23 命令往返、UID/GID 0、退出重连及拍摄、保存、回放 | 身份验证边界、其他网络与机身、原厂 1.05 的默认状态、独立重建候选 |
| ULOGIO USB 终端 | 作者 r2，2026-10-30，认证后双向 bulk、root 命令、退出后停用及重开，USB 活动时与 Telnet 共存 | 2026-11-16 轮次的 USB 验收、其他 USB 环境、完整作业控制 |
| SSH/SFTP | 公钥认证、root命令、PTY、文件上传下载与删除、Windows原生SSH、普通拍摄 | 与ULOGIO方案叠加后的整体验收 |
| p1/Cetus UART | 二进制双向传输、缓存跨界、root命令、exit后新Shell、回普通模式拍摄 | 长时间高负载、无标记分支执行真实SD更新、其他机身 |

网络与USB的ULOGIO创建路径、SSH服务和UART桥接是不同实现。不能把门控放行、符号名、组件存在、离线模型或文件系统可枚举当作真实连接证明。

## ULOGIO 候选与回退证据

- 作者报告绑定 ILCE-7M4 原厂 6.02 p15 目标组件布局与原厂 1.05 p15 供体函数。r2 的 `libosal_ulogio.so` 为 71,176 B，SHA-256 为 `3b40a6117e3320929f21fa18977e586b1b610ac6f4287cd13a175d1ca230bad2`。11 月 16 日报告记录候选 p15 完整读回一致、正常启动及 `im.elf` 映射该库并持有监听。10 月 30 日 USB 回读镜像复核绑定同一库哈希，并有当轮共存记录。见 [Telnet 实机原文](https://github.com/Erlla/sony-a7m4-custom-firmware/issues/31#issuecomment-6913856053) 和 [USB 实机历史复核](https://github.com/Erlla/sony-a7m4-custom-firmware/issues/31#issuecomment-6916285672)。
- 本地静态校验和 v4 离线镜像内文件读回各有候选身份。v9 是不同的库，74,120 B，SHA-256 为 `1df1d9811f5adece6be804f285e8c23fd8aad844f57a79cbb8deabacbe94720a`。历史 v8 至 v9 增量日志记录五个变更块逐块匹配，覆盖范围为增量。另存的单范围写入收据仅记载 `[0,4096)` 匹配，且其设备查询未绑定物理机身或固件版本。
- 本地 v9 的普通启动后 TCP 23 探测收到拒绝。该轮缺少库映射、初始化和监听归属的运行证据。`socket` 失败路径的返回值和 TCP 23 探测失败的原因仍未证实。作者 r2 的运行结果只适用于该作者报告的产物和环境。
- Issue #31 首帖将报告者环境标为国行 6.02，另记 8 段匹配、约 556 MB 前缀比对及 TCP 23 超时。这是另一轮记录，不能用来补齐 v9 的整分区读回或运行证据。见 [Issue #31 首帖](https://github.com/Erlla/sony-a7m4-custom-firmware/issues/31)。
- 作者报告的完整读回对象是修改后候选。对照的本地收据也未建立同一机身同轮次原厂 p15 完整恢复、读回匹配和正常启动的证据链。回退门槛见[组合与回退](telnet-usb.md#组合与回退)。

## 文件系统与账号

- p15镜像内 `/lib`、`/bin`、`/ssh` 在普通系统分别变为 `/usr/lib`、`/usr/bin`、`/usr/ssh`。
- p15文件系统长度与物理分区长度不同，保留完整尾部。新ext目录的记录填充、位图和计数必须正确；宽松解析器列出文件不证明内核能读目录。
- NSS读取逗号格式，按名和按UID构造必须一致。只改配置中的PermitRootLogin或一个shell字符串不足以实现登录。
- `/tmp`、`/etc`、`/var` 同属持久p10；旧marker不是本次启动证明。

## 终端与硬件

- USB TERM_MODE用输入／输出双字节，不按旧端点算式选错bulk接口。
- Telnet建立监听后必须纳入select nfds；仅TCP握手不能证明accept和PTY循环正常。
- CP UART0与AP ttyAMA0不是同一物理通路；RP2040的Output disable也不是自动开漏UART。
- p1更新环境不运行普通拍摄系统，关闭客户端或exit不会自行移除p2启动标记。

组件来源见 [reference-layout.json](reference-layout.json)。其中是厂商组件和接口布局身份，不是个人分区备份或当前相机状态清单。
