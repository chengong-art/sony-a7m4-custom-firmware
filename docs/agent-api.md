# Agent 接口

[English](agent-api.en.md) · [返回](../README.md)

这套工具给 agent 的入口只有一个：**JSON-RPC 2.0 over stdio**。没有 TCP 监听器，
不开端口，不依赖终端交互——AI 与脚本用同一条路，人用的图形界面也走同一条路。

```bash
a7m4 agent              # 长时间会话：读一行请求，写一行响应
a7m4 --demo agent       # 同上，但用合成相机
a7m4 state              # 一次性：打印状态快照后退出（等价于 status 方法）
a7m4 state --include session,host --backup-limit 3
```

## 信封

请求一行一个 JSON 对象；响应按 `id` 对应；运行期间还会收到**通知**（无 `id`）。

```json
{"jsonrpc":"2.0","id":1,"method":"status","params":{"include":["session","host"]}}
{"jsonrpc":"2.0","id":1,"result":{ ... }}
{"jsonrpc":"2.0","method":"progress","params":{"phase":"backup","partition":"nflasha1","percent":42}}
```

- 输出一律 UTF-8、单行 JSON、每行 flush；**不要在 stdout 上打日志**。
- 请求上限 1 MiB，超过报 `REQUEST_SIZE`。
- `id` 必须是标量（数字或字符串）；缺 `id`、`jsonrpc` 不是 `"2.0"`、或 `id` 是对象/数组 → `REQUEST`。
- 第一个该调的方法永远是 `capabilities`：它自带方法目录与状态字段说明。

## 方法

| 方法 | 作用 | 主要参数 |
| --- | --- | --- |
| `capabilities` | 自描述：方法目录、状态模式、通知类型 | 无 |
| `status` | **统一状态快照**（只读，不发设备命令） | `include`、`backup_limit` |
| `doctor` | 校验哈希钉住的资源包 | 无 |
| `connect_backup` | 进入 fastboot 并做**强制完整备份** | `destination`、`enter` |
| `inspect` | 从已验证会话快照读版本与更新器状态 | 无 |
| `keygen` | 生成本地 RSA-2048 密钥对（**不返回私钥内容**） | `directory` |
| `prepare` | 用已验证备份构建可复核镜像（**不写相机**） | `action`、`value`、`public_key_path`、`source_backup`、`partition` |
| `apply` | 应用一个已复核计划并做完整回读校验 | `plan_id`、`confirmation` |
| `reboot` | 退出 fastboot，**作废本次会话的写权限** | 无 |

参数是白名单：出现未知键即 `PARAMS`，不会静默忽略。

## 状态快照

`status` 是"现在是什么状态"的唯一答案，六个段可按需取（`include`）：

```json
{
  "api_version": 1,
  "protocol": "jsonrpc-2.0-stdio",
  "toolkit_version": "0.2.0",
  "sections": ["backups", "device", "host", "resources", "session"],

  "state": "disconnected",
  "write_enabled": false,
  "backup": null,
  "partitions": [],
  "last_result": null,

  "session": {"connected": false, "fastboot": false, "backup_folder": null,
              "backup_complete": null, "device": null,
              "plans_pending": 0, "partitions_known": 0},
  "next_actions": ["doctor", "connect_backup"],

  "resources": {"ready": true, "errors": [], "distribution": "LOCAL-ONLY; …", "checked": true},

  "device": {"payload_usb_present": false, "bootsel_volumes": [], "observation_only": true},

  "host": {"platform": "macos", "data_dir": "…/A7M4Toolkit",
           "lock": {"path": "…/device-lock", "held_by_this_process": false, "note": "…"}},

  "backups": {"root": "…/backups", "count": 1, "returned": 1,
              "items": [{"folder": "…", "name": "…", "complete": true, "created": 1792874931.39,
                         "partitions": 17, "device": {...}, "readable": true}]}
}
```

- `state` / `write_enabled` / `backup` / `partitions` / `last_result` 是**会话内**状态，
  与旧客户端的字段保持一致；`session` 是对同一事实的显式展开。
