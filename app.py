from flask import Flask, request, render_template
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import logging
import os
import time

app = Flask(__name__)

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BASE_URL = "https://duunitori.fi"

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
    alue = request.args.get("alue", "").strip()
    page = request.args.get("page", "1")
    try:
        page_num = max(1, int(page))
    except ValueError:
        page_num = 1

    jobs = []
    has_next = False
    error = None

    try:
        encoded_haku = quote(haku)
        encoded_alue = quote(alue) if alue else ""
        url = f"{BASE_URL}/tyopaikat?haku={encoded_haku}"
        if encoded_alue:
            url += f"&alue={encoded_alue}"
        url += f"&sivu={page_num}"

        logger.info("Fetching Duunitori: %s", url)
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        jobs, soup = parse_jobs_from_page(resp.text)
        has_next = page_has_next(soup, page_num)
        time.sleep(0.4)
    except requests.RequestException as e:
        logger.error("Request error: %s", e)
        error = "Haun suorittamisessa tapahtui virhe. Yritä hetken päästä."

    logger.info("Final counts on page: duunitori=%d, total=%d", len(jobs), len(jobs))

    return render_template(
        "index.html",
        haku=haku,
        alue=alue,
        page=page_num,
        jobs=jobs,
        has_next=has_next,
        error=error
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port) 
