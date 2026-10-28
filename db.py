"""Database access layer.

Two drivers behind one interface, so the same queries run on either:

  * SQLite (local development) -- stdlib sqlite3, no install required.
  * PostgreSQL (production / Vercel) -- psycopg 3, selected by DATABASE_URL.

Queries are written once using SQLite's ``?`` placeholder and translated to
``%s`` for Postgres. Rows come back dict-like from both drivers, so ``row["x"]``
works unchanged in app.py.
"""

import os
import sqlite3
from pathlib import Path

from flask import g

BASE_DIR = Path(__file__).parent
SCHEMA_SQLITE = BASE_DIR / "schema.sql"
SCHEMA_POSTGRES = BASE_DIR / "schema.postgres.sql"

# Keep seed data out of the driver shim's way.
SEED_CATEGORIES = [
    ("seating", "Seating", "Sculptural chairs and sofas in linen and boucle.",
     "/static/images/collection-seating.png", 1),
    ("lighting", "Lighting", "Handcrafted lamps that cast a warm, gentle glow.",
     "/static/images/collection-lighting.png", 2),
    ("textiles", "Textiles", "Natural throws, cushions, and layered comfort.",
     "/static/images/collection-textiles.png", 3),
]

# (slug, name, category_slug, price_paise, description, image, stock, featured)
SEED_PRODUCTS = [
    ("terracotta-vessel", "Terracotta Vessel", "seating", 280000,
     "Hand-thrown in warm clay with a matte, unglazed finish. Each piece carries "
     "the slight irregularity of its maker. Holds a single branch as beautifully "
     "as it sits empty.",
     "/static/images/product-vase.png", 24, 1),
    ("arch-oak-mirror", "Arch Oak Mirror", "seating", 1250000,
     "A solid oak frame with a soft arch and a bevelled edge. Hangs from a "
     "recessed keyhole so the profile stays clean against the wall.",
     "/static/images/product-mirror.png", 11, 1),
    ("oatmeal-knit-throw", "Oatmeal Knit Throw", "textiles", 420000,
     "Loomed from undyed lambswool in a soft, open stitch. Light enough to drape "
     "over an arm, warm enough to keep.",
     "/static/images/product-throw.png", 42, 1),
    ("sculpted-oak-stool", "Sculpted Oak Stool", "seating", 980000,
     "Turned from a single length of oak and finished with hardwax oil. Works as "
     "a side table, a landing spot, or a spare seat at a long dinner.",
     "/static/images/product-stool.png", 8, 1),
    ("linen-slipcover-sofa", "Linen Slipcover Sofa", "seating", 6800000,
     "A deep, low frame in washed Belgian linen. The covers unzip for cleaning "
     "and re-tailor when the years start to show.",
     "/static/images/collection-seating.png", 6, 0),
    ("paper-lantern-pendant", "Paper Lantern Pendant", "lighting", 760000,
     "Mulberry paper stretched over a bamboo rib. Throws a warm, diffuse light "
     "that flatters a room without glare.",
     "/static/images/collection-lighting.png", 15, 0),
    ("brass-table-lamp", "Brass Table Lamp", "lighting", 1350000,
     "Unlacquered brass that will patina with age. A linen shade keeps the light "
     "low and warm.",
     "/static/images/collection-lighting.png", 9, 0),
    ("boucle-cushion-pair", "Boucle Cushion Pair", "textiles", 540000,
     "Chunky loops of wool boucle in an oat tone. Sold as a pair, sized for "
     "standard 18 inch inserts.",
     "/static/images/collection-textiles.png", 30, 0),
]


# --------------------------------------------------------------- driver

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
# Vercel injects POSTGRES_URL-style vars; treat any postgres URL as Postgres.
USING_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))


def _translate(sql: str) -> str:
    """Rewrite SQLite ``?`` placeholders to Postgres ``%s``.

    Safe here because no query embeds a literal ``?`` inside a string. The
    INSERT ... RETURNING clause supplies lastrowid, which psycopg does not
    expose the way sqlite3 does.
    """
    out = sql.replace("?", "%s")
    head = out.lstrip().upper()
    if head.startswith("INSERT") and "RETURNING" not in out.upper():
        out += " RETURNING id"
    return out


