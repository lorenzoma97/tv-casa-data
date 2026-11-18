"""Golden test delle regole concordate (nessuna rete). python -m unittest -v"""
import datetime as dt
import unittest

import content as c

TODAY = dt.date(2026, 10, 26)


def cand(votes, start=2021, typ="tvSeries"):
    return {"type": typ, "start": start, "votes": votes}


class Eligible(unittest.TestCase):
    def test_soglie_e_lingue(self):
        cases = [
            (cand(25000), "en", True),            # soglia piena
            (cand(24999), "en", False),
            (cand(5000), "it", True),             # serie italiane: soglia ridotta
            (cand(4999), "it", False),
            (cand(500000), "ko", False),          # coreane escluse anche se votatissime
            (cand(320000), "hi", False),          # indiane escluse (Aspirants)
            (cand(460000), "ja", False),          # anime esclusi
            (cand(30000), "tr", True),            # Turchia dentro (europee)
            (cand(100000, start=2019), "en", False),   # prima del 2020
            (cand(100000, typ="tvMiniSeries"), "en", True),  # miniserie incluse
            (cand(100000, typ="tvMovie"), "en", False),
        ]
        for row, lang, want in cases:
            with self.subTest(row=row, lang=lang):
                self.assertEqual(c.eligible(row, lang), want)


class Pool(unittest.TestCase):
    def test_ordine_tagli_e_generi(self):
        rows = [{"imdb": f"tt{i:03}", "tmdb": i, "rating": 9.0 - i * 0.01, "votes": 100000, "services": ["netflix"],
                 "genres": ["Drama"] if i % 2 else ["Drama", "Thriller"]} for i in range(150)]
        rows.append({"imdb": "tt999", "tmdb": 999, "rating": 9.5, "votes": 30000, "services": ["prime"], "genres": ["Sci-Fi"]})
        items, all_ids, by_genre = c.build_pool(rows)
        self.assertEqual(all_ids[0], 999)                      # voto piu alto in testa
        self.assertEqual(len(all_ids), 100)
        self.assertEqual(len(by_genre["drammatico"]), 100)     # tetto per genere
        self.assertEqual(len(by_genre["thriller"]), 75)        # 75 thriller su 150
        self.assertIn(148, by_genre["thriller"])               # oltre le prime 100 complessive, ma top del genere
        self.assertIn(148, {i["tmdb"] for i in items})         # serve all'elenco per genere -> presente negli items
        self.assertEqual(by_genre["fantascienza"], [999])

    def test_pari_voto_vince_chi_ha_piu_voti(self):
        rows = [{"imdb": "a", "tmdb": 1, "rating": 8.5, "votes": 30000, "services": ["netflix"], "genres": []},
                {"imdb": "b", "tmdb": 2, "rating": 8.5, "votes": 90000, "services": ["netflix"], "genres": []}]
        self.assertEqual(c.build_pool(rows)[1], [2, 1])


