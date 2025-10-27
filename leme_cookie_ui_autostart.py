#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# leme_cookie_ui_autostart.py

import os
import sys
import time
import requests
from http.cookies import SimpleCookie

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"

def log(*args):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), *args, flush=True)

def parse_cookie_str(cookie_str: str) -> dict:
    c = SimpleCookie()
    c.load(cookie_str)
    return {k: morsel.value for k, morsel in c.items()}

def build_session(cookie_str: str, csrf_token: str | None, ua: str | None) -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "Accept": "text/html, */*; q=0.01",
        "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundarySqVYQgc1RP5NLc5Z",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": ua or DEFAULT_UA,
        "Origin": "https://lemehost.com",
        "Referer": "https://lemehost.com/server/3302157/free-plan",
        "X-PJAX": "true",
        "X-PJAX-Container": "#p0",
    })
    if cookie_str:
        for k, v in parse_cookie_str(cookie_str).items():
            s.cookies.set(k, v)
    if csrf_token:
        s.headers["X-CSRF-Token"] = csrf_token
        s.headers["X-CSRF-TOKEN"] = csrf_token
    return s

def renew_server(session: requests.Session, base: str, sid: str, timeout: int) -> tuple[bool, str]:
    url = f"{base}/server/{sid}/free-plan"
    boundary = "----WebKitFormBoundarySqVYQgc1RP5NLc5Z"
    body = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"renewal\"\r\n\r\n"
        f"1\r\n"
        f"--{boundary}--\r\n"
    )
    r = session.post(url, data=body, timeout=timeout)
    if r.status_code == 200:
        return True, "200 OK"
    return False, f"{r.status_code} {r.text[:300]}"

def main():
    base = os.getenv("PANEL_BASE", "https://lemehost.com").rstrip("/")
    sid = os.getenv("SERVER_IDS", "3302157").split(",")[0].strip()
    cookie_str = os.getenv("COOKIE", "")
    csrf_token = os.getenv("CSRF_TOKEN")
    timeout = int(os.getenv("TIMEOUT", "20"))
    ua = os.getenv("USER_AGENT")

    if not base or not cookie_str:
        log("ERROR: 缺少必要环境变量 PANEL_BASE / COOKIE")
        sys.exit(2)

    session = build_session(cookie_str, csrf_token, ua)

    while True:
        log(f"Attempting renewal for server {sid}...")
        ok, info = renew_server(session, base, sid, timeout)
        log(f"Renewal result: {info}")
        time.sleep(300)  # 每 5 分钟

if __name__ == "__main__":
    main()
