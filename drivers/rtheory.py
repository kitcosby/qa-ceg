"""R-theory-rewrite suite driver: performs each outcome group's representative
case against the live book pages through the SSH tunnel.

Static effects (e7, e8, e9, e6-api) via HTTP; JS effects via one shared
Playwright browser (e1, e2, e3, e4, e5, e10, f1, f2, f3-rendered).

Mapping notes (documented):
- e1 "dropdown navigates to chosen book page": the real dropdown has no
  per-book option; the driver selects "Volume 0" (closest page destination)
  and verifies navigation.
- e9 handoff card lives on book6 (volume boundary), not book3.
"""
import os
import re
import urllib.request
import urllib.parse

PORT = int(os.environ.get("QA_TUNNEL_PORT", "8906"))
BASE = f"http://127.0.0.1:{PORT}"
BOOK = "/r-theory/book3/"

DEFAULTS = {
    "c1": "absent", "c2": "none", "c3": "empty", "c4": "not_submitted",
    "c5": "off", "c6": "no", "c7": "no", "c8": "logged_out",
    "c9": "absent", "c10": "absent", "c11": "absent", "c12": "none",
}

JS_EFFECTS = {"e1", "e2", "e3", "e4", "e5", "e10", "f1", "f2"}

_browser = None
_playwright = None

PERF_USER = "performer_rth"
PERF_PASS = "PerformerPass123!"

# Chromium for the JS effects. Playwright's bundled chromium is not
# downloadable here (egress throttles the CDN) and Meta's /opt/meta-chromium
# build blocks all local-network navigations (ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS).
# The Puppeteer-cached headless shell works through the SSH tunnel.
CHROMIUM_PATH = os.environ.get(
    "QA_CHROMIUM_PATH",
    "/home/hatch/.cache/puppeteer/chrome-headless-shell/linux-153.0.8010.36"
    "/chrome-headless-shell-linux64/chrome-headless-shell")


def _full(inputs):
    d = dict(DEFAULTS)
    d.update(inputs)
    return d


def _get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, "EXC: " + str(e)[:200]


