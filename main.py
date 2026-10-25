import os
import uuid
import secrets
import hashlib
import hmac
import time
import smtplib
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from io import BytesIO

from PIL import Image, UnidentifiedImageError
from flask import (
    Flask, render_template, request, redirect, url_for, session,
    jsonify, flash, current_app
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from flask_socketio import SocketIO, join_room, emit

from config import BASE_DIR
from db import get_db_connection

# ==================================================
# 1. APP CONFIGURATION
# ==================================================

load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
# Keep Socket.IO on HTTP long-polling for now. This avoids failed WebSocket
# upgrade attempts in environments where the WebSocket transport is unavailable.
# Re-enable upgrades after installing/configuring simple-websocket and testing.
socketio = SocketIO(app, async_mode="threading", allow_upgrades=False)

if not app.config["SECRET_KEY"]:
    raise RuntimeError("SECRET_KEY missing from .env")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# Allow multipart overhead; each image is limited to 5 MB.
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024

ALLOWED_IMAGE_FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}
ALLOWED_QUANTITY_UNITS = {"plates", "kg", "packets", "litres"}
MAX_QUANTITY = Decimal("1000000")
MAX_PRICE_PER_UNIT = Decimal("1000000")

# Fixed development admin credentials (change before production deployment).
ADMIN_EMAIL = "admin@foodshare.com"
ADMIN_PASSWORD = "foodadmin123"
EXPIRY_ALERT_HOURS = int(os.getenv("EXPIRY_ALERT_HOURS", "24"))
EXPIRY_CHECK_INTERVAL_SECONDS = int(os.getenv("EXPIRY_CHECK_INTERVAL_SECONDS", "900"))


# ==================================================
# NOTIFICATION HELPERS
# ==================================================

def create_notification(user_id, title, message, notification_type, related_url=None):
    """Persist a notification and push it to the user's live Socket.IO room."""
    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO notifications
                    (user_id, title, message, notification_type, related_url)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, title[:150], message[:500], notification_type[:50], related_url),
            )
            notification_id = cursor.lastrowid
        connection.commit()
        payload = {
            "notification_id": notification_id,
            "user_id": user_id,
            "title": title[:150],
            "message": message[:500],
            "notification_type": notification_type[:50],
            "related_url": related_url,
            "is_read": False,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        socketio.emit("new_notification", payload, to=f"user_{user_id}")
        return True
    except Exception:
        if connection:
            connection.rollback()
        app.logger.exception("Creating notification failed for user_id=%s", user_id)
        return False
    finally:
        if connection:
            connection.close()


def send_expiry_email(recipient_email, recipient_name, food_name, expiry_time):
    """Send an expiry reminder if SMTP settings are configured; otherwise return False."""
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM_EMAIL") or username
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
    if not all((host, username, password, sender, recipient_email)):
        app.logger.warning("Expiry email skipped: SMTP settings are incomplete.")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"FoodShare expiry reminder: {food_name}"
    msg["From"] = sender
    msg["To"] = recipient_email
    msg.set_content(
        f"Hello {recipient_name},\n\n"
        f'Your FoodShare listing "{food_name}" is due to expire at '
        f"{expiry_time}.\nPlease review the listing and update its availability if needed.\n\n"
        "FoodShare"
    )
    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(msg)
        return True
    except Exception:
        app.logger.exception("Sending expiry email failed to %s", recipient_email)
        return False


def check_food_expiry_alerts():
    """Create one in-app/email alert per listing before it expires."""
    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT fl.food_id, fl.food_name, fl.expiry_time, fl.donor_id,
                       u.full_name, u.email
                FROM food_listings fl
                JOIN users u ON u.user_id = fl.donor_id
                LEFT JOIN food_expiry_alerts ea
                  ON ea.food_id = fl.food_id AND ea.alert_type = %s
                WHERE fl.status IN ('available', 'reserved')
                  AND fl.expiry_time > NOW()
                  AND fl.expiry_time <= DATE_ADD(NOW(), INTERVAL %s HOUR)
                  AND ea.alert_id IS NULL
                """,
                (f"{EXPIRY_ALERT_HOURS}_hours", EXPIRY_ALERT_HOURS),
            )
            due_listings = cursor.fetchall()

        for food_id, food_name, expiry_time, donor_id, donor_name, donor_email in due_listings:
            # Claim the unique alert row first so repeated checks do not resend it.
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT IGNORE INTO food_expiry_alerts (food_id, alert_type)
                    VALUES (%s, %s)
                    """,
                    (food_id, f"{EXPIRY_ALERT_HOURS}_hours"),
                )
                claimed = cursor.rowcount == 1
            connection.commit()
            if not claimed:
                continue

            expiry_label = expiry_time.strftime("%d %b %Y, %I:%M %p") if expiry_time else "soon"
            create_notification(
                donor_id,
                "Food expiry reminder",
                f'Your listing "{food_name}" expires at {expiry_label}.',
                "food_expiry",
                "/my-donations",
            )
            send_expiry_email(donor_email, donor_name, food_name, expiry_label)

    except Exception:
        if connection:
            connection.rollback()
        app.logger.exception("Checking food expiry alerts failed")
    finally:
        if connection:
            connection.close()


def expiry_alert_worker():
    """Background worker launched by Socket.IO when the app starts."""
    while True:
        try:
            with app.app_context():
                check_food_expiry_alerts()
        except Exception:
            app.logger.exception("Expiry alert worker iteration failed")
        socketio.sleep(max(60, EXPIRY_CHECK_INTERVAL_SECONDS))


# ==================================================
# 2. HELPER FUNCTIONS
# ==================================================

def login_required():
    return bool(session.get("user_id"))


def donor_required():
    return login_required() and session.get("role") == "donor"


def update_expired_food():
    """Mark available/reserved listings expired after their expiry time."""
    connection = None

    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE food_listings
                SET status = 'expired'
                WHERE status IN ('available', 'reserved')
                  AND expiry_time <= NOW()
                """
            )
        connection.commit()

    except Exception:
        if connection:
            connection.rollback()
        app.logger.exception("Updating expired food listings failed")

    finally:
        if connection:
            connection.close()


def parse_positive_decimal(value, field_name, maximum=None):
    """Parse a finite positive Decimal value or raise ValueError."""
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Please enter a valid {field_name}.")

    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field_name.title()} must be greater than zero.")

    if maximum is not None and result > maximum:
        raise ValueError(f"{field_name.title()} cannot exceed {maximum}.")

    return result


def save_uploaded_food_image(uploaded_file):
    """Validate and save a food image. Returns (filename, path)."""
    if not uploaded_file or not uploaded_file.filename:
        return None, None

    uploaded_file.stream.seek(0, os.SEEK_END)
    file_size = uploaded_file.stream.tell()
    uploaded_file.stream.seek(0)

    if file_size == 0:
        raise ValueError("The selected image is empty.")

    if file_size > 5 * 1024 * 1024:
        raise ValueError("Image must be 5 MB or smaller.")

    try:
        with Image.open(uploaded_file.stream) as image:
            image_format = image.format
            image.verify()
    except Exception as error:
        raise ValueError("Please select a valid JPG, PNG or WEBP image.") from error

    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise ValueError("Only JPG, PNG and WEBP images are allowed.")

    image_filename = f"{uuid.uuid4().hex}{ALLOWED_IMAGE_FORMATS[image_format]}"
    upload_folder = (
        Path(current_app.root_path) / "static" / "uploads" / "foods"
    )
    upload_folder.mkdir(parents=True, exist_ok=True)
    image_path = upload_folder / image_filename

    uploaded_file.stream.seek(0)
    with image_path.open("xb") as destination:
        destination.write(uploaded_file.stream.read())

    return image_filename, image_path


# ==================================================
# 3. HOME
# ==================================================

@app.route("/")
def home():
    """Public FoodShare landing/about page."""
    return render_template("about.html")


@app.route("/about")
def about():
    """Public About FoodShare page."""
    return render_template("about.html")


def admin_required():
    return login_required() and session.get("role") == "admin"


# ==================================================
# 4. REGISTRATION
# ==================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    role = request.form.get("role", "recipient").strip()
    password = request.form.get("password", "")

    if not full_name or not email or not password:
        return "Name, email and password are required.", 400

    if role not in ("donor", "recipient"):
        return "Invalid role.", 400

    if len(password) < 8:
        return "Password must be at least 8 characters.", 400

    password_hash = generate_password_hash(password)
    connection = None

    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users
                    (full_name, email, password_hash, phone, role)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (full_name, email, password_hash, phone or None, role),
            )

        connection.commit()
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))

    except Exception as error:
        if connection:
            connection.rollback()

        if getattr(error, "args", [None])[0] == 1062:
            return "This email is already registered.", 409

        app.logger.exception("Registration failed")
        return "Registration failed. Check the PyCharm terminal.", 500

    finally:
        if connection:
            connection.close()


# ==================================================
# 5. LOGIN
# ==================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    login_mode = request.args.get("role", "user").strip().lower()
    if request.method == "GET":
        if login_required():
            return redirect(url_for("admin_dashboard") if admin_required() else url_for("dashboard"))
        if login_mode == "admin":
            return render_template("admin_login.html", error=None)
        return render_template("login.html", login_mode="user")

    login_mode = request.form.get("login_mode", "user").strip().lower()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        message = "Please enter both your email and password."
        if login_mode == "admin":
            return render_template("admin_login.html", error=message), 400
        return render_template(
            "login.html",
            login_mode="user",
            error=message,
        ), 400

    # Admin authentication is independent of the users table.
    if login_mode == "admin":
        if hmac.compare_digest(email, ADMIN_EMAIL) and hmac.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["user_id"] = -1
            session["full_name"] = "FoodShare Admin"
            session["email"] = ADMIN_EMAIL
            session["role"] = "admin"
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_login.html", error="Invalid admin email or password."), 401

    connection = None

    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, full_name, email, password_hash, role
                FROM users
                WHERE email = %s
                """,
                (email,),
            )
            user = cursor.fetchone()

        if not user or not check_password_hash(user[3], password):
            return render_template(
                "login.html",
                login_mode="user",
                error="Invalid email or password. Please try again.",
            ), 401

        session.clear()
        session["user_id"] = user[0]
        session["full_name"] = user[1]
        session["email"] = user[2]
        session["role"] = user[4]
        if user[4] == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))

    except Exception:
        app.logger.exception("Login failed")
        return "Login failed. Check the PyCharm terminal.", 500

    finally:
        if connection:
            connection.close()


