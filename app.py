"""AuraNest — Flask app serving HTML pages and a JSON API backed by SQLite."""

import os
import re
import secrets
from datetime import date

from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_QTY_PER_LINE = 10

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
# Pick up template/CSS edits without needing the (double-process) reloader.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.teardown_appcontext(db.close_db)

# Signs the session cookie that stores the cart id. Override in production.
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-not-for-production")


@app.context_processor
def inject_globals():
    return {"year": date.today().year, "cart_count": cart_count()}


# ---------------------------------------------------------------- money

def inr(paise: int) -> str:
    """Format paise as Indian Rupees using lakh/crore digit grouping."""
    negative = paise < 0
    rupees, remainder = divmod(abs(int(paise)), 100)

    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail

    out = f"₹{digits}"
    if remainder:
        out += f".{remainder:02d}"
    return f"-{out}" if negative else out


@app.template_filter("inr")
def inr_filter(paise):
    return inr(paise)


# ---------------------------------------------------------------- cart

def cart_id() -> str:
    """Stable id for this visitor's cart, stored in a signed cookie."""
    cid = session.get("cart_id")
    if not cid:
        cid = secrets.token_hex(16)
        session["cart_id"] = cid
        session.permanent = True
    return cid


def cart_count() -> int:
    """Total units in the cart. Safe to call outside a request (returns 0)."""
    if not session.get("cart_id"):
        return 0
    row = db.query_one(
        "SELECT COALESCE(SUM(quantity), 0) AS n FROM cart_items WHERE cart_id = ?",
        (session["cart_id"],),
    )
    return int(row["n"]) if row else 0


CART_SELECT = """
    SELECT ci.id AS line_id, ci.quantity,
           p.id, p.slug, p.name, p.price_paise, p.image, p.stock
    FROM cart_items ci
    JOIN products p ON p.id = ci.product_id
    WHERE ci.cart_id = ?
"""

CART_TOTALS = """
    SELECT COALESCE(SUM(ci.quantity * p.price_paise), 0) AS subtotal,
           COALESCE(SUM(ci.quantity), 0) AS units
    FROM cart_items ci
    JOIN products p ON p.id = ci.product_id
    WHERE ci.cart_id = ?
"""

FREE_SHIPPING_THRESHOLD_PAISES = 50_000_00  # ₹50,000
SHIPPING_FLAT_PAISES = 1_500_00              # ₹1,500


def cart_payload() -> dict:
    lines = db.query(CART_SELECT, (cart_id(),))
    totals = db.query_one(CART_TOTALS, (cart_id(),))
    subtotal = int(totals["subtotal"]) if totals else 0
    units = int(totals["units"]) if totals else 0
    shipping = 0 if subtotal == 0 or subtotal >= FREE_SHIPPING_THRESHOLD_PAISES else SHIPPING_FLAT_PAISES
    return {
        "items": [
            {
                "line_id": r["line_id"],
                "slug": r["slug"],
                "name": r["name"],
                "image": r["image"],
                "price_paise": r["price_paise"],
                "price": inr(r["price_paise"]),
                "quantity": r["quantity"],
                "line_total_paise": r["quantity"] * r["price_paise"],
                "line_total": inr(r["quantity"] * r["price_paise"]),
                "stock": r["stock"],
            }
            for r in lines
        ],
        "units": units,
        "subtotal_paise": subtotal,
        "subtotal": inr(subtotal),
        "shipping_paise": shipping,
        "shipping": inr(shipping),
        "total_paise": subtotal + shipping,
        "total": inr(subtotal + shipping),
        "free_shipping_threshold_paise": FREE_SHIPPING_THRESHOLD_PAISES,
        "free_shipping_threshold": inr(FREE_SHIPPING_THRESHOLD_PAISES),
        "amount_to_free_shipping_paise": max(0, FREE_SHIPPING_THRESHOLD_PAISES - subtotal),
        "amount_to_free_shipping": inr(max(0, FREE_SHIPPING_THRESHOLD_PAISES - subtotal)),
    }


def product_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "category": row["category_slug"],
        "category_name": row["category_name"],
        "price_paise": row["price_paise"],
        "price": inr(row["price_paise"]),
        "description": row["description"],
        "image": row["image"],
        "stock": row["stock"],
        "in_stock": row["stock"] > 0,
        "featured": bool(row["featured"]),
    }


PRODUCT_SELECT = """
    SELECT p.*, c.slug AS category_slug, c.name AS category_name
    FROM products p
    LEFT JOIN categories c ON c.id = p.category_id
"""


# ---------------------------------------------------------------- pages

@app.get("/")
def index():
    featured = db.query(PRODUCT_SELECT + " WHERE p.featured = 1 ORDER BY p.id LIMIT 4")
    categories = db.query(
        "SELECT c.*, (SELECT COUNT(*) FROM products p WHERE p.category_id = c.id) "
        "AS item_count FROM categories c ORDER BY c.sort_order"
    )
    return render_template(
        "index.html",
        products=[product_row_to_dict(r) for r in featured],
        collections=categories,
        page="home",
    )


