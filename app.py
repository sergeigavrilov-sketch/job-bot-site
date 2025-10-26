from flask import Flask, render_template, request, jsonify
import requests
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

DUUNITORI_URL = "https://duunitori.fi/tyopaikat"
TE_API_URL = "https://paikat.te-palvelut.fi/tpt-api/v1/search"

def fetch_duunitori(query="", city="", page=1):
    params = {"haku": query, "alue": city, "sivu": page}
    logging.info(f"Fetching Duunitori: {DUUNITORI_URL} with {params}")
    r = requests.get(DUUNITORI_URL, params=params)
    jobs = []
    if r.status_code == 200:
        data = r.json() if 'application/json' in r.headers.get('Content-Type', '') else []
        for item in data:
            jobs.append({
                "title": item.get("title"),
                "company": item.get("company"),
                "city": item.get("location"),
                "url": item.get("url"),
                "source": "Duunitori"
            })
    return jobs

def fetch_te(query="", city="", page=1):
    params = {"q": query, "location": city, "page": page}
    logging.info(f"Fetching TE API: {params}")
    jobs = []
    try:
        r = requests.get(TE_API_URL, params=params)
        r.raise_for_status()
        data = r.json().get("jobs", [])
        for item in data:
            jobs.append({
                "title": item.get("title"),
                "company": item.get("company"),
                "city": item.get("city"),
                "url": item.get("url"),
                "source": "Työmarkkinatori"
            })
    except requests.RequestException as e:
        logging.warning(f"TE API request failed: {e}")
    return jobs

def filter_by_city(jobs, city):
    if not city:
        return jobs
    city_lower = city.lower()
    filtered = [job for job in jobs if city_lower in job.get("city", "").lower()]
    logging.info(f"After city filter: {len(filtered)} items (city={city})")
    return filtered

@app.route("/")
def index():
    query = request.args.get("haku", "")
    city = request.args.get("alue", "")
    page = int(request.args.get("page", 1))

    duunitori_jobs = fetch_duunitori(query, city, page)
    te_jobs = fetch_te(query, city, page)

    all_jobs = duunitori_jobs + te_jobs
    all_jobs = filter_by_city(all_jobs, city)

    return render_template("index.html", jobs=all_jobs, query=query, city=city, page=page)

@app.route("/load_more")
def load_more():
    query = request.args.get("haku", "")
    city = request.args.get("alue", "")
    page = int(request.args.get("page", 1))

    duunitori_jobs = fetch_duunitori(query, city, page)
    te_jobs = fetch_te(query, city, page)

    all_jobs = duunitori_jobs + te_jobs
    all_jobs = filter_by_city(all_jobs, city)

    return jsonify(all_jobs)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000) 
