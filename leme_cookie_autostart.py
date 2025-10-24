#!/usr/bin/env python3
import os, time, json, re, requests
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

CAP_PROVIDER = os.getenv("CAP_PROVIDER", "2captcha")  # 目前示例仅实现 2captcha
CAP_API_KEY = os.getenv("CAP_API_KEY")                # 2captcha 的 API key
BASE = os.getenv("PANEL_BASE", "").rstrip("/")
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")
LOGIN_PATH = os.getenv("LOGIN_PATH", "/auth/login")   # Pterodactyl 默认
HEADLESS = os.getenv("HEADLESS", "1") == "1"

def solve_2captcha(kind, sitekey, pageurl, enterprise=0):
    if not CAP_API_KEY:
        raise RuntimeError("缺少 CAP_API_KEY")
    method_map = {
        "turnstile": "turnstile",
        "hcaptcha": "hcaptcha",
        "recaptcha": "userrecaptcha",
    }
    method = method_map[kind]
    data = {
        "key": CAP_API_KEY,
        "method": method,
        "sitekey": sitekey,
        "pageurl": pageurl,
        "json": 1,
    }
    if kind == "recaptcha":
        data["version"] = "v2"
        if enterprise:
            data["enterprise"] = 1
    r = requests.post("https://2captcha.com/in.php", data=data, timeout=30).json()
    if r.get("status") != 1:
        raise RuntimeError(f"2captcha in.php 失败: {r}")
    rid = r["request"]
    # 轮询结果
    for _ in range(30):
        time.sleep(4)
        rs = requests.get("https://2captcha.com/res.php", params={"key": CAP_API_KEY, "action": "get", "id": rid, "json": 1}, timeout=30).json()
        if rs.get("status") == 1:
            return rs["request"]
        if rs.get("request") not in ("CAPCHA_NOT_READY", "ERROR_CAPTCHA_UNSOLVABLE"):
            raise RuntimeError(f"2captcha res.php 出错: {rs}")
    raise RuntimeError("2captcha 超时未返回结果")

def detect_captcha(page):
    html = page.content()
    # Cloudflare Turnstile
    m = re.search(r'data-sitekey=["\']([0-9a-zA-Z_-]{10,})["\'][^>]*?(?:class|id)[^>]*?turnstile', html)
    if m or 'challenges.cloudflare.com' in html:
        # 更稳：找 data-sitekey
        sitekey = page.evaluate("""
        () => {
          const el = document.querySelector('[data-sitekey].cf-challenge, [data-sitekey][class*="turnstile"], div[ data-sitekey][id*="turnstile"]');
          return el ? el.getAttribute('data-sitekey') : null;
        }
        """)
        return ("turnstile", sitekey)
    # hCaptcha
    if 'hcaptcha.com' in html or 'h-captcha' in html:
        sitekey = page.evaluate("""
        () => {
          const el = document.querySelector('[data-sitekey].h-captcha, div.h-captcha[data-sitekey], [data-sitekey][id*="hcaptcha"]');
          return el ? el.getAttribute('data-sitekey') : null;
        }
        """)
        return ("hcaptcha", sitekey)
    # reCAPTCHA
    if 'google.com/recaptcha' in html or 'g-recaptcha' in html:
        sitekey = page.evaluate("""
        () => {
          const el = document.querySelector('[data-sitekey].g-recaptcha, div.g-recaptcha[data-sitekey]');
          return el ? el.getAttribute('data-sitekey') : null;
        }
        """)
        return ("recaptcha", sitekey)
    return (None, None)

def inject_token(page, kind, token):
    # 按约定字段名注入响应
    name = {
        "turnstile": "cf-turnstile-response",
        "hcaptcha": "h-captcha-response",
        "recaptcha": "g-recaptcha-response",
    }[kind]
    page.evaluate(
        """
        ([name, token]) => {
          let el = document.querySelector(`[name="${name}"]`);
          if (!el) {
            el = document.createElement('textarea');
            el.name = name;
            el.style.display = 'none';
            const form = document.querySelector('form') || document.body;
            form.appendChild(el);
          }
          el.value = token;
        }
        """,
        [name, token],
    )

def main():
    if not (BASE and EMAIL and PASSWORD):
        raise SystemExit("缺少 PANEL_BASE/EMAIL/PASSWORD")
    login_url = urljoin(BASE + "/", LOGIN_PATH.lstrip("/"))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(login_url, wait_until="domcontentloaded", timeout=60000)

        # 检测验证码
        kind, sitekey = detect_captcha(page)
        if kind:
            if not sitekey:
                raise RuntimeError(f"检测�� {kind}，但未找到 sitekey")
            if CAP_PROVIDER != "2captcha":
                raise RuntimeError("当前示例只实现了 2captcha")
            token = solve_2captcha(kind, sitekey, login_url)
            inject_token(page, kind, token)

        # 填表并登录（Pterodactyl 默认 email/password）
        if page.locator('input[name="email"]').count():
            page.fill('input[name="email"]', EMAIL)
        elif page.locator('input[name="username"]').count():
            page.fill('input[name="username"]', EMAIL)
        else:
            raise RuntimeError("找不到 email/username 输入框")

        if page.locator('input[name="password"]').count():
            page.fill('input[name="password"]', PASSWORD)
        else:
            raise RuntimeError("找不到 password 输入框")

        # 提交
        if page.locator('button[type="submit"]').count():
            page.click('button[type="submit"]')
        else:
            page.press('input[name="password"]', "Enter")

        page.wait_for_load_state("networkidle", timeout=60000)

        # 导出 Cookie（筛选当前域相关）
        cookies = ctx.cookies()
        jar = []
        for c in cookies:
            if BASE.split("://",1)[1].split("/",1)[0].endswith(c.get("domain", "").lstrip(".")):
                jar.append(f"{c['name']}={c['value']}")
        cookie_str = "; ".join(jar)
        print(f"COOKIE={cookie_str}")  # 供后续步骤解析
        browser.close()

if __name__ == "__main__":
    main()