@app.get("/shop")
def shop():
    category = request.args.get("category", "").strip()
    sql = PRODUCT_SELECT
    args: tuple = ()
    if category:
        sql += " WHERE c.slug = ?"
        args = (category,)
    sql += " ORDER BY p.featured DESC, p.price_paise ASC"

    products = [product_row_to_dict(r) for r in db.query(sql, args)]
    categories = db.query("SELECT slug, name FROM categories ORDER BY sort_order")
    return render_template(
        "shop.html",
        products=products,
        collections=categories,
        active_category=category or "all",
        page="shop",
    )


@app.get("/product/<slug>")
def product_detail(slug):
    row = db.query_one(PRODUCT_SELECT + " WHERE p.slug = ?", (slug,))
    if row is None:
        abort(404)
    related = db.query(
        PRODUCT_SELECT
        + " WHERE p.category_id = ? AND p.slug != ? ORDER BY RANDOM() LIMIT 3",
        (row["category_id"], slug),
    )
    return render_template(
        "product.html",
        product=product_row_to_dict(row),
        related=[product_row_to_dict(r) for r in related],
        page="shop",
    )


@app.get("/cart")
def cart_page():
    return render_template("cart.html", cart=cart_payload(), page="cart")


@app.post("/cart/add/<slug>")
def cart_add_page(slug):
    row = db.query_one("SELECT id, stock FROM products WHERE slug = ?", (slug,))
    if row is None:
        abort(404)

    raw = request.form.get("quantity")
    try:
        qty = max(1, int(raw)) if raw not in (None, "") else 1
    except (TypeError, ValueError):
        qty = 1

    add_to_cart(row["id"], qty)
    return redirect(url_for("cart_page"))


def add_to_cart(product_id: int, qty: int) -> tuple[int, str | None]:
    """Add qty units, clamped to available stock. Returns (units_added, error)."""
    row = db.query_one("SELECT stock FROM products WHERE id = ?", (product_id,))
    if row is None:
        return 0, "Product not found."
    stock = int(row["stock"])
    if stock <= 0:
        return 0, "This piece is currently unavailable."

    current = db.query_one(
        "SELECT quantity FROM cart_items WHERE cart_id = ? AND product_id = ?",
        (cart_id(), product_id),
    )
    existing = int(current["quantity"]) if current else 0
    target = min(existing + qty, stock, MAX_QTY_PER_LINE)

    if target <= existing:
        return 0, f"Only {stock} in stock."

    db.execute(
        "INSERT INTO cart_items (cart_id, product_id, quantity) VALUES (?,?,?) "
        "ON CONFLICT (cart_id, product_id) DO UPDATE SET quantity = excluded.quantity",
        (cart_id(), product_id, target),
    )
    return target - existing, None


@app.get("/book")
def book():
    return render_template("book.html", page="book")


@app.post("/book")
def book_submit():
    form = request.form
    name = form.get("name", "").strip()
    email = form.get("email", "").strip()
    preferred_date = form.get("preferred_date", "").strip()

    if not name or not EMAIL_RE.match(email):
        return render_template(
            "book.html",
            page="book",
            error="Please provide your name and a valid email address.",
            form=form,
        ), 400

    if preferred_date and not ISO_DATE_RE.match(preferred_date):
        return render_template(
            "book.html",
            page="book",
            error="Please choose a valid date.",
            form=form,
        ), 400

    db.execute(
        "INSERT INTO bookings (name, email, phone, service, preferred_date, message) "
        "VALUES (?,?,?,?,?,?)",
        (
            name,
            email,
            form.get("phone", "").strip(),
            form.get("service", "Full-home styling").strip() or "Full-home styling",
            preferred_date or None,
            form.get("message", "").strip(),
        ),
    )
    return redirect(url_for("book_confirmation"))


@app.get("/book/thanks")
def book_confirmation():
    return render_template("book-thanks.html", page="book")


@app.get("/contact")
def contact():
    return redirect(url_for("book"))


# ---------------------------------------------------------------- JSON API

@app.get("/api/products")
def api_products():
    category = request.args.get("category", "").strip()
    sql = PRODUCT_SELECT
    args: tuple = ()
    if category:
        sql += " WHERE c.slug = ?"
        args = (category,)
    sql += " ORDER BY p.featured DESC, p.price_paise ASC"
    return jsonify({"products": [product_row_to_dict(r) for r in db.query(sql, args)]})


@app.get("/api/products/<slug>")
def api_product(slug):
    row = db.query_one(PRODUCT_SELECT + " WHERE p.slug = ?", (slug,))
    if row is None:
        return jsonify({"error": "Product not found"}), 404
    return jsonify({"product": product_row_to_dict(row)})


@app.get("/api/categories")
def api_categories():
    rows = db.query(
        "SELECT c.slug, c.name, c.tagline, c.image, "
        "(SELECT COUNT(*) FROM products p WHERE p.category_id = c.id) AS item_count "
        "FROM categories c ORDER BY c.sort_order"
    )
    return jsonify({"categories": [dict(r) for r in rows]})


