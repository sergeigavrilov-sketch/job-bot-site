from flask import Flask, request, render_template, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import logging
import time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BASE_URL = "https://duunitori.fi"
TM_BASE = "https://tyomarkkinatori.fi"

DUUNITORI = "duunitori"
TM = "tyomarkkinatori"

# Набор регионов (slugs)
REGIONS = {
    "uusimaa", "varsinais-suomi", "pirkanmaa", "lappi", "satakunta",
    "kanta-hame", "paijat-hame", "kymenlaakso", "etelä-karjala", "etelä-savo",
    "pohjois-savo", "pohjois-karjala", "keski-suomi", "etelä-pohjanmaa",
    "pohjanmaa", "pohjois-pohjanmaa", "kainuu", "ahvenanmaa"
}

# ---------- DUUNITORI парсер ----------
def parse_jobs_from_duunitori_page(html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    for box in soup.select("div.job-box"):
        a = box.select_one('a.job-box__hover[href*="/tyopaikat/tyo/"]')
        if not a:
            continue
        href = a.get("href", "")
        title = a.get_text(strip=True)
        company = a.get("data-company") or "—"

        loc_tag = box.select_one(".job-box__job-location span")
        city = loc_tag.get_text(strip=True).replace("–", "").strip() if loc_tag else "—"

        jobs.append({
            "title": title.strip(),
            "company": company.strip(),
            "city": city.strip(),
            "link": BASE_URL + href,
            "source": DUUNITORI
        })

    return jobs, soup

def duunitori_has_next(soup, page):
    next_num = page + 1
    for a in soup.select(".pagination a"):
        href = a.get("href", "")
        if f"sivu={next_num}" in href:
            return True
    return False

# ---------- Työmarkkinatori (HTML скрейпер) ----------
def fetch_tm_jobs(haku, alue, page):
    """
    Скрейпит публичную страницу Työmarkkinatori (henkiloasiakkaat / avoimet-tyopaikat).
    Возвращает список вакансий в формате {title, company, city, link, source}.
    """
    headers = HEADERS.copy()
    headers["Referer"] = "https://tyomarkkinatori.fi/"
    params = []
    if haku:
        params.append(("haku", haku))
    if alue:
        params.append(("alue", alue))
    params.append(("page", str(max(1, page))))

    url = f"{TM_BASE}/henkiloasiakkaat/avoimet-tyopaikat"
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
    except requests.RequestException as e:
        app.logger.warning("Työmarkkinatori request failed: %s", e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []

    # Ищем блоки вакансий — универсальная попытка
    # Сбор ссылок, которые выглядят как карточки
    for card in soup.select("article, .job-card, .job-list-item, .search-item"):
        # Найдём ссылку внутри карточки
        a = card.select_one("a[href*='/tyopaikat/'], a[href*='/avoimet-tyopaikat/']")
        if not a:
            # fallback: найти любой <a> с href содержащим 'tyopaikat'
            a = card.find("a", href=lambda v: v and "/tyopaikat" in v)
            if not a:
                continue
        href = a.get("href", "")
        title = a.get_text(strip=True) or (card.select_one("h3,h2") and card.select_one("h3,h2").get_text(strip=True)) or ""
        if not title:
            # пытаемся взять заголовок из дочернего тега
            h = card.select_one("h3, h2")
            if h:
                title = h.get_text(strip=True)
        if not title or not href:
            continue

        # Company
        company = "—"
        c = card.select_one(".company, .employer, .job-card__company, .organizer, .employer-name")
        if c:
            company = c.get_text(strip=True)

        # City
        city = "—"
        loc = card.select_one(".location, .job-card__location, .municipality, .place, .job-location")
        if loc:
            city = loc.get_text(strip=True)
        else:
            # small heuristic: find small span
            spans = card.find_all("span")
            for s in spans:
                text = s.get_text(strip=True)
                if text and len(text) < 60 and any(ch.isalpha() for ch in text):
                    city = text
                    break

        # Normalize link
        full_link = TM_BASE + href if href.startswith("/") else href

        results.append({
            "title": title.strip(),
            "company": (company or "—").strip(),
            "city": (city or "—").strip(),
            "link": full_link,
            "source": TM
        })

    return results

# ---------- HELPERS ----------
def normalize_key(title, company, city):
    return (title.strip().lower(), company.strip().lower(), city.strip().lower())

def is_region_slug(alue_raw):
    if not alue_raw:
        return False
    a = alue_raw.strip().lower().replace(" ", "-")
    return a in REGIONS

# ---------- ROUTES ----------
@app.route("/", methods=["GET"])
def index():
    haku = request.args.get("haku", "").strip()
    alue = request.args.get("alue", "").strip()
    page = request.args.get("page", "1")
    try:
        page_num = max(1, int(page))
    except ValueError:
        page_num = 1

    # Если нет параметров — показываем пустую форму (reset state)
    if not haku and not alue:
        return render_template(
            "index.html",
            haku="",
            alue="",
            page=1,
            jobs=[],
            has_next=False,
            has_prev=False,
            error=None
        )

    # region detection
    alue_slug = alue.strip().lower().replace(" ", "-") if alue else ""
    search_is_region = is_region_slug(alue_slug)

    jobs_combined = []
    seen = {}
    error = None
    has_next = False
    has_prev = page_num > 1

    # --- DUUNITORI ---
    if alue and not haku and search_is_region:
        duu_url = f"{BASE_URL}/tyopaikat/alue/{quote(alue_slug)}?sivu={page_num}"
    else:
        encoded_haku = quote(haku) if haku else ""
        encoded_alue = quote(alue) if alue else ""
        duu_url = f"{BASE_URL}/tyopaikat?"
        if encoded_haku:
            duu_url += f"haku={encoded_haku}"
        if encoded_alue:
            duu_url += f"&alue={encoded_alue}"
        duu_url += f"&sivu={page_num}"

    app.logger.info("Fetching Duunitori: %s", duu_url)
    try:
        r = requests.get(duu_url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        duu_jobs, soup = parse_jobs_from_duunitori_page(r.text)
        has_next = duunitori_has_next(soup, page_num)
        time.sleep(0.3)
    except requests.RequestException as e:
        app.logger.warning("Duunitori request error: %s", e)
        duu_jobs = []
        error = "Haun suorittamisessa tapahtui virhe. Yritä hetken päästä."

    # --- Työmarkkinatori ---
    try:
        tm_jobs = fetch_tm_jobs(haku, alue if alue else "", page_num)
        time.sleep(0.3)
    except Exception as e:
        app.logger.warning("Työmarkkinatori fetch error: %s", e)
        tm_jobs = []

    app.logger.info("Duunitori returned: %d items; Työmarkkinatori returned: %d items", len(duu_jobs), len(tm_jobs))

    # MERGE (Duunitori priority)
    for j in duu_jobs:
        key = normalize_key(j["title"], j["company"], j["city"])
        j["alt_sources"] = []
        seen[key] = j
        jobs_combined.append(j)

    for t in tm_jobs:
        key = normalize_key(t["title"], t["company"], t["city"])
        if key in seen:
            seen[key]["alt_sources"].append({"source": TM, "link": t.get("link") or ""})
        else:
            t["alt_sources"] = []
            jobs_combined.append(t)
            seen[key] = t

    app.logger.info("After merge: combined count = %d", len(jobs_combined))

    # ФИЛЬТРАЦИЯ по городу/региону
    if alue:
        if search_is_region:
            # регион — оставляем всё, что пришло (уже ограничено по region в запросах)
            pass
        else:
            # пользователь ввёл конкретный город — используем contains-match (вариант B)
            city_lower = alue.strip().lower()
            filtered = []
            for j in jobs_combined:
                city = j.get("city", "").strip().lower()
                # отбрасываем агрегированные поля типа "Paimio ja 1 muu" только если они не содержат нужный город
                if city_lower in city:
                    filtered.append(j)
            jobs_combined = filtered
            app.logger.info("After city contains filter: %d items (city=%s)", len(jobs_combined), city_lower)

    MAX_SHOW = 500
    jobs_combined = jobs_combined[:MAX_SHOW]

    # Логи по итоговым источникам
    cnt_du = sum(1 for j in jobs_combined if j.get("source") == DUUNITORI)
    cnt_tm = sum(1 for j in jobs_combined if j.get("source") == TM)
    app.logger.info("Final counts on page: duunitori=%d, tyomarkkinatori=%d, total=%d", cnt_du, cnt_tm, len(jobs_combined))

    return render_template(
        "index.html",
        haku=haku,
        alue=alue,
        page=page_num,
        jobs=jobs_combined,
        has_next=has_next,
        has_prev=has_prev,
        error=error
    )

# --- AJAX endpoint для подгрузки следующей страницы ---
@app.route("/load_more", methods=["GET"])
def load_more():
    haku = request.args.get("haku", "").strip()
    alue = request.args.get("alue", "").strip()
    page = request.args.get("page", "1")
    try:
        page_num = max(1, int(page))
    except ValueError:
        page_num = 1

    # получаем те же данные, что и в index (но только список вакансий)
    # region detection
    alue_slug = alue.strip().lower().replace(" ", "-") if alue else ""
    search_is_region = is_region_slug(alue_slug)

    jobs_combined = []
    seen = {}

    # Duunitori
    if alue and not haku and search_is_region:
        duu_url = f"{BASE_URL}/tyopaikat/alue/{quote(alue_slug)}?sivu={page_num}"
    else:
        encoded_haku = quote(haku) if haku else ""
        encoded_alue = quote(alue) if alue else ""
        duu_url = f"{BASE_URL}/tyopaikat?"
        if encoded_haku:
            duu_url += f"haku={encoded_haku}"
        if encoded_alue:
            duu_url += f"&alue={encoded_alue}"
        duu_url += f"&sivu={page_num}"

    try:
        r = requests.get(duu_url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        duu_jobs, soup = parse_jobs_from_duunitori_page(r.text)
    except requests.RequestException as e:
        app.logger.warning("Duunitori request error (load_more): %s", e)
        duu_jobs = []

    # Työmarkkinatori
    try:
        tm_jobs = fetch_tm_jobs(haku, alue if alue else "", page_num)
    except Exception as e:
        app.logger.warning("Työmarkkinatori request error (load_more): %s", e)
        tm_jobs = []

    # merge
    for j in duu_jobs:
        key = normalize_key(j["title"], j["company"], j["city"])
        j["alt_sources"] = []
        seen[key] = j
        jobs_combined.append(j)

    for t in tm_jobs:
        key = normalize_key(t["title"], t["company"], t["city"])
        if key in seen:
            seen[key]["alt_sources"].append({"source": TM, "link": t.get("link") or ""})
        else:
            t["alt_sources"] = []
            jobs_combined.append(t)
            seen[key] = t

    # фильтрация города если нужно (contains)
    if alue and not search_is_region:
        city_lower = alue.strip().lower()
        jobs_combined = [j for j in jobs_combined if city_lower in j.get("city", "").strip().lower()]

    # Ограничение и формирование JSON
    jobs_combined = jobs_combined[:500]
    json_list = [{
        "title": j["title"],
        "company": j["company"],
        "city": j["city"],
        "link": j["link"],
        "source": j.get("source", "")
    } for j in jobs_combined]

    # Определяем, есть ли следующая страница (по Duunitori pagination)
    has_next = False
    try:
        # проверим пагинацию Duunitori: если на странице есть ссылка на sivu=page+1
        # r above may exist in local scope; perform a small request to get pagination info
        resp = requests.get(duu_url, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        _, soup_check = parse_jobs_from_duunitori_page(resp.text)
        if duunitori_has_next(soup_check, page_num):
            has_next = True
    except Exception:
        has_next = False

    return jsonify({"jobs": json_list, "has_next": has_next})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