# ==================================================
# 6. ADMIN DASHBOARD
# ==================================================

@app.route("/admin")
@app.route("/admin/dashboard")
def admin_dashboard():
    if not login_required():
        return redirect(url_for("login", role="admin"))
    if not admin_required():
        flash("Admin access is required.", "error")
        return redirect(url_for("dashboard"))

    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    COUNT(*),
                    COALESCE(SUM(role = 'donor'), 0),
                    COALESCE(SUM(role = 'recipient'), 0)
                FROM users
                WHERE role IN ('donor', 'recipient')
            """)
            user_stats = cursor.fetchone()

            cursor.execute("""
                SELECT COUNT(*),
                       COALESCE(SUM(quantity), 0),
                       COALESCE(SUM(status = 'collected'), 0)
                FROM food_listings
            """)
            food_stats = cursor.fetchone()

            cursor.execute("""
                SELECT COUNT(*),
                       COALESCE(SUM(CASE WHEN status = 'completed'
                                         THEN requested_quantity ELSE 0 END), 0),
                       COALESCE(SUM(status = 'completed'), 0)
                FROM food_requests
            """)
            request_stats = cursor.fetchone()

            cursor.execute("""
                SELECT u.user_id, u.full_name, u.email, u.phone,
                       COUNT(DISTINCT fl.food_id) AS donation_count,
                       COALESCE(ROUND(AVG(fr.rating), 2), 0) AS avg_rating,
                       COUNT(DISTINCT fr.review_id) AS review_count
                FROM users u
                LEFT JOIN food_listings fl ON fl.donor_id = u.user_id
                LEFT JOIN food_reviews fr ON fr.reviewee_id = u.user_id
                WHERE u.role = 'donor'
                GROUP BY u.user_id, u.full_name, u.email, u.phone
                ORDER BY avg_rating DESC, donation_count DESC, u.full_name
            """)
            donors = cursor.fetchall()

            cursor.execute("""
                SELECT u.user_id, u.full_name, u.email, u.phone,
                       COUNT(rq.food_id) AS request_count,
                       COALESCE(SUM(CASE WHEN rq.status = 'completed'
                                         THEN rq.requested_quantity ELSE 0 END), 0)
                           AS received_quantity
                FROM users u
                LEFT JOIN food_requests rq ON rq.recipient_id = u.user_id
                WHERE u.role = 'recipient'
                GROUP BY u.user_id, u.full_name, u.email, u.phone
                ORDER BY request_count DESC, u.full_name
            """)
            receivers = cursor.fetchall()

            cursor.execute("""
                SELECT donor.user_id, donor.full_name, donor.email,
                       ROUND(AVG(r.rating), 2) AS avg_rating,
                       COUNT(r.review_id) AS review_count
                FROM users donor
                JOIN food_reviews r ON r.reviewee_id = donor.user_id
                WHERE donor.role = 'donor'
                GROUP BY donor.user_id, donor.full_name, donor.email
                HAVING COUNT(r.review_id) > 0
                ORDER BY avg_rating DESC, review_count DESC, donor.full_name
                LIMIT 1
            """)
            top_donor = cursor.fetchone()

            cursor.execute("""
                SELECT donor.user_id, donor.full_name, donor.email,
                       ROUND(AVG(r.rating), 2) AS avg_rating,
                       COUNT(r.review_id) AS review_count
                FROM users donor
                JOIN food_reviews r ON r.reviewee_id = donor.user_id
                WHERE donor.role = 'donor'
                GROUP BY donor.user_id, donor.full_name, donor.email
                HAVING COUNT(r.review_id) > 0
                ORDER BY avg_rating ASC, review_count DESC, donor.full_name
                LIMIT 1
            """)
            lowest_donor = cursor.fetchone()

            cursor.execute("""
                SELECT r.review_id, r.rating, r.review_text, r.created_at,
                       reviewer.full_name AS reviewer_name,
                       reviewer.email AS reviewer_email,
                       donor.full_name AS donor_name,
                       donor.email AS donor_email,
                       f.food_name
                FROM food_reviews r
                JOIN users reviewer ON reviewer.user_id = r.reviewer_id
                JOIN users donor ON donor.user_id = r.reviewee_id
                JOIN food_listings f ON f.food_id = r.food_id
                ORDER BY r.rating DESC, r.created_at DESC, r.review_id DESC
                LIMIT 1
            """)
            top_review = cursor.fetchone()

            cursor.execute("""
                SELECT r.review_id, r.rating, r.review_text, r.created_at,
                       reviewer.full_name AS reviewer_name,
                       reviewer.email AS reviewer_email,
                       donor.full_name AS donor_name,
                       donor.email AS donor_email,
                       f.food_name
                FROM food_reviews r
                JOIN users reviewer ON reviewer.user_id = r.reviewer_id
                JOIN users donor ON donor.user_id = r.reviewee_id
                JOIN food_listings f ON f.food_id = r.food_id
                ORDER BY r.rating ASC, r.created_at DESC, r.review_id DESC
                LIMIT 1
            """)
            lowest_review = cursor.fetchone()

            cursor.execute("""
                SELECT u.user_id, u.full_name, u.email,
                       COUNT(DISTINCT fl.food_id) AS donation_count,
                       COALESCE(SUM(fl.quantity), 0) AS quantity_total
                FROM users u
                LEFT JOIN food_listings fl ON fl.donor_id = u.user_id
                WHERE u.role = 'donor'
                GROUP BY u.user_id, u.full_name, u.email
                ORDER BY donation_count DESC, quantity_total DESC, u.full_name
                LIMIT 5
            """)
            top_donors = cursor.fetchall()

            cursor.execute("""
                SELECT r.review_id, r.rating, r.review_text, r.created_at,
                       reviewer.full_name AS reviewer_name,
                       reviewer.email AS reviewer_email,
                       donor.full_name AS donor_name,
                       donor.email AS donor_email,
                       f.food_name
                FROM food_reviews r
                JOIN users reviewer ON reviewer.user_id = r.reviewer_id
                JOIN users donor ON donor.user_id = r.reviewee_id
                JOIN food_listings f ON f.food_id = r.food_id
                ORDER BY r.rating ASC, r.created_at DESC
            """)
            all_reviews = cursor.fetchall()

        return render_template(
            "admin_dashboard.html",
            user_stats=user_stats,
            food_stats=food_stats,
            request_stats=request_stats,
            donors=donors,
            receivers=receivers,
            top_donor=top_donor,
            lowest_donor=lowest_donor,
            top_review=top_review,
            lowest_review=lowest_review,
            top_donors=top_donors,
            all_reviews=all_reviews,
        )
    except Exception:
        app.logger.exception("Admin dashboard loading failed")
        return "Admin dashboard could not be loaded. Check the PyCharm terminal and verify your MySQL schema.", 500
    finally:
        if connection:
            connection.close()


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
def admin_delete_user(user_id):
    if not login_required():
        return redirect(url_for("login", role="admin"))
    if not admin_required():
        return "Forbidden: admin access required.", 403
    if user_id == session.get("user_id"):
        flash("You cannot delete your own admin account.", "error")
        return redirect(url_for("admin_dashboard"))

    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id, full_name, role FROM users WHERE user_id = %s",
                (user_id,),
            )
            target = cursor.fetchone()
            if not target or target[2] not in ("donor", "recipient"):
                flash("Donor/receiver account was not found.", "error")
                return redirect(url_for("admin_dashboard"))

            cursor.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
        connection.commit()
        flash(f"Account for {target[1]} was deleted.", "success")
    except Exception as error:
        if connection:
            connection.rollback()
        app.logger.exception("Admin user deletion failed for user_id=%s", user_id)
        if getattr(error, "args", [None])[0] == 1451:
            flash("This account has linked food/request/review records. Configure the database foreign keys with ON DELETE CASCADE or archive the account instead.", "error")
        else:
            flash("Could not delete this account. Check the server log and database constraints.", "error")
    finally:
        if connection:
            connection.close()
    return redirect(url_for("admin_dashboard"))


# ==================================================
# 6. DASHBOARD
# ==================================================

@app.route("/dashboard")
def dashboard():
    if not login_required():
        return redirect(url_for("login"))
    if admin_required():
        return redirect(url_for("admin_dashboard"))

    update_expired_food()
    connection = None

    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            if session.get("role") == "donor":
                cursor.execute(
                    """
                    SELECT
                        COUNT(*),
                        COALESCE(SUM(status = 'available'), 0),
                        COALESCE(SUM(status = 'reserved'), 0),
                        COALESCE(SUM(status = 'collected'), 0),
                        COALESCE(SUM(status = 'expired'), 0)
                    FROM food_listings
                    WHERE donor_id = %s
                    """,
                    (session["user_id"],),
                )
                stats = cursor.fetchone()

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM food_requests fr
                    JOIN food_listings fl ON fr.food_id = fl.food_id
                    WHERE fl.donor_id = %s AND fr.status = 'pending'
                    """,
                    (session["user_id"],),
                )
                pending_requests = cursor.fetchone()[0]

                return render_template(
                    "dashboard.html",
                    full_name=session.get("full_name"),
                    email=session.get("email"),
                    role="donor",
                    stats=stats,
                    pending_requests=pending_requests,
                )

            return render_template(
                "dashboard.html",
                full_name=session.get("full_name"),
                email=session.get("email"),
                role="recipient",
            )

    except Exception:
        app.logger.exception("Dashboard loading failed")
        return "Dashboard could not be loaded. Check the PyCharm terminal.", 500

    finally:
        if connection:
            connection.close()


