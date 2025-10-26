from flask import Flask, request, render_template
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import logging
import time

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
DUUNITORI_BASE = "https://duunitori.fi"
TE_BASE = "https://paikat.te-palvelut.fi/tpt-api/v1/search"

def parse_duunitori(html):
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
            "city": city.lower(),
            "link": DUUNITORI_BASE + href,
            "source": "duunitori"
        })
    return jobs, soup

def fetch_duunitori(haku, alue, page):
    encoded_haku = quote(haku)
    encoded_alue = quote(alue) if alue else ""
    url = f"{DUUNITORI_BASE}/tyopaikat?haku={encoded_haku}"
    if encoded_alue:
        url += f"&alue={encoded_alue}"
    url += f"&sivu={page}"

    app.logger.info("Fetching Duunitori: %s", url)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        jobs, soup = parse_duunitori(resp.text)
        return jobs, soup
    except requests.RequestException as e:
        app.logger.warning("Duunitori request failed: %s", e)
        return [], None

def fetch_te(location, page):
    params = {"location": location, "page": page}
    app.logger.info("Fetching TE API: %s", params)
    try:
        resp = requests.get(TE_BASE, params=params, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for item in data.get("jobs", []):
            jobs.append({
                "title": item.get("title"),
                "company": item.get("employer"),
                "city": item.get("location", "").lower(),
                "link": item.get("url"),
                "source": "tyomarkkinatori"
            })
        return jobs
    except requests.RequestException as e:
        app.logger.warning("TE API request failed: %s", e)
        return []

def merge_jobs(list1, list2):
    combined = list1[:]
    seen = set((job["title"], job["company"], job["city"]) for job in combined)
    for job in list2:
        key = (job["title"], job["company"], job["city"])
        if key not in seen:
            combined.append(job)
            seen.add(key)
    return combined

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

    duunitori_jobs, duunitori_soup = fetch_duunitori(haku, alue, page_num)
    te_jobs = fetch_te(alue, page_num)

    combined = merge_jobs(duunitori_jobs, te_jobs)

    # фильтр по точному совпадению города
    if alue:
        combined = [job for job in combined if job["city"] == alue.lower()]

    has_next = False
    if duunitori_soup:
        next_page_num = page_num + 1
        for a in duunitori_soup.select(".pagination a"):
            href = a.get("href", "")
            if f"sivu={next_page_num}" in href:
                has_next = True
                break

    app.logger.info(
        "Final counts on page: duunitori=%d, tyomarkkinatori=%d, total=%d",
        len(duunitori_jobs), len(te_jobs), len(combined)
    )

    return render_template(
        "index.html",
        haku=haku,
        alue=alue,
        page=page_num,
        jobs=combined,
        has_next=has_next
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
