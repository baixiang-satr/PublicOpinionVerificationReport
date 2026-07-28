from src.crawler.platform_catalog import find_platform
from src.crawler.platform_fallbacks import (
    navigation_candidates,
    should_try_next_candidate,
)


def test_hupu_switches_between_mobile_and_desktop_official_hosts() -> None:
    url = "https://m.hupu.com/bbs/641349741.html"
    definition = find_platform(url)

    candidates = navigation_candidates(url, definition)

    assert candidates == (
        url,
        "https://bbs.hupu.com/bbs/641349741.html",
    )


def test_tieba_adds_author_only_and_mobile_variants() -> None:
    url = "https://tieba.baidu.com/p/10843752913"
    definition = find_platform(url)

    candidates = navigation_candidates(url, definition)

    assert candidates[0] == url
    assert "see_lz=1" in candidates[1]
    assert candidates[2] == "https://tieba.baidu.com/mo/q/m?tid=10843752913"


def test_fallbacks_only_run_for_known_page_failure_modes() -> None:
    assert should_try_next_candidate("HTTP_405_ACCESS_RESTRICTED")
    assert should_try_next_candidate("EMPTY_RENDERED_PAGE")
    assert not should_try_next_candidate("LOGIN_REQUIRED")