# ==================================================
# 7. SWITCH ROLE
# ==================================================

@app.route("/switch-role", methods=["POST"])
def switch_role():
    if not login_required():
        return redirect(url_for("login"))

    new_role = request.form.get("role", "").strip()
    if new_role not in ("donor", "recipient"):
        return "Invalid role selected.", 400

    if new_role == session.get("role"):
        flash(f"You are already using {new_role.title()} mode.", "info")
        return redirect(url_for("dashboard"))

    connection = None

    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id FROM users WHERE user_id = %s",
                (session["user_id"],),
            )
            user = cursor.fetchone()

            if not user:
                session.clear()
                return redirect(url_for("login"))

            cursor.execute(
                "UPDATE users SET role = %s WHERE user_id = %s",
                (new_role, session["user_id"]),
            )

        connection.commit()
        session["role"] = new_role
        flash(f"Switched to {new_role.title()} mode successfully!", "success")
        return redirect(url_for("dashboard"))

    except Exception:
        if connection:
            connection.rollback()
        app.logger.exception("Role switching failed")
        return "Could not change role. Check the PyCharm terminal.", 500

    finally:
        if connection:
            connection.close()


#
# ==================================================
# 8. ADD FOOD - DONOR ONLY (CATEGORY + PRICING)
# ==================================================

@app.route("/add-food", methods=["GET", "POST"])
def add_food():
    # Check login
    if not login_required():
        return redirect(url_for("login"))

    # Only donors can publish food
    if not donor_required():
        return "Switch to Donor mode to add food listings.", 403

    # Display form
    if request.method == "GET":
        return render_template("add_food.html")

    # --------------------------------------------------
    # 1. GET FORM DATA
    # --------------------------------------------------

    food_name = request.form.get("food_name", "").strip()
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()

    quantity_text = request.form.get("quantity", "").strip()
    quantity_unit = request.form.get("quantity_unit", "").strip()

    pickup_location = request.form.get("pickup_location", "").strip()
    expiry_text = request.form.get("expiry_time", "").strip()

    latitude_text = request.form.get("latitude", "").strip()
    longitude_text = request.form.get("longitude", "").strip()

    donation_type = request.form.get(
        "donation_type", "free"
    ).strip().lower()

    price_text = request.form.get("price_per_unit", "").strip()

    # --------------------------------------------------
    # 2. VALIDATE CATEGORY
    # --------------------------------------------------

    allowed_categories = {
        "Meals",
        "Fruits & Vegetables",
        "Bakery",
        "Dairy & Beverages",
        "Packaged Food",
        "Other",
    }

    if category not in allowed_categories:
        return "Please select a valid food category.", 400

    # --------------------------------------------------
    # 3. VALIDATE REQUIRED FIELDS
    # --------------------------------------------------

    if not all([
        food_name,
        quantity_text,
        quantity_unit,
        pickup_location,
        expiry_text,
    ]):
        return "Please fill in all required fields.", 400

    if quantity_unit not in ALLOWED_QUANTITY_UNITS:
        return "Invalid quantity unit.", 400

    if donation_type not in ("free", "paid"):
        return "Invalid donation type.", 400

    # --------------------------------------------------
    # 4. VALIDATE QUANTITY AND EXPIRY
    # --------------------------------------------------

    try:
        quantity = parse_positive_decimal(
            quantity_text,
            "quantity",
            MAX_QUANTITY
        )

        expiry_time = datetime.strptime(
            expiry_text,
            "%Y-%m-%dT%H:%M"
        )

    except ValueError as error:
        return str(error) or "Invalid quantity or expiry date.", 400

    if expiry_time <= datetime.now():
        return "Expiry time must be in the future.", 400

    # --------------------------------------------------
    # 5. VALIDATE DONATION PRICE
    # --------------------------------------------------

    if donation_type == "paid":
        try:
            price_per_unit = parse_positive_decimal(
                price_text,
                "price per unit",
                MAX_PRICE_PER_UNIT
            )
        except ValueError as error:
            return str(error), 400

    else:
        # Free donations do not have a price
        price_per_unit = None

    # --------------------------------------------------
    # 6. VALIDATE MAP COORDINATES
    # --------------------------------------------------

    if bool(latitude_text) != bool(longitude_text):
        return "Both latitude and longitude are required.", 400

    latitude = None
    longitude = None

    if latitude_text and longitude_text:
        try:
            latitude = Decimal(latitude_text)
            longitude = Decimal(longitude_text)

        except (InvalidOperation, ValueError):
            return "Invalid map coordinates.", 400

        if not latitude.is_finite() or not longitude.is_finite():
            return "Map coordinates must be valid numbers.", 400

        if not Decimal("-90") <= latitude <= Decimal("90"):
            return "Latitude must be between -90 and 90.", 400

        if not Decimal("-180") <= longitude <= Decimal("180"):
            return "Longitude must be between -180 and 180.", 400

    # --------------------------------------------------
    # 7. PROCESS FOOD IMAGE
    # --------------------------------------------------

    uploaded_file = next(
        (
            file
            for file in request.files.getlist("food_image")
            if file and file.filename
        ),
        None,
    )

    image_filename = None
    image_path = None

    try:
        image_filename, image_path = save_uploaded_food_image(
            uploaded_file
        )

    except ValueError as error:
        return str(error), 400

    except Exception:
        app.logger.exception("Food image upload failed")
        return "Could not process the selected image.", 400

    # --------------------------------------------------
    # 8. INSERT FOOD LISTING INTO MYSQL
    # --------------------------------------------------

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO food_listings (
                    donor_id,
                    food_name,
                    category,
                    description,
                    quantity,
                    quantity_unit,
                    pickup_location,
                    latitude,
                    longitude,
                    expiry_time,
                    status,
                    image_filename,
                    donation_type,
                    price_per_unit,
                    remaining_quantity
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, 'available', %s, %s, %s, %s
                )
                """,
                (
                    session["user_id"],
                    food_name,
                    category,
                    description or None,
                    quantity,
                    quantity_unit,
                    pickup_location,
                    latitude,
                    longitude,
                    expiry_time,
                    image_filename,
                    donation_type,
                    price_per_unit,
                    quantity,
                ),
            )

        # Commit database transaction
        connection.commit()

        flash(
            "Food listing published successfully!",
            "success"
        )

        return redirect(url_for("my_donations"))

    except Exception:
        if connection:
            connection.rollback()

        # Remove uploaded image if database insertion fails
        if image_path and image_path.exists():
            try:
                image_path.unlink()
            except OSError:
                app.logger.exception(
                    "Could not remove image after database failure"
                )

        app.logger.exception("Food listing creation failed")

        return (
            "Could not publish food. Check the PyCharm terminal.",
            500
        )

    finally:
        if connection:
            connection.close()


# ==================================================
# 9. MY DONATIONS - DONOR ONLY (WITH PRICING & REMAINING QUANTITY)
# ==================================================

@app.route("/my-donations")
def my_donations():
    if not login_required():
        return redirect(url_for("login"))

    if not donor_required():
        return "Switch to Donor mode to view your donations.", 403

    update_expired_food()
    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    food_id,
                    food_name,
                    description,
                    quantity,
                    quantity_unit,
                    pickup_location,
                    expiry_time,
                    status,
                    created_at,
                    image_filename,
                    donation_type,
                    price_per_unit,
                    COALESCE(remaining_quantity, quantity)
                        AS remaining_quantity
                FROM food_listings
                WHERE donor_id = %s
                ORDER BY created_at DESC
                """,
                (session["user_id"],),
            )

            donations = cursor.fetchall()

        return render_template(
            "my_donations.html",
            donations=donations,
            full_name=session.get("full_name"),
        )

    except Exception:
        app.logger.exception("Loading donations failed")
        return "Could not load donations. Check the PyCharm terminal.", 500

    finally:
        if connection:
            connection.close()

# ==================================================
# 10. DONOR FOOD REQUESTS
# ==================================================

