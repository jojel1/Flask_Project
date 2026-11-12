
import os
import hmac
from datetime import datetime, timezone, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
)
from flask_wtf.csrf import CSRFProtect
from supabase import create_client

load_dotenv()

# ==================================================
# 1. FLASK CONFIGURATION
# ==================================================

app = Flask(__name__)

app.config.update(
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY", ""),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.getenv("COOKIE_SECURE", "false").lower() == "true"
    ),
)

if not app.config["SECRET_KEY"]:
    raise RuntimeError("Set FLASK_SECRET_KEY in your .env file.")

csrf = CSRFProtect(app)

# ==================================================
# 2. ENVIRONMENT VARIABLES AND SUPABASE
# ==================================================

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()

DEVICE_ID = os.getenv("DEVICE_ID", "pig-feeder-01").strip()
DEVICE_TOKEN = os.getenv("DEVICE_TOKEN", "")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

if not all([
    SUPABASE_URL,
    SUPABASE_KEY,
    DEVICE_TOKEN,
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
]):
    raise RuntimeError(
        "Check SUPABASE_URL, SUPABASE_SECRET_KEY, DEVICE_TOKEN, "
        "ADMIN_USERNAME, ADMIN_PASSWORD, and FLASK_SECRET_KEY in .env."
    )

db = create_client(SUPABASE_URL, SUPABASE_KEY)

PORT = 5050

# Exact table name from your Supabase screenshot.
HISTORY_TABLE = "Historical Pig Feeding Records"

# ==================================================
# 3. AUTHENTICATION HELPERS
# ==================================================

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify(
                    success=False,
                    error="Login required. Please log in again.",
                ), 401

            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped

def device_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        expected = "Bearer " + DEVICE_TOKEN

        if not hmac.compare_digest(header, expected):
            return jsonify(error="Unauthorized device"), 401

        return view(*args, **kwargs)

    return wrapped

# ==================================================
# 4. LIVE TELEMETRY HELPER
# ==================================================

def latest_telemetry():
    result = (
        db.table("telemetry")
        .select("*")
        .eq("device_id", DEVICE_ID)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    return result.data[0] if result.data else None

# ==================================================
# 5. HEALTH CHECK
# ==================================================

@app.get("/health")
def health():
    return jsonify(
        status="ok",
        port=PORT,
    )

# ==================================================
# 6. LOGIN AND LOGOUT
# ==================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        valid_user = hmac.compare_digest(
            username, ADMIN_USERNAME
        )
        valid_password = hmac.compare_digest(
            password, ADMIN_PASSWORD
        )

        if valid_user and valid_password:
            session.clear()
            session["logged_in"] = True
            session["username"] = username

            return redirect(url_for("dashboard"))

        flash("Invalid username or password.")

    return render_template("login.html")

@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/")
def home():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))

@app.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")

# ==================================================
# 7. LIVE TELEMETRY API
# ==================================================

@app.get("/api/telemetry/latest")
@login_required
def telemetry_latest():
    try:
        return jsonify(latest_telemetry())

    except Exception:
        app.logger.exception("Unable to retrieve telemetry")

        return jsonify(
            error="Unable to retrieve telemetry"
        ), 500

@app.post("/api/telemetry")
@csrf.exempt
@device_required
def telemetry():
    data = request.get_json(silent=True) or {}

    def number(name, low, high):
        value = data.get(name)

        if value is None:
            return None

        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            raise ValueError(name)

        if not low <= value <= high:
            raise ValueError(name)

        return float(value)

    try:
        record = {
            "device_id": DEVICE_ID,
            "temperature_c": number(
                "temperature_c", -40, 80
            ),
            "humidity_pct": number(
                "humidity_pct", 0, 100
            ),
            "pig_weight_kg": number(
                "pig_weight_kg", 0, 1000
            ),
            "weigh_bin_weight_g": number(
                "weigh_bin_weight_g", 0, 10000
            ),
            "target_feed_g": number(
                "target_feed_g", 0, 5000
            ),
            "feeder_status": str(
                data.get("feeder_status", "unknown")
            )[:50],
        }

    except ValueError:
        return jsonify(
            error="Invalid sensor value"
        ), 400

    try:
        db.table("telemetry").insert(record).execute()

        return jsonify(ok=True), 201

    except Exception:
        app.logger.exception("Unable to save telemetry")

        return jsonify(
            error="Unable to save telemetry"
        ), 500

# ==================================================
# 8. HISTORICAL PIG FEEDING RECORDS
# ==================================================

@app.get("/api/feeding/recovered")
@login_required
def recovered_feeding_history():

    try:
        response = (
            db.table(HISTORY_TABLE)
            .select(
                "device_id,"
                "feeding_date,"
                "feeding_time,"
                "pig_weight_kg,"
                "weight_range_category,"
                "feed_amount_kg,"
                "record_source"
            )

            # Keep records with valid dates and feeding times.
            # Do not exclude records with NULL measurements.
            .not_.is_("feeding_date", "null")
            .not_.is_("feeding_time", "null")

            # Display newest feeding sessions first.
            .order("feeding_date", desc=True)
            .order("feeding_time", desc=True)

            # Retrieve up to 500 historical records.
            .limit(500)
            .execute()
        )

        records = response.data or []

        for record in records:

            # Convert date into a string for JavaScript.
            if record.get("feeding_date") is not None:
                record["feeding_date"] = str(
                    record["feeding_date"]
                )

            # Use NOT RECORDED if the result/source is missing.
            record["result"] = (
                record.get("record_source") or "NOT RECORDED"
            )

        return jsonify({
            "success": True,
            "count": len(records),
            "records": records,
        }), 200

    except Exception:
        app.logger.exception(
            "Unable to retrieve historical feeding records"
        )

        return jsonify({
            "success": False,
            "error": (
                "Unable to retrieve historical records. "
                "Check the Flask terminal for details."
            ),
            "records": [],
        }), 500

