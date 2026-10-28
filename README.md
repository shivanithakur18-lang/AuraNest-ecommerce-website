# AuraNest

Furniture, lighting and interior styling shop. Prices in Indian Rupees (₹).

**Stack:** plain HTML + CSS + vanilla JS on the front end, Python (Flask) on the
back end, SQLite for storage. No front-end framework, no ORM, no build step.

```
app.py            Flask routes: HTML pages + JSON API
db.py             SQLite connection helper and seed data
schema.sql        SQLite table definitions (pure SQL)
schema.postgres.sql  PostgreSQL equivalent
api/index.py      Vercel serverless entry point
templates/        Jinja HTML templates
static/css/       Stylesheet (design tokens + components)
static/js/        Mobile nav, newsletter form, cart interactions
static/images/    Site imagery
vercel.json       Vercel build + routing config
auranest.db       SQLite database (created on first run)
```

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>. The database is created and seeded automatically on
first run.

| Variable       | Default | Purpose                               |
| -------------- | ------- | ------------------------------------- |
| `PORT`         | `5000`  | Port to listen on                     |
| `FLASK_DEBUG`  | off     | `1` enables the debugger and reloader |
| `SECRET_KEY`   | dev key | **Set this in production**            |
| `AURANEST_DB`  | `./auranest.db` | SQLite file location        |
| `DATABASE_URL` | unset   | Set to a `postgres://` URL to use Postgres instead of SQLite |

To start clean, delete `auranest.db` and run again.

## Pages

| Route              | Description                                        |
| ------------------ | -------------------------------------------------- |
| `/`                | Home — hero, collections, styling, featured pieces  |
| `/shop`            | Catalog, filterable by category                     |
| `/shop?category=x` | Catalog filtered by category slug                   |
| `/product/<slug>`  | Product detail with related items                   |
| `/cart`            | Cart — quantities, removal, totals                  |
| `/book`            | Consultation booking form                          |
| `/book/thanks`     | Booking confirmation                               |

## JSON API

| Method | Route                  | Description                              |
| ------ | ---------------------- | ---------------------------------------- |
| GET    | `/api/products`        | All products (`?category=` to filter)    |
| GET    | `/api/products/<slug>` | Single product                           |
| GET    | `/api/categories`      | Categories with product counts           |
| GET    | `/api/cart`            | Current cart with totals                 |
| POST   | `/api/cart`            | `{slug, quantity}` — add a line          |
| PATCH  | `/api/cart/<line_id>`  | `{quantity}` — set quantity (0 removes)  |
| DELETE | `/api/cart/<line_id>`  | Remove one line                          |
| DELETE | `/api/cart`            | Empty the cart                           |
| POST   | `/api/subscribe`       | `{email}` — inserts into `subscribers`   |
| POST   | `/api/bookings`        | Consultation request → `bookings`        |
| GET    | `/api/stats`           | Row counts per table                     |
| GET    | `/healthz`             | Liveness check                           |

## Schema

- `categories` — slug, name, tagline, image, sort order
- `products` — slug, name, category, price (integer **paise**), description,
  image, stock, featured
- `cart_items` — cart_id, product_id, quantity (unique per cart + product)
- `subscribers` — email (unique), created_at
- `bookings` — name, email, phone, service, preferred_date, message, created_at

Money is stored as integer paise and rendered with Indian lakh/crore digit
grouping (`₹12,34,567`). Cart totals, shipping, and the free-shipping threshold
(₹50,000) are all computed server-side.

The cart is tracked by a signed session cookie holding a random cart id; the
lines live in `cart_items`. Quantities are clamped to available stock and to 10
per line.

## Deployment

### GitHub

```bash
git init
git add .
git commit -m "AuraNest: Flask + SQLite storefront with cart and INR pricing"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

### Vercel

```bash
npm i -g vercel
vercel          # preview
vercel --prod   # production
```

Or import the GitHub repo at [vercel.com/new](https://vercel.com/new) — Vercel
detects the `@vercel/python` build from `vercel.json`.

**Read this before deploying.** Two things will not work as-is on Vercel:

1. **SQLite does not persist.** Vercel mounts the project directory read-only,
   so the database is redirected to `/tmp/auranest.db` — writable, but ephemeral
   and per-instance. The schema is recreated on every cold start, so pages render
   correctly, but carts, subscribers, and bookings are silently discarded between
   invocations. Treat a Vercel deploy as a demo, not production. Switch to a
   hosted Postgres before going live — see the notes below.
2. **Set `SECRET_KEY`.** Without it every deploy uses the hard-coded dev
   fallback, which invalidates carts and, if leaked, allows session forgery.

Postgres support is built in — set `DATABASE_URL` and the app switches drivers
automatically, which resolves the first issue. See the Postgres section below.

### PostgreSQL (recommended for production)

Postgres support is already wired up. Set `DATABASE_URL` and the app switches
drivers on its own — no code changes:

```bash
DATABASE_URL=postgresql://user:pass@host/dbname python app.py
```

On Vercel, add `DATABASE_URL` as an environment variable (Neon, Supabase, and
Vercel Postgres all provide one).

How it works:

- `db.py` selects the driver from `DATABASE_URL` and exposes the same
  `query` / `query_one` / `execute` interface either way.
- Queries are written once with SQLite's `?` placeholders and translated to
  `%s` for Postgres, so `app.py` stays driver-agnostic.
- Inserts append `RETURNING id` on Postgres to supply `lastrowid`, which psycopg
  does not expose.
- Duplicate-signup detection uses `db.unique_violation_error()` instead of
  `sqlite3.IntegrityError`.
- The schema lives in two files: `schema.sql` (SQLite) and
  `schema.postgres.sql` (Postgres). They are otherwise identical — same tables,
  same columns, same constraints.
- The table is created and seeded automatically on first connect, so an empty
  database needs no migration step.

Without `DATABASE_URL` the app stays on SQLite, which is what you want for
local work.

Verified: all 21 SQL statements in `app.py` parse as valid PostgreSQL, and
`schema.postgres.sql` parses into 5 tables plus 5 indexes. Both paths run the
same regression suite.

## Notes

- `python app.py` runs the Flask development server. Use gunicorn or waitress
  for anything beyond local development.
- The dev server deliberately runs without the auto-reloader, which in debug
  mode spawns a second process and can serve stale routes. Template auto-reload
  stays enabled so edits show up immediately.
- Checkout is not implemented. "Proceed to checkout" routes to the consultation
  form, which is where a real order flow would begin.