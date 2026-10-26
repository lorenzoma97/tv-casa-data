"""Dati per la home della TV (fork lorenzoma97/cleanyourtv): docs/content.json, pubblicato su GitHub Pages.

Regole concordate con Lorenzo (24-26 ott 2026):
  Servizi attivi  Netflix (8), Prime Video (119, 2100 con pubblicita), RaiPlay (222), Mediaset Infinity (359), Italia.
                  NOW non e attivo: escluso.
  Pool serie      IMDb: tvSeries + tvMiniSeries, prima uscita dal 2020, almeno 25.000 voti (5.000 se in lingua
                  italiana); lingua originale inglese o europea (no coreane, anime, indiane); disponibili sui servizi
                  attivi. Ordine: voto IMDb, poi voti. "all" = prime 100; per ogni genere IMDb le prime fino a 100.
  Novita          serie nuove o nuove stagioni USCITE negli ultimi 30 giorni, per servizio (stesso filtro lingua del
                  pool). Non "aggiunte al catalogo": quel dato gratis non esiste. Le nuove stagioni contano solo per
                  serie partite dal 2018 con voto IMDb almeno 7 (via i procedurali lunghi tipo Law & Order, NCIS);
                  con meno di 1.000 voti il voto non e affidabile e la stagione non entra.
  Top 10          classifica ufficiale Netflix Italia, settimana piu recente, solo serie TV.

Il file pubblico contiene solo ID e posizioni (niente voti IMDb, testi o immagini TMDB): i dettagli li scarica la TV
con la chiave TMDB di Lorenzo. Uso personale, non commerciale; crediti TMDB/JustWatch/IMDb mostrati sulla TV.

Uso: TMDB_API_KEY=... python content.py [--skip netflix] [--imdb-dir DIR]
"""
import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs" / "content.json"
CACHE = ROOT / "cache" / "tmdb_map.json"

SERVICES = {
    "netflix": {"name": "Netflix", "package": "com.netflix.ninja", "providers": [8]},
    "prime": {"name": "Prime Video", "package": "com.amazon.amazonvideo.livingroom", "providers": [119, 2100]},
    "raiplay": {"name": "RaiPlay", "package": "it.rainet.androidtv", "providers": [222]},
    "infinity": {"name": "Mediaset Infinity", "package": "it.mediaset.infinitytv", "providers": [359]},
}
PROVIDER_TO_SERVICE = {p: s for s, v in SERVICES.items() for p in v["providers"]}
# Inglese ed europee (Turchia compresa), concordato il 26 ott. Fuori: ko, ja, zh, hi e altre lingue indiane, ar...
ALLOWED_LANGS = {"en", "it", "es", "fr", "de", "pt", "nl", "sv", "no", "nb", "da", "fi", "is", "pl", "cs", "sk", "hu",
                 "ro", "el", "tr", "ca", "eu", "gl", "sl", "hr", "sr", "bs", "bg", "uk", "ru", "lt", "lv", "et", "ga",
                 "cy", "sq", "mk", "mt", "lb"}
MIN_VOTES, MIN_VOTES_IT, FIRST_YEAR = 25000, 5000, 2020
POOL_ALL, POOL_PER_GENRE, NOVITA_DAYS, NOVITA_MAX = 100, 100, 30, 20
SEASON_FIRST_YEAR, SEASON_MIN_RATING, SEASON_MIN_VOTES = 2018, 7.0, 1000
GENRES = {  # IMDb -> (codice, etichetta)
    "Drama": ("drammatico", "Drammatico"), "Action": ("azione", "Azione"), "Comedy": ("commedia", "Commedia"),
    "Crime": ("crime", "Crime"), "Mystery": ("mistero", "Mistero"), "Adventure": ("avventura", "Avventura"),
    "Romance": ("romantico", "Romantico"), "Thriller": ("thriller", "Thriller"), "Animation": ("animazione", "Animazione"),
    "Fantasy": ("fantasy", "Fantasy"), "Horror": ("horror", "Horror"), "History": ("storico", "Storico"),
    "Documentary": ("documentario", "Documentario"), "Biography": ("biografico", "Biografico"),
    "Sci-Fi": ("fantascienza", "Fantascienza"), "Sport": ("sport", "Sport"), "War": ("guerra", "Guerra"),
    "Western": ("western", "Western"), "Music": ("musica", "Musica"), "Musical": ("musical", "Musical"),
    "Family": ("famiglia", "Famiglia"), "Reality-TV": ("reality", "Reality"), "Talk-Show": ("talk", "Talk show"),
    "Game-Show": ("quiz", "Quiz"), "News": ("notizie", "Notizie"), "Short": ("corto", "Corti"),
}
IMDB_NULL = "\\N"

