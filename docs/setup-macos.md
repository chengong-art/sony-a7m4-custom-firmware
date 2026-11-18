# macOS 适配说明

[English](setup-macos.en.md) · [返回](../README.md)

本目录是 [Erlla/sony-a7m4-custom-firmware](https://github.com/Erlla/sony-a7m4-custom-firmware)
上游 HEAD `fc76ca8ce40910eb785f0098c5d24ff3a5beb309` 的 macOS 适配分支（初始移植基线为 `0332a1fa…`，2026-11-14、2026-11-15、2026-11-18 已三次跟进上游）。上游只发布 Windows 10/11
主机工具；这里把它做成 macOS 可用的等价实现，**不改变任何固件侧配方、地址、哈希或验证边界**。

相机侧的一切约束（原厂电池、完整 eMMC 备份、自制刷机线、精确哈希识别基线）完全继承上游，
先读 [上游风险与备份要求](../README.md) 与 [接线说明](wiring.md)。

## 图形入口（A7M4 ToolKit.app）

```bash
.venv/bin/python tools/make_app_macos.py      # 生成 A7M4 ToolKit.app（在仓库根）
open "A7M4 ToolKit.app"
```

窗口不是"假装终端"，而是 [agent 接口](agent-api.md)的**客户端**：启动时拉一个
`a7m4 agent` 子进程，用 `capabilities` 发现方法表，按方法自带的**安全级别**分组渲染按钮
（只读 / 写密钥 / 接触相机 / 写相机），你编辑参数 JSON 后执行，进度通知与结果和 agent
收到的完全一样。顶部状态条每 3 秒自动刷新一次 `status`。

因此**不存在需要你回答的提示**：方法用参数而不是提问，所以没有"卡在等一次回车"这种状态。

按钮显示中文名（如「连接并完整备份」），方法 id（如 `connect_backup`）显示在选择行里——
界面文字是中文，契约里的名字不变，agent 和人看的是同一个请求。

- 参数框就是 agent 会发的那段 JSON，可以改；参数错会原样显示服务端的错误码（`PARAMS` 等）。
- `prepare` 成功后会把 `plan_id` 与确认串自动填进 `apply` 的参数模板——但 `apply` 仍要求
  原样回传确认串，这是**服务端**强制的，窗口绕不过去。
- 长操作进行中不能并行发请求（会话是单线程的），要停下就按「中止并重开会话」；写权限随
  会话结束而失效。
- 演示模式勾选后重开会话即生效（合成相机）。

四点如实说明：

- **未签名**。本机生成、本机运行没问题；拷给别人会被 Gatekeeper 拦。
- **菜单栏显示 `python` 而不是 `A7M4 ToolKit`**。bundle 主程序是脚本、最后 exec 到解释器，
  macOS 就按解释器命名进程。属外观问题，未做（要彻底改需嵌入解释器）。
- app 要与 `advanced-macos/`、`vendor/`、`.venv/` 同层（即仓库根），别单独拷走。
- 产物已被 `.gitignore` 忽略，用上面的命令重建。

## 适配范围

| 项 | 上游 Windows | 本分支 macOS | 依据 |
| --- | --- | --- | --- |
| fastboot 主机程序 | `bin/fastboot.exe` | `bin/fastboot`（Android Platform Tools，通用二进制） | 协议相同；`fastboot devices/oem partition/get_staged/flash` 均为平台无关的 USB 传输 |
| USB 驱动绑定 | 安装 WinUSB INF（`pnputil` 提权） | **无需安装**：Apple USB 栈直接绑定 VID `18D1` / PID `4EE0` | `a7m4/hardware.py:install_driver` 在 macOS 返回 `not-required-on-macos` 并写进度事件，不假装安装 |
| 设备锁 | Win32 命名互斥量 | `flock(2)` 锁目录 + 写入前重新校验，锁登记**按线程**，路径不随 `A7M4_DATA_DIR` 移动 | 复核证实进程全局登记会让别的线程替本线程背书（fail-open），故改为线程局部；失效时报 `LOCK_LOST` |
| RP2040 BOOTSEL 卷 | 盘符 `D:`…`Z:` 扫 `INFO_UF2.TXT` | 扫 `/Volumes/*/INFO_UF2.TXT` | `a7m4/host.py:bootsel_volumes` |
| 串口 | `COMx` | `/dev/cu.usbmodem*`（pyserial 自动枚举，VID `2e8a`） | `hardware.rp_ports` 未改逻辑 |
| SquashFS | `sqfs2tar.exe` / `tar2sqfs.exe` | 同版本 1.3.2 的 macOS arm64 构建 | 见下节；配方里 `-c gzip -b BLOCK [--exportable]` 参数完全一致 |
| 入口脚本 | `advanced/*.bat` + `_launch.cmd` | `advanced-macos/*.command` + `_launch.sh` | 同样的参数转发、`Exit code:` 输出与 `A7M4_DEMO` 语义 |
| 数据目录 | `%LOCALAPPDATA%\A7M4Toolkit` | `~/Library/Application Support/A7M4Toolkit` | 可用 `A7M4_DATA_DIR` 改到外置卷 |

**未适配、也不会假装适配的部分**：CP 合并需要的 `arm-none-eabi-gcc` 与 `feature_integration`
的编译器哈希钉仍是原样（上游 `layout.json` 钉的是某个具体 Windows GCC 二进制）；
`jpeg_exif_dimensions` 钉的是 Zig 0.14.1 **Windows x86_64** 二进制。这两处是
*输入身份*问题，不是操作系统问题：换一个编译器就是换一个基线，必须重新建立整链证据。
本分支没有放宽任何哈希检查来"让它跑起来"。

## 安装

```bash
cd ports/erlla-a7m4-macos
python3.12 -m venv .venv          # 任何 3.11+ 都可以，别用系统自带的 3.9/3.10
.venv/bin/python -m pip install pyserial pyelftools cryptography
.venv/bin/python -m pip install numpy "dissect.extfs" "dissect.util" pytest   # 可选：离线固件与测试
```

**不要用系统自带的 `python3`**：macOS 常见的是 3.9/3.10，低于 `requires-python` 的 3.11，
会在很后面报一个看不出原因的错误（`hashlib.file_digest`）。`advanced-macos/_launch.sh`
现在会先探测版本，挑 `python3.13`→`python3.12`→`python3.11`→`python3` 中第一个合格的，
都不合格就打印 `Python 3.11+ not found` 并以 2 退出，不会带着坏解释器继续跑。

装好后**直接用 Finder 双击** `advanced-macos/` 里的 `.command` 即可，不需要命令行：
先双击「检查运行环境」确认 `ready: true`，再按上游文档的顺序操作。

`pyproject.toml` 未改动，运行时依赖仍是 `pyserial==3.5`、`pyelftools==0.32`、
`cryptography==46.0.3`。

## 准备本地资源

上游仓库不分发外部二进制；macOS 需要同一组资源，只是主程序文件名不同。把资源放进任意目录，
保持下面的布局，然后注册：

```text
<资源目录>/
  bin/fastboot                  # Android Platform Tools 的 macOS 构建
  bin/fastboot.bin              # 相机端启动 payload（ILCE-7M4-RE，AArch64）
  bin/uart_boot.ino.uf2         # RP2040-Zero 启动桥（Open-Drain）
  squashfs/sqfs2tar             # squashfs-tools-ng 1.3.2
  squashfs/tar2sqfs
  squashfs/libsquashfs.1.dylib  # 与上面两个工具同批构建
```

```bash
.venv/bin/python tools/register_resources.py <资源目录>
.venv/bin/python -m a7m4 doctor
```

`doctor` 会打印 `"platform": "macos"` 并要求上表全部通过哈希校验。资源包默认放在
`vendor/`（被 `.gitignore` 忽略）；也可以把包放在别处并设 `A7M4_RESOURCES=<包路径>`。

**`libsquashfs.1.dylib` 必须与两个 squashfs 工具一起注册。** 这份构建把工具链接到
`@loader_path/libsquashfs.1.dylib`，缺了它注册不会报错，但第一次调用就会
`Library not loaded`。`register_resources.py` 现在会把 `.dylib`/`.so` 一并复制并保留权限位
（此前只认 `.exe/.dll/.bin/...`，于是漏掉了这个库）。

只做救砖（相机已能进 fastboot）时，最小集合是 `bin/fastboot` 一个文件：

```bash
.venv/bin/python tools/register_resources.py <资源目录> --recovery-only
```

### SquashFS 工具

`docs/setup.md` 要求 squashfs-tools-ng 1.3.2。macOS 上没有官方预编译包，
从源码构建即可（本仓库的 `tools/squashfs-ng-1.3.2-macos-arm64/` 就是这样来的）：

```bash
curl -L -o sqfs.tar.gz https://codeload.github.com/AgentD/squashfs-tools-ng/tar.gz/refs/tags/v1.3.2
tar xzf sqfs.tar.gz && cd squashfs-tools-ng-1.3.2
autoreconf --force --install --symlink -I /opt/homebrew/share/aclocal
CONFIG_SITE=/dev/null ./configure --without-selinux
make -j8
```

两个 macOS 特有坑：

1. `autogen.sh` 用 `aclocal --print-ac-dir` 找 `pkg.m4`，Homebrew 的 `pkgconf` 把它装在
   `$(brew --prefix)/share/aclocal`，所以要显式给 `-I`。
2. 源码路径含空格（本项目就在 `相机固件 逆向 Workspace` 下）会让 `configure` 的 site-script
   与 `libtool` 的 install 路径解析出错：用 `CONFIG_SITE=/dev/null`，并把构建/安装目录放在
   无空格路径下。

工具链用 `@loader_path` 引用 `libsquashfs.1.dylib`，因此 `sqfs2tar` 与 dylib 必须同目录
（本分支的 `tools/squashfs-ng-1.3.2-macos-arm64/` 已按此布局改好）。

## 使用

`.command` 双击即可运行，或在终端执行：

```bash
export A7M4_DATA_DIR="$PWD/../../tmp/a7m4-state"   # 可选：把备份放到外置卷
advanced-macos/检查运行环境.command                  # doctor
advanced-macos/备份全部分区.command                  # 强制完整备份
advanced-macos/查看或设置固件版本.command
```

| 脚本 | 等价上游 BAT |
| --- | --- |
| `检查运行环境.command` | `检查运行环境.bat` |
| `只检查fastboot连接.command` | `只检查fastboot连接.bat` |
| `一键救砖_从SD卡恢复.command` | `一键救砖_从SD卡恢复.bat` |
| `备份全部分区.command` | `备份全部分区.bat` |
| `进阶维护菜单.command` | `进阶维护菜单.bat` |
| `查看或设置固件版本.command` | `查看或设置固件版本.bat` |
| `刷入验签跳过版更新器.command` | `刷入验签跳过版更新器.bat` |
| `恢复官方验签公钥.command` | `恢复官方验签公钥.bat` |
| `恢复已备份的分区.command` | `恢复已备份的分区.bat` |

环境变量与 Windows 启动器同名同义：`A7M4_PYTHON`、`A7M4_DEMO`、`A7M4_NO_PAUSE`；
新增 `A7M4_RESOURCES`、`A7M4_DATA_DIR`。救砖入口仍然拒绝 `A7M4_DEMO=1`，
与上游 `_launch_recovery.cmd` 行为一致。

## 验证状态

本次只做离线与合成设备验证，**没有连接、读取或写入任何相机**：

- 上游测试套件在云端 macOS VM（26.6.2 arm64 / Python 3.12.16）通过：**352 passed /
  7 skipped**（7 项 skip 均为 Windows 专属入口，平台不适用）。macOS 专属测试覆盖资产别名、执行位（按逻辑名而非后缀）、
  锁互斥（锁目标被删、锁路径不随数据目录移动、文件目标被拒、无锁写入被拒）、
  payload 存在性探测、BOOTSEL 卷识别、驱动 no-op、资源注册与 `.command` 入口转发。
- **独立复核**：本移植经第三方模型（Gemini 3.8 Flash）以两份不同任务书复核，分别判
  `reject` 与 `accept-with-findings`，两份报告独立指出同一个阻断级缺陷：
  我的第一版目录锁在目录为空时仍可被 `rmdir` 删除，互斥会静默失效。该说法正确，
  已按「写入前重新校验锁身份」重新实现，并补了 4 项回归测试。其余有效项
  （Windows DLL 回归测试被削弱、启动器测试是同义反复、`.command` 丢弃参数、
  执行位检查按后缀判定、macOS 缺 payload 存在性检查）均已修复。
- **第三轮复核（两路独立）判 `reject` / `accept-with-findings`，抓到的又是我的修复本身**：
  我把锁登记写成进程全局栈，于是**一个从不取锁的线程只要别的线程持锁就能通过复核**（fail-open，实测复现）；
  更严重的是默认路径下，锁目录被删除重建后，旧持有者会去校验**新持有者**的有效 inode，
  从而漏掉自己的 `LOCK_LOST`，两个普通 Engine 操作可同时进行。还抓到 `append` 早于内层 `try`
  的异步中断泄漏窗口（实测注在那一行会留下已验证的僵尸登记）。
  已改为**按线程**的登记（无锁线程看到空栈即报 `LOCK`，fail-closed），把登记放进受保护块内，
  并补 9 项锁回归测试。**更正**：本轮提交时文档曾写「4 项已含错误顶层掩蔽」，复核者指出当时只有跨线程借用一项，掩蔽场景并未覆盖——该声明是错的，现已真正补上。当前固定住的行为：跨线程借用被拒、错误顶层掩蔽（旧持有者报 `LOCK_LOST` 而非校验替换持有者的 inode）、跨线程关闭不留僵尸登记、已释放锁拒绝校验、逐行异步中断不留下任何可授权写入的对象、`os.open` 边界中断不泄漏描述符、同线程嵌套出栈、Windows 递归取得与异常清理、以及包装器构造失败时释放内核句柄。
  同时如实记录残留：`verify` 与命令之间的窗口、长刷写途中的删除，用 POSIX 建议锁**无法**消除，
  `rm -rf` 仍在这把锁的承诺之外——只是普通 `rmdir` 已被标记文件挡住。
  另有三项残留同样如实记录：**清理块内的异步中断**会跳过该块剩余语句（因此实现改为先让锁对象失效、再清理列表，使幸存的登记只会拒绝而不会授权）；Windows 的 `WAIT_ABANDONED` 只转移所有权、**不证明**上一个持有者已发出的相机命令已经结束；锁范围按用户（POSIX）/按会话（Windows），**不排除**其他用户或会话。
- **第二轮复核判 `reject`，抓到的回归来自我的修复本身**：Windows 分支登记锁时只 `yield None`，
  于是接进写入路径的锁复核让 **Windows 全部刷写无条件失败**。本机用桩 kernel32 复现确认
  （持锁状态下仍抛 `LOCK`），改为登记内核句柄修好——命名互斥量不在路径上、不可删除，
  复核对它退化为空操作，但**仍要求写入处于 `hardware_lock` 之内**，因此没有采纳
  「Windows 直接跳过检查」的建议：跳过会把「破坏性步骤必须有锁」这条不变量丢掉。
  锁登记同时从单全局改为进程级栈（Win32 互斥量同线程可重入，单全局会被内层释放抹掉外层）。
  另修：测试争用真实全局锁、`engine` 与 `sd_recovery` 两处 `reboot` 复核不一致、
  原生名 `bin/fastboot` 绕过执行位门。
  **所以「Windows 行为不变」这项声称一度被我自己的修复打破**，现由
  `test_windows_lock_still_lets_a_write_through` 等桩测试锁死；Windows 分支仍未在真实
  Windows 上运行过，这一点保持不变。
- `doctor` 在真实注册资源包上返回 `ready: true`；`--demo` 备份/维护流程端到端跑通。
- `sqfs2tar` / `tar2sqfs` 在同一台机器上做过 tar→sqfs→tar 往返，产物头部为
  `hsqs`、gzip、v4.0、块大小与 `--exportable` 标志均可控。
- `fastboot devices` 在无设备时正常返回空列表（退出码 0），确认二进制可执行且不依赖驱动。

仍未验证、需要真机与相应授权：USB 枚举到 payload 设备、RP2040 串口握手、真实分区读写、
`oem partition dump` 的分块时序、写后读回。这些与操作系统无关，但本分支没有做过。

## 云端 macOS VM 排错

在 Devin Cloud macOS VM 部署与跑测试时遇到的报错（隧道断连、休眠丢 authorized_keys、
缺 `_tkinter`、git 索引依赖等）及解决方案见 [cloud-vm-troubleshooting.md](cloud-vm-troubleshooting.md)。

## 测试密钥对的处理

上游 `keys/published/` 公开了一对 RSA-2048 密钥，私钥本就是公开的——它的用途只是让自签
测试包被相机接受，**不提供任何身份保证**，任何人都能用它签名。维护者已明确授权公开，
因此本分支**与上游一致地把这对密钥入库**：

- `.gitignore` 只放行这两个路径，其他任何 `*.pem` 仍然禁止入库。
- 指纹钉在 `keys/published/fingerprints.json`；`tools/check_public_tree.py` 每次运行都
  核验私钥/公钥配对、公钥 DER 的 SHA-256 以及签名往返，并报告 `published_key_pair_verified`。
- 这对文件是受跟踪的，缺了就是真缺陷：`conftest.py` 在收集阶段直接报错终止，
  而不是跳过依赖它的测试。

实际给自己做自签 SD 更新包时**仍然应该用自己生成的密钥**：`maintain` 菜单里 PEM 路径留空
即自动生成，私钥离线保存。用公开的这把签名等于没有签名——任何人都能造出同样的包。
