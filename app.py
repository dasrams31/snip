#!/usr/bin/env python3
"""Snip - onion URL shortener over Tor.

Python stdlib only. SQLite (WAL). No accounts.

Only shortens .onion URLs. Click counter per link.
"""

import html
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "snip.db")
PORT = 18096

CODE_LEN = 5
URL_MAX = 2048

_db_lock = threading.Lock()

ONION_RE = re.compile(r"^https?://[a-z2-7]{16,62}\.onion(:\d+)?(/.*)?$", re.I)


def now():
    return int(time.time())


def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=10000")
    return c


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = db()
    c.execute("""
    CREATE TABLE IF NOT EXISTS links (
        code TEXT PRIMARY KEY,
        target TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        clicks INTEGER NOT NULL DEFAULT 0
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_target ON links(target)")
    c.commit()
    c.close()


def esc(s):
    return html.escape(s or "", quote=True)


def new_code(c):
    for _ in range(20):
        code = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789")
                       for _ in range(CODE_LEN))
        if not c.execute("SELECT 1 FROM links WHERE code=?", (code,)).fetchone():
            return code
    return secrets.token_hex(3)[:CODE_LEN]


def normalize_url(raw):
    raw = (raw or "").strip()
    if len(raw) > URL_MAX or not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    if not ONION_RE.match(raw):
        return None
    return raw


# ---------------------------------------------------------------- html ---

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d0d0e;--panel:#151517;--panel2:#1c1c1f;--border:#232326;--border2:#323237;
--text:#f4f4f5;--muted:#9c9ca6;--faint:#63636c;--acc:#8ab4ff}
html{-webkit-text-size-adjust:100%;overflow-x:hidden}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.5;font-size:15px;
-webkit-font-smoothing:antialiased;overflow-x:hidden;width:100%;max-width:100vw}
a{color:inherit;text-decoration:none}
.wrap{max-width:640px;margin:0 auto;min-height:100vh;width:100%;display:flex;
flex-direction:column;border-left:1px solid var(--border);border-right:1px solid var(--border)}
.topbar{position:sticky;top:0;z-index:20;background:rgba(13,13,14,.92);
backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
border-bottom:1px solid var(--border)}
.topbar-in{max-width:640px;margin:0 auto;display:flex;align-items:center;
justify-content:space-between;padding:10px 16px;gap:8px;min-height:60px}
.wordmark{font-size:20px;font-weight:800;letter-spacing:-.03em}
.wordmark .dot{color:var(--faint)}
.main{flex:1;padding:40px 22px;display:flex;flex-direction:column;justify-content:center}
.hero{text-align:center;margin-bottom:28px}
.hero h1{font-size:28px;font-weight:800;letter-spacing:-.03em;margin-bottom:8px}
.hero p{color:var(--muted);font-size:14.5px}
form.shorten{display:flex;flex-direction:column;gap:12px}
input[type=text]{width:100%;background:var(--panel);border:1px solid var(--border2);
border-radius:14px;padding:15px 18px;color:var(--text);font-size:16px;
font-family:ui-monospace,Menlo,Consolas,monospace;min-height:56px}
input[type=text]:focus{outline:none;border-color:var(--muted)}
input[type=text]::placeholder{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
button.btn{background:var(--text);color:#0d0d0e;border:none;border-radius:14px;
padding:15px 28px;font-weight:700;font-size:16px;cursor:pointer;min-height:56px;
font-family:inherit;width:100%}
button.btn:active{transform:scale(.98)}
.result{background:var(--panel);border:1px solid var(--border);border-radius:16px;
padding:22px;text-align:center}
.result .lbl{font-size:12.5px;font-weight:600;color:var(--muted);margin-bottom:10px;
text-transform:uppercase;letter-spacing:.05em}
.result .short{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:17px;
font-weight:700;color:var(--acc);word-break:break-all;margin-bottom:6px}
.result .target{font-size:12.5px;color:var(--faint);word-break:break-all;margin-bottom:16px}
.linkrow{display:flex;gap:8px;margin-top:14px}
.linkrow input{flex:1;background:var(--bg);border:1px solid var(--border2);
border-radius:10px;padding:12px 14px;color:var(--text);
font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;min-width:0;min-height:48px}
.copybtn{flex:none;background:var(--panel2);border:1px solid var(--border2);
color:var(--text);border-radius:10px;padding:12px 20px;font-size:14px;font-weight:700;
cursor:pointer;font-family:inherit;min-height:48px}
.stats{display:flex;gap:20px;justify-content:center;margin-top:18px;
color:var(--faint);font-size:13px}
.stats b{color:var(--text);font-size:16px;display:block}
.err{background:rgba(244,63,94,.08);border:1px solid rgba(244,63,94,.35);color:#fda4b4;
padding:12px 16px;border-radius:12px;margin-bottom:16px;font-size:14px;text-align:center}
.hint{font-size:12.5px;color:var(--faint);margin-top:16px;text-align:center}
.footer{padding:20px 16px 28px;text-align:center;color:var(--faint);font-size:12px}
@media(max-width:660px){
.wrap{border-left:none;border-right:none}
.main{padding:28px 16px}
.hero h1{font-size:24px}
.topbar-in{padding:8px 12px;min-height:56px}
}
"""


def page(title, body):
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1,"
            "viewport-fit=cover'>"
            "<title>%s - Snip</title><style>%s</style></head>"
            "<body><div class='wrap'>"
            "<div class='topbar'><div class='topbar-in'>"
            "<a class='wordmark' href='/'>Snip<span class='dot'>.</span></a>"
            "<span style='color:var(--faint);font-size:13px'>onion shortener</span>"
            "</div></div>"
            "<div class='main'>%s</div>"
            "<div class='footer'>Snip &mdash; short links for onion addresses<br>"
            "No accounts. No tracking beyond a click count.</div>"
            "</div>"
            "<script>"
            "function copyLink(){var i=document.getElementById('shortlink');"
            "i.select();i.setSelectionRange(0,99999);"
            "try{navigator.clipboard.writeText(i.value);}catch(e){"
            "document.execCommand('copy');}"
            "var b=document.getElementById('copybtn');var t=b.textContent;"
            "b.textContent='Copied';setTimeout(function(){b.textContent=t;},2000);}"
            "</script>"
            "</body></html>") % (esc(title), CSS, body)