class Titles(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(c.norm_title("Mercoledì"), "mercoledi")
        self.assertEqual(c.norm_title("Love & Death"), c.norm_title("Love and Death"))
        self.assertEqual(c.norm_title("The Witcher: Blood Origin"), "the witcher blood origin")

    def test_pick_match(self):
        res = [{"id": 1, "name": "Wednesday", "original_name": "Wednesday", "popularity": 50},
               {"id": 2, "name": "Wednesday", "original_name": "Wednesday", "popularity": 900},  # omonima, non su Netflix
               {"id": 3, "name": "Wednesday Addams Show", "original_name": "x", "popularity": 2000}]
        self.assertEqual(c.pick_match("Wednesday", res, on_netflix={1}), 1)      # identica + su Netflix vince
        self.assertEqual(c.pick_match("Wednesday", res, on_netflix=set()), 2)    # identica, la piu popolare
        self.assertEqual(c.pick_match("Mercoledi", res, on_netflix={3}), 3)      # nessuna identica: popolare su Netflix
        self.assertIsNone(c.pick_match("Mercoledi", res, on_netflix=set()))      # meglio un buco che una sbagliata
        heist = [{"id": 7, "name": "Money Heist", "original_name": "La casa de papel", "popularity": 10}]
        self.assertEqual(c.pick_match("La Casa de Papel", heist, on_netflix=set()), 7)  # titolo originale


class Top10(unittest.TestCase):
    TSV = ("country_name\tcountry_iso2\tweek\tcategory\tweekly_rank\tshow_title\tseason_title\tcumulative_weeks_in_top_10\n"
           "Italy\tIT\t2026-10-12\tTV\t1\tOld Show\tOld Show: Season 1\t3\n"
           "Italy\tIT\t2026-10-19\tTV\t2\tSecond\tN/A\t1\n"
           "Italy\tIT\t2026-10-19\tTV\t1\tFirst\tFirst: Season 2\t2\n"
           "Italy\tIT\t2026-10-19\tFilms\t1\tA Film\tN/A\t1\n"
           "Spain\tES\t2026-10-19\tTV\t1\tSpanish\tN/A\t1\n")

    def test_ultima_settimana_italia_solo_tv(self):
        week, rows = c.top10_rows(self.TSV)
        self.assertEqual(week, "2026-10-19")
        self.assertEqual([(r["rank"], r["title"], r["season"]) for r in rows],
                         [(1, "First", "First: Season 2"), (2, "Second", None)])


class Novita(unittest.TestCase):
    def test_tipi(self):
        cases = [
            ({"first_air_date": "2026-10-10", "seasons": [{"season_number": 1, "air_date": "2026-10-10"}]}, ("new_series", 1)),
            ({"first_air_date": "2019-01-01", "seasons": [{"season_number": 1, "air_date": "2019-01-01"},
                                                         {"season_number": 3, "air_date": "2026-10-01"}]}, ("new_season", 3)),
            # stagione partita due mesi fa, episodi settimanali ancora in onda: non e una novita
            ({"first_air_date": "2024-01-01", "seasons": [{"season_number": 2, "air_date": "2026-08-20"}]}, (None, None)),
            # gli speciali (stagione 0) non contano
            ({"first_air_date": "2020-01-01", "seasons": [{"season_number": 0, "air_date": "2026-10-20"}]}, (None, None)),
            ({"first_air_date": "2026-09-26", "seasons": []}, ("new_series", 1)),   # bordo: 30 giorni esatti
            ({"first_air_date": "2026-09-25", "seasons": []}, (None, None)),
            ({"first_air_date": None, "seasons": [{"season_number": 1, "air_date": "boh"}]}, (None, None)),
        ]
        for details, want in cases:
            with self.subTest(details=details):
                self.assertEqual(c.novita_kind(details, TODAY), want)


class NuoveStagioni(unittest.TestCase):
    def test_solo_serie_recenti_e_buone(self):
        cases = [
            ("1999-09-20", (8.1, 140000), False),   # Law & Order SVU: serie troppo vecchia
            ("2003-09-23", (7.8, 210000), False),   # NCIS
            ("2021-01-08", (7.5, 150000), True),    # Lupin
            ("2023-04-20", (8.0, 90000), True),     # The Diplomat
            ("2020-05-01", (6.4, 30000), False),    # recente ma voto sotto 7
            ("2018-01-01", (7.0, 1000), True),      # bordi inclusi
            ("2022-02-02", (8.9, 999), False),      # voto non affidabile
            ("2022-02-02", None, False),            # IMDb non la conosce
            (None, (8.0, 50000), False),            # data mancante
        ]
        for first, rating, want in cases:
            with self.subTest(first=first, rating=rating):
                self.assertEqual(c.season_counts(first, rating), want)


class PoolSenzaSerieSparite(unittest.TestCase):
    def test_un_404_di_tmdb_non_ferma_il_pool(self):
        import urllib.error
        from unittest import mock
        cands = [{"imdb": "tt1", "type": "tvSeries", "start": 2021, "rating": 8.0, "votes": 90000, "genres": ["Drama"]},
                 {"imdb": "tt2", "type": "tvSeries", "start": 2021, "rating": 7.5, "votes": 90000, "genres": ["Drama"]}]
        cache = {"tt1": {"tmdb": 1, "lang": "en"}, "tt2": {"tmdb": 2, "lang": "en"}}

        def services(tmdb_id, kind="tv"):
            if tmdb_id == 2:
                raise urllib.error.HTTPError("u", 404, "Not Found", None, None)
            return ["hbomax"]

        with mock.patch.object(c, "imdb_candidates", return_value=cands), mock.patch.object(c, "tmdb_services", services),                 mock.patch.object(c, "log", lambda m: None):
            pool = c.section_pool(None, {}, cache)
        self.assertEqual(pool["all"], [1])
        self.assertEqual(pool["items"][0]["s"], ["hbomax"])
        self.assertNotIn("tt2", cache)                         # si rimappa alla prossima corsa


class Film(unittest.TestCase):
    def test_soglie_film(self):
        cases = [
            (cand(25000, typ="movie"), "en", True),
            (cand(24999, typ="movie"), "en", False),
            (cand(5000, typ="movie"), "it", True),
            (cand(4999, typ="movie"), "it", False),
            (cand(400000, typ="movie"), "ko", False),
            (cand(90000, start=2019, typ="movie"), "en", False),
            (cand(90000, typ="tvSeries"), "en", False),       # una serie non entra nel pool film
            (cand(90000, typ="tvMovie"), "en", False),        # film TV fuori (regola da validare)
        ]
        for row, lang, want in cases:
            with self.subTest(row=row, lang=lang):
                self.assertEqual(c.eligible(row, lang, "movie"), want)
        self.assertFalse(c.eligible(cand(90000, typ="movie"), "en"))  # e un film non entra nel pool serie

    def test_righe_film_della_top10(self):
        week, rows = c.top10_rows(Top10.TSV, category="Films")
        self.assertEqual(week, "2026-10-19")
        self.assertEqual([(r["rank"], r["title"], r["season"]) for r in rows], [(1, "A Film", None)])

    def test_pick_match_film(self):
        res = [{"id": 10, "title": "Frankenstein", "original_title": "Frankenstein", "popularity": 900},
               {"id": 11, "title": "Frankenstein", "original_title": "Frankenstein", "popularity": 40},
               {"id": 12, "title": "Il ragazzo", "original_title": "The Boy", "popularity": 5}]
        fields = c.KINDS["movie"]["titles"]
        self.assertEqual(c.pick_match("Frankenstein", res, {11}, fields), 11)
        self.assertEqual(c.pick_match("Frankenstein", res, set(), fields), 10)
        self.assertEqual(c.pick_match("The Boy", res, set(), fields), 12)          # titolo originale
        self.assertIsNone(c.pick_match("Frankenstein", res, set()))                # campi delle serie: nessun match


class CacheSerieFilm(unittest.TestCase):
    def test_una_serie_non_si_legge_come_film(self):
        cache = {"tt1": {"tmdb": 1, "lang": "en"},                       # voce storica: serie
                 "tt2": {"tmdb": 2, "lang": "en", "kind": "movie"}}
        self.assertEqual(c.cached(cache, "tt1", "tv")["tmdb"], 1)
        self.assertIsNone(c.cached(cache, "tt1", "movie"))
        self.assertEqual(c.cached(cache, "tt2", "movie")["tmdb"], 2)
        self.assertIsNone(c.cached(cache, "tt2", "tv"))
        self.assertIsNone(c.cached(cache, "tt3", "tv"))

    def test_map_film_usa_movie_results_e_non_tocca_le_serie(self):
        from unittest import mock
        cache = {"tt1": {"tmdb": 1, "lang": "en"}, "tt2": {"tmdb": 99, "lang": "en"}}   # tt2 in cache come serie
        calls = []

        def fake(path, **params):
            calls.append(path)
            return {"tv_results": [{"id": 555, "original_language": "en"}],
                    "movie_results": [{"id": 777, "original_language": "it"}]}

        with mock.patch.object(c, "tmdb", fake):
            c.map_tmdb([{"imdb": "tt1"}], cache, "tv")                   # gia in cache: nessuna chiamata
            self.assertEqual(calls, [])
            c.map_tmdb([{"imdb": "tt2"}, {"imdb": "tt3"}], cache, "movie")
        self.assertEqual(cache["tt1"], {"tmdb": 1, "lang": "en"})
        self.assertEqual(cache["tt2"], {"tmdb": 777, "lang": "it", "kind": "movie"})   # rimappata come film
        self.assertEqual(cache["tt3"], {"tmdb": 777, "lang": "it", "kind": "movie"})

    def test_un_404_non_ferma_il_pool_film(self):
        import urllib.error
        from unittest import mock
        cands = [{"imdb": "tt1", "type": "movie", "start": 2022, "rating": 8.0, "votes": 90000, "genres": ["Drama"]},
                 {"imdb": "tt2", "type": "movie", "start": 2022, "rating": 7.5, "votes": 90000, "genres": ["Drama"]}]
        cache = {"tt1": {"tmdb": 1, "lang": "en", "kind": "movie"}, "tt2": {"tmdb": 2, "lang": "en", "kind": "movie"}}
        seen = []

        def services(tmdb_id, kind="tv"):
            seen.append(kind)
            if tmdb_id == 2:
                raise urllib.error.HTTPError("u", 404, "Not Found", None, None)
            return ["netflix"]

        with mock.patch.object(c, "imdb_candidates", return_value=cands), mock.patch.object(c, "tmdb_services", services), \
                mock.patch.object(c, "log", lambda m: None):
            pool = c.section_pool(None, {}, cache, "movie")
        self.assertEqual(pool["all"], [1])
        self.assertEqual(set(seen), {"movie"})                 # provider chiesti su /movie, non su /tv
        self.assertNotIn("tt2", cache)


# Risposta reale di ESPN (uefa.champions, novembre 2026) ridotta a tre partite e ai campi usati.
ESPN_UCL = {"events": [
    {"date": "2026-11-24T17:45Z", "status": {"type": {"name": "STATUS_SCHEDULED"}}, "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"id": "432", "shortDisplayName": "Galatasaray", "displayName": "Galatasaray", "logo": "https://a.espncdn.com/i/teamlogos/soccer/500/432.png"}},
        {"homeAway": "away", "team": {"id": "362", "shortDisplayName": "Aston Villa", "displayName": "Aston Villa", "logo": "https://a.espncdn.com/i/teamlogos/soccer/500/362.png"}}]}]},
    {"date": "2026-11-24T20:00Z", "status": {"type": {"name": "STATUS_SCHEDULED"}}, "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"id": "382", "shortDisplayName": "Man City", "displayName": "Manchester City", "logo": "https://a.espncdn.com/i/teamlogos/soccer/500/382.png"}},
        {"homeAway": "away", "team": {"id": "114", "shortDisplayName": "Napoli", "displayName": "Napoli", "logo": "https://a.espncdn.com/i/teamlogos/soccer/500/114.png"}}]}]},
    {"date": "2026-11-25T20:00Z", "status": {"type": {"name": "STATUS_SCHEDULED"}}, "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"id": "160", "shortDisplayName": "PSG", "displayName": "Paris Saint-Germain", "logo": "https://a.espncdn.com/i/teamlogos/soccer/500/160.png"}},
        {"homeAway": "away", "team": {"id": "104", "shortDisplayName": "AS Roma", "displayName": "AS Roma", "logo": "https://a.espncdn.com/i/teamlogos/soccer/500/104.png"}}]}]},
]}
ITALIAN = {"103", "104", "110", "114", "2572"}
UTC = dt.timezone.utc