@app.route("/donor-requests")
def donor_requests():
    if not login_required():
        return redirect(url_for("login"))

    if not donor_required():
        flash("Switch to Donor mode to view food requests.", "info")
        return redirect(url_for("dashboard"))

    update_expired_food()
    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    fr.food_id,
                    fr.recipient_id,
                    fr.requested_quantity,
                    fr.status,
                    fr.requested_at,
                    fl.food_name,
                    fl.quantity_unit,
                    u.full_name AS recipient_name,
                    u.email AS recipient_email
                FROM food_requests AS fr
                INNER JOIN food_listings AS fl
                    ON fr.food_id = fl.food_id
                INNER JOIN users AS u
                    ON fr.recipient_id = u.user_id
                WHERE fl.donor_id = %s
                ORDER BY
                    CASE
                        WHEN fr.status = 'pending' THEN 0
                        ELSE 1
                    END,
                    fr.requested_at DESC
                """,
                (session["user_id"],),
            )

            requests_list = cursor.fetchall()

        return render_template(
            "donor_requests.html",
            requests_list=requests_list,
        )

    except Exception:
        app.logger.exception(
            "Failed to load donor requests for user_id=%s",
            session.get("user_id"),
        )
        return (
            "Could not load requests. Check the PyCharm terminal.",
            500,
        )

    finally:
        if connection:
            connection.close()


#


@app.route("/donor-requests/<int:food_id>/<int:recipient_id>/<action>", methods=["POST"])
def update_food_request(food_id, recipient_id, action):
    if not login_required():
        return redirect(url_for("login"))

    if not donor_required():
        return redirect(url_for("dashboard"))
    if action not in ("approve", "reject", "complete"):
        flash("Invalid request action.", "danger")
        return redirect(url_for("donor_requests"))

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            connection.begin()

            # Lock the food listing to prevent simultaneous quantity updates.
            cursor.execute(
                """
                SELECT status, expiry_time, quantity, remaining_quantity
                FROM food_listings
                WHERE food_id = %s AND donor_id = %s
                FOR UPDATE
                """,
                (food_id, session["user_id"]),
            )
            food = cursor.fetchone()

            if not food:
                connection.rollback()
                flash("Food listing not found.", "danger")
                return redirect(url_for("donor_requests"))

            food_status, expiry_time, original_quantity, remaining_quantity = food

            # Lock the recipient's request.
            cursor.execute(
                """
                SELECT requested_quantity, status
                FROM food_requests
                WHERE food_id = %s AND recipient_id = %s
                FOR UPDATE
                """,
                (food_id, recipient_id),
            )
            food_request = cursor.fetchone()

            if not food_request:
                connection.rollback()
                flash("Food request not found.", "danger")
                return redirect(url_for("donor_requests"))

            requested_quantity, request_status = food_request

            # Handle approval.
            if action == "approve":
                if request_status != "pending":
                    connection.rollback()
                    flash("Only pending requests can be approved.", "warning")
                    return redirect(url_for("donor_requests"))

                if food_status not in ("available", "reserved"):
                    connection.rollback()
                    flash("This food listing is no longer available.", "warning")
                    return redirect(url_for("donor_requests"))

                # Initialize remaining quantity if needed.
                if remaining_quantity is None:
                    remaining_quantity = original_quantity

                if remaining_quantity is None or remaining_quantity <= 0:
                    connection.rollback()
                    flash("No food quantity is available.", "warning")
                    return redirect(url_for("donor_requests"))

                if requested_quantity is None or requested_quantity <= 0:
                    connection.rollback()
                    flash("Invalid requested quantity.", "danger")
                    return redirect(url_for("donor_requests"))

                if requested_quantity > remaining_quantity:
                    connection.rollback()
                    flash(
                        f"Only {remaining_quantity} units remain available. "
                        "This request cannot be approved.",
                        "warning",
                    )
                    return redirect(url_for("donor_requests"))

                # Reject approval if the food has expired.
                from datetime import datetime

                if expiry_time and expiry_time <= datetime.now():
                    cursor.execute(
                        """
                        UPDATE food_listings
                        SET status = 'expired'
                        WHERE food_id = %s
                        """,
                        (food_id,),
                    )
                    connection.commit()
                    flash("This food listing has expired.", "warning")
                    return redirect(url_for("donor_requests"))

                new_remaining = remaining_quantity - requested_quantity

                # Deduct the approved quantity.
                # Keep the listing available if any quantity remains.
                new_food_status = (
                    "available" if new_remaining > 0 else "reserved"
                )

                cursor.execute(
                    """
                    UPDATE food_listings
                    SET remaining_quantity = %s, status = %s
                    WHERE food_id = %s
                    """,
                    (new_remaining, new_food_status, food_id),
                )

                cursor.execute(
                    """
                    UPDATE food_requests
                    SET status = 'approved'
                    WHERE food_id = %s AND recipient_id = %s
                    """,
                    (food_id, recipient_id),
                )

                # Create a chat conversation for the approved request.
                cursor.execute(
                    """
                    INSERT INTO conversations
                        (food_id, donor_id, recipient_id)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        conversation_id = LAST_INSERT_ID(conversation_id)
                    """,
                    (food_id, session["user_id"], recipient_id),
                )

                connection.commit()
                create_notification(
                    recipient_id,
                    "Food request approved",
                    f'Your request for "food #{food_id}" was approved. Remaining listing quantity: {new_remaining}.',
                    "request_approved",
                    url_for("my_food_requests"),
                )
                # Inform recipients who still have pending requests for this listing.
                pending_connection = None
                try:
                    pending_connection = get_db_connection()
                    with pending_connection.cursor() as pending_cursor:
                        pending_cursor.execute(
                            "SELECT recipient_id FROM food_requests WHERE food_id = %s AND status = 'pending'",
                            (food_id,),
                        )
                        pending_users = pending_cursor.fetchall()
                    for (pending_user_id,) in pending_users:
                        create_notification(
                            pending_user_id,
                            "Food availability updated",
                            f"A quantity was allocated from food listing #{food_id}. Check the current remaining quantity before requesting.",
                            "food_availability",
                            url_for("food_details", food_id=food_id),
                        )
                finally:
                    if pending_connection:
                        pending_connection.close()
                flash(
                    f"Request approved. Remaining quantity: {new_remaining}.",
                    "success",
                )

            # Handle rejection of pending requests.
            elif action == "reject":
                if request_status != "pending":
                    connection.rollback()
                    flash("Only pending requests can be rejected.", "warning")
                    return redirect(url_for("donor_requests"))

                cursor.execute(
                    """
                    UPDATE food_requests
                    SET status = 'rejected'
                    WHERE food_id = %s AND recipient_id = %s
                    """,
                    (food_id, recipient_id),
                )

                connection.commit()
                create_notification(
                    recipient_id,
                    "Food request not approved",
                    f'Your request for food listing #{food_id} was rejected by the donor.',
                    "request_rejected",
                    url_for("my_food_requests"),
                )
                flash("Food request rejected.", "success")

            # Handle completion of an approved request.
            elif action == "complete":
                if request_status != "approved":
                    connection.rollback()
                    flash("Only approved requests can be completed.", "warning")
                    return redirect(url_for("donor_requests"))

                cursor.execute(
                    """
                    UPDATE food_requests
                    SET status = 'completed'
                    WHERE food_id = %s AND recipient_id = %s
                    """,
                    (food_id, recipient_id),
                )

                # Mark the whole listing collected only when no quantity
                # remains available and no other approved requests exist.
                if remaining_quantity is None:
                    remaining_quantity = original_quantity

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM food_requests
                    WHERE food_id = %s AND status = 'approved'
                    """,
                    (food_id,),
                )
                pending_approved = cursor.fetchone()[0]

                if remaining_quantity <= 0 and pending_approved == 1:
                    cursor.execute(
                        """
                        UPDATE food_listings
                        SET status = 'collected'
                        WHERE food_id = %s
                        """,
                        (food_id,),
                    )
                elif remaining_quantity > 0:
                    cursor.execute(
                        """
                        UPDATE food_listings
                        SET status = 'available'
                        WHERE food_id = %s
                        """,
                        (food_id,),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE food_listings
                        SET status = 'reserved'
                        WHERE food_id = %s
                        """,
                        (food_id,),
                    )

                connection.commit()
                flash("Food request marked as completed.", "success")

    except Exception:
        if connection:
            connection.rollback()
        app.logger.exception("Error updating food request")
        flash("Something went wrong while updating the request.", "danger")

    finally:
        if connection:
            connection.close()

    return redirect(url_for("donor_requests"))



# ==================================================
# NOTIFICATIONS: LIST, UNREAD COUNT, MARK READ
# ==================================================

@app.route("/notifications")
def notifications_page():
    if not login_required():
        return redirect(url_for("login"))
    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT notification_id, title, message, notification_type,
                          related_url, is_read, created_at
                   FROM notifications WHERE user_id = %s
                   ORDER BY created_at DESC LIMIT 100""",
                (session["user_id"],),
            )
            notification_rows = cursor.fetchall()
        return render_template("notifications.html", notifications=notification_rows)
    except Exception:
        app.logger.exception("Loading notifications failed")
        return "Could not load notifications. Check the PyCharm terminal.", 500
    finally:
        if connection:
            connection.close()


@app.route("/notifications/unread-count")
def notification_unread_count():
    if not login_required():
        return jsonify(success=False, message="Please log in."), 401
    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = FALSE",
                (session["user_id"],),
            )
            count = cursor.fetchone()[0]
        return jsonify(success=True, count=count)
    except Exception:
        app.logger.exception("Unread notification count failed")
        return jsonify(success=False, count=0), 500
    finally:
        if connection:
            connection.close()


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
def mark_notification_read(notification_id):
    if not login_required():
        return jsonify(success=False, message="Please log in."), 401
    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE notifications SET is_read = TRUE WHERE notification_id = %s AND user_id = %s",
                (notification_id, session["user_id"]),
            )
        connection.commit()
        return jsonify(success=True)
    except Exception:
        if connection:
            connection.rollback()
        app.logger.exception("Marking notification read failed")
        return jsonify(success=False), 500
    finally:
        if connection:
            connection.close()

# ==================================================
# NOTIFICATION API (USED BY static/js/notifications.js)
# ==================================================

