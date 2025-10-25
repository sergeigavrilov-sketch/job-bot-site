from flask import Flask, request, render_template
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import logging
import time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Источники
DUUNITORI = "duunitori"
TEAPI = "te-palvelut"

BASE_URL = "https://duunitori.fi"
TE_API_BASE = "https://paikat.te-palvelut.fi/tpt-api/v1/search"

# Набор регионов (slugs / lower-case). Можно дополнять.
REGIONS = {
    "varsinais-suomi", "uusimaa", "pirkanmaa", "lappi", "satakunta",
    "kanta-hame", "kymenlaakso", "etelä-karjala", "pohjois-savo",
    "pohjanmaa", "pohjois-pohjanmaa", "keski-suomi", "paijat-hame",
    "etelä-pohjanmaa", "ita-uusimaa", "ahvenanmaa", "kainuu",
    "pohjois-karjala", "maakunta"  # maakunta — общий запасной
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
            "title": title,
            "company": company.strip(),
            "city": city,
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
    Запрашивает TE API.
    Параметры: haku (строка) — профессия/ключевое слово (может быть пустым),
                alue (строка) — город или регион (может быть пустым),
                page (int) — номер страницы (1-based)
    Возвращает: список вакансий в формате: {title, company, city, link, source}
    ---
    ВНИМАНИЕ: структура JSON у TE может меняться; если не вернёт список —
    функция вернёт [] и не упадёт.
    """
    params = {}
    if haku:
        params["keywords"] = haku  # параметр может называться иначе; при необходимости поменять
    if alue:
        params["location"] = alue
    params["page"] = page - 1  # предположение: API может быть 0-based; при ошибках можно убрать -1

    try:
        r = requests.get(TE_API_BASE, params=params, timeout=10, headers=HEADERS)
        r.raise_for_status()
    except requests.RequestException as e:
        app.logger.warning("TE API request failed: %s", e)
        return []

    # Ожидаем JSON
    try:
        data = r.json()
    except ValueError:
        app.logger.warning("TE API did not return JSON")
        return []

    results = []
    # Попробуем найти список вакансий в стандартных полях
    candidates = None
    if isinstance(data, dict):
        # возможные ключи: 'results', 'data', 'hits', 'jobs'
        for key in ("results", "data", "hits", "jobs", "items"):
            if key in data and isinstance(data[key], list):
                candidates = data[key]
                break
    if candidates is None:
        # возможно API возвращает сам массив
        if isinstance(data, list):
            candidates = data

    if not candidates:
        return []

    # Пробуем извлечь основные поля. Поля у TE API могут быть:
    # title/name, employer/organizer, location/displayName, link/url, id
    for item in candidates:
        # гибкие извлечения:
        title = item.get("title") or item.get("name") or item.get("position") or ""
        company = item.get("employer") or item.get("employerName") or item.get("organizer") or item.get("company") or "—"
        # location может быть строкой или объектом
        city = "—"
        if isinstance(item.get("location"), dict):
            city = item["location"].get("displayName") or item["location"].get("locality") or city
        else:
            city = item.get("location") or item.get("area") or item.get("municipality") or city

        # Ссылка — иногда url / originalUri / link
        link = item.get("url") or item.get("link") or item.get("originalUrl") or ""
        if not link:
            # возможно нужно сформировать ссылку через id
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

# ---------- UTILS ----------
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
    seen = {}  # key -> primary job dict (to merge alt sources)
    error = None
    has_next = False

    # Выполняем поиск, если пользователь ввел хоть что-то (haku или alue)
    if haku or alue:
        # подготовим параметры
        # определим, является ли введённое областью
        alue_slug = alue.strip().lower().replace(" ", "-") if alue else ""
        search_is_region = is_region_slug(alue_slug)

        # --- 1) Duunitori: формируем URL корректно ---
        # Если указан только область (регион) и професии нет => использовать /alue/{region}
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
            # есть ли следующая страница на Duunitori?
            has_next = duunitori_has_next(soup, page_num)
            time.sleep(0.3)  # politeness
        except requests.RequestException as e:
            app.logger.warning("Duunitori request error: %s", e)
            duu_jobs = []
            error = "Haun suorittamisessa tapahtui virhe. Yritä hetken päästä."

        # --- 2) TE-palvelut: вызов API для той же страницы/параметров ---
        # Для регионального поиска TE API хорошо поддерживает location=region
        te_jobs = []
        try:
            # TE API может ожидать region or municipality; мы передаём raw alue (user input)
            te_jobs = fetch_te_jobs(haku, alue if alue else "", page_num)
            time.sleep(0.3)
        except Exception as e:
            app.logger.warning("TE fetch error: %s", e)
            te_jobs = []

        # --- 3) объединение и де-дулинг ---
        # Сначала добавим Duunitori: у них приоритет (как ты хотел)
        for j in duu_jobs:
            key = normalize_key(j["title"], j["company"], j["city"])
            # добавляем поле alt_sources (список)
            j["alt_sources"] = []
            seen[key] = j
            jobs_combined.append(j)

        # Затем TE: если ключ совпадает — добавим как alt_source, иначе добавим как отдельную карточку
        for t in te_jobs:
            key = normalize_key(t["title"], t["company"], t["city"])
            if key in seen:
                # добавляем вторичный источник
                seen[key]["alt_sources"].append({
                    "source": TEAPI,
                    "link": t.get("link") or ""
                })
            else:
                # новый элемент — пометим, что источник TE
                t["alt_sources"] = []
                jobs_combined.append(t)
                seen[key] = t

    # Сортировка: можем сортировать по релевантности — пока просто как есть
    # Обрежем большие наборы — например, показывать первые 200
    MAX_SHOW = 500
    jobs_combined = jobs_combined[:MAX_SHOW]

    return render_template(
        "index.html",
        haku=haku,
        alue=alue,
        page=page_num,
        jobs=jobs_combined,
        has_next=has_next,
        error=error
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
