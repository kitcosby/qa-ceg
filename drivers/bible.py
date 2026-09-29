"""Bible-project suite driver: performs each outcome group's representative
case against the v2 Flask app (website/app_v2.py) via its test client
(in-process, live logic), reading the normalized bible_v2.db corpus.

app.db is backed up before the run and restored afterwards, so choice/
translation writes never pollute the real file.
"""
import html
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
    "c7": "no", "c8": "no", "c9": "no",
}

_client = None
_tid = None  # test translation id
_BIBLE_DB = None  # corpus path, taken from the imported v2 app module


def _full(inputs):
    d = dict(DEFAULTS)
    d.update(inputs)
    return d


def _corpus():
    import sqlite3
    return sqlite3.connect(_BIBLE_DB)


def setup_run(suite):
    global _client, _tid, _BIBLE_DB
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
    import app_v2 as bible_app
    _BIBLE_DB = bible_app.BIBLE_DB
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
    global _client, _tid, _BIBLE_DB
    tid, _tid = _tid, None
    _client = None
    _BIBLE_DB = None
    if os.path.exists(BACKUP):
        shutil.copy2(BACKUP, APP_DB)
        os.remove(BACKUP)
    # remove the choices file for the test translation only — never glob
    # other translations' files
    if tid is not None:
        try:
            os.remove(os.path.join(WEB, "user_data",
                                   f"translation_{tid}.choices"))
        except OSError:
            pass


def _t():
    return f"?t={_tid}"


def _wanted(effect, want_success, want_fail):
    return effect in want_success or effect in want_fail