def _forum_session():
    """Return a CookieJar logged in as a throwaway forum user."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from drivers import forum as fd
    c = fd.Client()
    tok = c.csrf("/app/register")
    if tok:
        st, _, url = c.post("/app/register", {
            "username": PERF_USER, "email": PERF_USER + "@example.com",
            "password": PERF_PASS, "csrf_token": tok, "next": ""})
        if url.rstrip("/").endswith("/app"):
            if PERF_USER not in fd._created_users:
                fd._created_users.append(PERF_USER)
            return c.cj
    c2 = fd.Client()
    tok = c2.csrf("/app/login")
    if tok:
        st, _, url = c2.post("/app/login", {
            "username": PERF_USER, "password": PERF_PASS,
            "csrf_token": tok, "next": ""})
        if url.rstrip("/").endswith("/app"):
            return c2.cj
    return None


def _api_comments(cj=None):
    url = BASE + "/app/api/comments?site=r-theory&page=book3"
    try:
        if cj is not None:
            op = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(cj))
            with op.open(url, timeout=25) as r:
                return r.status, r.read().decode()
        with urllib.request.urlopen(url, timeout=25) as r:
            return r.status, r.read().decode()
    except Exception as e:  # noqa: BLE001
        return None, "EXC: " + str(e)[:200]


def setup_run(suite):
    st, _ = _get(BOOK)
    if st != 200:
        raise RuntimeError(f"tunnel/r-theory not reachable (status={st})")
    global _browser, _playwright
    from playwright.sync_api import sync_playwright
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(
        executable_path=CHROMIUM_PATH, args=["--no-sandbox"])


def teardown_run():
    global _browser, _playwright
    if _browser:
        _browser.close()
        _browser = None
    if _playwright:
        _playwright.stop()
        _playwright = None
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from drivers import forum as fd
    if PERF_USER not in fd._created_users:
        fd._created_users.append(PERF_USER)
    fd.teardown_run()


def _static(iv, want):
    obs = {}
    st, html = _get(BOOK)
    if st != 200:
        return {e: None for e in want}

    if "e6" in want:
        if iv["c8"] == "logged_in":
            cj = _forum_session()
            if cj is None:
                obs["e6"] = None
            else:
                ast, body = _api_comments(cj)
                flat = body.replace(" ", "")
                obs["e6"] = ast == 200 and '"logged_in":true' in flat \
                    and PERF_USER in body

    if "e7" in want:
        obs["e7"] = "/app/login?next=" in html and "rth-comments-login" in html

    if "e8" in want:
        obs["e8"] = ("Volume I" in html and "Book 3" in html
                     and "4 of 7" in html)

    if "e9" in want:
        page = "/r-theory/book6/" if iv["c11"] == "present" else BOOK
        pst, phtml = _get(page)
        obs["e9"] = pst == 200 and "booknav-handoff" in phtml \
            and "#b85f1d" in phtml

    return obs


def _js(iv, want):
    obs = {}
    need = set(want) & (JS_EFFECTS | {"f3"})
    if not need:
        return obs
    ctx = _browser.new_context()
    try:
        page = ctx.new_page()
        # Masking (M(c6,c5) in the CEG): when c6 is active, c5's rules are
        # skipped by the model, so the toggle action must NOT be performed
        # and localStorage is pre-seeded only from the c7=yes persisted input.
        # Pre-seeding on c5=on would invert the e4 toggle click (driver bug).
        if iv["c7"] == "yes":
            page.add_init_script(
                "try{localStorage.setItem('rth-large-text','1');}catch(e){}")
        page.goto(BASE + BOOK, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)

        if "e1" in want:
            try:
                page.select_option("#rthSiteDest", "../vol0/")
                page.wait_for_url(lambda u: "/vol0/" in u, timeout=10000)
                obs["e1"] = True
            except Exception:  # noqa: BLE001
                obs["e1"] = False

        if "e2" in want:
            try:
                page.select_option("#rthSiteDest", "../contents/#vol-2")
                page.wait_for_url(lambda u: "contents" in u and "vol-2" in u,
                                  timeout=10000)
                obs["e2"] = True
            except Exception:  # noqa: BLE001
                obs["e2"] = False

        if "e3" in want or "f1" in want:
            term = "black holes" if iv["c3"] == "valid" else ""
            page.fill("#rthSearchTerm", term)
            if "e3" in want:
                # This sandbox has no egress to google.com, so the popup can
                # never load. Capture the intended navigation URL with a
                # request listener: e3 is "opens new tab with site: query",
                # i.e. a new tab is opened for the Google search URL with the
                # site: query built from the term.
                nav_urls = []
                capturing = {"on": True}

                def _rec(req):
                    if capturing["on"] and "google.com" in req.url:
                        nav_urls.append(req.url)

                ctx.on("request", _rec)
                try:
                    with page.expect_popup(timeout=8000) as pop:
                        page.click("#rthSiteSearch button[type=submit]")
                    popup = pop.value
                    popup.wait_for_timeout(800)
                    popup.close()
                    opened = True
                except Exception:  # noqa: BLE001
                    opened = False
                finally:
                    capturing["on"] = False
                u = urllib.parse.unquote(nav_urls[0]) if nav_urls else ""
                obs["e3"] = (opened and "google.com/search" in u and
                             "site:cosbykit-afk.github.io/r-theory-rewrite" in u)
            if "f1" in want:
                # "stays on page": the blocked submit must not navigate.
                # Compare against the URL from before the click, since e1/e2
                # may already have moved us off book3.
                url_before = page.url
                page.click("#rthSiteSearch button[type=submit]")
                page.wait_for_timeout(600)
                toast = page.text_content("#rthToast") or ""
                obs["f1"] = ("Enter a search term" in toast
                             and page.url == url_before)

        if "e4" in want:
            page.click("#rthLargeText")
            page.wait_for_timeout(600)
            cls = page.evaluate(
                "document.documentElement.classList.contains('rth-large-text')")
            toast = page.text_content("#rthToast") or ""
            stored = page.evaluate(
                "try{localStorage.getItem('rth-large-text')}catch(e){''}")
            obs["e4"] = bool(cls) and "Large text on" in toast and stored == "1"

        if "e5" in want or "f2" in want:
            page.reload(wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(800)
            cls = page.evaluate(
                "document.documentElement.classList.contains('rth-large-text')")
            if "e5" in want:
                obs["e5"] = bool(cls)
            if "f2" in want:
                obs["f2"] = not bool(cls)

        if "e10" in want:
            page.focus("#rthSearchTerm")
            page.wait_for_timeout(300)
            outline = page.evaluate(
                "getComputedStyle(document.getElementById('rthSearchTerm')).outline")
            obs["e10"] = ("3px" in outline
                          and ("79, 195, 184" in outline or "4fc3b8" in outline))

        if "f3" in want:
            # logged-out: API anonymous AND prompt rendered with next=
            ast, body = _api_comments(None)
            api_anon = ast == 200 and '"logged_in":false' in body.replace(" ", "")
            prompt = page.query_selector(".rth-comments-login")
            href = (page.get_attribute(".rth-comments-login a", "href")
                    if prompt else "") or ""
            obs["f3"] = bool(api_anon and prompt is not None and "next=" in href)

        page.close()
    finally:
        ctx.close()
    return obs


def perform(inputs, want_success, want_fail):
    iv = _full(inputs)
    want = set(want_success) | set(want_fail)
    obs = {}
    obs.update(_static(iv, want))
    obs.update(_js(iv, want))
    return obs
