import sqlite3

import store


def make_db(path):
    conn = sqlite3.connect(path)
    store.init_db(conn)
    conn.executemany("INSERT INTO probs VALUES (?,?,?,?,?,?,?,?)", [
        ("2026-09-28", "t1", "2026-10-01", "VAN", "EDM", 0.3, 0.7, "moneypuck"),
        ("2026-09-28", "t1", "2026-10-01", "VAN", "EDM", 0.28, 0.72, "consensus"),
        ("2026-09-29", "t2", "2026-10-01", "VAN", "EDM", 0.25, 0.75, "moneypuck"),
    ])
    conn.execute("INSERT INTO odds VALUES ('2026-09-29','t2','2026-10-01','VAN','EDM',"
                 "'pinnacle',3.9,1.3,0.25,0.75)")
    conn.execute("INSERT INTO schedule VALUES (2026020010,'2026-10-01','x','VAN','EDM',2,4,'OT')")
    conn.execute("INSERT INTO schedule (game_id, game_date, start_utc, away, home) "
                 "VALUES (2026020011,'2026-10-02','x','EDM','CGY')")
    conn.commit()
    return conn


def dump(conn):
    return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall())
            for t in ("probs", "odds", "schedule")}


def test_export_then_build_roundtrip(tmp_path):
    conn = make_db(tmp_path / "a.db")
    data = tmp_path / "data"
    files = store.export_all(conn, data)
    assert {p.name for p in files} == {"2026-09-28.csv.gz", "2026-09-29.csv.gz", "schedule.csv"}

    store.build_db(tmp_path / "b.db", data)
    rebuilt = sqlite3.connect(tmp_path / "b.db")
    assert dump(rebuilt) == dump(conn)   # types compris (REAL, INTEGER)


def test_export_is_stable(tmp_path):
    conn = make_db(tmp_path / "a.db")
    data = tmp_path / "data"
    store.export_all(conn, data)
    assert store.export_all(conn, data) == []   # rien ne change → aucun fichier réécrit
    conn.execute("UPDATE probs SET p_away=0.26 WHERE snapshot='2026-09-29'")
    changed = store.export_snapshot(conn, "2026-09-29", data)
    assert [p.name for p in changed] == ["2026-09-29.csv.gz"]


# ── Blessures : situation actuelle, réécrite à chaque collecte ─────────────

def test_les_blessures_font_laller_retour_par_le_fichier(tmp_path):
    import collect_injuries as inj
    conn = sqlite3.connect(tmp_path / "a.db")
    store.init_db(conn)
    conn.executemany("INSERT INTO injuries VALUES (?,?,?,?,?)", [
        ("COL", "Trent Miner", "G", "IR", "Undisclosed"),
        ("NJD", "Connor Brown", "RW", "Out", "Lower Body"),
    ])
    conn.commit()
    data = tmp_path / "data"
    assert store.export_injuries(conn, data)
    assert (data / "injuries.csv").exists()

    neuve = tmp_path / "b.db"
    store.build_db(neuve, data)
    lignes = sqlite3.connect(neuve).execute(
        f"SELECT {','.join(inj.COLS)} FROM injuries ORDER BY team").fetchall()
    assert lignes == [("COL", "Trent Miner", "G", "IR", "Undisclosed"),
                      ("NJD", "Connor Brown", "RW", "Out", "Lower Body")]


def test_un_second_export_identique_ne_change_rien(tmp_path):
    """Sinon chaque collecte commiterait un fichier identique."""
    conn = sqlite3.connect(tmp_path / "a.db")
    store.init_db(conn)
    conn.execute("INSERT INTO injuries VALUES ('COL','X','C','IR','Genou')")
    conn.commit()
    assert store.export_injuries(conn, tmp_path / "data")
    assert store.export_injuries(conn, tmp_path / "data") == []


def test_une_table_vide_nefface_pas_le_fichier(tmp_path):
    """Une collecte qui échoue ne doit pas effacer les blessés connus."""
    conn = sqlite3.connect(tmp_path / "a.db")
    store.init_db(conn)
    data = tmp_path / "data"
    data.mkdir()
    (data / "injuries.csv").write_text("team,player,pos,status,injury\nCOL,X,C,IR,Genou\n")
    assert store.export_injuries(conn, data) == []
    assert "COL,X" in (data / "injuries.csv").read_text()


def test_les_gardiens_font_laller_retour_par_le_fichier(tmp_path):
    conn = sqlite3.connect(tmp_path / "a.db")
    store.init_db(conn)
    ligne = ("2026-10-10", "VAN", "NJD", "NJD", "Jake Allen", "Expected", "Oct 6, 4:23 AM")
    vide = ("2026-10-10", "VAN", "NJD", "VAN", "", "", "Oct 6, 4:23 AM")
    conn.executemany("INSERT INTO goalies VALUES (?,?,?,?,?,?,?)", [ligne, vide])
    conn.commit()
    data = tmp_path / "data"
    assert store.export_goalies(conn, data)

    neuve = tmp_path / "b.db"
    store.build_db(neuve, data)
    lignes = sorted(sqlite3.connect(neuve).execute("SELECT * FROM goalies").fetchall())
    assert lignes == sorted([ligne, vide])     # le « pas encore annoncé » survit aussi