IMDB_BASE = "https://datasets.imdbws.com/"
NETFLIX_TSV = "https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv"
TMDB = "https://api.themoviedb.org/3"
WIKIDATA = "https://query.wikidata.org/sparql"
UA = "tv-casa-data/1.0 (personal TV home; github.com/lorenzoma97/tv-casa-data)"


# ----------------------------------------------------------------------------------------------- regole (pure)

def eligible(c, lang):
    """Una candidata IMDb entra nel pool? c: {type, start, votes}; lang: lingua originale TMDB."""
    if c["type"] not in ("tvSeries", "tvMiniSeries") or c["start"] < FIRST_YEAR or lang not in ALLOWED_LANGS:
        return False
    return c["votes"] >= (MIN_VOTES_IT if lang == "it" else MIN_VOTES)


def build_pool(rows):
    """rows: candidate idonee con servizi, gia ordinate. -> (items, all_ids, by_genre)."""
    rows = sorted(rows, key=lambda r: (-r["rating"], -r["votes"], r["imdb"]))
    by_genre = {}
    for r in rows:
        for g in r["genres"]:
            code = GENRES.get(g, (g.lower(), g))[0]
            lst = by_genre.setdefault(code, [])
            if len(lst) < POOL_PER_GENRE:
                lst.append(r["tmdb"])
    all_ids = [r["tmdb"] for r in rows[:POOL_ALL]]
    needed = set(all_ids) | {i for lst in by_genre.values() for i in lst}
    items = [{"tmdb": r["tmdb"], "s": r["services"], "g": [GENRES.get(g, (g.lower(), g))[0] for g in r["genres"]]}
             for r in rows if r["tmdb"] in needed]
    return items, all_ids, by_genre


def norm_title(s):
    """Confronto titoli: minuscole, senza accenti e punteggiatura, '&' = 'and'."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def pick_match(title, results, on_netflix):
    """Sceglie la serie TMDB per un titolo della Top 10 Netflix.
    results: risultati di search/tv (id, name, original_name, popularity); on_netflix: id disponibili su Netflix IT.
    Regola: titolo identico (name o original_name) E su Netflix IT; poi titolo identico; poi la piu popolare su Netflix
    IT; altrimenti None (meglio un buco che una serie sbagliata)."""
    t = norm_title(title)
    exact = [r for r in results if t in (norm_title(r.get("name")), norm_title(r.get("original_name")))]
    for group in ([r for r in exact if r["id"] in on_netflix], exact,
                  [r for r in results if r["id"] in on_netflix]):
        if group:
            return max(group, key=lambda r: r.get("popularity") or 0)["id"]
    return None


def top10_rows(tsv_text, country="IT"):
    """Righe TV dell'ultima settimana per il paese, ordinate per posizione."""
    rows = [r for r in csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
            if r.get("country_iso2") == country and r.get("category", "").startswith("TV")]
    if not rows:
        return None, []
    week = max(r["week"] for r in rows)
    last = sorted((r for r in rows if r["week"] == week), key=lambda r: int(r["weekly_rank"]))
    return week, [{"rank": int(r["weekly_rank"]), "title": r["show_title"],
                   "season": r.get("season_title") if r.get("season_title") not in (None, "", "N/A") else None}
                  for r in last]


