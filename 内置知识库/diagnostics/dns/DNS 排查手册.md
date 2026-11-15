# DNS 排查手册

域名解析异常的标准排查路径：先定位是「解析不出来」还是「解析错了」，再按解析链路逐层验证。

## 解析链路（Linux）

1. 应用先查 `/etc/nsswitch.conf` 的 hosts 顺序（通常 `files dns`：先 `/etc/hosts` 再 DNS）。
2. DNS 服务器取 `/etc/resolv.conf` 的 nameserver；systemd-resolved 环境它往往指向 127.0.0.53，真实上游要查 `resolvectl status`。
3. 注意：容器内 `/etc/resolv.conf` 由运行时注入，宿主机改了不影响已有容器。

## 常用命令

```bash
dig +short example.com            # 只看结果
dig example.com A +noall +answer  # 看 TTL 与完整应答
dig @8.8.8.8 example.com          # 指定 DNS 服务器对比
dig +trace example.com            # 逐级追踪权威链
nslookup example.com              # 快速验证
getent hosts example.com          # 走系统解析栈（含 /etc/hosts）
```

## 常见故障与判定

| 现象 | 常见原因 | 验证方法 |
|---|---|---|
| 个别机器解析失败 | resolv.conf 配错、本地 DNS 挂了 | `dig @<上游>` 对比 |
| 解析结果与预期不一致 | /etc/hosts 旧记录、缓存负缓存 | `getent hosts` 看 files 层 |
| 时好时坏 | 多 nameserver 轮询到坏节点、上游超时 | `dig` 指定各上游分别测 |
| 改了记录不生效 | TTL 未过期、上游缓存 | 看应答 TTL；等 TTL 或换服务器验证 |
| 只有 IPv6/IPv4 失败 | A/AAAA 记录缺失或防火墙拦了族 | 分别 dig A 与 AAAA |
| 证书报主机名不匹配 | 域名解析到错误 IP（如 CDN 旧节点） | dig 对比权威 NS 应答 |

## 处理要点

- 改 `/etc/hosts` 只影响本机 files 层，服务环境（容器/Pod）要另行处理。
- 生产变更 DNS 记录先把 TTL 调低，改完观察再调回，缩短故障半径。
- 域名整体失效先查是否到期（`whois` 的 Expiry Date）。
