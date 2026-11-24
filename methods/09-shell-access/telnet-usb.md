# 恢复 ULOGIO 的 Telnet 与 USB Shell

[总览](README.md) · [组件身份](reference-layout.json) · [验证边界](validation.md)

普通启动使用 p7 根文件系统，并将 p15 挂到 `/usr`。本方法仅修改 p15 的 `/lib/libosal_ulogio.so` 与 `/lib/libsencore.so`，运行时分别为 `/usr/lib/...`。PTY 子进程执行 p7 中已有的 `/bin/ash --login`。

适用范围为 Sony ILCE-7M4（A7 IV）原厂 6.02 的指定 p15 组件布局，供体函数来自同型号原厂 1.05 p15。以[组件身份](reference-layout.json)中的哈希和布局确认输入。其他版本、地区变体、机身及重建产物须独立核对。作者的 r2 实机结果见[验证边界](validation.md)。

## 动态装载与初始化入口

以下地址对应组件身份文件绑定的原厂 6.02 ULOGIO 库。动态装载器处理重定位后，通过 ELF 构造函数数组调用初始化入口。

| 项目 | 原厂 6.02 值 |
|---|---|
| `DT_INIT_ARRAY` | `0x18d18` |
| `DT_INIT_ARRAYSZ` | `0x10`，共两个 8 字节项 |
| 第二个构造项 | VA `0x18d20`，文件偏移 `0x8d20` |
| 对应 `.rela.dyn` 项 | `r_offset=0x18d20`，`R_AARCH64_ABS64=257`，`r_addend=0` |
| 重定位目标 | 原厂 dynsym #112，`__ulogio_so_init @ 0x3238` |

第二个构造项在文件中的原值为 0，装载时由符号重定位填入地址。检查入口时需要解析重定位的符号索引。重建 dynsym 后，应让该项继续解析到 `__ulogio_so_init`，使用重建后的实际索引。

`__ulogio_so_init` 的函数范围为 `0x3238..0x3368`。它通过 OSAL 同步消息取得配置，再以 `0x362c` 为入口创建工作线程。线程体在 `0x38dc` 通过 PLT `0x3060` 调用 `ulogio_telnetd_init @ 0x50b4`，并在 `0x36b0` 经 PLT `0x2bd0` 进入主循环。

```text
动态装载器 → .init_array 的重定位后函数指针
  → __ulogio_so_init @ 0x3238
  → OSAL 配置消息、工作线程 @ 0x362c
  → 0x38dc 调用 ulogio_telnetd_init @ 0x50b4
  → 移植后的监听初始化
```

ELF 重建时一并核对 `DT_INIT_ARRAY/DT_INIT_ARRAYSZ`、`.rela.dyn`、dynsym 及符号版本。原 `.rela.plt` 的顺序只覆盖 PLT 调用相关检查，构造项使用的 `.rela.dyn` 仍需同步。实际执行还依赖库成功装载、OSAL 配置消息成功和线程创建成功。

## 恢复终端创建代码

1.05 p15 的 ULOGIO 库包含网络监听、PTY 与 ash 创建；6.02 同库删除了这些创建路径，但保留部分 `telnetd` 名称。整库回退会丢失 6.02 的 PROX 配置／分派，采用有边界的函数移植。

从 1.05 供体提取以下半开区间，地址为 ELF VA：

```text
0x4448..0x4490    0x4490..0x44cc    0x44cc..0x4884
0x496c..0x49e0    0x4ad4..0x4bbc    0x4bbc..0x4e9c
0x5afc..0x5da8
```

这七段共 2852 字节，需要重定位其中的直接分支、ADRP/ADD、GOT/全局引用和 PLT 调用。6.02 既有代码、80 个公开导出、数据地址及 PROX 路径保留，移植函数放入新增 RX 段，再由原入口跳转接入。新 libc 依赖使用独立 GOT 槽和 AArch64 桩，新增导入绑定 libc 的 `GLIBC_2.17` 版本项。

参考实现采用下列地址映射，保留供体各段之间的相对间距。

```text
clone_base = 0x1a000
mapped(donor_va) = clone_base + donor_va - 0x4000
ulogio_telnetd_init 的新入口 = mapped(0x5afc) = 0x1bafc
6.02 原入口 0x50b4 改为 b 0x1bafc
```

原入口只需容纳跳转指令。供体 init 的 684 字节放在新段中，原函数的 `st_size=0x1c8` 保留。使用其他新增段基址或布局时，按该布局重新计算分支、引用和入口映射，并核对装载段范围与权限。

### 固定启用监听创建

本方法将移植 init 中两处读取启用参数的指令改成 `mov w0, #1`，小端机器码均为 `20 00 80 52`。

| 1.05 供体 VA | 上述映射下的 VA | 原指令 | 修改后 |
|---|---|---|---|
| `0x5b48` | `0x1bb48` | `ldr w0, [x21, #0x18]` | `mov w0, #1` |
| `0x5b6c` | `0x1bb6c` | `ldr w0, [x21, #0x18]` | `mov w0, #1` |

两处指令原先读取同一启用参数，第一处参与是否尝试创建监听的判断。参考实现固定了这两处的读取结果，因而不能把 `/etc/ulogio.bin` 的 TELN 或 `ulogio telnet up/down` 当作该实现的监听开关或访问控制。现有收据未实测 `socket` 失败时初始化函数的返回值，也未验证各配置组合下的实际网络可达性。

