"""Profile suite driver: SKIP — manual/live routine.

Every profile effect is a live action on Kit's Facebook account (accepting
friend requests, replying to comments and Messenger threads, publishing
posts). Those are performed by the daily `fb-profile-notifications-daily`
cron (~09:47 PT) under its own safety rules. The performer must not
duplicate live social actions, so every group is reported unobservable
with the reason recorded.
"""

REASON = ("live-account routine: performed by the daily 09:47 PT profile "
          "cron; performer does not duplicate live social actions")


def setup_run(suite):
    pass


def teardown_run():
    pass


def perform(inputs, want_success, want_fail):
    return {e: None for e in list(want_success) + list(want_fail)}
