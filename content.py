"""Dati per la home della TV (fork lorenzoma97/cleanyourtv): docs/content.json, pubblicato su GitHub Pages.

Regole concordate con Lorenzo (24-26 ott 2026):
  Servizi attivi  Netflix (8), Prime Video (119, 2100 con pubblicita), RaiPlay (222), Mediaset Infinity (359),
                  HBO Max (1899, dal 18 nov 2026; non il canale HBO Max dentro Prime, 1825), Italia.
                  NOW non e attivo: escluso.
  Pool serie      IMDb: tvSeries + tvMiniSeries, prima uscita dal 2020, almeno 25.000 voti (5.000 se in lingua
                  italiana); lingua originale inglese o europea (no coreane, anime, indiane); disponibili sui servizi
                  attivi. Ordine: voto IMDb, poi voti. "all" = prime 100; per ogni genere IMDb le prime fino a 100.
  Novita          serie nuove o nuove stagioni USCITE negli ultimi 30 giorni, per servizio (stesso filtro lingua del
                  pool). Non "aggiunte al catalogo": quel dato gratis non esiste. Le nuove stagioni contano solo per
                  serie partite dal 2018 con voto IMDb almeno 7 (via i procedurali lunghi tipo Law & Order, NCIS);
                  con meno di 1.000 voti il voto non e affidabile e la stagione non entra.
  Top 10          classifica ufficiale Netflix Italia, settimana piu recente, solo serie TV.

Aggiunte del 18 nov 2026, DA VALIDARE con Lorenzo:
  Top 10 film     stessa fonte, categoria Films (top10_netflix_film).
  Pool film       regola film: speculare alle serie, DA VALIDARE con Lorenzo. IMDb titleType "movie", uscita dal
                  FILM_FIRST_YEAR, almeno FILM_MIN_VOTES voti (FILM_MIN_VOTES_IT se in italiano), stesse lingue e
                  stessi servizi, stesso ordine (pool_film, stessa forma di pool).
  Sport           Serie A, Champions League (ESPN, JSON pubblico senza chiave) e F1 (Jolpica/Ergast, come
                  tools/race_calendar.py di cleanyourtv): eventi dei prossimi 8 giorni di calendario di Roma, oggi
                  compreso. Tabella SPORT_BROADCAST (dove si vede in Italia: Serie A su DAZN, Champions e F1 su
                  Sky/NOW) DA VALIDARE: e una nostra affermazione sui diritti TV, non un dato della fonte.

Cache TMDB (cache/tmdb_map.json): chiave = ID IMDb (unico fra serie e film). Le voci dei film portano "kind": "movie";
le voci senza "kind" sono serie (tutte quelle scritte prima del 18 nov). cached() legge una voce solo se il tipo
coincide: una serie non viene mai letta come film ne viceversa, e una voce del tipo sbagliato si rimappa.

Il file pubblico contiene solo ID e posizioni (niente voti IMDb, testi o immagini TMDB): i dettagli li scarica la TV
con la chiave TMDB di Lorenzo. Uso personale, non commerciale; crediti TMDB/JustWatch/IMDb mostrati sulla TV.

Uso: TMDB_API_KEY=... python content.py [--skip netflix|novita|pool|film|sport] [--imdb-dir DIR]
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
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs" / "content.json"
CACHE = ROOT / "cache" / "tmdb_map.json"

SERVICES = {
    "netflix": {"name": "Netflix", "package": "com.netflix.ninja", "providers": [8]},
    "prime": {"name": "Prime Video", "package": "com.amazon.amazonvideo.livingroom", "providers": [119, 2100]},
    "raiplay": {"name": "RaiPlay", "package": "it.rainet.androidtv", "providers": [222]},
    "infinity": {"name": "Mediaset Infinity", "package": "it.mediaset.infinitytv", "providers": [359]},
    "hbomax": {"name": "HBO Max", "package": "com.wbd.stream", "providers": [1899]},
}
PROVIDER_TO_SERVICE = {p: s for s, v in SERVICES.items() for p in v["providers"]}
# Inglese ed europee (Turchia compresa), concordato il 26 ott. Fuori: ko, ja, zh, hi e altre lingue indiane, ar...
ALLOWED_LANGS = {"en", "it", "es", "fr", "de", "pt", "nl", "sv", "no", "nb", "da", "fi", "is", "pl", "cs", "sk", "hu",
                 "ro", "el", "tr", "ca", "eu", "gl", "sl", "hr", "sr", "bs", "bg", "uk", "ru", "lt", "lv", "et", "ga",
                 "cy", "sq", "mk", "mt", "lb"}
MIN_VOTES, MIN_VOTES_IT, FIRST_YEAR = 25000, 5000, 2020
# Film: speculare alle serie, DA VALIDARE con Lorenzo.
FILM_MIN_VOTES, FILM_MIN_VOTES_IT, FILM_FIRST_YEAR = 25000, 5000, 2020
# Per tipo TMDB ("tv" | "movie"): titoli IMDb ammessi, soglie, campi del titolo su TMDB, categoria della Top 10 Netflix,
# proprieta Wikidata con l'ID TMDB (l'ID Netflix e sempre P1874).
KINDS = {
    "tv": {"imdb_types": ("tvSeries", "tvMiniSeries"), "first_year": FIRST_YEAR, "min_votes": MIN_VOTES,
           "min_votes_it": MIN_VOTES_IT, "titles": ("name", "original_name"), "top10": "TV", "wikidata": "P4983"},
    "movie": {"imdb_types": ("movie",), "first_year": FILM_FIRST_YEAR, "min_votes": FILM_MIN_VOTES,
              "min_votes_it": FILM_MIN_VOTES_IT, "titles": ("title", "original_title"), "top10": "Films",
              "wikidata": "P4947"},
}
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
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"
JOLPICA = "https://api.jolpi.ca/ergast/f1/current.json?limit=100"

# Dove si vede in Italia. DA VALIDARE con Lorenzo (affermazione nostra sui diritti TV, non un dato della fonte).
SPORT_BROADCAST = {
    "seriea": {"name": "Serie A", "where": "DAZN", "app": "dazn"},
    "champions": {"name": "Champions League", "where": "Sky · NOW", "app": None},
    "f1": {"name": "Formula 1", "where": "Sky · NOW", "app": None},
}
SPORT_DAYS = 8                                   # oggi compreso, giorni di calendario di Roma
ROME = ZoneInfo("Europe/Rome")
ESPN_LEAGUES = {"seriea": "ita.1", "champions": "uefa.champions"}
ESPN_DROP = {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_ABANDONED"}   # partite che non si giocano piu
# Nomi delle squadre come si dicono in Italia, solo dove lo shortDisplayName di ESPN e diverso. Il resto resta com'e.
TEAM_IT = {"Inter Milan": "Inter", "Internazionale": "Inter", "AC Milan": "Milan", "AS Roma": "Roma",
           "Hellas Verona": "Verona", "Bayern": "Bayern Monaco", "Bayern Munich": "Bayern Monaco",
           "Man Utd": "Manchester United", "Man City": "Manchester City", "Atlético": "Atlético Madrid",
           "S Bratislava": "Slovan Bratislava", "Slavia Prague": "Slavia Praga", "Sporting": "Sporting Lisbona",
           "FC Porto": "Porto", "Red Star Belgrade": "Stella Rossa", "Copenhagen": "Copenaghen"}
# Sessioni F1 (chiavi Jolpica, come tools/race_calendar.py di cleanyourtv); niente prove libere. La gara e il blocco
# principale della corsa ("date"/"time").
F1_SESSIONS = (("SprintQualifying", "Qualifiche sprint"), ("SprintShootout", "Qualifiche sprint"),
               ("Sprint", "Sprint"), ("Qualifying", "Qualifiche"))
# "Las Vegas Grand Prix" -> "GP di Las Vegas". Un GP nuovo non in tabella resta "<nome> GP", come sul launcher Metz.
F1_GP_IT = {"Australian": "d'Australia", "Chinese": "di Cina", "Japanese": "del Giappone", "Bahrain": "del Bahrain",
            "Saudi Arabian": "d'Arabia Saudita", "Miami": "di Miami", "Emilia Romagna": "dell'Emilia-Romagna",
            "Canadian": "del Canada", "Monaco": "di Monaco", "Barcelona": "di Barcellona", "Spanish": "di Spagna",
            "Austrian": "d'Austria", "British": "di Gran Bretagna", "Belgian": "del Belgio",
            "Hungarian": "d'Ungheria", "Dutch": "d'Olanda", "Italian": "d'Italia", "Azerbaijan": "dell'Azerbaigian",
            "Singapore": "di Singapore", "United States": "degli Stati Uniti", "Mexico City": "del Messico",
            "São Paulo": "di San Paolo", "Brazilian": "del Brasile", "Las Vegas": "di Las Vegas",
            "Qatar": "del Qatar", "Abu Dhabi": "di Abu Dhabi", "Madrid": "di Madrid"}


# ----------------------------------------------------------------------------------------------- regole (pure)

def eligible(c, lang, kind="tv"):
    """Una candidata IMDb entra nel pool (kind "tv") o nel pool film ("movie")? c: {type, start, votes}; lang: lingua
    originale TMDB."""
    k = KINDS[kind]
    if c["type"] not in k["imdb_types"] or c["start"] < k["first_year"] or lang not in ALLOWED_LANGS:
        return False
    return c["votes"] >= (k["min_votes_it"] if lang == "it" else k["min_votes"])


def cached(cache, imdb, kind):
    """Voce della cache TMDB per questo ID IMDb, solo se e dello stesso tipo (voci senza "kind" = serie)."""
    v = cache.get(imdb)
    return v if v is not None and v.get("kind", "tv") == kind else None


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


def pick_match(title, results, on_netflix, fields=KINDS["tv"]["titles"]):
    """Sceglie la serie (o il film) TMDB per un titolo della Top 10 Netflix.
    results: risultati di search/tv (id, name, original_name, popularity) o search/movie (fields = title,
    original_title); on_netflix: id disponibili su Netflix IT.
    Regola: titolo identico (name o original_name) E su Netflix IT; poi titolo identico; poi la piu popolare su Netflix
    IT; altrimenti None (meglio un buco che una serie sbagliata)."""
    t = norm_title(title)
    exact = [r for r in results if t in {norm_title(r.get(f)) for f in fields}]
    for group in ([r for r in exact if r["id"] in on_netflix], exact,
                  [r for r in results if r["id"] in on_netflix]):
        if group:
            return max(group, key=lambda r: r.get("popularity") or 0)["id"]
    return None


def top10_rows(tsv_text, country="IT", category="TV"):
    """Righe dell'ultima settimana per paese e categoria ("TV" o "Films"), ordinate per posizione."""
    rows = [r for r in csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
            if r.get("country_iso2") == country and r.get("category", "").startswith(category)]
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


