"""Structured acceptance pipeline for author-home screenshot evidence.

Every author-home candidate must produce an :class:`AuthorEvidenceDecision`
before a screenshot may enter the workbook or ZIP.  The decision records the
candidate source, the expected and detected identities, the classified page
type, the access and overlay states and the final accept/reject outcome, so
quality reports and the pre-ZIP audit can be built from persisted facts
instead of log messages.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class ProfilePageType(StrEnum):
    PERSON_PROFILE = "PERSON_PROFILE"
    MEDIA_PROFILE = "MEDIA_PROFILE"
    STORE_PROFILE = "STORE_PROFILE"
    COMMENT_USER_PAGE = "COMMENT_USER_PAGE"
    CORPORATE_SECTION = "CORPORATE_SECTION"
    ARTICLE_PAGE = "ARTICLE_PAGE"
    LOGIN_OR_CHALLENGE = "LOGIN_OR_CHALLENGE"
    DELETED_OR_EMPTY = "DELETED_OR_EMPTY"
    UNKNOWN = "UNKNOWN"


#: Page types that may proceed to identity validation and screenshot.
CAPTURABLE_PAGE_TYPES = {
    ProfilePageType.PERSON_PROFILE,
    ProfilePageType.MEDIA_PROFILE,
    ProfilePageType.STORE_PROFILE,
}

#: Overlay must cover at most this share of the viewport before capture.
MAX_OVERLAY_COVERAGE = 0.15


@dataclass
class AuthorEvidenceDecision:
    candidate_url: str
    evidence_id: int = 0
    candidate_source: str = "unknown"
    expected_name: str | None = None
    expected_id: str | None = None
    detected_name: str | None = None
    detected_id: str | None = None
    detected_id_source: str = ""  # "href" | "text" | "douyin"
    page_type: str = ProfilePageType.UNKNOWN.value
    access_state: str = "unknown"
    overlay_state: str = "unknown"
    capture_region: str | None = None
    identity_state: str = "unverified"
    accepted: bool = False
    rejection_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "AuthorEvidenceDecision":
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in known})


def decision_sidecar_name(evidence_id: int) -> str:
    return f"{evidence_id:03d}主页.decision.json"


def write_decision(decision: AuthorEvidenceDecision, output_dir: Path) -> Path:
    """Persist a decision next to the staged screenshots for the ZIP audit."""

    if not decision.evidence_id:
        raise ValueError("write_decision requires a non-zero evidence_id")
    path = Path(output_dir) / decision_sidecar_name(decision.evidence_id)
    path.write_text(
        json.dumps(decision.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def read_decision(path: Path) -> AuthorEvidenceDecision | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    return AuthorEvidenceDecision.from_dict(value)


def normalize_identity(value: str | None) -> str:
    """Case-folded alphanumeric key; tolerant of prefixes/suffixes/spacing."""

    return "".join(
        character.casefold() for character in (value or "") if character.isalnum()
    )


def _same_id_namespace(left: str, right: str) -> bool:
    """Heuristic: same-namespace account ids share a shape.

    A profile page displays one canonical account id; ids from another
    namespace (douyin's internal 19-digit uid vs the public 抖音号) differ
    wildly in length or alphabet.  Only same-shaped ids may disprove
    identity.
    """

    if not left or not right:
        return False
    if left.isdigit() != right.isdigit():
        return False
    shorter, longer = sorted((len(left), len(right)))
    return longer - shorter <= 3


# ── Page signal extraction (runs inside the candidate page) ──────────────

PAGE_SIGNAL_SCRIPT = """() => {
    const headerSelectors = [
      '[class*="profile-header"] [class*="name"]',
      '[class*="user-info"] [class*="name"]',
      '[class*="author-info"] [class*="name"]',
      '[class*="nickname"]',
      '[class*="display-name"]',
      '[class*="user-name"]',
      'main h1',
      'main h2',
      'h1'
    ];
    let headerName = '';
    for (const selector of headerSelectors) {
      const element = document.querySelector(selector);
      const text = (element?.innerText || element?.textContent || '').trim();
      if (text && text.length <= 100) { headerName = text; break; }
    }
    const idSelectors = [
      '[class*="profile-header"] a[href*="/user"]',
      '[class*="user-info"] a[href*="/user"]',
      'a[href*="space.bilibili.com"]',
      'a[href*="weibo.com/u/"]',
      '[class*="uid"]',
      '[class*="user-id"]'
    ];
    let headerId = '';
    let headerIdSource = '';
    for (const selector of idSelectors) {
      const element = document.querySelector(selector);
      const href = element?.getAttribute?.('href') || '';
      const match = href.match(/(?:\\/u\\/|\\/user\\/|\\/profile\\/|space\\.bilibili\\.com\\/)([A-Za-z0-9_-]{4,})/);
      if (match) { headerId = match[1]; headerIdSource = 'href'; break; }
      const text = (element?.innerText || element?.textContent || '').trim();
      const textMatch = text.match(/(?:UID|id|ID|号)[:：\\s]*([A-Za-z0-9_-]{4,})/);
      if (textMatch) { headerId = textMatch[1]; headerIdSource = 'text'; break; }
    }
    if (!headerId) {
      // Douyin shows 「抖音号：xxx」 as plain header text, outside any of the
      // anchor/uid selectors above.
      const headText = (document.body?.innerText || '').slice(0, 2000);
      const dyMatch = headText.match(/抖音号[:：\\s]*([A-Za-z0-9_.-]{3,})/);
      if (dyMatch) { headerId = dyMatch[1]; headerIdSource = 'douyin'; }
    }
    const hasProfileSurface = Boolean(document.querySelector(
      'h1, main h2, [class*="profile"], [class*="user-info"], '
      + '[class*="author-info"], [class*="avatar"], '
      + '[class*="display-name"], [class*="user-name"], '
      + '[class*="author-name"], [class*="nickname"]'
    ));
    return {
      headerName,
      headerId,
      headerIdSource,
      title: (document.title || '').trim(),
      body: (document.body?.innerText || '').slice(0, 5000),
      hasProfileSurface
    };
}"""

# ── Overlay dismissal (semantically explicit buttons only) ───────────────

_DISMISS_OVERLAY_SCRIPT = """() => {
    const labels = ['关闭', '取消', '我知道了', '知道了', '稍后', '跳过',
                    '同意', '接受', '拒绝', 'close', 'cancel', 'accept', 'reject'];
    let clicked = 0;
    const candidates = document.querySelectorAll(
      '[class*="close"], [class*="dismiss"], [aria-label], [title], button, [role="button"]'
    );
    for (const element of candidates) {
      if (clicked >= 5) break;
      const label = (
        element.getAttribute('aria-label')
        || element.getAttribute('title')
        || element.innerText
        || element.textContent
        || ''
      ).trim();
      if (!label || label.length > 12) continue;
      if (!labels.some((word) => label === word || label.includes(word))) continue;
      const rect = element.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      const style = window.getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      try { element.click(); clicked += 1; } catch (error) { /* keep scanning */ }
    }
    return clicked;
}"""

_MEASURE_OVERLAY_SCRIPT = """() => {
    const vw = window.innerWidth || 1;
    const vh = window.innerHeight || 1;
    let covered = 0;
    let loginOverlay = false;
    const elements = document.querySelectorAll('body *');
    for (const element of elements) {
      const style = window.getComputedStyle(element);
      if (style.position !== 'fixed' && style.position !== 'absolute') continue;
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      const zIndex = Number.parseInt(style.zIndex, 10);
      if (!Number.isFinite(zIndex) || zIndex < 100) continue;
      const rect = element.getBoundingClientRect();
      const width = Math.min(rect.width, vw);
      const height = Math.min(rect.height, vh);
      if (width <= 0 || height <= 0) continue;
      if (width < vw * 0.2 || height < vh * 0.2) continue;
      covered += width * height;
      const text = (element.innerText || '').slice(0, 500);
      if (/登录|扫码|验证码|安全验证|sign in|log in/i.test(text)) loginOverlay = true;
    }
    return {
      coverage: Math.min(1, covered / (vw * vh)),
      loginOverlay
    };
}"""


async def dismiss_profile_overlays(page: Any) -> str:
    """Click only semantically explicit close/cancel/cookie buttons.

    Returns ``clear`` when no meaningful overlay remains, ``dismissed`` when
    at least one button was clicked and the page is now clear, and ``blocked``
    when an overlay still covers more than 15% of the viewport or a login
    prompt still covers the profile surface.  Never inputs passwords,
    verification codes or scan-login flows.
    """

    if not hasattr(page, "evaluate"):
        return "unknown"
    try:
        first = await page.evaluate(_MEASURE_OVERLAY_SCRIPT)
    except Exception:
        return "unknown"
    if not _overlay_blocking(first):
        return "clear"
    try:
        await page.evaluate(_DISMISS_OVERLAY_SCRIPT)
    except Exception:
        pass
    wait = getattr(page, "wait_for_timeout", None)
    if callable(wait):
        try:
            await wait(400)
        except Exception:
            pass
    try:
        second = await page.evaluate(_MEASURE_OVERLAY_SCRIPT)
    except Exception:
        return "unknown"
    return "blocked" if _overlay_blocking(second) else "dismissed"


def _overlay_blocking(measurement: Any) -> bool:
    if not isinstance(measurement, dict):
        return False
    coverage = float(measurement.get("coverage") or 0.0)
    return coverage > MAX_OVERLAY_COVERAGE or bool(measurement.get("loginOverlay"))


# ── Page type classification ─────────────────────────────────────────────

_LOGIN_MARKERS = (
    "登录后查看",
    "请先登录",
    "扫码登录",
    "安全验证",
    "访问验证",
    "sign in to continue",
    "log in to continue",
    "verify you are human",
)

_DELETED_MARKERS = (
    "页面不存在",
    "用户不存在",
    "账号不存在",
    "主页不存在",
    "内容已失效",
    "访问出错",
    "参数错误",
)

_CORPORATE_PATH = re.compile(
    r"/(?:csr|esg|about|help|introduce|gongyi|corp|responsibility)(?:/|$)",
    re.I,
)

_CORPORATE_MARKERS = (
    "企业社会责任",
    "帮助中心",
    "公司简介",
    "关于我们",
    "投资者关系",
)

_ARTICLE_PATH = re.compile(
    r"/(?:article|news|p|a|dy|question|video|programs|v|note|explore|item|detail|post|thread|status)/",
    re.I,
)

_PROFILE_PATH = re.compile(
    r"/(?:profile|user|space|author|people|member|u|home|shop|store)(?:/|$)",
    re.I,
)

_STORE_PATH = re.compile(r"/(?:shop|store|mall)(?:/|$)", re.I)

_MEDIA_NAME_MARKERS = (
    "新华社",
    "人民日报",
    "央视",
    "观察者网",
    "澎湃",
    "新京报",
    "日报",
    "晚报",
    "晨报",
    "电视台",
    "广播",
    "新闻网",
    "传媒",
    "官方",
    "网易",
    "凤凰",
    "搜狐",
    "腾讯",
    "新浪",
)


def classify_profile_page(
    *,
    url: str,
    title: str,
    body_text: str,
    has_profile_surface: bool,
    detected_name: str | None = None,
) -> ProfilePageType:
    """Classify a candidate author-home page from extracted signals."""

    normalized = f"{title}\n{body_text[:5_000]}".casefold()
    path = urlsplit(url).path or "/"

    if title.strip().casefold() in {"登录", "安全验证", "访问验证", "sign in", "log in"}:
        return ProfilePageType.LOGIN_OR_CHALLENGE
    if any(marker.casefold() in normalized for marker in _LOGIN_MARKERS):
        return ProfilePageType.LOGIN_OR_CHALLENGE
    if any(marker in normalized for marker in _DELETED_MARKERS):
        return ProfilePageType.DELETED_OR_EMPTY
    compact = "".join(body_text.split())
    if not has_profile_surface and len(compact) < 20:
        return ProfilePageType.DELETED_OR_EMPTY

    is_profile_path = bool(re.search(_PROFILE_PATH, path))
    if re.search(_CORPORATE_PATH, path) or (
        not is_profile_path
        and any(marker in normalized for marker in _CORPORATE_MARKERS)
    ):
        # Corporate/CSR/help sections take precedence: paths like
        # /csr/people/... contain profile-looking segments but are company
        # columns, never author homes (regression: evidence 057/058).
        return ProfilePageType.CORPORATE_SECTION
    if not is_profile_path:
        if re.search(_ARTICLE_PATH, path):
            return ProfilePageType.ARTICLE_PAGE

    if re.search(_STORE_PATH, path):
        return ProfilePageType.STORE_PROFILE
    if has_profile_surface or is_profile_path:
        name = detected_name or ""
        if any(marker in name for marker in _MEDIA_NAME_MARKERS):
            return ProfilePageType.MEDIA_PROFILE
        return ProfilePageType.PERSON_PROFILE
    return ProfilePageType.UNKNOWN


# ── Identity validation ──────────────────────────────────────────────────

def identity_verdict(
    *,
    expected_name: str | None,
    expected_id: str | None,
    detected_name: str | None,
    detected_id: str | None,
    body_text: str,
    page_url: str = "",
) -> tuple[str, str | None]:
    """Return ``(identity_state, rejection_code)``.

    ``identity_state`` is one of ``verified``, ``mismatch`` or ``unverified``.
    Header evidence (profile-header nickname / account ID) is the only strong
    signal: when the header names somebody else, navigation, sidebar or
    recommendation mentions of the expected author must not pass.  Account ID
    matches outrank nickname matches; nicknames are normalized and allow
    reasonable prefix/suffix differences.  A profile URL that itself carries
    the expected account ID is strong evidence too: the platform resolved
    that URL to the account's own home page, so a greedy header-ID probe
    picking up a recommendation link cannot cause a false mismatch.
    """

    expected_name_key = normalize_identity(expected_name)
    expected_id_key = normalize_identity(expected_id)
    detected_name_key = normalize_identity(detected_name)
    detected_id_key = normalize_identity(detected_id)
    body_key = normalize_identity(body_text)
    url_key = normalize_identity(page_url)

    if detected_id_key and detected_id_key in url_key:
        # A header "id" that merely echoes the profile URL's own identifier
        # (douyin's sec_uid inside /user/{sec_uid}) carries no evidence
        # about the expected platform account id — never let it disprove.
        detected_id_key = ""

    if expected_id_key and expected_id_key in url_key:
        return "verified", None

    if expected_id_key and detected_id_key:
        if expected_id_key == detected_id_key:
            return "verified", None
        if _same_id_namespace(expected_id_key, detected_id_key):
            return "mismatch", "AUTHOR_IDENTITY_MISMATCH"
        # Cross-namespace ids (douyin uid vs 抖音号/unique_id shown on the
        # profile) must not disprove a matching header name — fall through
        # and let the name comparison carry the verdict.  Two different
        # people sharing a nickname remain a documented acceptance risk
        # mitigated by the attached profile screenshot.

    if detected_name_key:
        if expected_name_key and (
            expected_name_key in detected_name_key
            or detected_name_key in expected_name_key
        ):
            return "verified", None
        if expected_name_key:
            # The header explicitly names somebody else.
            return "mismatch", "AUTHOR_IDENTITY_MISMATCH"
        if expected_id_key:
            # Header name exists but cannot disprove the ID; check the body.
            if expected_id_key in body_key:
                return "verified", None
            return "unverified", "AUTHOR_IDENTITY_UNVERIFIED"
        return "unverified", "AUTHOR_IDENTITY_UNVERIFIED"

    # No usable header evidence: fall back to exact body matches only.
    if expected_id_key and expected_id_key in body_key:
        return "verified", None
    if expected_name_key and expected_name_key in body_key:
        return "verified", None
    return "unverified", "AUTHOR_IDENTITY_UNVERIFIED"