def perform(inputs, want_success, want_fail):
    iv = _full(inputs)
    c = _client
    obs = {}
    page = iv["c0"]

    if page == "index":
        r = c.get("/")
        body = r.get_data(as_text=True)
        if _wanted("e1", want_success, want_fail):
            obs["e1"] = r.status_code == 200 and "Genesis" in body

    elif page == "book":
        n = 1 if iv["c1"] == "yes" else 999
        r = c.get(f"/book/{n}")
        body = r.get_data(as_text=True)
        if iv["c1"] == "yes":
            if _wanted("e2", want_success, want_fail):
                obs["e2"] = r.status_code == 200 and "Chapter 1" in body
        else:
            if _wanted("f1", want_success, want_fail):
                obs["f1"] = (r.status_code == 404 or "not found" in body.lower()
                             or "unknown" in body.lower()
                             or "Chapter 1" not in body)

    elif page == "chapter":
        n, ch = (1, 1) if iv["c1"] == "yes" else (999, 1)
        r = c.get(f"/chapter/{n}/{ch}")
        body = r.get_data(as_text=True)
        if iv["c1"] == "yes":
            if _wanted("e3", want_success, want_fail):
                obs["e3"] = r.status_code == 200 and "Verse 1" in body
        else:
            if _wanted("f2", want_success, want_fail):
                obs["f2"] = (r.status_code == 404 or "not found" in body.lower()
                             or "unknown" in body.lower()
                             or "Verse 1" not in body)

    elif page == "verse":
        n, ch, v = (1, 1, 1) if iv["c1"] == "yes" else (999, 1, 1)
        r = c.get(f"/verse/{n}/{ch}/{v}{_t()}")
        body = r.get_data(as_text=True)
        if iv["c1"] == "no":
            if _wanted("f3", want_success, want_fail):
                # no words rendered: the wordbox DIV is absent (the CSS
                # class name is always in the page <style>, so check the div)
                obs["f3"] = (r.status_code == 404
                             or '<div class="wordbox"' not in body)
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
            if iv["c2"] == "kjv" and _wanted("e4", want_success, want_fail):
                obs["e4"] = r.status_code == 200 and re.search(
                    r'<option value="\d+" selected>KJV:', body) is not None
            elif iv["c2"] == "ylt" and _wanted("e5", want_success, want_fail):
                obs["e5"] = r.status_code == 200 and re.search(
                    r'<option value="\d+" selected>Young', body) is not None
            elif iv["c2"] == "custom" and _wanted("e6", want_success, want_fail):
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
            ok = (r.status_code == 302
                  and r.headers.get("Location", "").startswith("/verse/"))
            if iv["c2"] == "kjv" and _wanted("e7", want_success, want_fail):
                obs["e7"] = ok
            elif iv["c2"] == "ylt" and _wanted("e8", want_success, want_fail):
                obs["e8"] = ok
            elif iv["c2"] == "custom" and _wanted("e9", want_success, want_fail):
                obs["e9"] = ok

    elif page == "translations":
        r = c.get("/translations")
        body = r.get_data(as_text=True)
        if _wanted("e10", want_success, want_fail):
            obs["e10"] = r.status_code == 200 and "New translation" in body

    elif page == "reading":
        n, ch, v = (1, 1, 1) if iv["c1"] == "yes" else (999, 1, 1)
        if iv["c2"] in ("kjv", "ylt"):
            _set_choice(custom=False, which=iv["c2"])
        r = c.get(f"/reading/{_tid}/{n}/{ch}/{v}")
        body = r.get_data(as_text=True)
        if iv["c1"] == "no":
            if _wanted("f4", want_success, want_fail):
                # invalid reference: no word spans rendered
                obs["f4"] = r.status_code == 404 or "<span" not in body
        else:
            # reading renders the chosen text for the word we set
            # (the app html-escapes the rendering, so compare escaped)
            chosen = _chosen_text(which=iv["c2"])
            has_text = (r.status_code == 200 and chosen
                        and html.escape(chosen) in body)
            if iv["c2"] == "kjv" and _wanted("e11", want_success, want_fail):
                obs["e11"] = bool(has_text)
            elif iv["c2"] == "ylt" and _wanted("e12", want_success, want_fail):
                obs["e12"] = bool(has_text)

    elif page == "export":
        if iv["c4"] == "unsupported":
            if _wanted("e18", want_success, want_fail):
                # Kit 2026-09-28: unrecognized formats are never rejected;
                # the export flags them for research and still renders the
                # full translation with special characters displayed
                r = c.get(f"/export/{_tid}?format=xyz")
                body = r.get_data(as_text=True)
                obs["e18"] = (r.status_code == 200
                              and "flagged for research" in body
                              and "'xyz'" in body
                              and "Genesis 1:1" in body)
        else:
            if _wanted("e13", want_success, want_fail):
                r = c.get(f"/export/{_tid}")
                body = r.get_data(as_text=True)
                obs["e13"] = (r.status_code == 200
                              and "attachment" in r.headers.get(
                                  "Content-Disposition", "")
                              and "Genesis 1:1" in body)

    elif page == "word":
        if iv["c5"] == "valid":
            wid = _pick_word(affixes=iv["c6"] == "yes",
                             strongs_source=iv["c7"] == "yes",
                             ylt_contexts=iv["c8"] == "yes",
                             found_verses=iv["c9"] == "yes")
            if wid is None:
                for e in list(want_success) + list(want_fail):
                    obs[e] = None
            else:
                r = c.get(f"/word/{wid}{_t()}")
                body = r.get_data(as_text=True)
                ok = r.status_code == 200
                # v2 word detail always renders the rows; the c6..c9 flags
                # mean the underlying normalized data is present, so each
                # effect checks its row shows real content, not "(none)".
                if _wanted("e14", want_success, want_fail):
                    obs["e14"] = (ok and "Affixes" in body
                                  and "<th>Affixes</th><td>(none)</td>"
                                  not in body)
                if _wanted("e15", want_success, want_fail):
                    obs["e15"] = (ok and "source:" in body
                                  and "source: ?" not in body)
                if _wanted("e16", want_success, want_fail):
                    obs["e16"] = (ok and "<th>YLT verse contexts</th>"
                                         "<td>(none)</td>" not in body)
                if _wanted("e17", want_success, want_fail):
                    obs["e17"] = (ok and "<th>Found verses</th>"
                                         "<td>(none)</td>" not in body)
        else:
            if _wanted("f6", want_success, want_fail):
                r = c.get(f"/word/999999999{_t()}")
                body = r.get_data(as_text=True)
                obs["f6"] = r.status_code == 404 and "Unknown word" in body

    return obs


def _first_word_id():
    """First Gen 1:1 word with both KJV and YLT renderings (v2 child tables)."""
    con = _corpus()
    r = con.execute(
        """SELECT w.word_id FROM words w
           JOIN verses v ON v.verse_id=w.verse_id
           WHERE v.book_id=1 AND v.chapter=1 AND v.verse=1
             AND EXISTS (SELECT 1 FROM lexicon_kjv_rendering k
                         WHERE k.root_id=w.root_id
                           AND k.root_form_seq=w.root_form_seq
                           AND k.vowel_seq=w.root_vowel_seq)
             AND EXISTS (SELECT 1 FROM lexicon_ylt_rendering y
                         WHERE y.root_id=w.root_id
                           AND y.root_form_seq=w.root_form_seq
                           AND y.vowel_seq=w.root_vowel_seq)
           ORDER BY w.word_pos LIMIT 1""").fetchone()
    if not r:
        r = con.execute(
            """SELECT w.word_id FROM words w
               JOIN verses v ON v.verse_id=w.verse_id
               WHERE v.book_id=1 AND v.chapter=1 AND v.verse=1
               ORDER BY w.word_pos LIMIT 1""").fetchone()
    con.close()
    return r[0]


