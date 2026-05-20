#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import json
import secrets
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
WORKSPACE_ROOT = Path("/Users/zhangzheng/KianWorkspace")
SETTINGS_PATH = WORKSPACE_ROOT / ".kian/settings.json"
TOOL_ROOT = Path(__file__).resolve().parent
STATE_PATH = TOOL_ROOT / "state/user-auth.json"
DEFAULT_REDIRECT_URI = "http://localhost:8765/feishu/oauth/callback"
AUTH_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_V2_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
TOKEN_V1_URL = "https://open.feishu.cn/open-apis/authen/v1/access_token"
REFRESH_V1_URL = "https://open.feishu.cn/open-apis/authen/v1/refresh_access_token"
TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
# Keep the default OAuth request minimal. Some Feishu permission names that
# are valid as app/tenant scopes are rejected on the OAuth authorize page
# (error 20043) when placed in the `scope` query parameter. Additional scopes
# can be tested explicitly via `auth-url --scope ...`.
DEFAULT_SCOPES = ["offline_access"]


class FeishuUserAuthError(RuntimeError):
    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.payload = payload or {}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req_headers = dict(headers or {})
    if payload is not None:
        req_headers.setdefault("Content-Type", "application/json; charset=utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload_json = json.loads(body)
        except Exception:
            payload_json = {"raw": body}
        raise FeishuUserAuthError(f"HTTP {exc.code} calling {url}", payload_json) from exc
    except urllib.error.URLError as exc:
        raise FeishuUserAuthError(f"Network error calling {url}: {exc.reason}", {"raw": str(exc.reason)}) from exc
    except http.client.RemoteDisconnected as exc:
        raise FeishuUserAuthError(f"Network error calling {url}: remote disconnected", {"raw": str(exc)}) from exc


def extract_code(value: str) -> str:
    text = value.strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.query:
        query = urllib.parse.parse_qs(parsed.query)
        code_values = query.get("code")
        if code_values and code_values[0]:
            return code_values[0]
    return text


def iso_from_epoch(value: Optional[int]) -> Optional[str]:
    if not value:
        return None
    return datetime.fromtimestamp(value, tz=TZ).isoformat()


class FeishuUserAuth:
    def __init__(
        self,
        settings_path: Path = SETTINGS_PATH,
        state_path: Path = STATE_PATH,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        scopes: Optional[Sequence[str]] = None,
    ):
        self.settings_path = settings_path
        self.state_path = state_path
        self.redirect_uri = redirect_uri
        self.scopes = list(DEFAULT_SCOPES if scopes is None else scopes)
        self.settings = load_json(settings_path, {})
        try:
            feishu = self.settings["chatChannels"]["feishu"]
        except Exception as exc:
            raise FeishuUserAuthError(f"Invalid settings file, missing chatChannels.feishu: {settings_path}") from exc
        self.app_id = feishu.get("appId")
        self.app_secret = feishu.get("appSecret")
        if not self.app_id or not self.app_secret:
            raise FeishuUserAuthError("Missing Feishu appId/appSecret in settings.")

    def load_state(self) -> Dict[str, Any]:
        data = load_json(self.state_path, {})
        return data if isinstance(data, dict) else {}

    def save_state(self, state: Dict[str, Any]) -> None:
        save_json(self.state_path, state)

    def build_auth_url(self, state: Optional[str] = None) -> str:
        oauth_state = state or secrets.token_urlsafe(24)
        params = {
            "client_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": oauth_state,
        }
        if self.scopes:
            params["scope"] = " ".join(self.scopes)
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def tenant_access_token(self) -> str:
        data = http_json("POST", TENANT_TOKEN_URL, payload={"app_id": self.app_id, "app_secret": self.app_secret})
        token = data.get("tenant_access_token")
        if data.get("code") != 0 or not token:
            raise FeishuUserAuthError("Failed to get tenant_access_token", data)
        return str(token)

    def _normalize_token_payload(self, payload: Dict[str, Any], source: str) -> Dict[str, Any]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        access_token = data.get("access_token") or data.get("user_access_token")
        refresh_token = data.get("refresh_token")
        if payload.get("code") not in (None, 0) or not access_token:
            raise FeishuUserAuthError(f"Failed to get user_access_token via {source}", payload)
        now = int(time.time())
        expires_in = int(data.get("expires_in") or 0)
        refresh_expires_in = int(data.get("refresh_expires_in") or 0)
        state = self.load_state()
        state.update(
            {
                "app_id": self.app_id,
                "redirect_uri": self.redirect_uri,
                "open_id": data.get("open_id") or state.get("open_id"),
                "union_id": data.get("union_id") or state.get("union_id"),
                "user_id": data.get("user_id") or state.get("user_id"),
                "user_access_token": str(access_token),
                "refresh_token": str(refresh_token or state.get("refresh_token") or ""),
                "expires_at": now + expires_in if expires_in else state.get("expires_at"),
                "refresh_expires_at": now + refresh_expires_in if refresh_expires_in else state.get("refresh_expires_at"),
                "scope": data.get("scope") or state.get("scope") or " ".join(self.scopes),
                "token_source": source,
                "updated_at": datetime.now(TZ).isoformat(),
            }
        )
        self.save_state(state)
        return state

    def exchange_code(self, code: str) -> Dict[str, Any]:
        clean_code = extract_code(code)
        v2_payload = {
            "grant_type": "authorization_code",
            "code": clean_code,
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "redirect_uri": self.redirect_uri,
        }
        try:
            return self._normalize_token_payload(http_json("POST", TOKEN_V2_URL, payload=v2_payload), "authen.v2.oauth.token")
        except FeishuUserAuthError as v2_error:
            tenant_token = self.tenant_access_token()
            v1_payload = {"grant_type": "authorization_code", "code": clean_code}
            try:
                return self._normalize_token_payload(
                    http_json("POST", TOKEN_V1_URL, payload=v1_payload, headers={"Authorization": f"Bearer {tenant_token}"}),
                    "authen.v1.access_token",
                )
            except FeishuUserAuthError as v1_error:
                raise FeishuUserAuthError(
                    "Failed to exchange authorization code with both Feishu OAuth v2 and v1 endpoints",
                    {"v2": v2_error.payload, "v1": v1_error.payload},
                ) from v1_error

    def refresh(self) -> Dict[str, Any]:
        state = self.load_state()
        refresh_token = str(state.get("refresh_token") or "")
        if not refresh_token:
            raise FeishuUserAuthError("No refresh_token in user auth state.")
        v2_payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.app_id,
            "client_secret": self.app_secret,
        }
        try:
            return self._normalize_token_payload(http_json("POST", TOKEN_V2_URL, payload=v2_payload), "authen.v2.oauth.refresh")
        except FeishuUserAuthError as v2_error:
            tenant_token = self.tenant_access_token()
            v1_payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
            try:
                return self._normalize_token_payload(
                    http_json("POST", REFRESH_V1_URL, payload=v1_payload, headers={"Authorization": f"Bearer {tenant_token}"}),
                    "authen.v1.refresh_access_token",
                )
            except FeishuUserAuthError as v1_error:
                raise FeishuUserAuthError(
                    "Failed to refresh user_access_token with both Feishu OAuth v2 and v1 endpoints",
                    {"v2": v2_error.payload, "v1": v1_error.payload},
                ) from v1_error

    def ensure_access_token(self, leeway_seconds: int = 300) -> str:
        state = self.load_state()
        token = str(state.get("user_access_token") or "")
        expires_at = int(state.get("expires_at") or 0)
        if token and expires_at > int(time.time()) + leeway_seconds:
            return token
        refreshed = self.refresh()
        token = str(refreshed.get("user_access_token") or "")
        if not token:
            raise FeishuUserAuthError("Refresh completed but user_access_token is missing.")
        return token

    def status(self) -> Dict[str, Any]:
        state = self.load_state()
        now = int(time.time())
        expires_at = int(state.get("expires_at") or 0)
        refresh_expires_at = int(state.get("refresh_expires_at") or 0)
        return {
            "state_path": str(self.state_path),
            "app_id": self.app_id,
            "redirect_uri": state.get("redirect_uri") or self.redirect_uri,
            "open_id": state.get("open_id"),
            "has_user_access_token": bool(state.get("user_access_token")),
            "has_refresh_token": bool(state.get("refresh_token")),
            "expires_at": iso_from_epoch(expires_at),
            "refresh_expires_at": iso_from_epoch(refresh_expires_at),
            "is_access_token_valid": bool(state.get("user_access_token")) and expires_at > now,
            "is_refresh_token_valid": bool(state.get("refresh_token")) and (not refresh_expires_at or refresh_expires_at > now),
            "token_source": state.get("token_source"),
            "updated_at": state.get("updated_at"),
        }

    def test(self) -> Dict[str, Any]:
        token = self.ensure_access_token()
        data = http_json("GET", USER_INFO_URL, headers={"Authorization": f"Bearer {token}"})
        if data.get("code") not in (None, 0):
            raise FeishuUserAuthError("Failed to call authen.v1.user_info with user token", data)
        return {"ok": True, "endpoint": "authen.v1.user_info", "response": data}


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Authorize Feishu user OAuth and manage user_access_token.")
    # NOTE: --config is recognised but not yet honoured in 0.1.x; the wiring
    # in step 2 of the 0.2.0 refactor will hook it up to scripts/runtime.py.
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to feishu-task-sync config.json. Recognised in 0.1.x but the "
            "existing --settings-path / --state-path / --redirect-uri flags still "
            "take effect; full integration lands in 0.2.0."
        ),
    )
    parser.add_argument("--settings-path", default=str(SETTINGS_PATH))
    parser.add_argument("--state-path", default=str(STATE_PATH))
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI)
    subparsers = parser.add_subparsers(dest="command", required=True)
    auth_url = subparsers.add_parser("auth-url", help="Print OAuth authorization URL.")
    auth_url.add_argument("--scope", action="append", default=None, help="OAuth scope to request; repeatable. Defaults to offline_access only. Use --no-scope to omit scope entirely.")
    auth_url.add_argument("--no-scope", action="store_true", help="Omit the scope query parameter.")
    exchange = subparsers.add_parser("exchange", help="Exchange authorization code for user tokens.")
    group = exchange.add_mutually_exclusive_group(required=True)
    group.add_argument("--code")
    group.add_argument("--redirect-url")
    subparsers.add_parser("refresh", help="Refresh user_access_token.")
    subparsers.add_parser("status", help="Show sanitized user token status.")
    subparsers.add_parser("test", help="Validate user token with a lightweight Feishu API.")
    return parser.parse_args(argv)