def novita_kind(details, today, days=NOVITA_DAYS):
    """'new_series' se la serie e uscita nella finestra, 'new_season' se ci e uscita una stagione, altrimenti None.
    Una stagione vecchia che manda in onda episodi settimanali NON conta come novita."""
    lo = today - dt.timedelta(days=days)

    def in_window(d):
        try:
            return lo <= dt.date.fromisoformat(d) <= today
        except (TypeError, ValueError):
            return False

    if in_window(details.get("first_air_date")):
        return "new_series", 1
    for s in sorted(details.get("seasons") or [], key=lambda s: s.get("season_number") or 0, reverse=True):
        if (s.get("season_number") or 0) > 0 and in_window(s.get("air_date")):
            return "new_season", s["season_number"]
    return None, None


def season_counts(first_air_date, imdb_rating):
    """Una nuova stagione entra nelle Novita? Serie partita dal 2018 e voto IMDb >= 7 con almeno 1.000 voti.
    imdb_rating: (voto, voti) oppure None se IMDb non la conosce."""
    try:
        year = int((first_air_date or "")[:4])
    except ValueError:
        return False
    return (year >= SEASON_FIRST_YEAR and imdb_rating is not None
            and imdb_rating[0] >= SEASON_MIN_RATING and imdb_rating[1] >= SEASON_MIN_VOTES)


def log(msg):
    print(f"[{dt.datetime.now(dt.timezone.utc):%H:%M:%S}] {msg}", flush=True)


# ----------------------------------------------------------------------------------------------- rete

def http(url, headers=None, tries=4, timeout=60):
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(2 ** i * (int(e.headers.get("Retry-After") or 1)))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(2 ** i)
                continue
            raise


def tmdb(path, **params):
    params["api_key"] = os.environ["TMDB_API_KEY"]
    return json.loads(http(f"{TMDB}{path}?{urllib.parse.urlencode(params)}"))


def tmdb_services(tmdb_id):
    p = tmdb(f"/tv/{tmdb_id}/watch/providers").get("results", {}).get("IT", {})
    ids = {x["provider_id"] for k in ("flatrate", "free", "ads") for x in p.get(k, [])}
    return sorted({PROVIDER_TO_SERVICE[i] for i in ids if i in PROVIDER_TO_SERVICE})


# ----------------------------------------------------------------------------------------------- sorgenti