- `write_enabled` 只有在**本次会话**做完一次完整备份后才为 `true`。备份是相机侧的前提，
  不是流程建议：没有它，写入类方法一律 `BACKUP_REQUIRED`。
- `device` 段是**纯观察**：只查 USB 与挂载卷，不发任何设备命令。轮询它不会动相机。
- `resources.ready` 会校验资源包哈希（约 0.3 秒）；只想要会话状态时用
  `{"include":["session"]}`。
- `host.lock.held_by_this_process` 只回答"**本进程**是否持锁"。别的进程持锁无法纯查询得知，
  只会在真正写入时以 `BUSY` 暴露——这是 POSIX 建议锁的性质，不假装能绕过。
- `backups.count` 是磁盘上的目录数，`items` 只描述最近的 `backup_limit` 个。

## 错误

错误响应的 `data.code` 是稳定的机器可读码：

```json
{"jsonrpc":"2.0","id":7,"error":{"code":-32000,"message":"Unknown method",
 "data":{"code":"METHOD","hint":""}}}
```

| `data.code` | 含义 |
| --- | --- |
| `METHOD` | 方法不存在（先读 `capabilities`） |
| `PARAMS` | 未知/非法参数键，或取值不合法 |
| `REQUEST` | 信封不合法（缺 `id`、`jsonrpc` 不对、`id` 非标量） |
| `REQUEST_SIZE` | 单行请求超过 1 MiB |
| `INVALID_REQUEST` | JSON 本身坏了 |
| `BACKUP_REQUIRED` | 本次会话还没有完整备份 |
| `BUSY` | 另一个进程正持设备锁 |
| `LOCK` / `LOCK_LOST` | 没持锁 / 持有期间锁失效（例如锁目录被删） |
| `ASSETS_MISSING` / `ASSET_INTEGRITY` / `ASSET_PERMISSION` | 资源包缺失、哈希不符、不可执行 |
| `USB_COMMUNICATION`、`DISK_SPACE`、`CONFIRMATION`、`INTERNAL` … | 见 `a7m4/common.py` |

`INTERNAL` 表示未预期异常；除它之外都应视为"可以据此分支"的确定性结果。

## 安全契约（未改变）

- **不提供任意命令执行**：`arbitrary_commands: false`，方法表就是全部能力。
- **`apply` 需要两段式确认**：先 `prepare` 得到 `plan_id` 与确认串，`apply` 必须原样回传；
  计划与会话绑定，`reboot` 后失效。
- **`keygen` 不回传私钥内容**；密钥写在 `data_dir` 下。
- **没有 TCP 监听器**：不会在网络层面暴露；一个会话只服务一个客户端。

## 示例

```bash
# 一次性快照
a7m4 state | jq '.state, .resources.ready, .backups.count'

# 会话：拿能力表 → 查状态 → 跑 doctor
{ echo '{"jsonrpc":"2.0","id":1,"method":"capabilities"}'
  echo '{"jsonrpc":"2.0","id":2,"method":"status","params":{"include":["session"]}}'
  echo '{"jsonrpc":"2.0","id":3,"method":"doctor"}'
} | a7m4 agent | jq -c '{id, ok:(.error==null), code:.error.data.code}'
```

Python 客户端（把 `progress` 当事件流消费）：

```python
import json, subprocess

proc = subprocess.Popen(["a7m4", "agent"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
def call(method, **params):
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    while True:
        message = json.loads(proc.stdout.readline())
        if message.get("id") == 1:
            return message["result"] if "result" in message else message["error"]
        print("progress:", message["params"])      # 通知
```

## 边界

- 这套接口在 macOS 上已验证到"资源包就绪 + 会话状态 + 备份盘点 + 参数/错误契约"这一层；
  **相机操作 = 0**，所以 `connect_backup`/`apply` 的真实设备行为仍未验证。
- 图形界面与 agent 走同一套方法（同一份守卫），不再各自实现交互。
