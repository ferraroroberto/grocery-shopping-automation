"""login_check_runner's subprocess timeout (issue #339).

The login-check child used to be killed after a flat 360s, sized for the old
~4-minute profile wait. `automation.browser`'s backoff schedule grew to 900s
(issue #231) and the timeout never followed, so a login check during a busy
profile failed with "did not finish within 360s" well before the profile
could free up. The timeout is now derived from the schedule, so it cannot
drift from it again.
"""

import app.login_check_runner as login_check_runner
from automation import browser
from automation.run_automation import HANDLERS


def test_timeout_covers_the_full_profile_wait_schedule():
    assert login_check_runner._timeout_s() >= sum(browser._PROFILE_WAIT_BACKOFF_S)


def test_timeout_derives_from_the_schedule_not_a_literal(monkeypatch):
    """A change to the backoff schedule must move the timeout with it."""
    monkeypatch.setattr(browser, "_PROFILE_WAIT_BACKOFF_S", (1, 2, 3))
    expected = 1 + 2 + 3 + login_check_runner._PAGE_LOAD_ALLOWANCE_S * len(HANDLERS)
    assert login_check_runner._timeout_s() == expected