### 接入网络与 USB 生命周期

| 6.02 ULOGIO VA | 修改 |
|---|---|
| `0x50b4` | init 转入移植实现，创建 TCP 23 监听 |
| `0x4dac`、`0x4db0`、`0x4db4` | accepted socket 交给网络 PTY/ash helper |
| `0x48e8` | input=2 或 output bit1 时创建 USB PTY；保存寄存器和标志并重放原 LDR |
| `0x4100` | 删除终端节点前释放两条 ring |
| `0x49b8` | USB 停用只对正 PID 发 kill，处理子进程已退出状态 |
| `0x49d0` | 避免重复关闭同一 USB slave fd |

参考实现于初始化末尾重算 `select` 的 `nfds`，使已创建的 listener 纳入事件循环。它从映射后的供体 `0x5d64` 跳转到辅助代码，调用映射后的 `0x4448` 重算函数，重放原 `ldp x19, x20, [sp, #0x10]`，再跳回映射后的 `0x5d68`，由原尾声恢复现场并返回。按上述基址，接入点为 `0x1bd64`。

遗漏这一步时，TCP 可能先握手成功，但主循环始终没有执行 `accept`，队列积压后后续连接也可能超时。网络主循环接收连接后，表中的 `0x4dac/0x4db0/0x4db4` 接线将 socket 传给 PTY/ash helper，完成 `/dev/ptmx`、`grantpt/unlockpt/ptsname` 和 ash 创建。USB 创建、终端销毁与退出回收分别由其余接入点处理。

移植的 `vfork` 失败和分配失败进入清理路径；子 shell 退出后，终端必须可以关闭并再次创建。ELF 重建需保留段权限、原 VA 和动态重定位对齐，不能仅看反汇编中出现 `/bin/ash` 就认定完成。

## 放行普通系统 TERM_MODE

仅对 JSON 中绑定的 **6.02 p15 libsencore**：

```text
文件偏移 0x26e13：80 → 00
block 0x0601 / code 0x801f → code 0x001f
```

这不是 p1 的 `0x2bc43`。只改 p1 同名库，不能恢复普通 p15 的终端创建路径。新文件放回本机完整 p15 后重建文件系统，按[共同集成流程](README.md#共同的集成流程)部署和完整读回。

## 监听未出现时的定位

先对实际烧录的候选库检查动态标签、重定位和符号表。

```sh
readelf -W -d libosal_ulogio.so
readelf -W -r libosal_ulogio.so
readelf -W --dyn-syms libosal_ulogio.so
readelf -W -x .init_array libosal_ulogio.so
```

按顺序核对构造项解析结果、`0x50b4` 跳转目标、移植后的两处启用指令、全局/GOT/PLT 引用，以及 init 末尾的 nfds 重算。若已有运行期观测通路，再检查库的实际装载路径、OSAL 配置与线程创建、`socket/bind/listen` 返回值、listener fd 和 `accept`。

端口超时可能出现在监听初始化、网络路径或 accept 队列积压等阶段，需要结合上述证据定位。文件系统检查和刷写回读验证的是镜像结构与部署内容，ELF 重定位及运行期调用链应单独核对。

## 使用网络终端

此 Telnet 实现以明文传输命令和输出，可被网络路径上的他人读取或篡改。参考说明将其描述为免口令终端。作者 r2 实测记录了 root 命令往返，身份验证、客户端授权和接口访问限制均未验收。仅在隔离、可信的网络上评估，避免连接共享网络或将端口暴露给公网。Wi-Fi 联网本身不提供 Telnet 客户端授权。具体运行证据见[验证边界](validation.md)。

## 开启与关闭 USB 终端

USB 走 Sony 服务模式，不是 USB 网卡或串口类设备。Windows 需要相应正常／服务身份的 WinUSB；正常海量存储驱动恢复后，下次使用需重新准备驱动。先按实际接口枚举端点并完成 Senser keyappend 三轮认证。

协议顺序：

```text
ProductInfo TERM_CHANGE : (0, 0x00f1, 01)          成功码 1
AdjustControl TERM_MODE : (0x0601, 0x001f, 02 02) 成功码 0
终端读取：0x83 IN       终端写入：0x04 OUT
```

`02 02` 分别指定 USB 输入和输出。单字节 `02` 在此链上会被补为 `02 00`，只开输入、关闭输出；不能用它验证双向 Shell。端点是各方向 bulk 列表的第二项，不能按旧 `base+1` 算成 82/03。

结束顺序：

```text
TERM_MODE 00 00 → TERM_CHANGE 00 → StopSenser
```

`exit` 后仍须能停用并重新开启。`ulogio telnet up/down` 修改的是过滤字段，不能当作已恢复监听或 PTY 创建的证明。只出现 `Terminal activated` 也不等于收发已打通。

## 组合与回退

本方案不新增 SSH 账号、密钥或 SFTP 子程序。与 SSH 方案组合时，在同一离线 p15 中合并文件级修改，不能互相覆盖整分区；组合后另行验证。

部署或回退前，须具备同一机身的完整原厂 p15 备份，核对目标分区身份、物理大小以及备份长度和完整性哈希，并确认可信恢复通路可用。回退验收须有整分区读回与原厂备份逐字节或 SHA-256 一致的证据，并确认相机正常启动。任一前置条件缺失即停止，任一验收条件缺失即停止并保留未验收状态。现有收据未建立同一机身同轮次的完整原厂 p15 恢复证据。

