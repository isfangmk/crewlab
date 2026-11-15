# NTP 与时区

时间问题的表现往往不在时间本身：日志对不上、证书校验失败、定时任务错点、分布式校验超时。排查从「时区」与「时钟」两轴分开看。

## 看现状

```bash
timedatectl                # 时区、NTP 开关、时间是否同步
timedatectl timesync-status   # systemd-timesyncd 详情
chronyc tracking           # chrony 环境的同步状态
chronyc sources -v         # 上游源与偏差
date -R                    # 当前时间与偏移
```

`timedatectl` 里 `NTP service: inactive` 或 `System clock synchronized: no` 就是没同步；`Local time` 的偏移与预期时区不符就是时区问题。

## 时区问题

- 设置：`timedatectl set-timezone Asia/Shanghai`；容器里常缺时区数据，Dockerfile 里 `apk add tzdata` 或挂载 `/etc/localtime`。
- 典型症状：日志时间差整数小时（时区错）、日志时间差几分钟（时钟漂移）——两类原因不同，别混着修。
- 应用层还有自己的时区配置（如系统设置里的时区），与 OS 时区不一致时以应用配置为准，但两边要对齐。

## 时钟漂移

- 虚拟机/容器常见：宿主机时钟漂移会带偏所有来宾。
- chrony 环境 `chronyc makestep` 手动大步校正；NTP 服务起不来先看 UDP 123 是否被防火墙放行。
- 不要用 `date -s` 手工改生产时间（对运行中服务是破坏性的）；只做临时定位。

## 关联故障

| 现象 | 时间相关原因 |
|---|---|
| TLS 证书校验失败（未过期却报过期） | 系统时间偏移超证书有效期窗口 |
| 定时任务/触发器提前或滞后触发 | 时区错（Cron 用本地时区） |
| 分布式请求偶发超时 | 节点间时钟偏差导致令牌/签名校验失败 |
| 日志按时间聚合对不上 | 各服务时区不一致 |

## 纪律

- 生产统一 NTP（chrony 或 systemd-timesyncd），所有节点同上游。
- 变更时区先确认应用是否依赖 OS 时区；容器化部署把时区固化进镜像或编排配置。