def sanitized_exchange_result(state: Dict[str, Any], auth: FeishuUserAuth) -> Dict[str, Any]:
    return {
        "ok": True,
        "state_path": str(auth.state_path),
        "open_id": state.get("open_id"),
        "has_user_access_token": bool(state.get("user_access_token")),
        "has_refresh_token": bool(state.get("refresh_token")),
        "expires_at": iso_from_epoch(int(state.get("expires_at") or 0)),
        "refresh_expires_at": iso_from_epoch(int(state.get("refresh_expires_at") or 0)),
        "token_source": state.get("token_source"),
    }


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    scopes = None
    if args.command == "auth-url":
        if args.no_scope:
            scopes = []
        elif args.scope is not None:
            scopes = args.scope
    auth = FeishuUserAuth(Path(args.settings_path), Path(args.state_path), args.redirect_uri, scopes=scopes)
    if args.command == "auth-url":
        print(f"redirect_uri: {auth.redirect_uri}")
        print(f"scopes: {' '.join(auth.scopes) if auth.scopes else '(none)'}")
        print(f"auth_url: {auth.build_auth_url()}")
        return 0
    if args.command == "exchange":
        code = args.code or extract_code(args.redirect_url)
        print(json.dumps(sanitized_exchange_result(auth.exchange_code(code), auth), ensure_ascii=False, indent=2))
        return 0
    if args.command == "refresh":
        print(json.dumps(sanitized_exchange_result(auth.refresh(), auth), ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(auth.status(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "test":
        result = auth.test()
        response = result.get("response") if isinstance(result.get("response"), dict) else {}
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        print(
            json.dumps(
                {
                    "ok": True,
                    "endpoint": result.get("endpoint"),
                    "open_id": data.get("open_id"),
                    "name": data.get("name"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    raise FeishuUserAuthError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