class _PostgresCursor:
    """Minimal cursor facade matching the sqlite3.Cursor attributes we use.

    ``lastrowid`` is resolved lazily: psycopg has no such attribute, so the id
    comes from the RETURNING clause. Callers read ``lastrowid`` straight after
    an insert without calling fetchone() first, so the row is pulled here and
    cached to keep that pattern working.
    """

    def __init__(self, cur, resolves_id: bool):
        self._cur = cur
        self._resolves_id = resolves_id
        self._cached = None
        self._resolved = False
        self._lastrowid = None

    def _pull(self):
        if not self._resolved:
            self._cached = self._cur.fetchone()
            self._resolved = True
            if self._cached is not None and hasattr(self._cached, "keys"):
                try:
                    self._lastrowid = self._cached["id"]
                except (KeyError, IndexError):
                    pass
        return self._cached

    def fetchall(self):
        if self._resolved:
            return [self._cached] if self._cached is not None else []
        self._resolved = True
        return self._cur.fetchall()

    def fetchone(self):
        return self._pull()

    @property
    def lastrowid(self):
        if self._resolves_id:
            self._pull()
        return self._lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount


class _PostgresConn:
    def __init__(self, dsn: str):
        import psycopg  # imported lazily so SQLite works without it installed
        from psycopg.rows import dict_row

        self._conn = psycopg.connect(dsn, row_factory=dict_row)

    def execute(self, sql: str, args: tuple = ()):
        translated = _translate(sql)
        resolves_id = " RETURNING id" in translated
        return _PostgresCursor(
            self._conn.execute(translated, args or None), resolves_id
        )

    def commit(self):
        self._conn.commit()

    def executemany(self, sql: str, seq_of_args):
        # psycopg's execute() rejects a list of param sets, so batch inserts
        # must go through executemany -- same as sqlite3.
        return self._conn.cursor().executemany(_translate(sql), seq_of_args)

    def close(self):
        self._conn.close()

    def cursor(self):
        return self

    def executescript(self, script: str):
        self._conn.execute(script, prepare=False)


def _sqlite_path() -> Path:
    """Where the SQLite file lives.

    Vercel mounts the project directory read-only, so opening a database there
    raises "unable to open database file". /tmp is writable but ephemeral and
    per-instance -- fine for a demo, not for real data.
    """
    override = os.environ.get("AURANEST_DB")
    if override:
        return Path(override)
    if os.environ.get("VERCEL"):
        return Path("/tmp/auranest.db")
    return BASE_DIR / "auranest.db"


DB_PATH = _sqlite_path()


# --------------------------------------------------------- flask hooks

def get_db():
    """One connection per request, stored on flask.g."""
    if "db" not in g:
        if USING_POSTGRES:
            g.db = _PostgresConn(DATABASE_URL)
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            g.db = conn
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def query(sql: str, args: tuple = ()) -> list:
    return get_db().execute(sql, args).fetchall()


def query_one(sql: str, args: tuple = ()):
    return get_db().execute(sql, args).fetchone()


def execute(sql: str, args: tuple = ()):
    conn = get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur


def unique_violation_error():
    """The driver's UNIQUE-constraint exception.

    app.py catches this for duplicate newsletter signups; the type differs
    between sqlite3 and psycopg, so callers use this instead of importing
    either driver directly. psycopg is only imported when Postgres is in use.
    """
    if USING_POSTGRES:
        import psycopg.errors
        return psycopg.errors.UniqueViolation
    return sqlite3.IntegrityError


# ------------------------------------------------------------- schema

def _apply_schema(conn) -> None:
    path = SCHEMA_POSTGRES if USING_POSTGRES else SCHEMA_SQLITE
    conn.executescript(path.read_text(encoding="utf-8"))


def _is_empty(conn) -> bool:
    return conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"] == 0


def init_db() -> None:
    """Create tables if missing, then seed when the catalog is empty.

    Runs on import, not just under __main__: on Vercel the module is imported
    as ``app``, so a __main__-only call would never create the tables.
    """
    if USING_POSTGRES:
        conn = _PostgresConn(DATABASE_URL)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        _apply_schema(conn)
        conn.commit()
        if _is_empty(conn):
            seed(conn)
    finally:
        conn.close()


def seed(conn=None) -> None:
    """Insert starter catalog data. Only runs when the products table is empty."""
    owns = conn is None
    if owns:
        conn = _PostgresConn(DATABASE_URL) if USING_POSTGRES else sqlite3.connect(DB_PATH)
        if not USING_POSTGRES:
            conn.row_factory = sqlite3.Row
    try:
        _apply_schema(conn)

        conn.executemany(
            "INSERT INTO categories (slug, name, tagline, image, sort_order) "
            "VALUES (?,?,?,?,?)",
            SEED_CATEGORIES,
        )

        rows = []
        for slug, name, cat_slug, price, desc, image, stock, featured in SEED_PRODUCTS:
            cat = conn.execute(
                "SELECT id FROM categories WHERE slug = ?", (cat_slug,)
            ).fetchone()
            rows.append((slug, name, cat["id"], price, desc, image, stock, featured))

        conn.executemany(
            "INSERT INTO products "
            "(slug, name, category_id, price_paise, description, image, stock, featured) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        if owns:
            conn.close()