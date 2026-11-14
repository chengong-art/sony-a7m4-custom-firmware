# 云端 macOS VM 与移植排错记录

> 2026-11-14 起维护。记录在 Devin Cloud macOS VM 上部署、运行本移植与全量测试时遇到的实际报错及已验证的解决方案。接入方式见仓库根 AGENTS.md「云端 macOS 执行环境」与 `scripts/cloud-mac-tunnel` / `scripts/cloud-mac-ssh`。

## 一、VM 接入类

### 1. `devin forward` 隧道断连：`Timeout, server ssh.devin.ai not responding`

**现象**：`ssh devin-cloud-mac` 报 `Connection reset` / `Connection refused`，`/tmp/devin-cloud-mac-forward.log` 末尾出现 `Timeout, server ssh.devin.ai not responding`，`nc -z localhost 2222` 不通。

**原因**：隧道是 `devin forward` 维持的长连接，本机网络抖动或网关超时都会掐断。

**解决**：重跑 `scripts/cloud-mac-tunnel`（幂等，已监听则直接退出；否则重启 forward 并等待端口就绪）。转发进程需 devin CLI 已登录凭据。

### 2. `Permission denied (publickey)`（隧道通但鉴权失败）

**现象**：`nc -z localhost 2222` 通，`ssh devin-cloud-mac` 报 `devin@localhost: Permission denied (publickey)`。

**原因**：VM 休眠唤醒（或会话重建）后 `~/.ssh/authorized_keys` 被重置，本机公钥丢失。文件（如 `~/repos/`）保留，仅授权被清。

**解决**：走 Devin 网关备用路径重新追加公钥：

```bash
scripts/cloud-mac-ssh 'echo "<本机 ~/.ssh/devin_cloud_mac_ed25519.pub 内容>" >> ~/.ssh/authorized_keys'
```

之后 `ssh devin-cloud-mac` 恢复正常。

### 3. `devin ssh` 报 `Error: Device not configured (os error 6)`

**现象**：直接 `devin ssh <session> -- <cmd>` 报 os error 6。

**原因**：`devin ssh` 需要 TTY，无 PTY 的调用方（脚本、CI、agent）会失败。

**解决**：用 `script(1)` 包一层伪终端——`scripts/cloud-mac-ssh` 就是 `script -q /dev/null devin ssh <session> -- "$@"`。

### 4. TCP 通但 `Connection timed out during banner exchange`

**现象**：2222 端口能建连，SSH 读 banner 超时。

**原因**：VM 处于休眠态，sshd 未应答；forward 端口先就绪、后端还没醒。

**解决**：先 `scripts/cloud-mac-ssh 'echo ok'` 走网关唤醒 VM（`up 0 mins` 即刚唤醒），等几十秒再用隧道。

### 5. `devin --cloud` REPL 报 `out_of_quota / billing_restricted`

**现象**：`devin --cloud -p "..."` 返回 `Your organization has a billing error / out_of_quota`。

**说明**：云端 **agent 会话**受配额限制；但既有免费档 VM 的 `devin ssh`/`devin forward` 通道不受影响。不要为跑命令去新建 cloud agent 会话，也不要销毁该 VM。

## 二、shell / 传输类

### 6. zsh 报 `no matches found: refs/heads/*` / `== not found`

**原因**：远端 zsh 会把未加引号的 glob（`refs/heads/*`）和 `===` 当模式展开。

**解决**：给 refspec 与 echo 标记全部加引号，如 `git ls-remote <url> 'refs/heads/*'`、`echo "MARK"`。

### 7. scp 多小文件时停滞

**现象**：scp 一批小补丁文件时中途挂起无进展。

**解决**：小文件改 `ssh host 'cat /path/f' > local`；整树用 `rsync -az --partial --timeout=60`（断点续传）。隧道带宽有限，54 MB 需数分钟。

## 三、Python / 测试环境类

### 8. `ModuleNotFoundError: No module named '_tkinter'`

**现象**：pytest 收集 `tests/test_app_gui.py` 报 `_tkinter` 缺失。

**原因**：brew `python@3.12` 默认不带 Tk 绑定。

**解决**：`brew install python-tk@3.12`，验证 `.venv/bin/python -c "import tkinter"`。

