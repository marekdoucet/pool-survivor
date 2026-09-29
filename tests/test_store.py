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
    conn.execute("INSERT INTO schedule VALUES (2026020010,'2026-10-01','x','VAN','EDM')")
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
