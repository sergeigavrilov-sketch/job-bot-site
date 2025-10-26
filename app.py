from flask import Flask, request, render_template
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import logging
import time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BASE_URL = "https://duunitori.fi"
TE_API_BASE = "https://paikat.te-palvelut.fi/tpt-api/v1/search"

DUUNITORI = "duunitori"
TEAPI = "te-palvelut"

# Набор регионов (slugs)
REGIONS = {
    "uusimaa", "varsinais-suomi", "pirkanmaa", "lappi", "satakunta",
    "kanta-hame", "paijat-hame", "kymenlaakso", "etelä-karjala", "etelä-savo",
    "pohjois-savo", "pohjois-karjala", "keski-suomi", "etelä-pohjanmaa",
    "pohjanmaa", "pohjois-pohjanmaa", "kainuu", "ahvenanmaa"
}


# ---------- DUUNITORI ----------
def parse_jobs_from_duunitori_page(html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    for box in soup.select("div.job-box"):
        a = box.select_one('a.job-box__hover[href*="/tyopaikat/tyo/"]')
        if not a:
            continue
        href = a.get("href")
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


# ---------- TE-PALVELUT (API) ----------
def fetch_te_jobs(haku, alue, page):
    """
    Гибкий запрос к TE API. Возвращает список dict:
    {title, company, city, link, source}
    """
    params = {}
    if haku:
        # TE API may expect keywords parameter; try 'keywords' first
        params["keywords"] = haku
    if alue:
        params["location"] = alue
    # TE API often is 0-based pages — попробуем page-1, но если отсутствует, это не критично
    params["page"] = max(0, page - 1)

    try:
        r = requests.get(TE_API_BASE, params=params, timeout=10, headers=HEADERS)
        r.raise_for_status()
    except requests.RequestException as e:
        app.logger.warning("TE API request failed: %s", e)
        return []

    try:
        data = r.json()
    except ValueError:
        app.logger.warning("TE API did not return JSON")
        return []

    candidates = None
    if isinstance(data, dict):
        for key in ("results", "data", "hits", "jobs", "items"):
            if key in data and isinstance(data[key], list):
                candidates = data[key]
                break
    elif isinstance(data, list):
        candidates = data

    if not candidates:
        return []

    results = []
    for item in candidates:
        # try multiple field names (API may change)
        title = item.get("title") or item.get("name") or item.get("position") or ""
        company = item.get("employer") or item.get("employerName") or item.get("organizer") or item.get("company") or "—"

        city = "—"
        loc = item.get("location")
        if isinstance(loc, dict):
            city = loc.get("displayName") or loc.get("locality") or city
        else:
            city = item.get("location") or item.get("area") or item.get("municipality") or city

        link = item.get("url") or item.get("link") or item.get("originalUrl") or ""
        if not link:
            ident = item.get("id") or item.get("jobId")
            if ident:
                link = f"https://paikat.te-palvelut.fi/tyonhakijalle/avoimet-tyopaikat?jobId={ident}"

        title = (title or "").strip()
        company = (company or "—").strip()
        city = (city or "—").strip()

        if not title:
            continue

        results.append({
            "title": title,
            "company": company,
            "city": city,
            "link": link,
            "source": TEAPI
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


# ---------- ROUTE ----------
@app.route("/", methods=["GET"])
def index():
    haku = request.args.get("haku", "").strip()
    alue = request.args.get("alue", "").strip()
    page = request.args.get("page", "1")
    try:
        page_num = max(1, int(page))
    except ValueError:
        page_num = 1

    jobs_combined = []
    seen = {}
    error = None
    has_next = False
    has_prev = page_num > 1

    if haku or alue:
        # region detection (slug)
        alue_slug = alue.strip().lower().replace(" ", "-") if alue else ""
        search_is_region = is_region_slug(alue_slug)

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

        # --- TE API ---
        try:
            te_jobs = fetch_te_jobs(haku, alue if alue else "", page_num)
            time.sleep(0.3)
        except Exception as e:
            app.logger.warning("TE fetch error: %s", e)
            te_jobs = []

        # Логи по источникам (сырые количества)
        app.logger.info("Duunitori returned: %d items; TE returned: %d items", len(duu_jobs), len(te_jobs))

        # --- MERGE & DEDUPE (Duunitori priority) ---
        for j in duu_jobs:
            key = normalize_key(j["title"], j["company"], j["city"])
            j["alt_sources"] = []
            seen[key] = j
            jobs_combined.append(j)

        for t in te_jobs:
            key = normalize_key(t["title"], t["company"], t["city"])
            if key in seen:
                seen[key]["alt_sources"].append({"source": TEAPI, "link": t.get("link") or ""})
            else:
                t["alt_sources"] = []
                jobs_combined.append(t)
                seen[key] = t

        app.logger.info("After merge: combined count = %d", len(jobs_combined))

        # --- фильтрация по точному городу (если указали город, не регион) ---
        if alue and not search_is_region:
            city_lower = alue.strip().lower()
            filtered = []
            for j in jobs_combined:
                city = j.get("city", "").strip().lower()
                # убираем агрегированные city значения
                if "ja" in city or "muu" in city or "useita" in city:
                    continue
                if city == city_lower:
                    filtered.append(j)
            app.logger.info("After city exact filter: %d items (city=%s)", len(filtered), city_lower)
            jobs_combined = filtered

    # Ограничим выдачу на странице (safety)
    MAX_SHOW = 500
    jobs_combined = jobs_combined[:MAX_SHOW]

    # Подсчёт итоговых источников для логов/диагностики
    cnt_du = sum(1 for j in jobs_combined if j.get("source") == DUUNITORI)
    cnt_te = sum(1 for j in jobs_combined if j.get("source") == TEAPI)
    app.logger.info("Final counts on page: duunitori=%d, te=%d, total=%d", cnt_du, cnt_te, len(jobs_combined))

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000) 
