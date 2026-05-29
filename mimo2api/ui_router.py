import os
import json
import re
import time
import asyncio
import logging
import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .auth import (
    create_webui_session_token,
    get_webui_cookie_name,
    get_webui_session_ttl,
    get_webui_username,
    is_ai_auth_enabled,
    is_web_auth_enabled,
    is_webui_authenticated,
    verify_webui_login,
    webui_cookie_secure,
)
from .gateway_state import state
from .manager import trigger_rebuild, trigger_users_reload

router = APIRouter()
logger = logging.getLogger(__name__)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_DIR = os.path.join(ROOT_DIR, "users")
STATUS_CACHE_TTL_SECONDS = 120
STATUS_FETCH_RETRIES = 2
STATUS_FETCH_TIMEOUT_SECONDS = 8
DESTROYED_REBUILD_COOLDOWN_SECONDS = 60
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_FAILURES = 5
USER_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
_status_cache: dict[str, dict] = {}
_destroyed_rebuild_last: dict[str, float] = {}
_login_failures: dict[str, list[float]] = {}


@router.get("/")
async def root_page():
    return RedirectResponse(url="/webui", status_code=307)

@router.get("/webui")
async def webui_page():
    ui_path = os.path.join(os.path.dirname(__file__), "webui.html")
    if os.path.exists(ui_path):
        with open(ui_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return Response("webui.html not found", status_code=404)

@router.get("/api/system/status")
async def api_status():
    return JSONResponse({"active_clients": len(state.active_clients)})


@router.get("/api/auth/session")
async def api_auth_session(request: Request):
    auth_enabled = is_web_auth_enabled()
    authenticated = is_webui_authenticated(request)
    return JSONResponse({
        "enabled": auth_enabled,
        "authenticated": authenticated,
        "username": get_webui_username(),
        "ai_auth_enabled": is_ai_auth_enabled(),
    })


def login_key(request: Request, username: str) -> str:
    client_host = request.client.host if request.client else "unknown"
    return f"{client_host}:{username or '-'}"


def is_login_limited(key: str) -> bool:
    now = time.time()
    failures = [ts for ts in _login_failures.get(key, []) if now - ts <= LOGIN_WINDOW_SECONDS]
    _login_failures[key] = failures
    return len(failures) >= LOGIN_MAX_FAILURES


def record_login_failure(key: str) -> None:
    now = time.time()
    failures = [ts for ts in _login_failures.get(key, []) if now - ts <= LOGIN_WINDOW_SECONDS]
    failures.append(now)
    _login_failures[key] = failures


def clear_login_failures(key: str) -> None:
    _login_failures.pop(key, None)


@router.post("/api/auth/login")
async def api_auth_login(request: Request):
    if not is_web_auth_enabled():
        return JSONResponse({"ok": True, "enabled": False, "username": get_webui_username()})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "请求体不是合法 JSON"}, status_code=400)

    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    key = login_key(request, username)
    if is_login_limited(key):
        return JSONResponse({"detail": "登录失败次数过多，请稍后再试"}, status_code=429)
    if not verify_webui_login(username, password):
        record_login_failure(key)
        return JSONResponse({"detail": "用户名或密码错误"}, status_code=401)
    clear_login_failures(key)

    response = JSONResponse({"ok": True, "enabled": True, "username": get_webui_username()})
    response.set_cookie(
        key=get_webui_cookie_name(),
        value=create_webui_session_token(get_webui_username()),
        max_age=get_webui_session_ttl(),
        httponly=True,
        samesite="lax",
        secure=webui_cookie_secure(),
        path="/",
    )
    return response


@router.post("/api/auth/logout")
async def api_auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(key=get_webui_cookie_name(), path="/")
    return response

def token_preview(token: str | None) -> str:
    if not token:
        return ""
    token = str(token)
    if len(token) <= 10:
        return "*" * len(token)
    return f"{token[:6]}...{token[-4:]}"


def cached_status(uid: str) -> dict | None:
    cached = _status_cache.get(uid)
    if not cached:
        return None
    if time.time() - float(cached.get("cached_at", 0)) > STATUS_CACHE_TTL_SECONDS:
        return None
    return cached


def maybe_trigger_rebuild_for_destroyed(uid: str) -> None:
    now = time.time()
    last_notified = _destroyed_rebuild_last.get(uid, 0)
    if now - last_notified < DESTROYED_REBUILD_COOLDOWN_SECONDS:
        return
    _destroyed_rebuild_last[uid] = now
    logger.warning(f"检测到账号 {uid} 的 Claw 状态为 DESTROYED，触发 manager 立即重建。")
    trigger_users_reload()
    trigger_rebuild()


