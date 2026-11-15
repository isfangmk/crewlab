# TLS 证书排查

HTTPS 报错先分清是**证书本身**问题还是**链路**问题。客户端报错文本是最快的线索。

## 看证书现状

```bash
# 看远端证书有效期与主体（SNI 指定域名）
openssl s_client -connect example.com:443 -servername example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates

# 看完整证书链（找缺失的中间证书）
openssl s_client -showcerts -connect example.com:443 -servername example.com </dev/null

# 本地证书文件
openssl x509 -in cert.pem -noout -text
```

## 报错对照

| 客户端报错 | 常见原因 | 处理 |
|---|---|---|
| CERT_HAS_EXPIRED | 证书过期 | 续期并重载服务 |
| UNABLE_TO_VERIFY_LEAF_SIGNATURE | 缺中间证书 | 补全证书链（服务端要下发中间证书） |
| self signed certificate | 用了自签/私有 CA | 客户端装 CA 或改签发 |
| Hostname mismatch / CN 无效 | 域名不在 SAN 里 | 重签含正确 SAN 的证书 |
| SSL routines 协议错 | 两端 TLS 版本/套件不兼容 | 对齐最低 TLS 版本（建议 TLS1.2+） |

## 续期要点

- Let's Encrypt：`certbot renew --dry-run` 先试续；确认定时任务在跑。
- 续期后必须重载服务才生效（nginx -s reload / 服务热加载），重载后用 openssl 再验一次生效结果。
- 证书透明日志（ct）可查域名签发历史，排查「谁签的」。
- 通配符证书不含二级子域（`*.a.com` 不覆盖 `x.y.a.com`），规划 SAN 时注意。

## 预防

- 监控证书到期（剩 30 天告警）；多节点部署逐节点验证。
- 变更证书前保留旧证书回滚；链文件顺序为「站点证书 → 中间证书」。