def imdb_files(imdb_dir=None):
    """Cartella con i due .tsv.gz di IMDb: quella data, oppure li scarica (in streaming su disco) in una temporanea."""
    if imdb_dir:
        return Path(imdb_dir)
    d = Path(tempfile.mkdtemp(prefix="imdb-"))
    for name in ("title.ratings.tsv.gz", "title.basics.tsv.gz"):
        req = urllib.request.Request(IMDB_BASE + name, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(d / name, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        log(f"IMDb {name}: {(d / name).stat().st_size // 1_000_000} MB")
    return d


def imdb_ratings(d, min_votes=SEASON_MIN_VOTES):
    """tconst -> (voto, voti), solo titoli con almeno min_votes voti."""
    out = {}
    with gzip.open(d / "title.ratings.tsv.gz", "rt", encoding="utf-8") as f:
        next(f)
        for line in f:
            t, avg, n = line.rstrip("\n").split("\t")
            if int(n) >= min_votes:
                out[t] = (float(avg), int(n))
    return out


def imdb_candidates(d, ratings):
    """Serie/miniserie dal 2020 con almeno MIN_VOTES_IT voti (la soglia piena si applica dopo, con la lingua)."""
    out = []
    with gzip.open(d / "title.basics.tsv.gz", "rt", encoding="utf-8") as f:
        next(f)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[1] not in ("tvSeries", "tvMiniSeries") or p[0] not in ratings or p[5] == IMDB_NULL or int(p[5]) < FIRST_YEAR:
                continue
            avg, n = ratings[p[0]]
            if n < MIN_VOTES_IT:
                continue
            out.append({"imdb": p[0], "type": p[1], "start": int(p[5]), "rating": avg, "votes": n,
                        "genres": [] if p[8] == IMDB_NULL else p[8].split(",")})
    return out


def map_tmdb(cands, cache):
    """imdb -> {tmdb, lang}; la mappa resta in cache/tmdb_map.json (ID e lingua non cambiano)."""
    todo = [c["imdb"] for c in cands if c["imdb"] not in cache]

    def one(imdb):
        r = tmdb(f"/find/{imdb}", external_source="imdb_id").get("tv_results") or []
        return imdb, ({"tmdb": r[0]["id"], "lang": r[0].get("original_language")} if r else {"tmdb": None, "lang": None})

    with ThreadPoolExecutor(8) as ex:
        for imdb, v in ex.map(one, todo):
            cache[imdb] = v
    return cache


def section_pool(d, ratings, cache):
    cands = imdb_candidates(d, ratings)
    log(f"pool: {len(cands)} candidate IMDb, {sum(c['imdb'] not in cache for c in cands)} da mappare su TMDB")
    map_tmdb(cands, cache)
    ok = [dict(c, **cache[c["imdb"]]) for c in cands
          if cache[c["imdb"]]["tmdb"] and eligible(c, cache[c["imdb"]]["lang"])]
    with ThreadPoolExecutor(8) as ex:
        for c, s in zip(ok, ex.map(lambda c: tmdb_services(c["tmdb"]), ok)):
            c["services"] = s
    rows = [c for c in ok if c["services"]]
    items, all_ids, by_genre = build_pool(rows)
    labels = {code: label for code, label in GENRES.values()}
    return {"items": items, "all": all_ids, "by_genre": by_genre,
            "genres": {g: {"label": labels.get(g, g), "count": len(v)} for g, v in by_genre.items()},
            "candidates": len(cands), "eligible": len(ok), "on_services": len(rows)}


def netflix_ids(tmdb_ids):
    """TMDB tv id -> Netflix id da Wikidata (P4983 TMDB TV, P1874 Netflix). CC0; puo mancare."""
    if not tmdb_ids:
        return {}
    values = " ".join(f'"{i}"' for i in tmdb_ids)
    q = f'SELECT ?t ?n WHERE {{ VALUES ?t {{ {values} }} ?i wdt:P4983 ?t ; wdt:P1874 ?n . }}'
    data = json.loads(http(f"{WIKIDATA}?{urllib.parse.urlencode({'query': q, 'format': 'json'})}",
                           headers={"Accept": "application/sparql-results+json"}))
    return {int(b["t"]["value"]): b["n"]["value"] for b in data["results"]["bindings"]}


def section_top10():
    raw = http(NETFLIX_TSV, timeout=120, tries=3)
    log(f"Netflix TSV: {len(raw) // 1_000_000} MB")
    week, rows = top10_rows(raw.decode("utf-8"))
    for r in rows:
        res = tmdb("/search/tv", query=r["title"], language="en-US").get("results", [])[:8]
        on_nf = {x["id"] for x in res if "netflix" in tmdb_services(x["id"])}
        r["tmdb"] = pick_match(r["title"], res, on_nf)
        if r["tmdb"] is None:
            r["unmatched"] = True
    nf = netflix_ids([r["tmdb"] for r in rows if r["tmdb"]])
    items = [{"rank": r["rank"], "tmdb": r["tmdb"], "season": r["season"], "netflix_id": nf.get(r["tmdb"]),
              **({"title": r["title"]} if r["tmdb"] is None else {})} for r in rows]
    return {"week": week, "items": items}


def section_novita(today, ratings):
    out = {}
    lo = (today - dt.timedelta(days=NOVITA_DAYS)).isoformat()
    for svc, v in SERVICES.items():
        seen, picked = set(), []
        for page in (1, 2, 3):
            res = tmdb("/discover/tv", watch_region="IT", with_watch_providers="|".join(map(str, v["providers"])),
                       with_watch_monetization_types="flatrate|free|ads", **{"air_date.gte": lo, "air_date.lte": today.isoformat()},
                       sort_by="popularity.desc", page=page).get("results", [])
            for r in res:
                if r["id"] in seen or r.get("original_language") not in ALLOWED_LANGS:
                    continue
                seen.add(r["id"])
                details = tmdb(f"/tv/{r['id']}", append_to_response="external_ids")
                kind, season = novita_kind(details, today)
                if kind == "new_season":
                    imdb = (details.get("external_ids") or {}).get("imdb_id")
                    if not season_counts(details.get("first_air_date"), ratings.get(imdb)):
                        continue
                if kind:
                    picked.append({"tmdb": r["id"], "kind": kind, "season": season})
            if len(picked) >= NOVITA_MAX or not res:
                break
        out[svc] = picked[:NOVITA_MAX]
        log(f"novita {svc}: {len(out[svc])}")
    return out


# ----------------------------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", action="append", default=[], choices=["netflix", "novita", "pool"])
    ap.add_argument("--imdb-dir", help="cartella con i .tsv.gz di IMDb gia scaricati (test in locale)")
    args = ap.parse_args()
    today = dt.date.today()
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    data = {"generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "services": {k: {"name": v["name"], "package": v["package"]} for k, v in SERVICES.items()},
            "credits": "Dati: TMDB (non approvato ne certificato da TMDB), JustWatch via TMDB, IMDb (uso personale), "
                       "Netflix Top 10, Wikidata.",
            "stale": {}}
    failed = False
    imdb = {}

    def imdb_data():
        """(cartella, voti) di IMDb, scaricati una volta sola per Novita e pool."""
        if not imdb:
            imdb["dir"] = imdb_files(args.imdb_dir)
            imdb["ratings"] = imdb_ratings(imdb["dir"])
            log(f"IMDb: {len(imdb['ratings'])} titoli con almeno {SEASON_MIN_VOTES} voti")
        return imdb["dir"], imdb["ratings"]

    jobs = {"top10_netflix": ("netflix", section_top10),
            "novita": ("novita", lambda: section_novita(today, imdb_data()[1])),
            "pool": ("pool", lambda: section_pool(*imdb_data(), cache))}
    for key, (flag, fn) in jobs.items():
        if flag in args.skip:
            data[key] = old.get(key)
            data["stale"][key] = "skipped"
            continue
        log(f"{key}: inizio")
        try:
            data[key] = fn()
            log(f"{key}: ok")
        except Exception as e:  # una fonte giu non deve buttare via le altre: si tiene il dato precedente
            log(f"{key}: ERRORE {e!r}")
            data[key] = old.get(key)
            data["stale"][key] = f"{type(e).__name__}"
            failed = True
    # ID Netflix (Wikidata) anche per pool e novita su Netflix: "Guarda" apre il titolo, non solo l'app.
    try:
        want = sorted({i["tmdb"] for i in (data.get("pool") or {}).get("items", []) if "netflix" in i["s"]}
                      | {i["tmdb"] for i in (data.get("novita") or {}).get("netflix", [])})
        nf = {}
        for k in range(0, len(want), 150):
            nf.update(netflix_ids(want[k:k + 150]))
        for i in (data.get("pool") or {}).get("items", []):
            if i["tmdb"] in nf:
                i["nf"] = nf[i["tmdb"]]
        for i in (data.get("novita") or {}).get("netflix", []):
            if i["tmdb"] in nf:
                i["nf"] = nf[i["tmdb"]]
        log(f"ID Netflix: {len(nf)} su {len(want)}")
    except Exception as e:
        log(f"ID Netflix: ERRORE {e!r} (Guarda aprira solo l'app)")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report(data)
    return 1 if failed else 0


def report(d):
    p, t, n = d.get("pool") or {}, d.get("top10_netflix") or {}, d.get("novita") or {}
    print("| Sezione | Valore |\n|---|---|")
    print(f"| Pool: candidate IMDb / idonee / sui servizi | {p.get('candidates')} / {p.get('eligible')} / {p.get('on_services')} |")
    genres = ", ".join(f"{v['label']} {v['count']}" for v in (p.get("genres") or {}).values() if v["count"] >= 5)
    print(f"| Pool: generi (>=5) | {genres} |")
    items = t.get("items") or []
    print(f"| Top 10 Netflix IT ({t.get('week')}) | {sum(1 for i in items if i['tmdb'])}/{len(items)} abbinate, "
          f"{sum(1 for i in items if i.get('netflix_id'))} con ID Netflix |")
    for svc, lst in n.items():
        print(f"| Novita {svc} | {len(lst)} ({sum(1 for x in lst if x['kind'] == 'new_series')} nuove serie) |")
    print(f"| Sezioni non aggiornate | {d.get('stale') or 'nessuna'} |")


if __name__ == "__main__":
    sys.exit(main())