def sport_window(now, days=SPORT_DAYS):
    """[inizio, fine) in UTC: da mezzanotte di oggi a Roma a mezzanotte di oggi+days a Roma. now: datetime con fuso."""
    day = now.astimezone(ROME).date()
    lo = dt.datetime.combine(day, dt.time(), ROME)
    hi = dt.datetime.combine(day + dt.timedelta(days=days), dt.time(), ROME)
    return lo.astimezone(dt.timezone.utc), hi.astimezone(dt.timezone.utc)


def utc_instant(s):
    """'2026-11-22T19:45Z' / '2026-11-22T04:00:00Z' -> datetime UTC (senza fuso = UTC, come race_calendar.py)."""
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)


def iso_z(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def team_name(team):
    name = team.get("shortDisplayName") or team.get("displayName") or ""
    return TEAM_IT.get(name, name)


def espn_events(data, comp, italian_ids, lo, hi):
    """Partite di una risposta scoreboard ESPN che iniziano in [lo, hi). italian_ids: ID ESPN dei club di Serie A."""
    out = []
    for e in data.get("events") or []:
        if ((e.get("status") or {}).get("type") or {}).get("name") in ESPN_DROP:
            continue
        start = utc_instant(e["date"])
        if not lo <= start < hi:
            continue
        side = {x.get("homeAway"): x.get("team") or {} for x in (e.get("competitions") or [{}])[0].get("competitors", [])}
        home, away = side.get("home"), side.get("away")
        if not home or not away:
            continue
        out.append({"comp": comp, "start": iso_z(start), "home": team_name(home), "away": team_name(away),
                    "home_logo": home.get("logo"), "away_logo": away.get("logo"),
                    "it": comp == "seriea" or str(home.get("id")) in italian_ids or str(away.get("id")) in italian_ids})
    return out


def gp_name_it(race_name):
    """'Las Vegas Grand Prix' -> 'GP di Las Vegas'; fuori tabella 'X Grand Prix' -> 'X GP' (come il launcher Metz)."""
    key = race_name.removesuffix(" Grand Prix")
    return f"GP {F1_GP_IT[key]}" if key in F1_GP_IT else race_name.replace("Grand Prix", "GP").strip()


def f1_events(races, lo, hi):
    """Qualifiche, qualifiche sprint, sprint e gara (Jolpica) che iniziano in [lo, hi); niente prove libere."""
    out = []
    for race in races:
        blocks = [(race.get(k), name) for k, name in F1_SESSIONS] + [(race, "Gara")]
        for b, session in blocks:
            if not b or not b.get("date") or not b.get("time"):    # orario non ancora pubblicato
                continue
            start = utc_instant(f"{b['date']}T{b['time']}")
            if lo <= start < hi:
                out.append({"comp": "f1", "start": iso_z(start), "title": gp_name_it(race["raceName"]),
                            "session": session})
    return out


def espn_months(lo, hi):
    """Mesi (YYYYMM) da chiedere a ESPN: lo scoreboard accetta un giorno o un mese, non un intervallo (risposta 400).
    Un giorno di margine per lato: il mese di ESPN e quello dell'ora americana."""
    d, end, out = (lo - dt.timedelta(days=1)).date(), (hi + dt.timedelta(days=1)).date(), []
    while d <= end:
        if f"{d:%Y%m}" not in out:
            out.append(f"{d:%Y%m}")
        d += dt.timedelta(days=1)
    return out


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


def tmdb_services(tmdb_id, kind="tv"):
    p = tmdb(f"/{kind}/{tmdb_id}/watch/providers").get("results", {}).get("IT", {})
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


def imdb_candidates(d, ratings, kind="tv"):
    """Serie/miniserie (o film) dal primo anno con almeno la soglia ridotta di voti (la soglia piena si applica dopo,
    con la lingua)."""
    k, out = KINDS[kind], []
    with gzip.open(d / "title.basics.tsv.gz", "rt", encoding="utf-8") as f:
        next(f)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[1] not in k["imdb_types"] or p[0] not in ratings or p[5] == IMDB_NULL or int(p[5]) < k["first_year"]:
                continue
            avg, n = ratings[p[0]]
            if n < k["min_votes_it"]:
                continue
            out.append({"imdb": p[0], "type": p[1], "start": int(p[5]), "rating": avg, "votes": n,
                        "genres": [] if p[8] == IMDB_NULL else p[8].split(",")})
    return out


def map_tmdb(cands, cache, kind="tv"):
    """imdb -> {tmdb, lang} (+ "kind": "movie" per i film); la mappa resta in cache/tmdb_map.json (ID e lingua non
    cambiano). Una voce in cache di tipo diverso non vale (vedi cached()) e viene rimappata."""
    todo = [c["imdb"] for c in cands if cached(cache, c["imdb"], kind) is None]
    extra = {} if kind == "tv" else {"kind": kind}     # le serie restano senza "kind", come le voci gia in cache

    def one(imdb):
        r = tmdb(f"/find/{imdb}", external_source="imdb_id").get(f"{kind}_results") or []
        return imdb, ({"tmdb": r[0]["id"], "lang": r[0].get("original_language")} if r else {"tmdb": None, "lang": None})

    with ThreadPoolExecutor(8) as ex:
        for imdb, v in ex.map(one, todo):
            cache[imdb] = {**v, **extra}
    return cache


def section_pool(d, ratings, cache, kind="tv"):
    name = "pool" if kind == "tv" else "pool_film"
    cands = imdb_candidates(d, ratings, kind)
    log(f"{name}: {len(cands)} candidate IMDb, {sum(cached(cache, c['imdb'], kind) is None for c in cands)} "
        f"da mappare su TMDB")
    map_tmdb(cands, cache, kind)
    ok = [dict(c, tmdb=v["tmdb"], lang=v["lang"]) for c in cands
          if (v := cached(cache, c["imdb"], kind))["tmdb"] and eligible(c, v["lang"], kind)]

    def services(c):
        try:
            return tmdb_services(c["tmdb"], kind)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            # Titolo tolto o fuso su TMDB: fuori dal pool, e alla prossima corsa si rimappa da IMDb. Un solo 404 ha
            # tenuto fermo tutto il pool dal 12 al 17 nov 2026.
            cache.pop(c["imdb"], None)
            log(f"{name}: {c['imdb']} non e piu su TMDB ({c['tmdb']}), saltato")
            return []

    with ThreadPoolExecutor(8) as ex:
        for c, s in zip(ok, ex.map(services, ok)):
            c["services"] = s
    rows = [c for c in ok if c["services"]]
    items, all_ids, by_genre = build_pool(rows)
    labels = {code: label for code, label in GENRES.values()}
    return {"items": items, "all": all_ids, "by_genre": by_genre,
            "genres": {g: {"label": labels.get(g, g), "count": len(v)} for g, v in by_genre.items()},
            "candidates": len(cands), "eligible": len(ok), "on_services": len(rows)}


def netflix_ids(tmdb_ids, kind="tv"):
    """TMDB id -> Netflix id da Wikidata (P4983 TMDB TV o P4947 TMDB film, P1874 Netflix). CC0; puo mancare."""
    if not tmdb_ids:
        return {}
    values = " ".join(f'"{i}"' for i in tmdb_ids)
    q = f'SELECT ?t ?n WHERE {{ VALUES ?t {{ {values} }} ?i wdt:{KINDS[kind]["wikidata"]} ?t ; wdt:P1874 ?n . }}'
    # POST and a short timeout: a long GET to the query service once hung the whole Actions job for 30 minutes.
    req = urllib.request.Request(WIKIDATA, data=urllib.parse.urlencode({"query": q}).encode(), method="POST",
                                 headers={"User-Agent": UA, "Accept": "application/sparql-results+json",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read())
    return {int(b["t"]["value"]): b["n"]["value"] for b in data["results"]["bindings"]}


@lru_cache(maxsize=1)
def netflix_tsv():
    """Il TSV della Top 10 Netflix, scaricato una volta per serie e film."""
    raw = http(NETFLIX_TSV, timeout=120, tries=3)
    log(f"Netflix TSV: {len(raw) // 1_000_000} MB")
    return raw.decode("utf-8")


def section_top10(kind="tv"):
    k = KINDS[kind]
    week, rows = top10_rows(netflix_tsv(), category=k["top10"])
    for r in rows:
        res = tmdb(f"/search/{kind}", query=r["title"], language="en-US").get("results", [])[:8]
        on_nf = {x["id"] for x in res if "netflix" in tmdb_services(x["id"], kind)}
        r["tmdb"] = pick_match(r["title"], res, on_nf, k["titles"])
    nf = netflix_ids([r["tmdb"] for r in rows if r["tmdb"]], kind)
    items = [{"rank": r["rank"], "tmdb": r["tmdb"], **({"season": r["season"]} if kind == "tv" else {}),
              "netflix_id": nf.get(r["tmdb"]), **({"title": r["title"]} if r["tmdb"] is None else {})} for r in rows]
    return {"week": week, "items": items}


def espn(league, month):
    return json.loads(http(f"{ESPN}/{league}/scoreboard?dates={month}&limit=200", timeout=30))


def section_sport(now):
    """Serie A e Champions (ESPN) e F1 (Jolpica) dei prossimi SPORT_DAYS giorni di Roma; fuori stagione events = []."""
    lo, hi = sport_window(now)
    teams = json.loads(http(f"{ESPN}/{ESPN_LEAGUES['seriea']}/teams", timeout=30))
    italian = {str(t["team"]["id"]) for lg in teams["sports"][0]["leagues"] for t in lg["teams"]}
    if not italian:
        raise ValueError("ESPN: nessuna squadra di Serie A")
    events = []
    for comp, league in ESPN_LEAGUES.items():
        seen = set()
        for month in espn_months(lo, hi):
            for e in espn_events(espn(league, month), comp, italian, lo, hi):
                if (e["start"], e["home"], e["away"]) not in seen:      # per sicurezza, se due mesi si sovrappongono
                    seen.add((e["start"], e["home"], e["away"]))
                    events.append(e)
    races = json.loads(http(JOLPICA, timeout=30))["MRData"]["RaceTable"]["Races"]
    events += f1_events(races, lo, hi)
    events.sort(key=lambda e: (e["start"], list(SPORT_BROADCAST).index(e["comp"]), e.get("home") or e.get("title")))
    for comp in SPORT_BROADCAST:
        log(f"sport {comp}: {sum(e['comp'] == comp for e in events)}")
    return {"broadcast": SPORT_BROADCAST, "events": events}


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


def add_netflix_ids(lists, kind, budget=120):
    """Scrive "nf" sugli elementi su Netflix (pool: "netflix" in s; novita netflix: tutti), a blocchi di 40, entro
    budget secondi per tipo. Un errore lascia gli elementi senza "nf" (Guarda apre solo l'app), non ferma la corsa."""
    try:
        items = [i for lst in lists for i in lst if "netflix" in i.get("s", ["netflix"])]
        want = sorted({i["tmdb"] for i in items})
        nf, deadline = {}, time.time() + budget
        for k in range(0, len(want), 40):
            if time.time() > deadline:
                log(f"ID Netflix {kind}: tempo scaduto, continuo senza i restanti")
                break
            try:
                nf.update(netflix_ids(want[k:k + 40], kind))
            except Exception as e:
                log(f"ID Netflix {kind}: blocco {k // 40 + 1} saltato ({type(e).__name__})")
        for i in items:
            if i["tmdb"] in nf:
                i["nf"] = nf[i["tmdb"]]
        log(f"ID Netflix {kind}: {len(nf)} su {len(want)}")
    except Exception as e:
        log(f"ID Netflix {kind}: ERRORE {e!r} (Guarda aprira solo l'app)")


# ----------------------------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", action="append", default=[], choices=["netflix", "novita", "pool", "film", "sport"],
                    help="film = top10_netflix_film e pool_film")
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
            "pool": ("pool", lambda: section_pool(*imdb_data(), cache)),
            "top10_netflix_film": ("film", lambda: section_top10("movie")),
            "pool_film": ("film", lambda: section_pool(*imdb_data(), cache, "movie")),
            "sport": ("sport", lambda: section_sport(dt.datetime.now(dt.timezone.utc)))}
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
    # ID Netflix (Wikidata) anche per pool e novita su Netflix: "Guarda" apre il titolo, non solo l'app. Serie e film
    # hanno ID TMDB indipendenti (lo stesso numero puo essere una serie e un film): due giri separati.
    add_netflix_ids([(data.get("pool") or {}).get("items", []), (data.get("novita") or {}).get("netflix", [])], "tv")
    add_netflix_ids([(data.get("pool_film") or {}).get("items", [])], "movie")
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
    tf, pf, sp = d.get("top10_netflix_film") or {}, d.get("pool_film") or {}, d.get("sport") or {}
    items = tf.get("items") or []
    print(f"| Top 10 film Netflix IT ({tf.get('week')}) | {sum(1 for i in items if i['tmdb'])}/{len(items)} abbinati, "
          f"{sum(1 for i in items if i.get('netflix_id'))} con ID Netflix |")
    print(f"| Pool film: candidati IMDb / idonei / sui servizi | {pf.get('candidates')} / {pf.get('eligible')} / "
          f"{pf.get('on_services')} ({sum(1 for i in pf.get('items') or [] if 'nf' in i)} con ID Netflix) |")
    ev = sp.get("events") or []
    print(f"| Sport (8 giorni) | " + ", ".join(f"{v['name']} {sum(e['comp'] == k for e in ev)}"
                                            for k, v in (sp.get("broadcast") or {}).items())
          + f"; con squadre italiane {sum(1 for e in ev if e.get('it'))} |")
    print(f"| Sezioni non aggiornate | {d.get('stale') or 'nessuna'} |")


if __name__ == "__main__":
    sys.exit(main())
