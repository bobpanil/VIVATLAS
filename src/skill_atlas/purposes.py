"""Для чего инструмент — одним значком.

Направления не выдуманы, а сложены из тегов, которые реально стоят на
карточках. Проверялось по живой базе: у скиллов встречаются web-accessibility,
playwright, lighthouse, security-scanning, code-refactoring — из них и
получились разделы. Тега нет ни в одном списке — значит направление не
определено, так и пишем. Придумывать значок наугад хуже, чем не ставить.

Значки одноцветные намеренно: в каталоге 74 дизайн-набора со своими палитрами,
и цветные иконки дрались бы с ними.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from skill_atlas.models import ArtifactTag, Tag


@dataclass
class Purpose:
    key: str
    label: str
    icon: str  # готовое содержимое <svg>, 16x16, рисуется цветом текста


# Порядок = приоритет при равном числе совпадений. Сначала узкое и говорящее,
# в конце широкое: "оформление" подошло бы почти всему, поэтому оно последнее.
PURPOSES: list[tuple[Purpose, set[str]]] = [
    (
        Purpose(
            "security",
            "безопасность",
            '<path d="M8 1.5 2.5 4v4c0 3 2.3 5.6 5.5 6.5 3.2-.9 5.5-3.5 5.5-6.5V4L8 1.5Z"/>',
        ),
        {
            "security-scanning",
            "secret-scanning",
            "static-analysis",
            "codeql",
            "gitleaks",
            "dependabot",
            "vulnerability",
            "security",
            "npm-audit",
            "semgrep",
            "sast",
        },
    ),
    (
        Purpose(
            "accessibility",
            "доступность",
            '<circle cx="8" cy="8" r="6.2"/><path d="M8 5.2v5.6M5.4 7h5.2"/>',
        ),
        {
            "web-accessibility",
            "accessibility",
            "wcag-compliance",
            "wcag",
            "aria",
            "wai-aria",
            "semantic-html",
            "html-semantics",
            "a11y",
        },
    ),
    (
        Purpose(
            "performance",
            "скорость",
            '<path d="M9 1.5 3.5 9H8l-1 5.5L12.5 7H8l1-5.5Z"/>',
        ),
        {
            "web-performance",
            "lighthouse",
            "performance",
            "core-web-vitals",
            "optimization",
            "speed",
        },
    ),
    (
        Purpose(
            "testing",
            "проверка",
            '<path d="M2.5 8.5 6 12l7.5-8"/>',
        ),
        {
            "web-testing",
            "automated-testing",
            "playwright",
            "testing",
            "code-audit",
            "html-auditing",
            "css-validation",
            "html-validation",
            "e2e",
            "ux-testing",
        },
    ),
    (
        Purpose(
            "research",
            "исследование",
            '<circle cx="7" cy="7" r="4.7"/><path d="M10.4 10.4 14 14"/>',
        ),
        {
            "data-gathering",
            "trend-analysis",
            "research",
            "ui-analysis",
            "analytics",
            "seo-audit",
            "seo",
            "monitoring",
            "deep-research",
        },
    ),
    (
        Purpose(
            "code",
            "код",
            '<path d="M5.5 4 2 8l3.5 4M10.5 4 14 8l-3.5 4"/>',
        ),
        {
            "code-refactoring",
            "code-generation",
            "code-quality",
            "ui-to-code",
            "ai-code-generation",
            "refactoring",
            "linting",
            "code-review",
        },
    ),
    (
        Purpose(
            "automation",
            "автоматизация",
            (
                '<circle cx="8" cy="8" r="2.3"/>'
                '<path d="M8 1.6v2.1M8 12.3v2.1M14.4 8h-2.1M3.7 8H1.6'
                'M12.5 3.5l-1.5 1.5M5 11l-1.5 1.5M12.5 12.5 11 11M5 5 3.5 3.5"/>'
            ),
        ),
        {
            "automation",
            "cli",
            "github-actions",
            "ci",
            "workflow",
            "context-management",
            "token-management",
            "prompt-engineering",
            "llm-optimization",
        },
    ),
    (
        Purpose(
            "design",
            "оформление",
            (
                '<path d="M8 1.6a6.4 6.4 0 1 0 0 12.8c.9 0 1.4-.6 1.4-1.3 0-.9-.7-1.2-.7-1.9'
                ' 0-.5.4-.9 1-.9h1.3a3.4 3.4 0 0 0 3.4-3.4c0-3-2.9-5.3-6.4-5.3Z"/>'
                '<circle cx="4.9" cy="7.5" r=".9" fill="currentColor" stroke="none"/>'
                '<circle cx="7.6" cy="4.9" r=".9" fill="currentColor" stroke="none"/>'
                '<circle cx="10.9" cy="5.9" r=".9" fill="currentColor" stroke="none"/>'
            ),
        ),
        {
            "design-system",
            "typography",
            "ui-components",
            "ui-kit",
            "design-tokens",
            "color-palette",
            "brand-identity",
            "brand-colors",
            "brand-assets",
            "grid-system",
            "web-design",
            "ui-design",
            "dark-mode",
            "minimalism",
            "branding",
            "responsive-design",
            "responsive-grid",
            "css-variables",
            "ui-ux",
            "visual-aesthetics",
            "mobile-design",
            "ui-prototyping",
            "prototyping",
            "web-interface",
            "frontend",
            "frontend-development",
            "html-css",
            "css",
            "layout",
        },
    ),
]

UNKNOWN = Purpose(
    "unknown",
    "не определено",
    '<path d="M3 8h10"/>',
)


# Слово в имени весит больше любого тега. Проверено на живой базе:
# site-accessibility-auditor по тегам выходил "проверкой" (playwright,
# web-testing перевесили), хотя доступность у него прямо в названии. Автор
# назвал инструмент — он и знает, для чего тот сделан.
_NAME_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("security", ("security", "vulnerab", "secret", "audit-sec")),
    ("accessibility", ("accessib", "a11y", "wcag")),
    ("performance", ("performance", "speed", "perf-", "lighthouse")),
    ("research", ("research", "analytic", "last30days", "trend")),
    ("testing", ("test", "checker", "validator")),
    ("code", ("refactor", "codegen", "code-quality", "linter")),
    ("automation", ("automat", "-cli", "context")),
    ("design", ("design", "brand", "theme", "ui-", "-ui", "typograph", "style")),
]

NAME_WEIGHT = 3
MIN_SCORE = 2  # одно случайное совпадение — не вывод


def detect(tag_slugs: list[str], name: str = "") -> tuple[Purpose, int]:
    """Направление по тегам и имени. Возвращает (направление, вес).

    Совпадений меньше двух — не определяем. Одинокий тег это случайность, а
    неверный значок хуже, чем никакого: он врёт, а пустой просто молчит.
    """
    tags = {t.lower() for t in tag_slugs}
    lowered = name.lower()

    scores: dict[str, int] = {}
    for purpose, slugs in PURPOSES:
        hits = len(tags & slugs)
        if hits:
            scores[purpose.key] = hits

    for key, words in _NAME_HINTS:
        if any(w in lowered for w in words):
            scores[key] = scores.get(key, 0) + NAME_WEIGHT

    if not scores:
        return UNKNOWN, 0

    order = {p.key: i for i, (p, _) in enumerate(PURPOSES)}
    best_key = min(scores, key=lambda k: (-scores[k], order.get(k, 99)))
    if scores[best_key] < MIN_SCORE:
        return UNKNOWN, scores[best_key]

    purpose = next(p for p, _ in PURPOSES if p.key == best_key)
    return purpose, scores[best_key]


def detect_for(session: Session, artifact_id: int, name: str = "") -> tuple[Purpose, int]:
    slugs = list(
        session.scalars(
            select(Tag.slug)
            .join(ArtifactTag, ArtifactTag.tag_id == Tag.id)
            .where(ArtifactTag.artifact_id == artifact_id)
        )
    )
    return detect(slugs, name)


def all_purposes() -> list[Purpose]:
    return [p for p, _ in PURPOSES]
