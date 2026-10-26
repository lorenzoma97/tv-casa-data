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


if __name__ == "__main__":
    unittest.main()