def _pick_word(affixes=False, strongs_source=False,
               ylt_contexts=False, found_verses=False):
    """A word satisfying the requested v2 data-presence flags, so the word
    detail page can show the Affixes row (normalized disp columns),
    the Strong's source (strongs_sources), YLT contexts
    (lexicon_ylt_context), and/or found verses (lexicon_found_verse)."""
    con = _corpus()
    joins = ""
    conds = []
    if affixes:
        joins += (" JOIN root_form rf ON rf.root_id=w.root_id"
                  " AND rf.form_seq=w.root_form_seq")
        cols = ("prefix1_disp", "prefix2_disp", "prefix3_disp",
                "suffix1_disp", "suffix2_disp")
        conds.append("(" + " OR ".join(
            f"(rf.{c} IS NOT NULL AND rf.{c} != '')" for c in cols) + ")")
    if strongs_source:
        conds.append("w.strongs_source_id IS NOT NULL")
    if ylt_contexts:
        conds.append(
            "EXISTS (SELECT 1 FROM lexicon_ylt_context c"
            " WHERE c.root_id=w.root_id"
            " AND c.root_form_seq=w.root_form_seq"
            " AND c.vowel_seq=w.root_vowel_seq)")
    if found_verses:
        conds.append(
            "EXISTS (SELECT 1 FROM lexicon_found_verse f"
            " WHERE f.root_id=w.root_id"
            " AND f.root_form_seq=w.root_form_seq"
            " AND f.vowel_seq=w.root_vowel_seq)")
    q = "SELECT w.word_id FROM words w" + joins
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " LIMIT 1"
    r = con.execute(q).fetchone()
    con.close()
    return r[0] if r else None


def _rendering_counts(wid):
    """(nkjv, nylt): dropdown-capped rendering counts for the word's root."""
    con = _corpus()
    w = con.execute(
        "SELECT root_id, root_form_seq, root_vowel_seq FROM words"
        " WHERE word_id=?", (wid,)).fetchone()
    nkjv = nylt = 0
    if w:
        nkjv = con.execute(
            "SELECT COUNT(*) FROM lexicon_kjv_rendering"
            " WHERE root_id=? AND root_form_seq=? AND vowel_seq=?",
            tuple(w)).fetchone()[0]
        nylt = con.execute(
            "SELECT COUNT(*) FROM lexicon_ylt_rendering"
            " WHERE root_id=? AND root_form_seq=? AND vowel_seq=?",
            tuple(w)).fetchone()[0]
    con.close()
    # the v2 dropdown takes the first 12 of each list, like v1's [:12]
    return min(nkjv, 12), min(nylt, 12)


def _item_index(wid, which):
    """Dropdown item index for KJV (1) or the first YLT option (v2 ordering:
    default, KJV renderings, Young's renderings, Other options)."""
    nkjv, nylt = _rendering_counts(wid)
    if which == 2:  # ylt -> first YLT index
        return 1 + nkjv if nylt else 1
    return 1


def _chosen_text(which="kjv"):
    """The first KJV / Young's rendering for the first Gen 1:1 word."""
    wid = _first_word_id()
    table = ("lexicon_kjv_rendering" if which == "kjv"
             else "lexicon_ylt_rendering")
    con = _corpus()
    w = con.execute(
        "SELECT root_id, root_form_seq, root_vowel_seq FROM words"
        " WHERE word_id=?", (wid,)).fetchone()
    txt = None
    if w:
        r = con.execute(
            f"SELECT rendering FROM {table}"
            " WHERE root_id=? AND root_form_seq=? AND vowel_seq=?"
            " ORDER BY seq LIMIT 1", tuple(w)).fetchone()
        txt = r[0] if r else None
    con.close()
    return txt


def _set_choice(custom=False, which="kjv"):
    wid = _first_word_id()
    data = {"word_id": str(wid), "next": f"/verse/1/1/1{_t()}"}
    if custom:
        data["other_txt"] = "qa-custom-rendering"
    else:
        data["item"] = str(_item_index(wid, 1 if which == "kjv" else 2))
    _client.post(f"/choice{_t()}", data=data)
