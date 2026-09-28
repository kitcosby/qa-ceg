"""Bible-project suite driver: performs each outcome group's representative
case against the Flask app via its test client (in-process, live logic).

app.db is backed up before the run and restored afterwards, so choice/
translation writes never pollute the real file.
"""
import os
import re
import shutil
import sys

WEB = os.path.expanduser("~/workspace/bible-project/website")
VENV_SITE = os.path.join(WEB, "venv", "lib", "python3.12", "site-packages")
APP_DB = os.path.join(WEB, "app.db")
BACKUP = "/tmp/bible_app.db.bak"

DEFAULTS = {
    "c0": "index", "c1": "no", "c2": "none", "c3": "no",
    "c4": "none", "c5": "invalid", "c6": "no",
}

_client = None
_tid = None  # test translation id


def _full(inputs):
    d = dict(DEFAULTS)
    d.update(inputs)
    return d


def setup_run(suite):
    global _client, _tid
    if os.path.exists(VENV_SITE) and VENV_SITE not in sys.path:
        sys.path.insert(0, VENV_SITE)
    if os.path.exists(APP_DB):
        shutil.copy2(APP_DB, BACKUP)
        # the app only creates its schema when app.db is absent; an
        # existing-but-empty file would crash every request, so start clean
        # (the backup is restored in teardown)
        import sqlite3
        try:
            con = sqlite3.connect(APP_DB)
            has = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table'").fetchone()
            con.close()
        except Exception:  # noqa: BLE001
            has = None
        if not has:
            os.remove(APP_DB)
    sys.path.insert(0, WEB)
    import app as bible_app
    bible_app.app.config["TESTING"] = True
    _client = bible_app.app.test_client()
    # create a test translation
    r = _client.post("/translations", data={
        "name": "QA Test", "description": "performer"})
    m = re.search(r"t=(\d+)", r.headers.get("Location", ""))
    if m:
        _tid = int(m.group(1))
    else:
        # fetch the id from the page
        r = _client.get("/translations")
        m = re.search(r"/verse/1/1/1\?t=(\d+)", r.get_data(as_text=True))
        _tid = int(m.group(1)) if m else None
    if not _tid:
        raise RuntimeError("could not create test translation")


def teardown_run():
    global _client, _tid
    _client = None
    _tid = None
    if os.path.exists(BACKUP):
        shutil.copy2(BACKUP, APP_DB)
        os.remove(BACKUP)
    # remove choices files for the test translation if any
    import glob
    for f in glob.glob(os.path.join(WEB, "user_data", f"translation_{_tid}.choices")):
        try:
            os.remove(f)
        except OSError:
            pass


def _t():
    return f"?t={_tid}"


