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

REGIONS = [
    "uusimaa", "varsinais-suomi", "pirkanmaa", "satakunta", "kanta-hame",
    "paijat-hame", "kymenlaakso", "etela-karjala", "etela-savo", "pohjois-savo",
    "pohjois-karjala", "keski-suomi", "etela-pohjanmaa", "pohjanmaa",
    "keski-pohjanmaa", "pohjois-pohjanmaa", "kainuu", "lappi", "ahvenanmaa"
]


def parse_jobs_from_page(html):
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
            "link": BASE_URL + href
        })

    return jobs, soup


def page_has_next(soup, page):
    next_num = page + 1
    for a in soup.select(".pagination a"):
        href = a.get("href", "")
        if f"sivu={next_num}" in href:
            return True
    return False


@app.route("/", methods=["GET"])
def index():
    haku = request.args.get("haku", "").strip()
    alue = request.args.get("alue", "").strip().lower()
    page = request.args.get("page", "1")

    try:
        page_num = max(1, int(page))
    except ValueError:
        page_num = 1

    # ✅ определяем, область ли это
    search_is_region = alue in REGIONS

    jobs_combined = []
    has_next = False
    has_prev = page_num > 1
    error = None

    if haku or alue:  # ⬅️ пустой поиск теперь тоже работает, если указан город/регион
        try:
            if search_is_region:
                url = f"{BASE_URL}/tyopaikat/alue/{quote(alue)}"
                if haku:
                    url += f"?haku={quote(haku)}"
                if page_num > 1:
                    url += ("&" if haku else "?") + f"sivu={page_num}"

            else:
                url = f"{BASE_URL}/tyopaikat?haku={quote(haku)}"
                if alue:
                    url += f"&alue={quote(alue)}"
                url += f"&sivu={page_num}"

            app.logger.info(f"Fetching: {url}")

            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()

            jobs_page, soup = parse_jobs_from_page(resp.text)
            jobs_combined.extend(jobs_page)
            has_next = page_has_next(soup, page_num)

            time.sleep(0.4)

        except requests.RequestException as e:
            app.logger.error(f"Request error: {e}")
            error = "Haussa tapahtui virhe. Yritä hetken päästä."

    # ✅ ФИЛЬТРАЦИЯ по ТОЧНОМУ совпадению города
    if alue and not search_is_region:
        city_lower = alue.strip().lower()
        filtered = []

        for j in jobs_combined:
            city = j.get("city", "").strip().lower()

            if "ja" in city or "muu" in city or "useita" in city:
                continue

            if city == city_lower:
                filtered.append(j)

        jobs_combined = filtered

    return render_template(
        "index.html",
        haku=haku,
        alue=alue,
        page=page_num,
        jobs=jobs_combined,
        has_next=has_next,
        has_prev=has_prev,
        error=error,
        total=len(jobs_combined),
        search_is_region=search_is_region
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