# ==================================================
# 9. CREATE MANUAL FEEDING COMMAND
# ==================================================

@app.post("/api/commands")
@login_required
def create_command():
    data = request.get_json(silent=True) or {}
    grams = data.get("target_grams")

    if data.get("command") != "dispense":
        return jsonify(
            error="Unsupported command"
        ), 400

    if (
        isinstance(grams, bool)
        or not isinstance(grams, (int, float))
        or not 1 <= grams <= 5000
    ):
        return jsonify(
            error="Invalid feed amount"
        ), 400

    try:
        active = (
            db.table("feeder_commands")
            .select("id")
            .eq("device_id", DEVICE_ID)
            .in_("status", ["pending", "running"])
            .limit(1)
            .execute()
        )

        if active.data:
            return jsonify(
                error="A feeding request is already active"
            ), 409

        now = datetime.now(timezone.utc)

        record = {
            "device_id": DEVICE_ID,
            "command": "dispense",
            "target_grams": float(grams),
            "requested_by": session.get(
                "username", ADMIN_USERNAME
            ),
            "status": "pending",
            "requested_at": now.isoformat(),
            "expires_at": (
                now + timedelta(minutes=10)
            ).isoformat(),
        }

        result = (
            db.table("feeder_commands")
            .insert(record)
            .execute()
        )

        if not result.data:
            return jsonify(
                error="Unable to queue request"
            ), 500

        return jsonify(
            id=result.data[0]["id"],
            status="pending",
            message="Feeding command queued successfully",
        ), 201

    except Exception:
        app.logger.exception(
            "Unable to create feeding command"
        )

        return jsonify(
            error="Unable to create feeding command"
        ), 500

# ==================================================
# 10. RECENT FEEDING COMMANDS
# ==================================================

@app.get("/api/commands/recent")
@login_required
def recent_commands():
    try:
        result = (
            db.table("feeder_commands")
            .select(
                "id,target_grams,status,result,"
                "requested_at,completed_at"
            )
            .eq("device_id", DEVICE_ID)
            .order("requested_at", desc=True)
            .limit(20)
            .execute()
        )

        return jsonify(result.data or [])

    except Exception:
        app.logger.exception(
            "Unable to retrieve recent commands"
        )

        return jsonify(
            error="Unable to retrieve recent commands"
        ), 500

# ==================================================
# 11. ESP32 REQUESTS NEXT FEEDING COMMAND
# ==================================================

@app.get("/api/device/commands/next")
@csrf.exempt
@device_required
def next_command():
    try:
        now = datetime.now(timezone.utc).isoformat()

        result = (
            db.table("feeder_commands")
            .select("*")
            .eq("device_id", DEVICE_ID)
            .eq("status", "pending")
            .gt("expires_at", now)
            .order("requested_at")
            .limit(1)
            .execute()
        )

        if not result.data:
            return jsonify(command=None)

        command = result.data[0]

        updated = (
            db.table("feeder_commands")
            .update({"status": "running"})
            .eq("id", command["id"])
            .eq("device_id", DEVICE_ID)
            .eq("status", "pending")
            .execute()
        )

        if not updated.data:
            return jsonify(command=None)

        return jsonify(command={
            "id": command["id"],
            "command": command["command"],
            "target_grams": command["target_grams"],
        })

    except Exception:
        app.logger.exception(
            "Unable to retrieve next feeding command"
        )

        return jsonify(
            error="Unable to retrieve command"
        ), 500

# ==================================================
# 12. ESP32 REPORTS COMMAND RESULT
# ==================================================

@app.post("/api/device/commands/<int:command_id>/result")
@csrf.exempt
@device_required
def command_result(command_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")

    if status not in ["completed", "failed"]:
        return jsonify(
            error="Invalid result status"
        ), 400

    try:
        result = (
            db.table("feeder_commands")
            .update({
                "status": status,
                "result": str(
                    data.get("result", "")
                )[:500],
                "completed_at": (
                    datetime.now(timezone.utc).isoformat()
                ),
            })
            .eq("id", command_id)
            .eq("device_id", DEVICE_ID)
            .eq("status", "running")
            .execute()
        )

        if not result.data:
            return jsonify(
                error="Command not found or not running"
            ), 404

        return jsonify(ok=True)

    except Exception:
        app.logger.exception(
            "Unable to update command result"
        )

        return jsonify(
            error="Unable to update command result"
        ), 500

# ==================================================
# 13. START FLASK ON PORT 5050
# ==================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5050,
        debug=os.getenv(
            "FLASK_DEBUG", "false"
        ).lower() == "true",
    )