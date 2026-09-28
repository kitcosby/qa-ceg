"""Forum suite driver: performs each outcome group's representative case
against the live forum through the SSH tunnel, observing effects directly.

Input defaults (first state in the .ceg) are filled in by the performer? No —
the JSON carries only non-default states; defaults are resolved here.
"""
import os
import re
import urllib.request
import urllib.parse
import http.cookiejar

PORT = int(os.environ.get("QA_TUNNEL_PORT", "8906"))
BASE = f"http://127.0.0.1:{PORT}"

DEFAULTS = {
    "c0": "register", "c1": "blank", "c2": "blank", "c3": "blank",
    "c4": "absent", "c5": "logged_out", "c6": "empty", "c7": "blank",
    "c8": "none", "c9": "missing", "c10": "empty", "c11": "no",
    "c12": "absent",
}

PERF_USER = "performer_qa"
PERF_PASS = "PerformerPass123!"
PERF_EMAIL = "performer_qa@example.com"

_created_users = []  # throwaway accounts to delete in teardown
_counter = [0]


def _full(inputs):
    d = dict(DEFAULTS)
    d.update(inputs)
    return d


class Client:
    """One fresh logged-out session per test case."""

    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj))
        self.op.addheaders = [("User-Agent", "muse-qa-perform")]

    def get(self, path):
        try:
            with self.op.open(BASE + path, timeout=25) as r:
                return r.status, r.read().decode("utf-8", "replace"), r.url
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:4000], BASE + path
        except Exception as e:  # noqa: BLE001
            return None, "EXC: " + str(e)[:200], BASE + path

    def post(self, path, data):
        try:
            req = urllib.request.Request(
                BASE + path, data=urllib.parse.urlencode(data).encode())
            with self.op.open(req, timeout=25) as r:
                return r.status, r.read().decode("utf-8", "replace"), r.url
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:4000], BASE + path
        except Exception as e:  # noqa: BLE001
            return None, "EXC: " + str(e)[:200], BASE + path

    def csrf(self, path):
        st, body, _ = self.get(path)
        if st != 200:
            return None
        m = re.search(r'name="csrf_token" value="([^"]+)"', body)
        return m.group(1) if m else None


def _unique(prefix):
    _counter[0] += 1
    return f"{prefix}_{_counter[0]}"


def _ensure_performer_account():
    """Create the shared performer account once (idempotent)."""
    c = Client()
    tok = c.csrf("/app/register")
    if not tok:
        return False
    st, body, url = c.post("/app/register", {
        "username": PERF_USER, "email": PERF_EMAIL,
        "password": PERF_PASS, "csrf_token": tok, "next": ""})
    ok = url.rstrip("/").endswith("/app") and st == 200
    if ok and PERF_USER not in _created_users:
        _created_users.append(PERF_USER)
    return ok


def _login(client):
    tok = client.csrf("/app/login")
    if not tok:
        return False
    st, body, url = client.post("/app/login", {
        "username": PERF_USER, "password": PERF_PASS,
        "csrf_token": tok, "next": ""})
    return url.rstrip("/").endswith("/app") and st == 200


def _errors(body):
    """Extract rendered form error strings precisely."""
    return [m.group(1).lower()
            for m in re.finditer(r'<div class="error">(.*?)</div>', body,
                                re.DOTALL)]


def _rtheory_link(body):
    return "/r-theory" in body or "r_theory" in body.lower()


def _username_for(client, v):
    if v == "new":
        return _unique("perfuser")
    if v == "existing":
        return PERF_USER
    if v == "invalid_chars":
        return "bad name!"
    return ""  # blank


def setup_run(suite):
    c = Client()
    st, _, _ = c.get("/app/")
    if st != 200:
        raise RuntimeError(f"tunnel/forum not reachable (status={st})")
    _ensure_performer_account()