class Sport(unittest.TestCase):
    LO, HI = dt.datetime(2026, 11, 17, 23, tzinfo=UTC), dt.datetime(2026, 11, 25, 23, tzinfo=UTC)

    def test_partite_champions_da_espn(self):
        ev = c.espn_events(ESPN_UCL, "champions", ITALIAN, self.LO, self.HI)
        self.assertEqual(ev[1], {"comp": "champions", "start": "2026-11-24T20:00:00Z", "home": "Manchester City",
                                 "away": "Napoli", "home_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/382.png",
                                 "away_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/114.png", "it": True})
        self.assertEqual([(e["home"], e["away"], e["it"]) for e in ev],
                         [("Galatasaray", "Aston Villa", False), ("Manchester City", "Napoli", True),
                          ("PSG", "Roma", True)])                       # "AS Roma" -> "Roma"

    def test_fuori_finestra_e_rinviate(self):
        ev = c.espn_events(ESPN_UCL, "champions", ITALIAN, self.LO, dt.datetime(2026, 11, 24, 20, tzinfo=UTC))
        self.assertEqual(len(ev), 1)                                    # fine esclusa
        import copy
        rinviata = copy.deepcopy(ESPN_UCL)
        rinviata["events"][0]["status"]["type"]["name"] = "STATUS_POSTPONED"
        self.assertEqual(len(c.espn_events(rinviata, "champions", ITALIAN, self.LO, self.HI)), 2)

    def test_serie_a_sempre_italiana(self):
        ev = c.espn_events(ESPN_UCL, "seriea", set(), self.LO, self.HI)
        self.assertTrue(all(e["it"] for e in ev))

    def test_nomi_squadre(self):
        for espn_name, want in [("Inter Milan", "Inter"), ("Internazionale", "Inter"), ("AC Milan", "Milan"),
                                ("Milan", "Milan"), ("AS Roma", "Roma"), ("Hellas Verona", "Verona"),
                                ("Bayern", "Bayern Monaco"), ("Juventus", "Juventus")]:
            self.assertEqual(c.team_name({"shortDisplayName": espn_name}), want)

    def test_sessioni_f1(self):
        races = [{"raceName": "Las Vegas Grand Prix", "date": "2026-11-22", "time": "04:00:00Z",
                  "FirstPractice": {"date": "2026-11-20", "time": "00:30:00Z"},
                  "ThirdPractice": {"date": "2026-11-21", "time": "00:30:00Z"},
                  "Qualifying": {"date": "2026-11-21", "time": "04:00:00Z"}},
                 {"raceName": "Qatar Grand Prix", "date": "2026-11-29", "time": "16:00:00Z",
                  "SprintQualifying": {"date": "2026-11-27", "time": "17:30:00Z"},
                  "Sprint": {"date": "2026-11-28", "time": "13:00:00Z"},
                  "Qualifying": {"date": "2026-11-28", "time": "17:00:00Z"}},
                 {"raceName": "Bahrain Grand Prix in Malaysia", "date": "2026-11-23", "time": None}]
        ev = c.f1_events(races, self.LO, dt.datetime(2026, 12, 1, tzinfo=UTC))
        self.assertEqual([(e["title"], e["session"], e["start"]) for e in ev], [
            ("GP di Las Vegas", "Qualifiche", "2026-11-21T04:00:00Z"),      # niente prove libere
            ("GP di Las Vegas", "Gara", "2026-11-22T04:00:00Z"),
            ("GP del Qatar", "Qualifiche sprint", "2026-11-27T17:30:00Z"),
            ("GP del Qatar", "Sprint", "2026-11-28T13:00:00Z"),
            ("GP del Qatar", "Qualifiche", "2026-11-28T17:00:00Z"),
            ("GP del Qatar", "Gara", "2026-11-29T16:00:00Z")])          # orario mancante: saltata
        self.assertEqual(c.gp_name_it("Bahrain Grand Prix in Malaysia"), "Bahrain GP in Malaysia")

    def test_finestra_8_giorni_ora_di_roma(self):
        lo, hi = c.sport_window(dt.datetime(2026, 11, 18, 10, tzinfo=UTC))
        self.assertEqual((lo, hi), (dt.datetime(2026, 11, 17, 23, tzinfo=UTC), dt.datetime(2026, 11, 25, 23, tzinfo=UTC)))
        # alle 23:30 UTC a Roma e gia domani: la partita delle 23:30 del 25 e fuori, quella del 17 e dentro
        sample = {"events": [dict(ESPN_UCL["events"][0], date=d) for d in ("2026-11-17T23:30Z", "2026-11-25T23:30Z")]}
        self.assertEqual([e["start"] for e in c.espn_events(sample, "champions", ITALIAN, lo, hi)], ["2026-11-17T23:30:00Z"])
        # alle 23:30 UTC del 18 (00:30 a Roma del 19) la finestra parte gia dal 19
        self.assertEqual(c.sport_window(dt.datetime(2026, 11, 18, 23, 30, tzinfo=UTC))[0],
                         dt.datetime(2026, 11, 18, 23, tzinfo=UTC))
        # in estate (ora legale) mezzanotte di Roma e alle 22 UTC
        self.assertEqual(c.sport_window(dt.datetime(2026, 7, 1, 12, tzinfo=UTC))[0], dt.datetime(2026, 6, 30, 22, tzinfo=UTC))

    def test_mesi_espn(self):
        self.assertEqual(c.espn_months(self.LO, self.HI), ["202611"])
        self.assertEqual(c.espn_months(dt.datetime(2026, 11, 27, 23, tzinfo=UTC), dt.datetime(2026, 12, 5, 23, tzinfo=UTC)),
                         ["202611", "202612"])


if __name__ == "__main__":
    unittest.main()