def home_page(err=""):
    ehtml = "<div class='err'>%s</div>" % esc(err) if err else ""
    return page("Shorten", (
        "<div class='hero'><h1>Shorten it.</h1>"
        "<p>Long .onion addresses become short links. Only onion URLs.</p></div>"
        "%s<form class='shorten' method='post' action='/'>"
        "<input type='text' name='url' maxlength='2048' "
        "placeholder='http://....onion/...' autocapitalize='off' "
        "autocomplete='off' autocorrect='off' spellcheck='false' required>"
        "<button class='btn' type='submit'>Snip it</button></form>"
        "<p class='hint'>Example: http://expyuzz4wqqyqhjn.onion/</p>") % ehtml)


def result_page(code, target, clicks, base_url):
    short = base_url + "/" + code if base_url else "/" + code
    return page("Snipped", (
        "<div class='result'><div class='lbl'>Your short link</div>"
        "<div class='short'><a href='/%s'>%s</a></div>"
        "<div class='target'>%s</div>"
        "<div class='linkrow'>"
        "<input id='shortlink' readonly value='%s' onclick='this.select()'>"
        "<button class='copybtn' id='copybtn' onclick='copyLink()'>Copy</button>"
        "</div>"
        "<div class='stats'><div><b>%d</b>clicks</div></div>"
        "</div>"
        "<p class='hint'><a href='/'>Snip another</a></p>") % (
            esc(code), esc(short), esc(target), esc(short), clicks))


# ---------------------------------------------------------------- server ---

class Handler(BaseHTTPRequestHandler):
    server_version = "Snip/1.0"
    protocol_version = "HTTP/1.1"

    def send_html(self, code, html_body):
        data = html_body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def read_form(self):
        try:
            ln = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if ln <= 0 or ln > 4096:
            return None
        raw = self.rfile.read(ln)
        try:
            return dict(urllib.parse.parse_qsl(raw.decode("utf-8", "replace")))
        except Exception:
            return None

    def base_url(self):
        host = self.headers.get("Host", "").split(",")[0].strip()
        if re.fullmatch(r"[a-z2-7]+\.onion(:\d+)?", host):
            return "http://" + host
        return ""

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, is_post):
        path = urllib.parse.urlparse(self.path).path
        try:
            self.c = db()
        except Exception:
            self.send_response(500)
            self.end_headers()
            return
        try:
            if is_post and path == "/":
                self.handle_create()
            elif not is_post and path == "/":
                self.send_html(200, home_page())
            elif not is_post:
                m = re.fullmatch(r"/([A-Za-z0-9]{3,12})", path)
                if m:
                    self.handle_redirect(m.group(1).lower())
                else:
                    self.send_html(404, page("Not found",
                                             "<div class='hero'><h1>Not found.</h1>"
                                             "<p><a href='/'>Snip a link</a></p></div>"))
            else:
                self.send_html(404, page("Not found", "<div class='hero'>"
                                                      "<h1>Not found.</h1></div>"))
        except BrokenPipeError:
            pass
        except Exception:
            try:
                self.send_html(500, page("Error", "<div class='hero'>"
                                                  "<h1>Something went wrong.</h1></div>"))
            except BrokenPipeError:
                pass
        finally:
            self.c.close()

    def handle_create(self):
        form = self.read_form()
        if form is None:
            self.send_html(400, home_page("Bad request."))
            return
        target = normalize_url(form.get("url", ""))
        if not target:
            self.send_html(400, home_page("Only valid .onion URLs can be shortened."))
            return
        with _db_lock:
            c = db()
            try:
                # reuse existing code for the same target
                row = c.execute("SELECT code, clicks FROM links WHERE target=?",
                                (target,)).fetchone()
                if row:
                    code, clicks = row["code"], row["clicks"]
                else:
                    code = new_code(c)
                    c.execute("INSERT INTO links(code, target, created_at)"
                              " VALUES (?,?,?)", (code, target, now()))
                    c.commit()
                    clicks = 0
            finally:
                c.close()
        self.send_html(200, result_page(code, target, clicks, self.base_url()))

    def handle_redirect(self, code):
        with _db_lock:
            c = db()
            try:
                row = c.execute("SELECT target FROM links WHERE code=?",
                                (code,)).fetchone()
                if not row:
                    self.send_html(404, page("Not found",
                                             "<div class='hero'><h1>Link not found.</h1>"
                                             "<p><a href='/'>Snip a link</a></p></div>"))
                    return
                c.execute("UPDATE links SET clicks = clicks + 1 WHERE code=?",
                          (code,))
                c.commit()
                target = row["target"]
            finally:
                c.close()
        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()


def main():
    init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("Snip on 127.0.0.1:%d" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
