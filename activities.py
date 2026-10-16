import re
from urllib.parse import urljoin, urlparse

ALLOWED_HOST = "esaip.gofluent.com"


def inspect_dashboard(page):
    """
    Collecte les cartes et éléments de navigation, y compris les div[role=link]
    utilisés par l'interface GoFluent. N'effectue aucun clic.
    """
    report = {
        "url": page.url,
        "title": "",
        "links": [],
        "buttons": [],
        "iframes": [],
    }

    try:
        report["title"] = page.title()
    except Exception:
        pass

    seen_links = set()
    link_selectors = [
        "a[href]",
        '[role="link"][href]',
        '[role="link"]',
        "[data-href]",
    ]

    for selector in link_selectors:
        try:
            locator = page.locator(selector)
            for i in range(min(locator.count(), 3000)):
                item = locator.nth(i)
                try:
                    if not item.is_visible():
                        continue
                    href = (
                        item.get_attribute("href")
                        or item.get_attribute("data-href")
                    )
                    if not href:
                        continue
                    url = urljoin(page.url, href)
                    if urlparse(url).hostname != ALLOWED_HOST:
                        continue
                    label = " ".join((item.inner_text(timeout=600) or "").split())
                    if not label:
                        label = (
                            item.get_attribute("aria-label")
                            or item.get_attribute("title")
                            or "(élément sans titre)"
                        )
                    key = (url, label)
                    if key not in seen_links:
                        seen_links.add(key)
                        report["links"].append({
                            "text": label[:180],
                            "url": url,
                            "tag": item.evaluate("(el) => el.tagName.toLowerCase()"),
                            "role": item.get_attribute("role") or "",
                        })
                except Exception:
                    continue
        except Exception:
            continue

    seen_buttons = set()
    for selector in [
        "button",
        '[role="button"]',
        'input[type="button"]',
        'input[type="submit"]',
        "[onclick]",
    ]:
        try:
            locator = page.locator(selector)
            for i in range(min(locator.count(), 1500)):
                item = locator.nth(i)
                try:
                    if not item.is_visible():
                        continue
                    text = " ".join((item.inner_text(timeout=400) or "").split())
                    label = (
                        text
                        or item.get_attribute("aria-label")
                        or item.get_attribute("title")
                        or "(sans texte)"
                    )
                    tag = item.evaluate("(el) => el.tagName.toLowerCase()")
                    key = (tag, label)
                    if key not in seen_buttons:
                        seen_buttons.add(key)
                        report["buttons"].append({
                            "tag": tag,
                            "text": label[:160],
                            "type": item.get_attribute("type") or "",
                            "aria_label": item.get_attribute("aria-label") or "",
                        })
                except Exception:
                    continue
        except Exception:
            continue

    try:
        locator = page.locator("iframe")
        for i in range(min(locator.count(), 200)):
            item = locator.nth(i)
            report["iframes"].append({
                "title": item.get_attribute("title") or "",
                "src": item.get_attribute("src") or "",
            })
    except Exception:
        pass

    return report


LEVEL_ORDER = {"PRE-A1": 0, "A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}


def get_cefr_levels(item):
    """Return all CEFR labels found, including ranges such as B1-B2."""
    text = f"{item.get('text', '')} {item.get('url', '')}".upper()
    return [match.group(0).replace(" ", "-").replace("PREA1", "PRE-A1")
            for match in re.finditer(r"(?<![A-Z0-9])(?:PRE[ -]?A1|A1|A2|B1|B2|C1|C2)(?![A-Z0-9])", text)]


def get_cefr_level(item):
    """Déduit le niveau CECRL depuis le titre ou l'URL de l'élément."""
    levels = get_cefr_levels(item)
    return levels[0] if levels else None


def filter_by_level(items, min_level="Tous", max_level="B2"):
    """Filtre les activités dont le niveau CECRL est identifiable."""
    minimum = LEVEL_ORDER.get(min_level.upper(), 0) if min_level != "Tous" else 0
    maximum = LEVEL_ORDER.get(max_level.upper(), 6)
    if minimum > maximum:
        return [], len(items), 0
    selected = []
    unknown = 0
    excluded = 0
    for item in items:
        levels = get_cefr_levels(item)
        if not levels:
            unknown += 1
            continue
        low = min(LEVEL_ORDER[level] for level in levels)
        high = max(LEVEL_ORDER[level] for level in levels)
        if low <= maximum and high >= minimum:
            selected.append({**item, "level": "-".join(levels)})
        else:
            excluded += 1
    return selected, unknown, excluded