### 9. `test_reference_recipe_and_native_source_manifest` 报 `fatal: not a git repository`

**现象**：该测试调用 `git ls-files --cached --others --exclude-standard`，rsync 出来的目录树没有 `.git`，返回 128。

**原因**：测试断言「清单内文件均已发布」，依赖 git 索引，属环境性失败而非代码缺陷。

**解决**：在 VM 树内 `git init && git add -A`（`.gitignore` 已含 `.venv/`，无需提交）再跑 pytest。

### 10. 3 项测试 skip：`could not import 'numpy'`

**现象**：`test_feature_integration.py` / `test_offline_methods.py` 共 3 项跳过。

**原因**：可选 `firmware` extra（numpy、dissect.extfs）未装。

**解决**：`pip install -e '.[test,firmware]'`；装齐后全量为 **282 passed / 4 skipped**（4 个跳过为 Windows BAT 入口，macOS 预期）。

## 四、补丁合并类

### 11. `git apply` 静默 `Skipped patch` 不落任何改动

**现象**：`git apply --check` 显示可应用，实际 `git apply` 输出 `Skipped patch 'README.md'` 且文件未变。

**原因**：补丁某 hunk 上下文与本树已有改动冲突时，git apply 会整文件跳过且不报错（本例为 macOS 分支改写过的描述段撞上上游文档增量）。

**解决**：改用 `patch -p1 -f`，失败 hunk 存 `*.rej` 后按上下文手工合并，删除 `.orig/.rej`。合并后逐文件与上游 blob/`git show` 输出核对（`git hash-object` 对 blob id，`diff` 对整文件）。

### 12. `devin forward` 通道被网关拒绝：`administratively prohibited`

**现象**：隧道进程存活、本地 2222 端口可连，但 ssh 报 `kex_exchange_identification: read: Connection reset by peer`；`devin forward` 日志含 `channel 1: open failed: administratively prohibited`。

**原因**：VM 重启/休眠唤醒窗口内，网关拒绝 direct-tcpip 转发通道；VM 完全启动后通常自行恢复。

**解决**：先 `scripts/cloud-mac-ssh 'uptime'` 确认 VM 已醒（该命令走 `devin ssh` 网关 exec 通道，不受转发策略影响），再 `pkill -f "devin forward"` 后重跑 `scripts/cloud-mac-tunnel`；若恢复后报 `Permission denied (publickey)`，按第 2 条重加公钥。持续被拒时的零传输方案：在 VM 本地仓库 `git worktree add <path> <commit>` 检出基线，再 scp 差量补丁覆盖（见下条）。

### 13. `patch -p1` 把 git 转义的 UTF-8 文件名写成字面乱名，且不补执行位

**现象**：`git diff` 对非 ASCII 路径输出 `"\344\270\200…command"` 的转义形式，`patch` 会把转义串当字面文件名创建（尾部还带 `"`）；同时 `patch` 不应用 `new file mode 100755`，新文件全是 644。

**后果**：`.command` 启动器文件全部乱名且不可执行，`test_macos_launchers` 批量失败（找不到匹配启动器 / `os.access(…, X_OK)` 为 False）。

**解决**：含非 ASCII 文件名或可执行位的差量**不要走 `patch`**——仓库内用 `git apply`（能正确处理转义名与 mode），跨机传输时对受影响目录打 `tar`（保留 UTF-8 名与权限位）再解压。已乱名的文件先按 `ls | grep '\\\\'` 找出删除。

## 五、验证基线

| 项 | 值 |
|---|---|
| 全量测试（917145d 合并树） | **352 passed / 7 skipped / 0 failed**（19.6s） |
| 7 个跳过 | Windows 专属入口：fastboot 安装器分派 1、`test_fastboot_release_archive` BAT 2、`test_sd_recovery_public` BAT 3、`test_workflows` BAT 1 |
| 环境 | macOS 26.6.2 arm64 / Python 3.12.16 / git worktree @917145d + 35 文件差量补丁 |
| 证据 | `docs/qa/2026-11-14-erlla-macos-port-upstream-917145d-cloud-vm-test.json`（pickerel 仓） |
| 上一基线 | 286 collected → 282 passed / 4 skipped / 0 failed（`2026-11-14-erlla-macos-port-cloud-vm-test.json`） |
