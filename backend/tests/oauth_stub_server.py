"""测试用本地 OAuth 授权服务器（AS）：真实实现 RFC 8414 / 7591 / PKCE。

给 test_mcp_oauth.py 当子进程起；文件名不以 test_ 开头，pytest 不会收集。
端口从命令行第一个参数读；GET /stats 返回调用计数供测试断言。
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(sys.argv[1])
ISSUER = f"http://127.0.0.1:{PORT}"
STATS = {"register": 0, "authorize": 0, "token": 0, "refresh": 0}
# code -> {"challenge": ..., "redirect_uri": ...}：真实 PKCE 校验用
CODES: dict[str, dict] = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        """静音访问日志，测试输出保持干净。"""

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/.well-known/oauth-authorization-server":
            self._json(
                200,
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "registration_endpoint": f"{ISSUER}/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "scopes_supported": ["mcp"],
                },
            )
            return
        if parsed.path == "/authorize":
            STATS["authorize"] += 1
            query = parse_qs(parsed.query)
            redirect_uri = query.get("redirect_uri", [""])[0]
            state = query.get("state", [""])[0]
            challenge = query.get("code_challenge", [""])[0]
            code = f"code-{secrets.token_hex(4)}"
            CODES[code] = {"challenge": challenge, "redirect_uri": redirect_uri}
            sep = "&" if "?" in redirect_uri else "?"
            self.send_response(302)
            self.send_header("Location", f"{redirect_uri}{sep}code={code}&state={state}")
            self.end_headers()
            return
        if parsed.path == "/stats":
            self._json(200, STATS)
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        if parsed.path == "/register":
            STATS["register"] += 1
            body = json.loads(raw or "{}")
            self._json(
                201,
                {
                    "client_id": "stub-client",
                    "redirect_uris": body.get("redirect_uris", []),
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
            return
        if parsed.path == "/token":
            form = {key: values[0] for key, values in parse_qs(raw).items()}
            grant = form.get("grant_type", "")
            if grant == "authorization_code":
                STATS["token"] += 1
                entry = CODES.pop(form.get("code", ""), None)
                if entry is None:
                    self._json(400, {"error": "invalid_grant"})
                    return
                verifier = form.get("code_verifier", "")
                digest = (
                    base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
                    .rstrip(b"=")
                    .decode("ascii")
                )
                if entry["challenge"] != digest:
                    self._json(400, {"error": "invalid_grant", "description": "PKCE"})
                    return
                self._json(
                    200,
                    {
                        "access_token": "access-1",
                        "refresh_token": "refresh-1",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
                return
            if grant == "refresh_token":
                STATS["refresh"] += 1
                if form.get("refresh_token") != "refresh-1":
                    self._json(400, {"error": "invalid_grant"})
                    return
                self._json(
                    200,
                    {
                        "access_token": "access-2",
                        "refresh_token": "refresh-2",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
                return
            self._json(400, {"error": "unsupported_grant_type"})
            return
        self._json(404, {"error": "not_found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
