#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# leme_cookie_ui_autostart.py

import os
import sys
import re
import json
import time
import traceback
import requests
from http.cookies import SimpleCookie
from urllib.parse import unquote as urlunquote

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118 Safari/537.36 GH-Actions/lemehost-ui-cookie-autostart"

def log(*args):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), *args, flush=True)

def parse_cookie_str(cookie_str: str) -> dict:
    c = SimpleCookie()
    c.load(cookie_str)
    return {k: morsel.value for k, morsel in c.items()}

def extract_meta_csrf(html: str) -> str | None:
    m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html, re.I)
    return m.group(1) if m else None

def build_session(cookie_str: str, csrf_token: str | None, ua: str | None) -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": ua or DEFAULT_UA,
    })
    # 注入 Cookie
    if cookie_str:
        for k, v in parse_cookie_str(cookie_str).items():
            s.cookies.set(k, v)

    # 注入 CSRF（顺序：env > XSRF-TOKEN Cookie > 页面 meta）
    token = csrf_token
    if not token:
        xsrf = s.cookies.get("XSRF-TOKEN") or s.cookies.get("XSRF_TOKEN")
        if xsrf:
            token = urlunquote(xsrf)

    if token:
        s.headers["X-CSRF-Token"] = token
        s.headers["X-CSRF-TOKEN"] = token

    return s

def get_html(session: requests.Session, url: str, base: str, referer: str | None, timeout: int) -> tuple[bool, int, str]:
    headers = {"Origin": base}
    if referer:
        headers["Referer"] = referer
    r = session.get(url, headers=headers, timeout=timeout)
    return r.ok, r.status_code, (r.text if r.text else "")

def has_inactivity_banner(html: str) -> bool:
    # 检查“因不活跃而停止”的提示
    txt = html.lower()
    if "recently stopped by reason of inactivity" in txt:
        return True
    # 兼容其它类似提示（可按需扩展）
    return False

def looks_offline_from_html(html: str) -> bool:
    # 粗略从页面文案判断离线
    # 只在包含明显“Offline/离线”的可见文本时返回 True，避免脚本/样式中的误判
    # 这里做个简单近似：查找 >Offline< 或 >离线<
    if re.search(r'>\s*offline\s*<', html, re.I):
        return True
    if re.search(r'>\s*离线\s*<', html, re.I):
        return True
    return False

def get_state_api(session: requests.Session, base: str, sid: str, timeout: int) -> tuple[bool, str | None, str]:
    url = f"{base}/api/client/servers/{sid}/resources"
    headers = {
        "Origin": base,
        "Referer": f"{base}/server/{sid}",
        "Accept": "application/json",
    }
    r = session.get(url, headers=headers, timeout=timeout)
    if not r.ok:
        return False, None, f"HTTP {r.status_code} {r.text[:200]}"
    try:
        data = r.json()
        state = data.get("attributes", {}).get("current_state")
        return True, state, ""
    except Exception as e:
        return False, None, f"JSON parse error: {e} {r.text[:200]}"

def start_server(session: requests.Session, base: str, sid: str, timeout: int) -> tuple[bool, str]:
    url = f"{base}/api/client/servers/{sid}/power"
    headers = {
        "Origin": base,
        "Referer": f"{base}/server/{sid}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {"signal": "start"}
    r = session.post(url, headers=headers, data=json.dumps(body), timeout=timeout)
    if r.status_code == 204:
        return True, "204 No Content"
    ok = 200 <= r.status_code < 300
    return ok, f"{r.status_code} {r.text[:300]}"

def main():
    base = (os.getenv("PANEL_BASE") or "").rstrip("/")
    ids_raw = os.getenv("SERVER_IDS") or ""
    cookie_str = os.getenv("COOKIE") or ""
    csrf_token = os.getenv("CSRF_TOKEN") or None
    timeout = int(os.getenv("TIMEOUT") or "20")
    ua = os.getenv("USER_AGENT") or None

    # 可自定义路径（如你的站点有前置门户）
    list_path = os.getenv("SERVER_LIST_PATH", "/")                  # 服务器列表页，默认首页
    view_tpl  = os.getenv("VIEW_PATH_TEMPLATE", "/server/{id}")     # 单服视图页
    # 例如如果你的“View”是 /servers/{id}/view，可设置 VIEW_PATH_TEMPLATE="/servers/{id}/view"

    if not base or not ids_raw or not cookie_str:
        log("ERROR: 缺少必要环境变量 PANEL_BASE / SERVER_IDS / COOKIE")
        sys.exit(2)

    ids = [x.strip() for x in ids_raw.split(",") if x.strip()]
    session = build_session(cookie_str, csrf_token, ua)

    # 先访问“Servers”列表页
    list_url = f"{base}{list_path if list_path.startswith('/') else '/' + list_path}"
    ok, code, html = get_html(session, list_url, base, None, timeout)
    if not ok:
        log(f"访问服务器列表页失败 {list_url} -> HTTP {code}")
        # 不致命，继续后续流程
    else:
        # 如果之前没有拿到 CSRF，这里尝试从 meta 里补充
        if ("X-CSRF-Token" not in session.headers) or not session.headers["X-CSRF-Token"]:
            meta_token = extract_meta_csrf(html)
            if meta_token:
                session.headers["X-CSRF-Token"] = meta_token
                session.headers["X-CSRF-TOKEN"] = meta_token

    results = []
    exit_code = 0

    for sid in ids:
        view_url = f"{base}{view_tpl.format(id=sid)}"
        try:
            # 访问 View 页面
            ok, code, html = get_html(session, view_url, base, list_url, timeout)
            if not ok:
                log(f"[{sid}] 打开 View 失败 -> HTTP {code}")
                results.append({"id": sid, "ok": False, "error": f"open view {code}"})
                exit_code = 1
                continue

            # 二次补充 CSRF（若还未拿到）
            if ("X-CSRF-Token" not in session.headers) or not session.headers["X-CSRF-Token"]:
                meta_token = extract_meta_csrf(html)
                if meta_token:
                    session.headers["X-CSRF-Token"] = meta_token
                    session.headers["X-CSRF-TOKEN"] = meta_token

            banner = has_inactivity_banner(html)
            offline_html = looks_offline_from_html(html)

            # 用 API 核实状态
            ok2, state, err = get_state_api(session, base, sid, timeout)
            if not ok2:
                log(f"[{sid}] 获取状态失败(API) -> {err}")
            else:
                log(f"[{sid}] current_state = {state}")

            need_start = False
            reason = []
            if banner:
                need_start = True
                reason.append("inactivity banner")
            if offline_html:
                need_start = True
                reason.append("offline html")
            if state and state.lower() == "offline":
                need_start = True
                reason.append("api offline")

            if not need_start:
                results.append({"id": sid, "ok": True, "message": f"no need to start (state={state})"})
                continue

            # 点击 Start（POST power: start），带一次重试
            for attempt in range(1, 3):
                ok3, info = start_server(session, base, sid, timeout)
                log(f"[{sid}] start attempt {attempt} ({'+'.join(reason)}) -> {info}")
                if ok3:
                    results.append({"id": sid, "ok": True, "message": "start sent"})
                    break
                if attempt == 1:
                    time.sleep(2)
            else:
                results.append({"id": sid, "ok": False, "error": "start failed"})
                exit_code = 1

        except Exception as e:
            log(f"[{sid}] 异常: {e}")
            traceback.print_exc()
            results.append({"id": sid, "ok": False, "error": str(e)})
            exit_code = 1

    print(json.dumps({"ok": exit_code == 0, "results": results}, ensure_ascii=False))
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