@app.route("/api/notifications")
def api_notifications():
    if not login_required():
        return jsonify(success=False, message="Please log in."), 401

    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT notification_id, title, message, notification_type,
                       related_url, is_read, created_at
                FROM notifications
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (session["user_id"],),
            )
            rows = cursor.fetchall()
            cursor.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = FALSE",
                (session["user_id"],),
            )
            unread_count = cursor.fetchone()[0]

        notifications = [
            {
                "notification_id": row[0],
                "title": row[1],
                "message": row[2],
                "notification_type": row[3],
                "related_url": row[4],
                "is_read": bool(row[5]),
                "created_at": row[6].strftime("%d %b %Y, %I:%M %p") if row[6] else "",
            }
            for row in rows
        ]
        return jsonify(success=True, notifications=notifications, unread_count=unread_count)
    except Exception:
        app.logger.exception("Notification API load failed")
        return jsonify(success=False, message="Could not load notifications."), 500
    finally:
        if connection:
            connection.close()


@app.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
def api_mark_notification_read(notification_id):
    if not login_required():
        return jsonify(success=False, message="Please log in."), 401

    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE notifications SET is_read = TRUE
                WHERE notification_id = %s AND user_id = %s
                """,
                (notification_id, session["user_id"]),
            )
            updated = cursor.rowcount
        connection.commit()
        if not updated:
            return jsonify(success=False, message="Notification not found."), 404
        return jsonify(success=True)
    except Exception:
        if connection:
            connection.rollback()
        app.logger.exception("Notification mark-read API failed")
        return jsonify(success=False, message="Could not update notification."), 500
    finally:
        if connection:
            connection.close()


@app.route("/api/notifications/read-all", methods=["POST"])
def api_mark_all_notifications_read():
    if not login_required():
        return jsonify(success=False, message="Please log in."), 401

    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE notifications SET is_read = TRUE
                WHERE user_id = %s AND is_read = FALSE
                """,
                (session["user_id"],),
            )
        connection.commit()
        return jsonify(success=True)
    except Exception:
        if connection:
            connection.rollback()
        app.logger.exception("Mark-all-read API failed")
        return jsonify(success=False, message="Could not update notifications."), 500
    finally:
        if connection:
            connection.close()


# ==================================================
# 12. LOGOUT
# ==================================================

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ==================================================
# 13. MYSQL CONNECTION TEST
# ==================================================

@app.route("/db-test")
def db_test():
    connection = None

    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            result = cursor.fetchone()

        return f"MySQL Connected Successfully! Database: {result[0]}"

    except Exception:
        app.logger.exception("MySQL connection test failed")
        return "Database connection failed. Check server logs.", 500

    finally:
        if connection:
            connection.close()


# ==================================================
# 14. BROWSE FOOD - RECIPIENT ONLY
#     CATEGORY + SEARCH + PRICING FILTERS
# ==================================================

@app.route("/browse-food")
def browse_food():
    if not login_required():
        return redirect(url_for("login"))

    if session.get("role") != "recipient":
        return "Switch to Recipient mode to browse food.", 403

    update_expired_food()

    # Read search and filter values from URL
    search_query = request.args.get("search", "").strip()
    selected_category = request.args.get("category", "").strip()
    selected_donation_type = (
        request.args.get("donation_type", "").strip().lower()
    )

    allowed_categories = {
        "Meals",
        "Fruits & Vegetables",
        "Bakery",
        "Dairy & Beverages",
        "Packaged Food",
        "Other",
    }

    allowed_donation_types = {"free", "paid"}

    if selected_category not in allowed_categories:
        selected_category = ""

    if selected_donation_type not in allowed_donation_types:
        selected_donation_type = ""

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            query = """
                SELECT
                    fl.food_id,
                    fl.food_name,
                    fl.description,
                    fl.quantity,
                    fl.quantity_unit,
                    fl.pickup_location,
                    fl.expiry_time,
                    fl.status,
                    fl.image_filename,
                    u.full_name AS donor_name,
                    fl.donation_type,
                    fl.price_per_unit,
                    fl.category
                FROM food_listings fl
                JOIN users u ON u.user_id = fl.donor_id
                WHERE fl.status = 'available'
                  AND fl.expiry_time > NOW()
            """

            params = []

            # Search food name, description or pickup location
            if search_query:
                query += """
                    AND (
                        fl.food_name LIKE %s
                        OR fl.description LIKE %s
                        OR fl.pickup_location LIKE %s
                    )
                """

                search_pattern = f"%{search_query}%"

                params.extend([
                    search_pattern,
                    search_pattern,
                    search_pattern,
                ])

            # Filter by category
            if selected_category:
                query += " AND fl.category = %s"
                params.append(selected_category)

            # Filter free or paid donations
            if selected_donation_type:
                query += " AND fl.donation_type = %s"
                params.append(selected_donation_type)

            query += " ORDER BY fl.created_at DESC"

            cursor.execute(query, tuple(params))
            foods = cursor.fetchall()

        return render_template(
            "browse_food.html",
            foods=foods,
            search_query=search_query,
            selected_category=selected_category,
            selected_donation_type=selected_donation_type,
            categories=[
                "Meals",
                "Fruits & Vegetables",
                "Bakery",
                "Dairy & Beverages",
                "Packaged Food",
                "Other",
            ],
        )

    except Exception:
        app.logger.exception("Browse food page loading failed")
        return (
            "Could not load available food. "
            "Check the PyCharm terminal.",
            500,
        )

    finally:
        if connection:
            connection.close()



# ==================================================
# 15. FOOD DETAILS - RECIPIENT ONLY (WITH PRICING, REVIEWS & REMAINING QUANTITY)
# ==================================================

@app.route("/food/<int:food_id>")
def food_details(food_id):
    if not login_required():
        return redirect(url_for("login"))

    if session.get("role") != "recipient":
        return "Switch to Recipient mode to view food details.", 403

    update_expired_food()
    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            # 1. Fetch food details and current remaining quantity.
            cursor.execute(
                """
                SELECT
                    fl.food_id,
                    fl.food_name,
                    fl.description,
                    fl.quantity,
                    fl.quantity_unit,
                    fl.pickup_location,
                    fl.expiry_time,
                    fl.status,
                    fl.image_filename,
                    fl.created_at,
                    u.full_name AS donor_name,
                    u.email AS donor_email,
                    u.phone AS donor_phone,
                    fl.donation_type,
                    fl.price_per_unit,
                    COALESCE(fl.remaining_quantity, fl.quantity)
                        AS remaining_quantity
                FROM food_listings fl
                JOIN users u ON u.user_id = fl.donor_id
                WHERE fl.food_id = %s
                """,
                (food_id,),
            )

            food = cursor.fetchone()

            if not food:
                return "Food listing not found.", 404

            # 2. Fetch current recipient's request.
            cursor.execute(
                """
                SELECT requested_quantity, status
                FROM food_requests
                WHERE food_id = %s AND recipient_id = %s
                """,
                (food_id, session["user_id"]),
            )

            recipient_request = cursor.fetchone()

            contact_unlocked = bool(
                recipient_request
                and recipient_request[1] == "approved"
            )

            # 3. Fetch donor's average rating and review count.
            cursor.execute(
                """
                SELECT
                    COALESCE(ROUND(AVG(rating), 1), 0) AS average_rating,
                    COUNT(*) AS total_reviews
                FROM food_reviews
                WHERE reviewee_id = (
                    SELECT donor_id
                    FROM food_listings
                    WHERE food_id = %s
                )
                """,
                (food_id,),
            )

            rating_summary = cursor.fetchone()

            # 4. Fetch recent reviews about this food's donor.
            cursor.execute(
                """
                SELECT
                    fr.rating,
                    fr.review_text,
                    fr.created_at,
                    reviewer.full_name AS reviewer_name
                FROM food_reviews fr
                JOIN users reviewer
                    ON reviewer.user_id = fr.reviewer_id
                WHERE fr.reviewee_id = (
                    SELECT donor_id
                    FROM food_listings
                    WHERE food_id = %s
                )
                ORDER BY fr.created_at DESC
                LIMIT 10
                """,
                (food_id,),
            )

            reviews = cursor.fetchall()

            # 5. Check whether the current recipient can review this donor.
            can_review = False
            already_reviewed = False

            if (
                recipient_request
                and recipient_request[1] == "completed"
            ):
                cursor.execute(
                    """
                    SELECT review_id
                    FROM food_reviews
                    WHERE food_id = %s
                      AND reviewer_id = %s
                    LIMIT 1
                    """,
                    (food_id, session["user_id"]),
                )

                existing_review = cursor.fetchone()
                already_reviewed = bool(existing_review)
                can_review = not already_reviewed

        return render_template(
            "food_details.html",
            food=food,
            recipient_request=recipient_request,
            contact_unlocked=contact_unlocked,
            rating_summary=rating_summary,
            reviews=reviews,
            can_review=can_review,
            already_reviewed=already_reviewed,
        )

    except Exception:
        app.logger.exception("Food details page loading failed")
        return (
            "Could not load food details. Check the PyCharm terminal.",
            500,
        )

    finally:
        if connection:
            connection.close()

# ==================================================
# 16. REQUEST FOOD - RECIPIENT ONLY
# ==================================================