def perform(inputs, want_success, want_fail):
    iv = _full(inputs)
    c = _client
    obs = {}
    page = iv["c0"]

    if page == "index":
        r = c.get("/")
        body = r.get_data(as_text=True)
        obs["e1"] = r.status_code == 200 and "Genesis" in body

    elif page == "book":
        n = 1 if iv["c1"] == "yes" else 999
        r = c.get(f"/book/{n}")
        body = r.get_data(as_text=True)
        if iv["c1"] == "yes":
            obs["e2"] = r.status_code == 200 and "Chapter 1" in body
        else:
            obs["f1"] = (r.status_code == 404 or "not found" in body.lower()
                         or "unknown" in body.lower()
                         or "Chapter 1" not in body)

    elif page == "chapter":
        n, ch = (1, 1) if iv["c1"] == "yes" else (999, 1)
        r = c.get(f"/chapter/{n}/{ch}")
        body = r.get_data(as_text=True)
        if iv["c1"] == "yes":
            obs["e3"] = r.status_code == 200 and "Verse 1" in body
        else:
            obs["f2"] = (r.status_code == 404 or "not found" in body.lower()
                         or "unknown" in body.lower()
                         or "Verse 1" not in body)

    elif page == "verse":
        n, ch, v = (1, 1, 1) if iv["c1"] == "yes" else (999, 1, 1)
        r = c.get(f"/verse/{n}/{ch}/{v}{_t()}")
        body = r.get_data(as_text=True)
        if iv["c1"] == "no":
            obs["f3"] = (r.status_code == 404 or "wordbox" not in body)
        else:
            # e4/e5/e6: the verse page offers KJV/YLT/custom renderings.
            # "Shows the text in X" = an X rendering is the selected choice.
            if iv["c2"] == "kjv":
                _set_choice(custom=False, which="kjv")
            elif iv["c2"] == "ylt":
                _set_choice(custom=False, which="ylt")
            elif iv["c2"] == "custom":
                _set_choice(custom=True)
            r = c.get(f"/verse/{n}/{ch}/{v}{_t()}")
            body = r.get_data(as_text=True)
            if iv["c2"] == "kjv":
                obs["e4"] = r.status_code == 200 and re.search(
                    r'<option value="\d+" selected>KJV:', body) is not None
            elif iv["c2"] == "ylt":
                obs["e5"] = r.status_code == 200 and re.search(
                    r'<option value="\d+" selected>Young', body) is not None
            elif iv["c2"] == "custom":
                obs["e6"] = "qa-custom-rendering" in body

    elif page == "choice":
        if iv["c3"] == "no":
            # no submit: nothing to observe; effects require c3=yes
            for e in list(want_success) + list(want_fail):
                obs[e] = None
        else:
            wid = _first_word_id()
            which = {"kjv": 1, "ylt": 2, "custom": 3}.get(iv["c2"], 1)
            data = {"word_id": str(wid), "next": f"/verse/1/1/1{_t()}"}
            if iv["c2"] == "custom":
                data["other_txt"] = "qa-custom-rendering"
            else:
                # pick a KJV (item 1) or YLT item index; find YLT index
                data["item"] = str(_item_index(wid, which))
            r = c.post(f"/choice{_t()}", data=data)
            ok = r.status_code == 302 and r.headers.get("Location", "").startswith("/verse/")
            if iv["c2"] == "kjv":
                obs["e7"] = ok
            elif iv["c2"] == "ylt":
                obs["e8"] = ok
            else:
                obs["e9"] = ok

    elif page == "translations":
        r = c.get("/translations")
        body = r.get_data(as_text=True)
        obs["e10"] = r.status_code == 200 and "New translation" in body

    elif page == "reading":
        n, ch, v = (1, 1, 1) if iv["c1"] == "yes" else (999, 1, 1)
        if iv["c2"] in ("kjv", "ylt"):
            _set_choice(custom=False, which=iv["c2"])
        r = c.get(f"/reading/{_tid}/{n}/{ch}/{v}")
        body = r.get_data(as_text=True)
        if iv["c1"] == "no":
            # invalid reference: no words rendered
            obs["f4"] = r.status_code == 404 or "wordbox" not in body.lower()
        else:
            # reading renders the chosen text for the word we set
            chosen = _chosen_text(which=iv["c2"])
            has_text = r.status_code == 200 and chosen and chosen in body
            if iv["c2"] == "kjv":
                obs["e11"] = bool(has_text)
            elif iv["c2"] == "ylt":
                obs["e12"] = bool(has_text)

    elif page == "export":
        if iv["c4"] == "unsupported":
            # the app has no format parameter; observe whether it rejects one
            r = c.get(f"/export/{_tid}?format=xyz")
            body = r.get_data(as_text=True)
            obs["f5"] = (r.status_code in (400, 404)
                         or "unsupported" in body.lower()
                         or "invalid" in body.lower())
        else:
            r = c.get(f"/export/{_tid}")
            body = r.get_data(as_text=True)
            obs["e13"] = (r.status_code == 200
                          and "attachment" in r.headers.get("Content-Disposition", "")
                          and "Genesis 1:1" in body)

    elif page == "word":
        if iv["c5"] == "valid":
            wid = _word_with_affixes() if iv["c6"] == "yes" else _first_word_id()
            if wid is None:
                return {e: None for e in list(want_success) + list(want_fail)}
            r = c.get(f"/word/{wid}{_t()}")
            body = r.get_data(as_text=True)
            # e14 wants the Affixes row shown
            obs["e14"] = r.status_code == 200 and "Affixes" in body
        else:
            r = c.get(f"/word/999999999{_t()}")
            body = r.get_data(as_text=True)
            obs["f6"] = r.status_code == 404 and "Unknown word" in body

    return obs


def _first_word_id():
    import sqlite3
    con = sqlite3.connect(os.path.join(os.path.dirname(WEB), "bible.db"))
    # a word with both KJV and YLT renderings so kjv/ylt/custom paths all work
    # (join matches the app's verse view exactly)
    r = con.execute(
        """SELECT w.word_id FROM words w LEFT JOIN lexicon l
             ON l.root_id=w.root_id AND l.root_form_seq=w.root_form_seq
                AND l.vowel_seq=w.root_vowel_seq
           WHERE w.book=1 AND w.chapter=1 AND w.verse=1
             AND l.kjv_renderings IS NOT NULL AND l.kjv_renderings != ''
             AND l.ylt_renderings_computed IS NOT NULL
             AND l.ylt_renderings_computed != ''
           ORDER BY w.word_pos LIMIT 1""").fetchone()
    if not r:
        r = con.execute(
            "SELECT word_id FROM words WHERE book=1 AND chapter=1 AND verse=1 "
            "ORDER BY word_pos LIMIT 1").fetchone()
    con.close()
    return r[0]


