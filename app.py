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
DUUNITORI = "duunitori"

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

    if not haku and not alue:
        return render_template("index.html",
            haku="", alue="", page=1,
            jobs=[], has_next=False, has_prev=False,
            error=None
        )

    alue_slug = alue.strip().lower().replace(" ", "-")
    search_is_region = is_region_slug(alue_slug)

    # Формирование URL Duunitori с сортировкой по новым
    if alue and not haku and search_is_region:
        duu_url = (
            f"{BASE_URL}/tyopaikat/alue/{quote(alue_slug)}"
            f"?order_by=date_posted&sivu={page_num}"
        )
    else:
        encoded_haku = quote(haku) if haku else ""
        encoded_alue = quote(alue) if alue else ""
        duu_url = f"{BASE_URL}/tyopaikat?order_by=date_posted"
        if encoded_haku:
            duu_url += f"&haku={encoded_haku}"
        if encoded_alue:
            duu_url += f"&alue={encoded_alue}"
        duu_url += f"&sivu={page_num}"

    app.logger.info("Fetching Duunitori: %s", duu_url)

    try:
        r = requests.get(duu_url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        duu_jobs, soup = parse_jobs_from_duunitori_page(r.text)
        has_next = duunitori_has_next(soup, page_num)
    except:
        duu_jobs = []
        has_next = False

    # Фильтрация по городу (если это не регион)
    if alue and not search_is_region:
        city_lower = alue.strip().lower()
        duu_jobs = [
            j for j in duu_jobs
            if city_lower in j["city"].lower()
        ]

    return render_template(
        "index.html",
        haku=haku,
        alue=alue,
        page=page_num,
        jobs=duu_jobs,
        has_next=has_next,
        has_prev=(page_num > 1),
        error=None
    )


@app.route("/load_more", methods=["GET"])
def load_more():
    haku = request.args.get("haku", "").strip()
    alue = request.args.get("alue", "").strip()
    page = request.args.get("page", "1")
    try:
        page_num = max(1, int(page))
    except:
        page_num = 1

    alue_slug = alue.strip().lower().replace(" ", "-")
    search_is_region = is_region_slug(alue_slug)

    if alue and not haku and search_is_region:
        duu_url = (
            f"{BASE_URL}/tyopaikat/alue/{quote(alue_slug)}"
            f"?order_by=date_posted&sivu={page_num}"
        )
    else:
        duu_url = f"{BASE_URL}/tyopaikat?order_by=date_posted"
        if haku:
            duu_url += f"&haku={quote(haku)}"
        if alue:
            duu_url += f"&alue={quote(alue)}"
        duu_url += f"&sivu={page_num}"

    try:
        r = requests.get(duu_url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        duu_jobs, soup = parse_jobs_from_duunitori_page(r.text)
        has_next = duunitori_has_next(soup, page_num)
    except:
        duu_jobs = []
        has_next = False

    if alue and not search_is_region:
        city_lower = alue.lower()
        duu_jobs = [j for j in duu_jobs if city_lower in j["city"].lower()]

    json_list = [{
        "title": j["title"],
        "company": j["company"],
        "city": j["city"],
        "link": j["link"],
        "source": j["source"],
    } for j in duu_jobs]

    return jsonify({"jobs": json_list, "has_next": has_next})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