@app.route("/food/<int:food_id>/request", methods=["POST"])
def request_food(food_id):
    if not login_required():
        return redirect(url_for("login"))

    if session.get("role") != "recipient":
        return "Switch to Recipient mode to request food.", 403

    update_expired_food()
    quantity_text = request.form.get("requested_quantity", "").strip()

    try:
        requested_quantity = parse_positive_decimal(
            quantity_text, "requested quantity", MAX_QUANTITY
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("food_details", food_id=food_id))

    connection = None

    try:
        connection = get_db_connection()
        connection.begin()

        with connection.cursor() as cursor:
            # Lock the listing while checking its remaining quantity.
            cursor.execute(
                """
                SELECT quantity, remaining_quantity, quantity_unit,
                       status, expiry_time, donor_id
                FROM food_listings
                WHERE food_id = %s
                FOR UPDATE
                """,
                (food_id,),
            )
            food = cursor.fetchone()

            if not food:
                connection.rollback()
                return "Food listing not found.", 404

            original_quantity = Decimal(str(food[0]))
            remaining_quantity = (
                Decimal(str(food[1]))
                if food[1] is not None
                else original_quantity
            )
            quantity_unit = food[2]
            food_status = food[3]
            expiry_time = food[4]
            donor_id = food[5]

            # Initialize remaining quantity for older listings.
            if food[1] is None:
                cursor.execute(
                    """
                    UPDATE food_listings
                    SET remaining_quantity = %s
                    WHERE food_id = %s
                    """,
                    (remaining_quantity, food_id),
                )

            if (
                food_status != "available"
                or expiry_time is None
                or expiry_time <= datetime.now()
                or remaining_quantity <= 0
            ):
                connection.rollback()
                flash("This food is no longer available.", "error")
                return redirect(url_for("food_details", food_id=food_id))

            if requested_quantity > remaining_quantity:
                connection.rollback()
                flash(
                    f"Only {remaining_quantity} {quantity_unit} "
                    f"are currently available.",
                    "error",
                )
                return redirect(url_for("food_details", food_id=food_id))

            if int(donor_id) == int(session["user_id"]):
                connection.rollback()
                flash("You cannot request your own donation.", "error")
                return redirect(url_for("food_details", food_id=food_id))

            cursor.execute(
                """
                SELECT status
                FROM food_requests
                WHERE food_id = %s AND recipient_id = %s
                FOR UPDATE
                """,
                (food_id, session["user_id"]),
            )
            existing_request = cursor.fetchone()

            if existing_request and existing_request[0] in (
                "pending",
                "approved",
            ):
                connection.rollback()
                flash(
                    "You already have an active request for this food.",
                    "info",
                )
                return redirect(url_for("food_details", food_id=food_id))

            if existing_request:
                cursor.execute(
                    """
                    UPDATE food_requests
                    SET requested_quantity = %s,
                        status = 'pending',
                        requested_at = NOW()
                    WHERE food_id = %s AND recipient_id = %s
                    """,
                    (requested_quantity, food_id, session["user_id"]),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO food_requests
                        (food_id, recipient_id, requested_quantity,
                         status, requested_at)
                    VALUES (%s, %s, %s, 'pending', NOW())
                    """,
                    (food_id, session["user_id"], requested_quantity),
                )

        connection.commit()

        # Notify the donor only after the food request is successfully committed.
        # Use a relative URL so notification creation does not depend on url_for context.
        create_notification(
            donor_id,
            "New food request",
            f'{session.get("full_name", "A recipient")} has requested food listing #{food_id}.',
            "food_request",
            "/donor-requests",
        )

        flash("Your food request has been sent to the donor.", "success")
        return redirect(url_for("food_details", food_id=food_id))

    except Exception:
        if connection:
            connection.rollback()

        app.logger.exception("Submitting food request failed")
        return "Could not submit request. Check the PyCharm terminal.", 500

    finally:
        if connection:
            connection.close()



# ==================================================
# MY FOOD REQUESTS - RECIPIENT
# ==================================================

@app.route("/my-food-requests")
def my_food_requests():
    if not login_required():
        return redirect(url_for("login"))

    if session.get("role") != "recipient":
        flash("Switch to Recipient mode to view your requests.", "info")
        return redirect(url_for("dashboard"))

    update_expired_food()
    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    fl.food_id,
                    fl.food_name,
                    fr.requested_quantity,
                    fl.quantity_unit,
                    fr.status,
                    fr.requested_at,
                    fl.expiry_time,
                    fl.donation_type,
                    fl.price_per_unit
                FROM food_requests AS fr
                INNER JOIN food_listings AS fl
                    ON fr.food_id = fl.food_id
                WHERE fr.recipient_id = %s
                ORDER BY fr.requested_at DESC
                """,
                (session["user_id"],),
            )

            requests_list = cursor.fetchall()

        return render_template(
            "my_food_requests.html",
            requests_list=requests_list,
        )

    except Exception:
        app.logger.exception("Loading recipient food requests failed")
        return "Could not load your food requests. Check the PyCharm terminal.", 500

    finally:
        if connection:
            connection.close()



# ==================================================
# RATINGS & REVIEWS - SUBMIT REVIEW
# ==================================================

@app.route("/food/<int:food_id>/review", methods=["POST"])
def submit_food_review(food_id):
    if not login_required():
        return redirect(url_for("login"))

    if session.get("role") != "recipient":
        flash("Switch to Recipient mode to submit a review.", "info")
        return redirect(url_for("food_details", food_id=food_id))

    reviewer_id = session["user_id"]
    connection = None

    try:
        # Validate rating and review text.
        try:
            rating = int(request.form.get("rating", ""))
        except (TypeError, ValueError):
            flash("Please select a rating from 1 to 5.", "error")
            return redirect(url_for("food_details", food_id=food_id))

        review_text = request.form.get("review_text", "").strip()

        if rating < 1 or rating > 5:
            flash("Rating must be between 1 and 5.", "error")
            return redirect(url_for("food_details", food_id=food_id))

        if len(review_text) > 1000:
            flash("Review must be 1000 characters or less.", "error")
            return redirect(url_for("food_details", food_id=food_id))

        connection = get_db_connection()

        with connection.cursor() as cursor:
            # Find the donor who owns this food listing.
            cursor.execute(
                """
                SELECT donor_id
                FROM food_listings
                WHERE food_id = %s
                """,
                (food_id,),
            )
            food = cursor.fetchone()

            if not food:
                flash("Food listing not found.", "error")
                return redirect(url_for("dashboard"))

            donor_id = food[0]

            if donor_id == reviewer_id:
                flash("You cannot review your own food listing.", "error")
                return redirect(
                    url_for("food_details", food_id=food_id)
                )

            # Only allow reviews after a completed exchange.
            cursor.execute(
                """
                SELECT status
                FROM food_requests
                WHERE food_id = %s
                  AND recipient_id = %s
                """,
                (food_id, reviewer_id),
            )
            food_request = cursor.fetchone()

            if not food_request or food_request[0] != "completed":
                flash(
                    "You can review the donor only after the exchange is completed.",
                    "warning",
                )
                return redirect(
                    url_for("food_details", food_id=food_id)
                )

            # Save the review. The table's unique constraint prevents duplicates.
            cursor.execute(
                """
                INSERT INTO food_reviews
                    (food_id, reviewer_id, reviewee_id, rating, review_text)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    food_id,
                    reviewer_id,
                    donor_id,
                    rating,
                    review_text or None,
                ),
            )

        connection.commit()

        flash("Thank you! Your rating and review have been submitted.", "success")

    except Exception as error:
        if connection:
            connection.rollback()

        # Duplicate review or another database error.
        app.logger.exception("Submitting food review failed")

        if error.__class__.__name__ == "IntegrityError":
            flash("You have already reviewed this food donation.", "warning")
        else:
            flash("Could not submit your review. Check the PyCharm terminal.", "error")

    finally:
        if connection:
            connection.close()

    return redirect(url_for("food_details", food_id=food_id))


@app.route("/my-reviews")
def my_reviews():
    if not login_required():
        return redirect(url_for("login"))

    if session.get("role") != "donor":
        flash("Only donors can access their received reviews.", "warning")
        return redirect(url_for("dashboard"))

    donor_id = session["user_id"]
    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            # Donor's overall rating and review count
            cursor.execute("""
                SELECT
                    COALESCE(ROUND(AVG(r.rating), 1), 0) AS average_rating,
                    COUNT(r.review_id) AS total_reviews
                FROM food_reviews r
                WHERE r.reviewee_id = %s
            """, (donor_id,))

            summary = cursor.fetchone()

            # Recent reviews received by this donor
            cursor.execute("""
                SELECT
                    r.rating,
                    r.review_text,
                    r.created_at,
                    u.full_name,
                    f.food_name
                FROM food_reviews r
                JOIN users u
                    ON r.reviewer_id = u.user_id
                JOIN food_listings f
                    ON r.food_id = f.food_id
                WHERE r.reviewee_id = %s
                ORDER BY r.created_at DESC
            """, (donor_id,))

            reviews = cursor.fetchall()

        return render_template(
            "my_reviews.html",
            summary=summary,
            reviews=reviews
        )

    except Exception:
        app.logger.exception("Unable to load donor reviews")
        flash("Unable to load your reviews right now.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if connection:
            connection.close()
# ==================================================
# 18. REAL-TIME CHAT EVENT HANDLERS
# ==================================================

def get_approved_chat(conversation_id, user_id):
    """
    Verify that the logged-in user belongs to this
    conversation and the food request is approved
    or completed.
    """
    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.conversation_id,
                    c.food_id,
                    c.donor_id,
                    c.recipient_id
                FROM conversations AS c
                INNER JOIN food_requests AS fr
                    ON fr.food_id = c.food_id
                   AND fr.recipient_id = c.recipient_id
                WHERE c.conversation_id = %s
                  AND (
                      c.donor_id = %s
                      OR c.recipient_id = %s
                  )
                  AND fr.status IN ('approved', 'completed')
                """,
                (conversation_id, user_id, user_id),
            )

            return cursor.fetchone()

    finally:
        if connection:
            connection.close()


@socketio.on("connect")
def handle_socket_connect(auth=None):
    """Join the authenticated user's private notification room."""
    user_id = session.get("user_id")
    if user_id:
        join_room(f"user_{user_id}")


# --------------------------------------------------
# JOIN CHAT ROOM
# --------------------------------------------------