def teardown_run():
    if not _created_users:
        return
    # Delete throwaway accounts (and their dependent rows) on the lampy
    # distro. The script travels via ssh stdin to avoid Windows cmd quoting.
    users = _created_users[:]
    script = (
        "import sys, psycopg\n"
        "users = sys.argv[1:]\n"
        "conn = psycopg.connect(host='/var/run/postgresql', dbname='forum', user='forum')\n"
        "cur = conn.cursor()\n"
        "cur.execute('SELECT id FROM users WHERE username = ANY(%s)', (users,))\n"
        "ids = [r[0] for r in cur.fetchall()]\n"
        "for tbl, col in [('forum_events','user_id'),('posts','user_id'),('threads','user_id')]:\n"
        "    cur.execute(f'DELETE FROM {tbl} WHERE {col} = ANY(%s)', (ids,))\n"
        "cur.execute('DELETE FROM users WHERE id = ANY(%s)', (ids,))\n"
        "conn.commit()\n"
        "print('deleted user ids:', ids)\n"
    )
    import subprocess
    with open("/tmp/forum_cleanup.py", "w") as f:
        f.write(script)
    # Usernames are alphanumeric-safe; they ride inside the single remote
    # command string because the wrapper passes only $1 to ssh.
    remote = "wsl -d lampy -u root -- python3 - " + " ".join(users)
    with open("/tmp/forum_cleanup.py") as f:
        r = subprocess.run(
            [os.path.expanduser("~/workspace/toetop-ssh-kitco.sh"), remote],
            stdin=f, capture_output=True, text=True, timeout=180)
    print("forum teardown:", (r.stdout or "")[:200], (r.stderr or "")[:200], flush=True)
    _created_users.clear()


def perform(inputs, want_success, want_fail):
    iv = _full(inputs)
    c = Client()
    obs = {}

    if iv["c5"] == "logged_in":
        if not _login(c):
            # cannot establish the required state -> unobservable
            return {e: None for e in want_success + want_fail}

    page = iv["c0"]

    if page == "register":
        obs.update(_do_register(c, iv))
    elif page == "login":
        obs.update(_do_login(c, iv))
    elif page == "new_thread":
        obs.update(_do_new_thread(c, iv))
    elif page == "thread":
        obs.update(_do_thread(c, iv))
    elif page == "search":
        obs.update(_do_search(c, iv))
    elif page == "api_comments":
        obs.update(_do_api_comments(c, iv))

    # e12 is observable on every fetched page; only report it when wanted
    return obs


def _do_register(c, iv):
    obs = {}
    if iv["c11"] == "no":
        st, body, _ = c.get("/app/register")
        obs["e1"] = st == 200 and 'name="next"' in body
        if "e12" in _wanted():
            obs["e12"] = _rtheory_link(body)
        return obs
    tok = c.csrf("/app/register")
    uname = _username_for(c, iv["c1"])
    email = {"valid": f"{uname or 'u'}@example.com",
             "invalid": "not-an-email", "blank": ""}[iv["c2"]]
    pw = {"provided": "TestPass123!", "blank": ""}[iv["c3"]]
    nxt = {"absent": "", "relative": "/r-theory/book3/",
           "external": "https://evil.example.com/"}[iv["c4"]]
    data = {"username": uname, "email": email, "password": pw, "next": nxt}
    if iv["c9"] == "valid":
        if not tok:
            return {e: None for e in _wanted()}
        data["csrf_token"] = tok
    st, body, url = c.post("/app/register", data)
    path = urllib.parse.urlparse(url).path

    obs["f10"] = st in (400, 403)
    if iv["c9"] == "missing":
        return obs
    # valid csrf from here; errors render in div.error
    errs = _errors(body)
    blob = " ".join(errs)
    obs["f1"] = st == 200 and any(
        w in blob for w in ("already", "taken", "exists"))
    obs["f2"] = st == 200 and "email" in blob
    obs["f3"] = st == 200 and "password" in blob
    obs["f4"] = "evil.example.com" not in url
    created = path.rstrip("/").endswith("/app") and st == 200 and not errs
    obs["e2"] = created and iv["c4"] == "absent"
    obs["e3"] = created and iv["c4"] == "relative" and path == "/r-theory/book3/"
    if created and iv["c1"] == "new":
        m = re.search(r"perfuser_\d+", uname)
        if m and uname not in _created_users:
            _created_users.append(uname)
    if "e12" in _wanted():
        obs["e12"] = _rtheory_link(body)
    return obs


def _do_login(c, iv):
    obs = {}
    if iv["c11"] == "no":
        st, body, url = c.get("/app/login")
        obs["e4"] = st == 200 and 'name="next"' in body
        if "e12" in _wanted():
            obs["e12"] = _rtheory_link(body)
        return obs
    tok = c.csrf("/app/login")
    uname = _username_for(c, iv["c1"])
    pw = {"provided": PERF_PASS if uname == PERF_USER else "whatever",
          "blank": ""}[iv["c3"]]
    nxt = {"absent": "", "relative": "/r-theory/book3/",
           "external": "https://evil.example.com/"}[iv["c4"]]
    data = {"username": uname, "password": pw, "next": nxt}
    if iv["c9"] == "valid":
        if not tok:
            return {e: None for e in _wanted()}
        data["csrf_token"] = tok
    st, body, url = c.post("/app/login", data)
    path = urllib.parse.urlparse(url).path
    obs["f10"] = st in (400, 403)
    if iv["c9"] == "missing":
        return obs
    errs = _errors(body)
    blob = " ".join(errs)
    obs["f5"] = st == 200 and any(
        w in blob for w in ("invalid", "incorrect", "unknown", "not found"))
    obs["f6"] = st == 200 and "password" in blob and path == "/app/login"
    obs["e5"] = path.rstrip("/").endswith("/app") and st == 200 and iv["c4"] == "absent"
    obs["e6"] = path == "/r-theory/book3/" and st == 200
    if "e12" in _wanted():
        obs["e12"] = _rtheory_link(body)
    return obs


