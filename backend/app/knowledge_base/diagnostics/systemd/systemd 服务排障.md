# systemd 服务排障

服务起不来、反复重启、状态异常时的排查顺序：先看状态与日志，再看依赖与环境。

## 排查顺序

```bash
systemctl status nginx -l        # 状态 + 最近日志，先看 Active 与退出码
journalctl -u nginx -b --no-pager | tail -50   # 本次启动的服务日志
journalctl -u nginx -f           # 实时跟踪
systemctl list-dependencies nginx   # 依赖树
```

`Active: failed` 时看 `Process: ... status=` 的退出码；`activating (auto-restart)` 说明在崩溃循环，先 `systemctl stop` 再排查，避免日志刷屏。

## 常见原因

| 现象 | 原因 | 处理 |
|---|---|---|
| start 后立即 failed | 配置文件语法错、端口占用 | 看日志里的报错行；`ss -lntp` 查端口 |
| 权限相关报错 | User/Group 权限、SELinux/AppArmor | 看 unit 的 User= 与目录属主 |
| 环境变量缺失 | EnvironmentFile 缺失或没加载 | 确认 EnvironmentFile 路径与内容 |
| 依赖服务未就绪 | After/Requires 目标没起 | `systemctl start` 依赖项或补依赖 |
| 开机不自启 | enable 没做 | `systemctl enable <svc>` |
| 单元文件改了没生效 | 没重载 unit | `systemctl daemon-reload` 后重启服务 |

## 状态词义

- `active (running)`：正常；`active (exited)`：oneshot 执行完正常退出。
- `inactive (dead)`：没在跑（可能没启动）。
- `failed`：启动失败或异常退出，`systemctl reset-failed` 清失败标记后可重试。
- `masked`：被屏蔽（指向 /dev/null），`unmask` 解除。

## 纪律

- 改 unit 文件后先 `systemctl daemon-reload` 再重启服务。
- 排查崩溃循环时先 stop，处理完再 start，别在循环里反复看日志。
- 生产禁用 `systemctl isolate` 等高危操作；改自启用 enable/disable，不用手删符号链接。