@socketio.on("join_chat")
def handle_join_chat(data):
    if not session.get("user_id"):
        emit(
            "chat_error",
            {"message": "Please log in to access chat."},
        )
        return

    try:
        conversation_id = int(data.get("conversation_id"))
    except (TypeError, ValueError, AttributeError):
        emit(
            "chat_error",
            {"message": "Invalid conversation ID."},
        )
        return

    if conversation_id <= 0:
        emit(
            "chat_error",
            {"message": "Invalid conversation ID."},
        )
        return

    chat = get_approved_chat(
        conversation_id,
        session["user_id"],
    )

    if not chat:
        emit(
            "chat_error",
            {"message": "Chat unavailable or access denied."},
        )
        return

    join_room(f"chat_{conversation_id}")

    emit(
        "chat_joined",
        {"conversation_id": conversation_id},
    )


# ==================================================
# SEND REAL-TIME TEXT MESSAGE
# ==================================================

@socketio.on("send_message")
def handle_send_message(data):
    if not session.get("user_id"):
        emit("chat_error", {"message": "Please log in to send messages."})
        return

    try:
        conversation_id = int(data.get("conversation_id"))
        message_text = data.get("message", "").strip()
    except (TypeError, ValueError, AttributeError):
        emit("chat_error", {"message": "Invalid message data."})
        return

    if conversation_id <= 0:
        emit("chat_error", {"message": "Invalid conversation ID."})
        return

    if not message_text:
        emit("chat_error", {"message": "Message cannot be empty."})
        return

    if len(message_text) > 2000:
        emit("chat_error", {"message": "Message cannot exceed 2000 characters."})
        return

    user_id = session["user_id"]

    if not get_approved_chat(conversation_id, user_id):
        emit("chat_error", {"message": "Chat unavailable or access denied."})
        return

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO messages
                    (conversation_id, sender_id, message_text)
                VALUES (%s, %s, %s)
                """,
                (conversation_id, user_id, message_text),
            )
            message_id = cursor.lastrowid

            cursor.execute(
                "SELECT full_name FROM users WHERE user_id = %s",
                (user_id,),
            )
            sender = cursor.fetchone()

        connection.commit()

        message_data = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "sender_id": user_id,
            "sender_name": sender[0] if sender else "User",
            "message": message_text,
            "image_url": None,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        socketio.emit(
            "new_message",
            message_data,
            to=f"chat_{conversation_id}",
        )
        chat_connection = None
        try:
            chat_connection = get_db_connection()
            with chat_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT donor_id, recipient_id, food_id FROM conversations WHERE conversation_id = %s",
                    (conversation_id,),
                )
                conversation = cursor.fetchone()
            if conversation:
                other_user_id = conversation[1] if int(user_id) == int(conversation[0]) else conversation[0]
                create_notification(
                    other_user_id,
                    "New chat message",
                    f'{message_data["sender_name"]} sent you a message.',
                    "chat_message",
                    url_for("chat_page", conversation_id=conversation_id),
                )
        finally:
            if chat_connection:
                chat_connection.close()

    except Exception:
        if connection:
            connection.rollback()

        app.logger.exception("Sending chat message failed")
        emit("chat_error", {"message": "Message could not be sent."})

    finally:
        if connection:
            connection.close()


# ==================================================
# UPLOAD IMAGE TO AN APPROVED CHAT
# ==================================================

@app.route("/chat/<int:conversation_id>/upload-image", methods=["POST"])
def upload_chat_image(conversation_id):
    if not session.get("user_id"):
        return jsonify(success=False, message="Please log in again."), 401

    user_id = session["user_id"]

    if not get_approved_chat(conversation_id, user_id):
        return jsonify(success=False, message="Chat access denied."), 403

    message_text = request.form.get("message", "").strip()

    if len(message_text) > 2000:
        return jsonify(success=False, message="Message is too long."), 400

    uploaded_file = request.files.get("image")

    if not uploaded_file or not uploaded_file.filename:
        return jsonify(success=False, message="Please select an image."), 400

    max_size = 5 * 1024 * 1024
    image_bytes = uploaded_file.stream.read(max_size + 1)

    if not image_bytes:
        return jsonify(success=False, message="The image is empty."), 400

    if len(image_bytes) > max_size:
        return jsonify(success=False, message="Image must be 5 MB or smaller."), 413

    formats = {
        "JPEG": ("JPEG", ".jpg"),
        "PNG": ("PNG", ".png"),
        "WEBP": ("WEBP", ".webp"),
    }

    safe_filename = None
    destination = None

    try:
        # Verify the actual image content, not its extension.
        with Image.open(BytesIO(image_bytes)) as img:
            img.verify()

        with Image.open(BytesIO(image_bytes)) as img:
            if img.format not in formats:
                return jsonify(
                    success=False,
                    message="Only JPG, PNG, and WebP images are allowed.",
                ), 400

            save_format, extension = formats[img.format]
            img.load()

            if save_format == "JPEG":
                img = img.convert("RGB")
            elif save_format == "PNG" and img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")

            safe_filename = f"{uuid.uuid4().hex}{extension}"

            upload_dir = os.path.join(
                app.root_path, "static", "uploads", "chat_images"
            )
            os.makedirs(upload_dir, exist_ok=True)
            destination = os.path.join(upload_dir, safe_filename)

            img.save(destination, format=save_format)

    except (UnidentifiedImageError, OSError, ValueError):
        return jsonify(
            success=False,
            message="The uploaded file is not a valid supported image.",
        ), 400

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO messages
                    (conversation_id, sender_id, message_text, image_filename)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    conversation_id,
                    user_id,
                    message_text,
                    safe_filename,
                ),
            )
            message_id = cursor.lastrowid

            cursor.execute(
                "SELECT full_name FROM users WHERE user_id = %s",
                (user_id,),
            )
            sender = cursor.fetchone()

        connection.commit()

        message_data = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "sender_id": user_id,
            "sender_name": sender[0] if sender else "User",
            "message": message_text,
            "image_url": url_for(
                "static",
                filename=f"uploads/chat_images/{safe_filename}",
            ),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        socketio.emit(
            "new_message",
            message_data,
            to=f"chat_{conversation_id}",
        )
        chat_connection = None
        try:
            chat_connection = get_db_connection()
            with chat_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT donor_id, recipient_id FROM conversations WHERE conversation_id = %s",
                    (conversation_id,),
                )
                conversation = cursor.fetchone()
            if conversation:
                other_user_id = conversation[1] if int(user_id) == int(conversation[0]) else conversation[0]
                create_notification(
                    other_user_id,
                    "New chat image",
                    f'{message_data["sender_name"]} shared an image with you.',
                    "chat_message",
                    url_for("chat_page", conversation_id=conversation_id),
                )
        finally:
            if chat_connection:
                chat_connection.close()

        return jsonify(success=True, message=message_data), 201

    except Exception:
        if connection:
            connection.rollback()

        app.logger.exception("Uploading chat image failed")

        if destination and os.path.exists(destination):
            try:
                os.remove(destination)
            except OSError:
                app.logger.exception("Image cleanup failed")

        return jsonify(
            success=False,
            message="Image upload failed. Check the PyCharm terminal.",
        ), 500

    finally:
        if connection:
            connection.close()


# ==================================================
# MY CHATS
# ==================================================

@app.route("/my-chats")
def my_chats():
    if not login_required():
        return redirect(url_for("login"))

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.conversation_id,
                    c.food_id,
                    fl.food_name,
                    CASE
                        WHEN c.donor_id = %s
                        THEN recipient.full_name
                        ELSE donor.full_name
                    END AS other_user_name,
                    (
                        SELECT m.message_text
                        FROM messages AS m
                        WHERE m.conversation_id = c.conversation_id
                        ORDER BY m.created_at DESC, m.message_id DESC
                        LIMIT 1
                    ) AS last_message,
                    (
                        SELECT m.created_at
                        FROM messages AS m
                        WHERE m.conversation_id = c.conversation_id
                        ORDER BY m.created_at DESC, m.message_id DESC
                        LIMIT 1
                    ) AS last_message_at
                FROM conversations AS c
                JOIN food_listings AS fl
                    ON fl.food_id = c.food_id
                JOIN users AS donor
                    ON donor.user_id = c.donor_id
                JOIN users AS recipient
                    ON recipient.user_id = c.recipient_id
                JOIN food_requests AS fr
                    ON fr.food_id = c.food_id
                    AND fr.recipient_id = c.recipient_id
                WHERE
                    (c.donor_id = %s OR c.recipient_id = %s)
                    AND fr.status IN ('approved', 'completed')
                ORDER BY c.conversation_id DESC
                """,
                (
                    session["user_id"],
                    session["user_id"],
                    session["user_id"],
                ),
            )

            conversations = cursor.fetchall()

        return render_template(
            "my_chats.html",
            conversations=conversations,
        )

    except Exception:
        app.logger.exception("Loading My Chats failed")
        return "Unable to load chats. Check the PyCharm terminal.", 500

    finally:
        if connection:
            connection.close()


# ==================================================
# OPEN INDIVIDUAL CHAT
# ==================================================