def _do_new_thread(c, iv):
    obs = {}
    if iv["c5"] == "logged_out":
        st, body, url = c.get("/app/new_thread?category=1")
        obs["f7"] = "login" in url.lower() and "next" in url.lower()
        if iv["c9"] == "missing" and iv["c11"] == "yes":
            st2, _, _ = c.post("/app/new_thread?category=1",
                               {"title": "x", "category": "1"})
            obs["f10"] = st2 in (400, 403) or "login" in _
        return obs
    # logged in
    st, body, url = c.get("/app/new_thread?category=1")
    obs["e7"] = st == 200 and 'name="title"' in body
    if iv["c11"] == "no":
        if "e12" in _wanted():
            obs["e12"] = _rtheory_link(body)
        return obs
    tok = c.csrf("/app/new_thread?category=1")
    title = {"provided": _unique("PerfThread"), "blank": ""}[iv["c7"]]
    data = {"title": title, "body": "performer body", "category": "1"}
    if iv["c9"] == "valid":
        if not tok:
            return {e: None for e in _wanted()}
        data["csrf_token"] = tok
    st, body, url = c.post("/app/new_thread?category=1", data)
    path = urllib.parse.urlparse(url).path
    obs["f10"] = st in (400, 403)
    obs["e8"] = st == 200 and path.startswith("/app/thread/")
    if "e12" in _wanted():
        obs["e12"] = _rtheory_link(body)
    return obs


def _do_thread(c, iv):
    obs = {}
    tid = "1"
    if iv["c5"] == "logged_out":
        st, body, url = c.post(f"/app/thread/{tid}",
                               {"body": "hello", "csrf_token": "bad"})
        obs["f8"] = st in (400, 403) or "login" in url.lower()
        return obs
    tok = c.csrf(f"/app/thread/{tid}")
    comment = {"valid": _unique("PerfComment body"), "empty": ""}[iv["c6"]]
    data = {"body": comment}
    if iv["c9"] == "valid":
        if not tok:
            return {e: None for e in _wanted()}
        data["csrf_token"] = tok
    st, body, url = c.post(f"/app/thread/{tid}", data)
    obs["f10"] = st in (400, 403)
    if iv["c9"] == "missing":
        return obs
    blob = " ".join(_errors(body))
    obs["f9"] = st == 200 and "empty" in blob
    obs["e9"] = st == 200 and comment in body
    if "e12" in _wanted():
        obs["e12"] = _rtheory_link(body)
    return obs


def _do_search(c, iv):
    obs = {}
    q = {"valid": "thread", "empty": ""}[iv["c10"]]
    st, body, _ = c.get("/app/search?q=" + urllib.parse.quote(q))
    obs["e10"] = st == 200 and iv["c10"] == "valid" and "/app/thread/" in body
    if iv["c9"] == "missing" and iv["c11"] == "yes":
        # search is GET-based; csrf n/a -> f10 not applicable here
        obs["f10"] = False
    if "e12" in _wanted():
        obs["e12"] = _rtheory_link(body)
    return obs


def _do_api_comments(c, iv):
    obs = {}
    st, body, _ = c.get("/app/api/comments?site=r-theory&page=book3")
    flat = body.replace(" ", "")
    if iv["c5"] == "logged_in":
        obs["e11"] = st == 200 and '"logged_in":true' in flat \
            and PERF_USER in body
    else:
        obs["f11"] = st == 200 and '"logged_in":false' in flat
    return obs


# The performer passes wanted sets; drivers stash them for e12 gating.
_want = {"s": set(), "f": set()}


def _wanted():
    return _want["s"] | _want["f"]


_orig_perform = perform


def perform(inputs, want_success, want_fail):  # noqa: F811
    _want["s"] = set(want_success)
    _want["f"] = set(want_fail)
    return _orig_perform(inputs, want_success, want_fail)
