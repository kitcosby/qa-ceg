#!/usr/bin/env python3
"""Perform generated .ceg test suites against the deployed systems.

Reads suites/<name>.tests.json, groups combinations by outcome signature,
and executes one representative per group through a product driver.

Usage:
    python3 perform.py [suite ...] [--tunnel-port N]

Suites default to all five. Each driver lives in drivers/<mod>.py and
implements:
    setup_run(suite) -> None
    perform(inputs, want_success, want_fail) -> dict(effect_id -> True/False/None)
        True  = effect observed, False = not observed, None = cannot observe
    teardown_run() -> None

Results go to logs/perform-<date>.md and stdout.
Exit code = number of failed groups.
"""
import json
import os
import sys
import datetime
import importlib

QA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QA)

SUITE_MODULES = {
    "bible-project": "bible",
    "forum": "forum",
    "lampy-installer": "installer",
    "profile": "profile",
    "r-theory-rewrite": "rtheory",
}


def load_suite(name):
    with open(os.path.join(QA, "suites", name + ".tests.json")) as f:
        return json.load(f)


def group_tests(suite):
    """Group by outcome signature; representative = fewest non-default inputs."""
    groups = {}
    for t in suite["tests"]:
        sig = (tuple(sorted(t["success"])), tuple(sorted(t["fail"])))
        key = (len(t["inputs"]), t["id"])
        if sig not in groups or key < groups[sig][0]:
            groups[sig] = (key, t)
    ordered = sorted(groups.items(), key=lambda kv: kv[1][1]["id"])
    return [(sig, t) for sig, (_, t) in ordered]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = 8906
    for a in sys.argv[1:]:
        if a.startswith("--tunnel-port="):
            port = int(a.split("=", 1)[1])
    names = args or list(SUITE_MODULES)
    os.environ["QA_TUNNEL_PORT"] = str(port)

    stamp = datetime.datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(QA, "logs", f"perform-{stamp}.md")
    os.makedirs(os.path.join(QA, "logs"), exist_ok=True)

    total_pass, total_fail, total_skip = 0, 0, 0
    with open(log_path, "a") as log:
        log.write(f"\n# Suite performance — {stamp} {datetime.datetime.now().strftime('%H:%M')}\n")
        for name in names:
            mod = importlib.import_module("drivers." + SUITE_MODULES[name])
            suite = load_suite(name)
            groups = group_tests(suite)
            log.write(f"\n## {name} — {len(groups)} outcome groups\n")
            print(f"\n=== {name}: {len(groups)} groups ===", flush=True)
            n_pass = n_fail = n_skip = 0
            try:
                if hasattr(mod, "setup_run"):
                    mod.setup_run(suite)
                for i, (sig, t) in enumerate(groups):
                    want_s, want_f = sig
                    try:
                        observed = mod.perform(t["inputs"], want_s, want_f)
                    except Exception as e:  # noqa: BLE001 — driver blew up; record it
                        observed = {"__driver_error__": str(e)[:200]}
                    if observed.get("__driver_error__"):
                        status, detail = "ERROR", observed["__driver_error__"]
                        n_fail += 1
                    elif any(v is None for v in observed.values()):
                        status = "SKIP"
                        detail = "; ".join(
                            f"{k}=unobservable" for k, v in observed.items() if v is None
                        )
                        n_skip += 1
                    else:
                        missing = [e for e in want_s + want_f if not observed.get(e, False)]
                        extra = [e for e, v in observed.items()
                                 if v and e not in want_s and e not in want_f]
                        if missing or extra:
                            status = "FAIL"
                            detail = f"missing={missing} extra={extra}"
                            n_fail += 1
                        else:
                            status, detail = "PASS", ""
                            n_pass += 1
                    gid = f"G{i+1:03d}"
                    line = f"{status} {name} {gid} ({t['id']}) want_s={list(want_s)} want_f={list(want_f)} {detail}"
                    print(line, flush=True)
                    log.write(f"- {line}\n")
            finally:
                try:
                    if hasattr(mod, "teardown_run"):
                        mod.teardown_run()
                except Exception as e:  # noqa: BLE001
                    print(f"teardown {name} error: {e}", flush=True)
            log.write(f"Result: {n_pass} PASS, {n_fail} FAIL, {n_skip} SKIP\n")
            print(f"{name}: {n_pass} PASS, {n_fail} FAIL, {n_skip} SKIP", flush=True)
            total_pass += n_pass
            total_fail += n_fail
            total_skip += n_skip
        log.write(f"\nTOTAL: {total_pass} PASS, {total_fail} FAIL, {total_skip} SKIP\n")
    print(f"\nTOTAL: {total_pass} PASS, {total_fail} FAIL, {total_skip} SKIP", flush=True)
    print(f"log: {log_path}", flush=True)
    return min(total_fail, 125)


if __name__ == "__main__":
    sys.exit(main())
