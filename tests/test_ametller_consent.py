"""Unit tests for the Ametller Didomi consent-notice dismissal (issue #143)."""

from automation import ametller


class FakeLocator:
    def __init__(self, present: bool, visible: bool = True) -> None:
        self.present = present
        self.visible = visible
        self.clicks = 0

    def count(self) -> int:
        return 1 if self.present else 0

    @property
    def first(self) -> "FakeLocator":
        return self

    def is_visible(self) -> bool:
        return self.visible

    def click(self) -> None:
        self.clicks += 1


class FakePage:
    def __init__(self, locator: FakeLocator) -> None:
        self._locator = locator
        self.selectors: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        self.selectors.append(selector)
        return self._locator


def test_dismiss_clicks_visible_notice(monkeypatch):
    monkeypatch.setattr(ametller, "human_delay", lambda *a, **k: None)
    loc = FakeLocator(present=True)
    page = FakePage(loc)
    ametller._dismiss_consent_notice(page)
    assert loc.clicks == 1
    assert page.selectors == [ametller.SELECTORS["consent_agree"]]


def test_dismiss_is_noop_when_absent(monkeypatch):
    monkeypatch.setattr(ametller, "human_delay", lambda *a, **k: None)
    loc = FakeLocator(present=False)
    ametller._dismiss_consent_notice(FakePage(loc))
    assert loc.clicks == 0


def test_dismiss_skips_hidden_notice(monkeypatch):
    monkeypatch.setattr(ametller, "human_delay", lambda *a, **k: None)
    loc = FakeLocator(present=True, visible=False)
    ametller._dismiss_consent_notice(FakePage(loc))
    assert loc.clicks == 0


def test_dismiss_swallows_click_errors(monkeypatch):
    monkeypatch.setattr(ametller, "human_delay", lambda *a, **k: None)

    class Boom(FakeLocator):
        def click(self) -> None:
            raise RuntimeError("detached")

    ametller._dismiss_consent_notice(FakePage(Boom(present=True)))