async def fetch_user_status(data: dict) -> dict:
    uid = str(data.get("userId") or "").strip()
    cookies = {
        "serviceToken": data.get("serviceToken", ""),
        "userId": uid,
        "xiaomichatbot_ph": data.get("xiaomichatbot_ph", "")
    }
    url = "https://aistudio.xiaomimimo.com/open-apis/user/mimo-claw/status"
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://aistudio.xiaomimimo.com",
        "Referer": "https://aistudio.xiaomimimo.com/",
        "User-Agent": "Mozilla/5.0"
    }
    last_error = ""
    async with httpx.AsyncClient(timeout=STATUS_FETCH_TIMEOUT_SECONDS) as c:
        for attempt in range(STATUS_FETCH_RETRIES):
            try:
                r = await c.get(url, cookies=cookies, headers=headers)
                if r.status_code == 401:
                    result = {**data, "claw_status": "EXPIRED(401)", "remain_sec": 0, "status_source": "live"}
                    _status_cache[uid] = {**result, "cached_at": time.time()}
                    return result
                r.raise_for_status()
                r_data = r.json()
                st = str(r_data.get("data", {}).get("status", "UNKNOWN")).strip() or "UNKNOWN"
                if st == "DESTROYED":
                    maybe_trigger_rebuild_for_destroyed(uid)
                expire_ms = r_data.get("data", {}).get("expireTime")
                remain_sec = max(0, int(int(expire_ms) / 1000 - time.time())) if expire_ms else 0
                result = {**data, "claw_status": st, "remain_sec": remain_sec, "status_source": "live"}
                _status_cache[uid] = {**result, "cached_at": time.time()}
                return result
            except Exception as exc:
                last_error = str(exc)
                if attempt + 1 < STATUS_FETCH_RETRIES:
                    await asyncio.sleep(0.5)

    cached = cached_status(uid)
    if cached:
        return {**data, "claw_status": cached.get("claw_status", "UNKNOWN"), "remain_sec": cached.get("remain_sec", 0), "status_source": "cache", "status_error": last_error[:200]}

    logger.warning(f"查询账号 {uid} Claw 状态失败: {last_error}")
    return {**data, "claw_status": "UNKNOWN", "remain_sec": 0, "status_source": "unknown", "status_error": last_error[:200]}

@router.get("/api/users/list")
async def api_users_list():
    raw_users = []
    if os.path.exists(USERS_DIR):
        for fn in os.listdir(USERS_DIR):
            if fn.startswith("user_") and fn.endswith(".json"):
                try:
                    with open(os.path.join(USERS_DIR, fn), "r", encoding="utf-8") as f:
                        raw_users.append(json.load(f))
                except Exception as exc:
                    logger.warning(f"读取用户文件失败 {fn}: {exc}")

    # 并发查询所有用户的实例状态
    tasks = [fetch_user_status(rd) for rd in raw_users]
    results = await asyncio.gather(*tasks) if raw_users else []

    users = []
    for data in results:
        users.append({
            "userId": data.get("userId"),
            "name": data.get("name"),
            "hasServiceToken": bool(data.get("serviceToken")),
            "tokenPreview": token_preview(data.get("serviceToken")),
            "claw_status": data.get("claw_status", "UNKNOWN"),
            "remain_sec": data.get("remain_sec", 0),
            "status_source": data.get("status_source", "unknown"),
            "status_error": data.get("status_error", ""),
        })
    return JSONResponse({"users": users})

@router.post("/api/users/add")
async def api_users_add(request: Request):
    try:
        body = await request.json()
        raw_text = body.get("raw_text", "")
        # 解析正则提取
        parsed = {}
        for match in re.finditer(r'([a-zA-Z0-9_]+)="?([^;"]+)"?', raw_text):
            parsed[match.group(1)] = match.group(2)
            
        uid = parsed.get("userId")
        st = parsed.get("serviceToken")
        ph = parsed.get("xiaomichatbot_ph")
        
        if not uid or not st or not ph:
            return JSONResponse({"detail": "缺少必要字段 userId, serviceToken 或 xiaomichatbot_ph"}, status_code=400)
        uid = str(uid).strip()
        st = str(st).strip()
        ph = str(ph).strip()
        if not USER_ID_PATTERN.fullmatch(uid):
            return JSONResponse({"detail": "userId 只能包含 1-64 位字母、数字、下划线或短横线"}, status_code=400)
        if len(st) < 8 or len(st) > 4096 or len(ph) < 3 or len(ph) > 512:
            return JSONResponse({"detail": "serviceToken 或 xiaomichatbot_ph 格式异常"}, status_code=400)

        os.makedirs(USERS_DIR, exist_ok=True)
        target_file = os.path.join(USERS_DIR, f"user_{uid}.json")
        tmp_file = f"{target_file}.tmp"

        user_data = {
            "userId": uid,
            "serviceToken": st,
            "xiaomichatbot_ph": ph,
            "name": f"Imported_{uid}"
        }
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, target_file)
        _status_cache.pop(uid, None)
        trigger_users_reload()

        return JSONResponse({"status": "ok", "userId": uid})
    except Exception as e:
        return JSONResponse({"detail": str(e)}, status_code=500)

@router.delete("/api/users/delete/{uid}")
async def api_users_delete(uid: str):
    uid = str(uid).strip()
    if not USER_ID_PATTERN.fullmatch(uid):
        return JSONResponse({"detail": "非法 userId"}, status_code=400)
    target_file = os.path.join(USERS_DIR, f"user_{uid}.json")
    if os.path.exists(target_file):
        os.remove(target_file)
        _status_cache.pop(uid, None)
        trigger_users_reload()
        return JSONResponse({"status": "ok"})
    return JSONResponse({"detail": "User not found"}, status_code=404)