@app.route("/chat/<int:conversation_id>")
def chat_page(conversation_id):
    if not login_required():
        return redirect(url_for("login"))

    user_id = session["user_id"]

    if not get_approved_chat(conversation_id, user_id):
        return "Chat not found or access denied.", 403

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.conversation_id,
                    c.food_id,
                    fl.food_name,
                    c.donor_id,
                    c.recipient_id,
                    donor.full_name AS donor_name,
                    recipient.full_name AS recipient_name,
                    CASE
                        WHEN c.donor_id = %s
                        THEN recipient.full_name
                        ELSE donor.full_name
                    END AS other_user_name
                FROM conversations AS c
                JOIN food_listings AS fl
                    ON fl.food_id = c.food_id
                JOIN users AS donor
                    ON donor.user_id = c.donor_id
                JOIN users AS recipient
                    ON recipient.user_id = c.recipient_id
                WHERE c.conversation_id = %s
                """,
                (user_id, conversation_id),
            )

            chat_info = cursor.fetchone()

            cursor.execute(
                """
                SELECT
                    m.message_id,
                    m.sender_id,
                    u.full_name,
                    m.message_text,
                    m.created_at,
                    m.image_filename
                FROM messages AS m
                JOIN users AS u
                    ON u.user_id = m.sender_id
                WHERE m.conversation_id = %s
                ORDER BY m.created_at, m.message_id
                """,
                (conversation_id,),
            )

            message_rows = cursor.fetchall()

        messages = []

        for row in message_rows:
            message = list(row)

            if message[5]:
                message[5] = url_for(
                    "static",
                    filename=f"uploads/chat_images/{message[5]}",
                )

            messages.append(tuple(message))

        return render_template(
            "chat.html",
            chat=chat_info,
            messages=messages,
            current_user_id=user_id,
        )

    except Exception:
        app.logger.exception("Opening chat failed")
        return "Could not open chat. Check the PyCharm terminal.", 500

    finally:
        if connection:
            connection.close()




# ==================================================
# FORGOT PASSWORD - OTP HELPERS
# ==================================================

def hash_reset_otp(otp):
    """HMAC the OTP so a database leak alone does not reveal short OTPs."""
    secret_key = app.config.get("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("SECRET_KEY is not configured")
    return hmac.new(
        secret_key.encode("utf-8"),
        otp.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def send_password_reset_otp(recipient_email, otp):
    """Send a password-reset OTP using the SMTP settings in .env."""
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM_EMAIL") or username
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() in ("1", "true", "yes")

    if not all((host, username, password, sender, recipient_email)):
        raise RuntimeError("SMTP settings are missing from the environment")

    message = EmailMessage()
    message["Subject"] = "FoodShare Password Reset OTP"
    message["From"] = sender
    message["To"] = recipient_email
    message.set_content(
        "Hello,\n\n"
        f"Your FoodShare password reset OTP is: {otp}\n\n"
        "This code expires in 5 minutes. Do not share it with anyone.\n"
        "If you did not request a password reset, you can ignore this email.\n\n"
        "FoodShare Team"
    )

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(username, password)
        smtp.send_message(message)


# ==================================================
# 1. REQUEST PASSWORD RESET OTP
# ==================================================

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")

    email = request.form.get("email", "").strip().lower()

    if not email or len(email) > 254:
        flash("Please enter a valid email address.", "error")
        return render_template("forgot_password.html")

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id FROM users WHERE email = %s",
                (email,),
            )
            user = cursor.fetchone()

            # Do not reveal whether an email is registered.
            if not user:
                flash(
                    "If this email is registered, a reset code will be sent.",
                    "info",
                )
                return render_template("forgot_password.html")

            cursor.execute(
                """
                SELECT last_sent_at
                FROM password_reset_otps
                WHERE email = %s
                """,
                (email,),
            )
            previous = cursor.fetchone()

            # Limit resending to once every 60 seconds.
            if previous:
                cursor.execute(
                    """
                    SELECT TIMESTAMPDIFF(
                        SECOND, last_sent_at, NOW()
                    )
                    FROM password_reset_otps
                    WHERE email = %s
                    """,
                    (email,),
                )
                elapsed_row = cursor.fetchone()
                elapsed_seconds = int(elapsed_row[0] or 0)

                if elapsed_seconds < 60:
                    flash(
                        "Please wait before requesting another OTP.",
                        "warning",
                    )
                    return render_template("forgot_password.html")

            otp = f"{secrets.randbelow(1_000_000):06d}"
            otp_hash = hash_reset_otp(otp)

            cursor.execute(
                """
                INSERT INTO password_reset_otps
                    (email, otp_hash, expires_at, attempts,
                     last_sent_at, verified_at, created_at)
                VALUES
                    (%s, %s, DATE_ADD(NOW(), INTERVAL 5 MINUTE),
                     0, NOW(), NULL, NOW())
                ON DUPLICATE KEY UPDATE
                    otp_hash = VALUES(otp_hash),
                    expires_at = VALUES(expires_at),
                    attempts = 0,
                    last_sent_at = NOW(),
                    verified_at = NULL,
                    created_at = NOW()
                """,
                (email, otp_hash),
            )

        connection.commit()

        try:
            send_password_reset_otp(email, otp)
        except Exception:
            app.logger.exception("Password reset email failed")

            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM password_reset_otps WHERE email = %s",
                    (email,),
                )
            connection.commit()

            flash(
                "Unable to send the email right now. Please try again later.",
                "error",
            )
            return render_template("forgot_password.html")

        session["password_reset_email"] = email

        flash("If this email is registered, a reset code will be sent.", "info")
        return redirect(url_for("verify_reset_otp"))

    except Exception:
        if connection:
            connection.rollback()

        app.logger.exception("Forgot password request failed")
        flash("Something went wrong. Please try again.", "error")
        return render_template("forgot_password.html")

    finally:
        if connection:
            connection.close()


# ==================================================
# 2. VERIFY OTP
# ==================================================

@app.route("/verify-reset-otp", methods=["GET", "POST"])
def verify_reset_otp():
    email = session.get("password_reset_email")

    if not email:
        flash("Please request a password reset first.", "warning")
        return redirect(url_for("forgot_password"))

    if request.method == "GET":
        return render_template("verify_otp.html", email=email)

    otp = request.form.get("otp", "").strip()

    if len(otp) != 6 or not otp.isdigit():
        flash("Enter the 6-digit OTP.", "error")
        return render_template("verify_otp.html", email=email)

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT otp_hash, expires_at, attempts, verified_at
                FROM password_reset_otps
                WHERE email = %s
                """,
                (email,),
            )
            reset_record = cursor.fetchone()

            if not reset_record:
                session.pop("password_reset_email", None)
                flash("Reset request not found. Request a new OTP.", "error")
                return redirect(url_for("forgot_password"))

            stored_hash, expires_at, attempts, verified_at = reset_record

            cursor.execute(
                """
                SELECT expires_at > NOW()
                FROM password_reset_otps
                WHERE email = %s
                """,
                (email,),
            )
            expiry_row = cursor.fetchone()
            is_valid_time = bool(expiry_row and expiry_row[0])

            if not is_valid_time:
                cursor.execute(
                    "DELETE FROM password_reset_otps WHERE email = %s",
                    (email,),
                )
                connection.commit()
                session.pop("password_reset_email", None)
                flash("OTP expired. Please request a new one.", "error")
                return redirect(url_for("forgot_password"))

            if verified_at is not None:
                return redirect(url_for("reset_password"))

            if int(attempts) >= 5:
                cursor.execute(
                    "DELETE FROM password_reset_otps WHERE email = %s",
                    (email,),
                )
                connection.commit()
                session.pop("password_reset_email", None)
                flash("Too many attempts. Please request a new OTP.", "error")
                return redirect(url_for("forgot_password"))

            if not secrets.compare_digest(
                str(stored_hash),
                hash_reset_otp(otp),
            ):
                cursor.execute(
                    """
                    UPDATE password_reset_otps
                    SET attempts = attempts + 1
                    WHERE email = %s
                    """,
                    (email,),
                )
                connection.commit()
                flash("Incorrect OTP. Please try again.", "error")
                return render_template("verify_otp.html", email=email)

            cursor.execute(
                """
                UPDATE password_reset_otps
                SET verified_at = NOW()
                WHERE email = %s
                """,
                (email,),
            )

        connection.commit()
        return redirect(url_for("reset_password"))

    except Exception:
        if connection:
            connection.rollback()

        app.logger.exception("OTP verification failed")
        flash("Could not verify the OTP. Please try again.", "error")
        return render_template("verify_otp.html", email=email)

    finally:
        if connection:
            connection.close()


# ==================================================
# 3. SET A NEW PASSWORD
# ==================================================

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    email = session.get("password_reset_email")

    if not email:
        flash("Please verify your OTP first.", "warning")
        return redirect(url_for("forgot_password"))

    connection = None

    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT verified_at, expires_at > NOW()
                FROM password_reset_otps
                WHERE email = %s
                """,
                (email,),
            )
            reset_record = cursor.fetchone()

        if (
            not reset_record
            or reset_record[0] is None
            or not bool(reset_record[1])
        ):
            session.pop("password_reset_email", None)
            flash("OTP expired or not verified. Please start again.", "error")
            return redirect(url_for("forgot_password"))

        if request.method == "GET":
            return render_template("reset_password.html")

        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("reset_password.html")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html")

        password_hash = generate_password_hash(password)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET password_hash = %s
                WHERE email = %s
                """,
                (password_hash, email),
            )

            if cursor.rowcount != 1:
                connection.rollback()
                flash("Unable to update the password.", "error")
                return redirect(url_for("forgot_password"))

            # Remove the OTP so it cannot be reused.
            cursor.execute(
                "DELETE FROM password_reset_otps WHERE email = %s",
                (email,),
            )

        connection.commit()
        session.pop("password_reset_email", None)

        flash("Password changed successfully. Please log in.", "success")
        return redirect(url_for("login"))

    except Exception:
        if connection:
            connection.rollback()

        app.logger.exception("Password reset failed")
        flash("Unable to reset the password. Please try again.", "error")
        return redirect(url_for("forgot_password"))

    finally:
        if connection:
            connection.close()

# ==================================================
# 17. RUN APPLICATION
# ==================================================

if __name__ == "__main__":
    # Start one background expiry checker per development-server process.
    socketio.start_background_task(expiry_alert_worker)
    socketio.run(app, debug=True)