@app.get("/api/cart")
def api_cart_get():
    return jsonify({"cart": cart_payload()})


@app.post("/api/cart")
def api_cart_add():
    payload = request.get_json(silent=True) or request.form
    slug = (payload.get("slug") or "").strip()

    # Note: `payload.get("quantity") or 1` would silently turn 0 into 1.
    raw = payload.get("quantity")
    raw = 1 if raw is None or raw == "" else raw
    try:
        qty = int(raw)
    except (TypeError, ValueError):
        return jsonify({"error": "Quantity must be a whole number."}), 400
    if qty < 1:
        return jsonify({"error": "Quantity must be at least 1."}), 400

    row = db.query_one("SELECT id FROM products WHERE slug = ?", (slug,))
    if row is None:
        return jsonify({"error": "Product not found."}), 404

    added, error = add_to_cart(row["id"], qty)
    if error:
        return jsonify({"error": error, "cart": cart_payload()}), 409

    return jsonify({"added": added, "cart": cart_payload()}), 201


@app.patch("/api/cart/<int:line_id>")
def api_cart_update(line_id):
    payload = request.get_json(silent=True) or request.form
    try:
        qty = int(payload.get("quantity"))
    except (TypeError, ValueError):
        return jsonify({"error": "Quantity must be a whole number."}), 400

    line = db.query_one(
        """SELECT ci.product_id, p.stock FROM cart_items ci
           JOIN products p ON p.id = ci.product_id
           WHERE ci.id = ? AND ci.cart_id = ?""",
        (line_id, cart_id()),
    )
    if line is None:
        return jsonify({"error": "Cart item not found."}), 404

    if qty <= 0:
        db.execute("DELETE FROM cart_items WHERE id = ? AND cart_id = ?", (line_id, cart_id()))
    else:
        clamped = min(qty, int(line["stock"]), MAX_QTY_PER_LINE)
        db.execute(
            "UPDATE cart_items SET quantity = ? WHERE id = ? AND cart_id = ?",
            (clamped, line_id, cart_id()),
        )

    return jsonify({"cart": cart_payload()})


@app.delete("/api/cart/<int:line_id>")
def api_cart_remove(line_id):
    cur = db.execute(
        "DELETE FROM cart_items WHERE id = ? AND cart_id = ?", (line_id, cart_id())
    )
    if cur.rowcount == 0:
        return jsonify({"error": "Cart item not found."}), 404
    return jsonify({"cart": cart_payload()})


@app.delete("/api/cart")
def api_cart_clear():
    db.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart_id(),))
    return jsonify({"cart": cart_payload()})


@app.post("/api/subscribe")
def api_subscribe():
    payload = request.get_json(silent=True) or request.form
    email = (payload.get("email") or "").strip().lower()

    if not EMAIL_RE.match(email):
        return jsonify({"error": "Please enter a valid email address."}), 400

    try:
        db.execute("INSERT INTO subscribers (email) VALUES (?)", (email,))
    except db.unique_violation_error():
        return jsonify({"message": "You are already on the list."}), 200

    return jsonify({"message": "Thank you — welcome to the circle."}), 201


@app.post("/api/bookings")
def api_booking():
    payload = request.get_json(silent=True) or request.form
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip()
    preferred_date = (payload.get("preferred_date") or "").strip()

    if not name or not EMAIL_RE.match(email):
        return jsonify({"error": "Name and a valid email are required."}), 400

    if preferred_date:
        if not ISO_DATE_RE.match(preferred_date):
            return jsonify({"error": "Please provide a valid date (YYYY-MM-DD)."}), 400
        try:
            if date.fromisoformat(preferred_date) < date.today():
                return jsonify({"error": "Please choose a future date."}), 400
        except ValueError:
            return jsonify({"error": "Please provide a valid date."}), 400

    cur = db.execute(
        "INSERT INTO bookings (name, email, phone, service, preferred_date, message) "
        "VALUES (?,?,?,?,?,?)",
        (
            name,
            email,
            (payload.get("phone") or "").strip(),
            (payload.get("service") or "Full-home styling").strip(),
            preferred_date or None,
            (payload.get("message") or "").strip(),
        ),
    )
    return jsonify({"message": "Consultation requested.", "id": cur.lastrowid}), 201


@app.get("/api/stats")
def api_stats():
    row = db.query_one(
        "SELECT (SELECT COUNT(*) FROM products) AS products, "
        "(SELECT COUNT(*) FROM categories) AS categories, "
        "(SELECT COUNT(*) FROM subscribers) AS subscribers, "
        "(SELECT COUNT(*) FROM bookings) AS bookings, "
        "(SELECT COUNT(*) FROM cart_items) AS cart_lines"
    )
    return jsonify(dict(row))


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("404.html", page=""), 404


# Runs on import, not just under __main__: on Vercel the module is imported as
# `app`, so a __main__-only call would never create the tables. init_db() is
# idempotent (CREATE TABLE IF NOT EXISTS) and seeds only when products is empty.
db.init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    # Reloader off by default: it double-processes and can serve stale routes.
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="127.0.0.1", port=port, debug=debug, use_reloader=debug)