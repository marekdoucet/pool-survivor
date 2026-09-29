"""
Stockage versionné dans Git.

survivor.db (SQLite) n'est qu'un cache local, jamais commité. La source de vérité
est le dossier data/ :
    data/probs/AAAA-MM-JJ.csv.gz   une collecte (toutes les sources)
    data/odds/AAAA-MM-JJ.csv.gz    cotes brutes des casinos de cette collecte
    data/schedule.csv              calendrier de la saison

Un fichier par collecte, écrit une fois : le dépôt grossit d'environ 30 Ko par
jour au lieu d'une copie complète de la base à chaque commit.

    python store.py export   # base → data/ (toutes les collectes)
    python store.py build    # data/ → base (reconstruit survivor.db)
"""

import csv
import gzip
import io
import sys
import sqlite3
from pathlib import Path

import collect_moneypuck as cm
import collect_odds as co
import schedule as sch

DATA_DIR = Path("data")
TABLES = {  # table → colonnes, dans l'ordre du CREATE TABLE
    "probs": ["snapshot", "collected_at", "game_date", "away", "home",
              "p_away", "p_home", "source"],
    "odds": ["snapshot", "collected_at", "game_date", "away", "home", "book",
             "price_away", "price_home", "p_away", "p_home"],
}


def init_db(conn):
    co.init_db(conn)    # crée aussi probs
    sch.init_db(conn)


def _write_if_changed(path, data):
    """N'écrit que si le contenu change (évite des commits vides)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def _csv_bytes(cols, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def export_snapshot(conn, snapshot, data_dir=DATA_DIR):
    """Écrit les fichiers de la collecte `snapshot`. Retourne les fichiers modifiés."""
    changed = []
    for table, cols in TABLES.items():
        rows = conn.execute(
            f"SELECT {','.join(cols)} FROM {table} WHERE snapshot=? "
            f"ORDER BY {','.join(cols)}", (snapshot,)).fetchall()
        if not rows:
            continue
        path = Path(data_dir) / table / f"{snapshot}.csv.gz"
        # mtime=0 : même contenu → mêmes octets, donc pas de faux changement
        data = gzip.compress(_csv_bytes(cols, rows), mtime=0)
        if _write_if_changed(path, data):
            changed.append(path)
    return changed


def export_schedule(conn, data_dir=DATA_DIR):
    rows = conn.execute(
        f"SELECT {','.join(sch.COLS)} FROM schedule ORDER BY game_date, game_id").fetchall()
    rows = [["" if v is None else v for v in row] for row in rows]
    path = Path(data_dir) / "schedule.csv"
    return [path] if rows and _write_if_changed(path, _csv_bytes(sch.COLS, rows)) else []


def export_all(conn, data_dir=DATA_DIR):
    snaps = [r[0] for r in conn.execute("SELECT DISTINCT snapshot FROM probs")]
    changed = [p for s in snaps for p in export_snapshot(conn, s, data_dir)]
    return changed + export_schedule(conn, data_dir)


def _read_csv(path):
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    reader = csv.reader(io.StringIO(raw.decode("utf-8")))
    return next(reader), list(reader)


def build_db(db_path=cm.DB_PATH, data_dir=DATA_DIR):
    """Reconstruit la base à partir de data/ (remplace le fichier existant)."""
    data_dir = Path(data_dir)
    tmp = Path(f"{db_path}.tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    init_db(conn)
    n = 0
    with conn:
        for table in TABLES:
            for path in sorted((data_dir / table).glob("*.csv.gz")):
                cols, rows = _read_csv(path)
                conn.executemany(
                    f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
                    f"VALUES ({','.join('?' * len(cols))})", rows)
                n += len(rows)
        if (data_dir / "schedule.csv").exists():
            cols, rows = _read_csv(data_dir / "schedule.csv")
            rows = [[None if v == "" else v for v in row] for row in rows]   # match non joué
            conn.executemany(f"INSERT INTO schedule ({','.join(cols)}) "
                             f"VALUES ({','.join('?' * len(cols))})", rows)
    conn.close()
    tmp.replace(db_path)
    return n


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "export":
        conn = sqlite3.connect(cm.DB_PATH)
        files = export_all(conn)
        print(f"{len(files)} fichier(s) écrit(s) dans {DATA_DIR}/")
    elif cmd == "build":
        print(f"{build_db()} lignes chargées dans {cm.DB_PATH}")
    else:
        sys.exit(__doc__)
