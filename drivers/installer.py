"""Lampy-installer suite driver: STATIC traceability verification.

The NSIS/PowerShell installer runs on Windows; it cannot be executed from
this Linux VM. For each outcome group the driver verifies that the
implementing logic for every expected effect is present in the installer
sources (lampy.nsi, install.ps1, uninstall.ps1). This is static
verification, not live execution — reported as such.
"""
import os
import re

ROOT = os.path.expanduser("~/workspace/lampy-installer")

# effect -> list of (file, regex) patterns that implement it
CHECKS = {
    "e1": [("lampy.nsi", r'Section "Install"'),
           ("install.ps1", r"Fresh install|fresh install")],
    "e2": [("install.ps1", r"already registered|existing.*distro|upgrade")],
    "e3": [("install.ps1", r"skipping image download")],
    "e4": [("install.ps1", r"download|Download")],
    "e5": [("uninstall.ps1", r"Type YES.*confirm uninstall")],
    "e6": [("install.ps1", r"hash OK|SHA256")],
    "e7": [("install.ps1", r"set-passwords\.py|Setting passwords")],
    "f1": [("lampy.nsi", r"RequestExecutionLevel admin")],
    "f2": [("install.ps1", r"Unregister it and import fresh\? ALL DATA")],
    "f3": [("install.ps1", r"FAILED hash check")],
    "f4": [("uninstall.ps1", r"Uninstall cancelled")],
    "f5": [("install.ps1", r"Repair mode.*exists|distro.*exists")],
}

_cache = {}


def _text(name):
    if name not in _cache:
        with open(os.path.join(ROOT, name), encoding="utf-8",
                  errors="replace") as f:
            _cache[name] = f.read()
    return _cache[name]


def setup_run(suite):
    for f in ("lampy.nsi", "install.ps1", "uninstall.ps1"):
        if not os.path.exists(os.path.join(ROOT, f)):
            raise RuntimeError(f"installer source missing: {f}")


def teardown_run():
    _cache.clear()


def perform(inputs, want_success, want_fail):
    obs = {}
    for e in list(want_success) + list(want_fail):
        pats = CHECKS.get(e)
        if not pats:
            obs[e] = None
            continue
        obs[e] = all(re.search(rx, _text(fn), re.IGNORECASE)
                     for fn, rx in pats)
    return obs