def _word_with_affixes():
    """Find a word whose detail page should show the Affixes row."""
    import sqlite3
    con = sqlite3.connect(os.path.join(os.path.dirname(WEB), "bible.db"))
    cols = [r[1] for r in con.execute("PRAGMA table_info(words)")]
    aff = [c for c in cols if "affix" in c.lower() or "disp" in c.lower()]
    wid = None
    if aff:
        q = f"SELECT word_id FROM words WHERE {' OR '.join(f'{c} IS NOT NULL AND {c} != \"\"' for c in aff[:3])} LIMIT 1"
        try:
            r = con.execute(q).fetchone()
            wid = r[0] if r else None
        except Exception:  # noqa: BLE001
            wid = None
    con.close()
    return wid
    import sqlite3
    con = sqlite3.connect(os.path.join(os.path.dirname(WEB), "bible.db"))
    r = con.execute(
        "SELECT word_id FROM words WHERE book=1 AND chapter=1 AND verse=1 "
        "ORDER BY word_pos LIMIT 1").fetchone()
    con.close()
    return r[0]


def _item_index(wid, which):
    """Return a dropdown item index for KJV (1) or a YLT option."""
    import sqlite3
    con = sqlite3.connect(os.path.join(os.path.dirname(WEB), "bible.db"))
    r = con.execute(
        """SELECT l.kjv_renderings, l.ylt_renderings_computed FROM words w
           LEFT JOIN lexicon l ON l.root_id=w.root_id
             AND l.root_form_seq=w.root_form_seq
             AND l.vowel_seq=w.root_vowel_seq
           WHERE w.word_id=?""", (wid,)).fetchone()
    con.close()
    # items: 0=default, 1..=KJV options, then YLT options
    nkjv = len([t for t in (r[0] or "").split(" | ") if t][:12]) if r else 0
    if which == 2:  # ylt -> first YLT index
        nylt = len([t for t in (r[1] or "").split(" | ") if t][:12]) if r else 0
        return 1 + nkjv if nylt else 1
    return 1


def _chosen_text(which="kjv"):
    """The rendering text currently chosen for the first Gen 1:1 word."""
    import sqlite3
    wid = _first_word_id()
    con = sqlite3.connect(os.path.join(os.path.dirname(WEB), "bible.db"))
    r = con.execute(
        """SELECT l.kjv_renderings, l.ylt_renderings_computed FROM words w
           LEFT JOIN lexicon l ON l.root_id=w.root_id
             AND l.root_form_seq=w.root_form_seq
             AND l.vowel_seq=w.root_vowel_seq
           WHERE w.word_id=?""", (wid,)).fetchone()
    con.close()
    if not r:
        return None
    src = r[0] if which == "kjv" else r[1]
    parts = [t for t in (src or "").split(" | ") if t][:12]
    return parts[0] if parts else None
    """Return a dropdown item index for KJV (1) or a YLT option."""
    import sqlite3
    con = sqlite3.connect(os.path.join(os.path.dirname(WEB), "bible.db"))
    r = con.execute(
        """SELECT l.kjv_renderings, l.ylt_renderings_computed FROM words w
           LEFT JOIN lexicon l ON l.root_id=w.root_id
             AND l.root_form_seq=w.root_form_seq
             AND l.vowel_seq=w.root_vowel_seq
           WHERE w.word_id=?""", (wid,)).fetchone()
    con.close()
    # items: 0=default, 1..=KJV options, then YLT options
    nkjv = len([t for t in (r[0] or "").split(" | ") if t][:12]) if r else 0
    if which == 2:  # ylt -> first YLT index
        nylt = len([t for t in (r[1] or "").split(" | ") if t][:12]) if r else 0
        return 1 + nkjv if nylt else 1
    return 1


def _set_choice(custom=False, which="kjv"):
    wid = _first_word_id()
    data = {"word_id": str(wid), "next": f"/verse/1/1/1{_t()}"}
    if custom:
        data["other_txt"] = "qa-custom-rendering"
    else:
        data["item"] = str(_item_index(wid, 1 if which == "kjv" else 2))
    _client.post(f"/choice{_t()}", data=data)
