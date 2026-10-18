from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import sqlite3
from pathlib import Path
from algorithms import schedule_patients, bankers_algorithm

app = Flask(__name__)
app.secret_key = "hospitalos-educational-demo-key"
DB_PATH = Path(__file__).with_name("hospitalos.db")
SPECIALIZATIONS = ["General Medicine", "Cardiology", "Neurology", "Orthopedics", "Pediatrics", "Emergency"]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(conn, table_name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone() is not None


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                arrival_time INTEGER NOT NULL,
                burst_time INTEGER NOT NULL,
                priority INTEGER NOT NULL DEFAULT 3,
                status TEXT NOT NULL DEFAULT 'Waiting'
            )
        """)
        doctors_table_was_missing = not table_exists(conn, "doctors")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS doctors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                specialization TEXT NOT NULL,
                max_patients INTEGER NOT NULL DEFAULT 5,
                on_duty INTEGER NOT NULL DEFAULT 1
            )
        """)
        patient_columns = {row[1] for row in conn.execute("PRAGMA table_info(patients)").fetchall()}
        if "specialization" not in patient_columns:
            conn.execute("ALTER TABLE patients ADD COLUMN specialization TEXT NOT NULL DEFAULT 'General Medicine'")
        if "doctor_id" not in patient_columns:
            conn.execute("ALTER TABLE patients ADD COLUMN doctor_id INTEGER REFERENCES doctors(id)")
        if doctors_table_was_missing:
            conn.executemany(
                "INSERT INTO doctors (name, specialization, max_patients, on_duty) VALUES (?, ?, ?, 1)",
                [("Dr. Arjun", "General Medicine", 5), ("Dr. Meera", "Cardiology", 4), ("Dr. Kavya", "Neurology", 3)],
            )


def active_patient_count(conn, doctor_id):
    return conn.execute(
        "SELECT COUNT(*) FROM patients WHERE doctor_id=? AND status!='Completed'", (doctor_id,)
    ).fetchone()[0]


def doctor_status(conn, doctor):
    if not doctor["on_duty"]:
        return "Off duty"
    active = active_patient_count(conn, doctor["id"])
    if active >= doctor["max_patients"]:
        return "At capacity"
    if active:
        return "Busy"
    return "Available"


def assign_doctor(conn, patient_id):
    """Assign the least-loaded on-duty doctor with matching specialization and free capacity."""
    patient = conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
    if not patient or patient["status"] == "Completed":
        return None
    if patient["doctor_id"]:
        current = conn.execute("SELECT * FROM doctors WHERE id=?", (patient["doctor_id"],)).fetchone()
        if current and current["on_duty"] and active_patient_count(conn, current["id"]) <= current["max_patients"]:
            return current["id"]
        conn.execute("UPDATE patients SET doctor_id=NULL WHERE id=?", (patient_id,))

    candidates = conn.execute(
        "SELECT * FROM doctors WHERE on_duty=1 AND specialization=? ORDER BY id",
        (patient["specialization"],),
    ).fetchall()
    eligible = []
    for doctor in candidates:
        load = active_patient_count(conn, doctor["id"])
        if load < doctor["max_patients"]:
            eligible.append((load, doctor["id"]))
    if not eligible:
        return None
    _, selected_id = min(eligible, key=lambda item: (item[0], item[1]))
    conn.execute("UPDATE patients SET doctor_id=? WHERE id=?", (selected_id, patient_id))
    return selected_id


@app.route("/")
def dashboard():
    with get_db() as conn:
        patients = conn.execute("""
            SELECT p.*, d.name AS doctor_name FROM patients p
            LEFT JOIN doctors d ON d.id=p.doctor_id ORDER BY p.id DESC
        """).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        waiting = conn.execute("SELECT COUNT(*) FROM patients WHERE status='Waiting'").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM patients WHERE status='Completed'").fetchone()[0]
        priority_rows = conn.execute("SELECT priority, COUNT(*) AS count FROM patients GROUP BY priority ORDER BY priority").fetchall()
        doctor_count = conn.execute("SELECT COUNT(*) FROM doctors WHERE on_duty=1").fetchone()[0]
        unassigned = conn.execute("SELECT COUNT(*) FROM patients WHERE status!='Completed' AND doctor_id IS NULL").fetchone()[0]
    priority_counts = {str(row["priority"]): row["count"] for row in priority_rows}
    return render_template("index.html", patients=patients, total=total, waiting=waiting,
                           completed=completed, priority_counts=priority_counts,
                           doctor_count=doctor_count, unassigned=unassigned, specializations=SPECIALIZATIONS)


@app.route("/patients", methods=["POST"])
def add_patient():
    name = request.form.get("name", "").strip()
    specialization = request.form.get("specialization", "General Medicine").strip()
    try:
        arrival = int(request.form.get("arrival_time", "0"))
        burst = int(request.form.get("burst_time", "1"))
        priority = int(request.form.get("priority", "3"))
    except ValueError:
        flash("Arrival time, treatment duration and priority must be whole numbers.", "error")
        return redirect(url_for("dashboard"))
    if not name or arrival < 0 or burst < 1 or priority < 1 or specialization not in SPECIALIZATIONS:
        flash("Enter a name, valid specialization, arrival ≥ 0, duration ≥ 1 and priority ≥ 1.", "error")
        return redirect(url_for("dashboard"))
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization) VALUES (?, ?, ?, ?, 'Waiting', ?)",
            (name, arrival, burst, priority, specialization),
        )
        patient_id = cursor.lastrowid
        doctor_id = assign_doctor(conn, patient_id)
        doctor = conn.execute("SELECT name FROM doctors WHERE id=?", (doctor_id,)).fetchone() if doctor_id else None
    if doctor:
        flash(f"Added {name}. Automatically assigned to {doctor['name']}.", "success")
    else:
        flash(f"Added {name}, but no on-duty {specialization} doctor has capacity. Patient remains unassigned.", "error")
    return redirect(url_for("dashboard"))


@app.route("/patients/<int:patient_id>/complete", methods=["POST"])
def complete_patient(patient_id):
    with get_db() as conn:
        conn.execute("UPDATE patients SET status='Completed' WHERE id=?", (patient_id,))
    flash("Patient marked completed; the doctor's active workload has been updated.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/patients/<int:patient_id>/unassign", methods=["POST"])
def unassign_patient(patient_id):
    with get_db() as conn:
        conn.execute("UPDATE patients SET doctor_id=NULL WHERE id=? AND status!='Completed'", (patient_id,))
    flash("Doctor assignment removed.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/patients/auto-assign", methods=["POST"])
def auto_assign_unassigned():
    assigned = 0
    with get_db() as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM patients WHERE status!='Completed' AND doctor_id IS NULL ORDER BY priority, arrival_time, id").fetchall()]
        for patient_id in ids:
            if assign_doctor(conn, patient_id):
                assigned += 1
    flash(f"Automatic assignment finished: {assigned} patient(s) assigned.", "success" if assigned else "error")
    return redirect(url_for("dashboard"))


@app.route("/doctors", methods=["GET", "POST"])
def doctors():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        specialization = request.form.get("specialization", "").strip()
        try:
            capacity = int(request.form.get("max_patients", "5"))
        except ValueError:
            capacity = 0
        if not name or specialization not in SPECIALIZATIONS or capacity < 1 or capacity > 100:
            flash("Enter a doctor name, valid specialization and capacity between 1 and 100.", "error")
            return redirect(url_for("doctors"))
        with get_db() as conn:
            conn.execute("INSERT INTO doctors (name, specialization, max_patients, on_duty) VALUES (?, ?, ?, 1)",
                         (name, specialization, capacity))
        flash(f"Added {name}. They are now on duty.", "success")
        return redirect(url_for("doctors"))

    with get_db() as conn:
        doctor_rows = conn.execute("SELECT * FROM doctors ORDER BY id").fetchall()
        doctor_cards = []
        for doctor in doctor_rows:
            active = active_patient_count(conn, doctor["id"])
            assigned = conn.execute("SELECT COUNT(*) FROM patients WHERE doctor_id=?", (doctor["id"],)).fetchone()[0]
            doctor_cards.append({"doctor": doctor, "active": active, "assigned": assigned,
                                 "status": doctor_status(conn, doctor),
                                 "patients": conn.execute("SELECT id, name, status FROM patients WHERE doctor_id=? ORDER BY id DESC", (doctor["id"],)).fetchall()})
        active_total = conn.execute("SELECT COUNT(*) FROM patients WHERE status!='Completed'").fetchone()[0]
        unassigned = conn.execute("SELECT COUNT(*) FROM patients WHERE status!='Completed' AND doctor_id IS NULL").fetchone()[0]
        on_duty = conn.execute("SELECT COUNT(*) FROM doctors WHERE on_duty=1").fetchone()[0]
    return render_template("doctors.html", doctor_cards=doctor_cards, specializations=SPECIALIZATIONS,
                           active_total=active_total, unassigned=unassigned, on_duty=on_duty)


@app.route("/doctors/<int:doctor_id>/edit", methods=["POST"])
def edit_doctor(doctor_id):
    name = request.form.get("name", "").strip()
    specialization = request.form.get("specialization", "").strip()
    duty = 1 if request.form.get("on_duty") == "on" else 0
    try:
        capacity = int(request.form.get("max_patients", "5"))
    except ValueError:
        capacity = 0
    if not name or specialization not in SPECIALIZATIONS or capacity < 1 or capacity > 100:
        flash("Enter a valid name, specialization and capacity between 1 and 100.", "error")
        return redirect(url_for("doctors"))
    with get_db() as conn:
        if not conn.execute("SELECT id FROM doctors WHERE id=?", (doctor_id,)).fetchone():
            flash("Doctor not found.", "error")
            return redirect(url_for("doctors"))
        active = active_patient_count(conn, doctor_id)
        if capacity < active:
            flash(f"Capacity cannot be lower than the {active} active patient(s) already assigned.", "error")
            return redirect(url_for("doctors"))
        conn.execute("UPDATE doctors SET name=?, specialization=?, max_patients=?, on_duty=? WHERE id=?",
                     (name, specialization, capacity, duty, doctor_id))
        # If a doctor is taken off duty, unassign their active patients so they can be reassigned safely.
        if not duty:
            conn.execute("UPDATE patients SET doctor_id=NULL WHERE doctor_id=? AND status!='Completed'", (doctor_id,))
    flash("Doctor details updated. Patients of an off-duty doctor have been unassigned.", "success")
    return redirect(url_for("doctors"))


@app.route("/api/schedule", methods=["POST"])
def run_schedule():
    data = request.get_json(silent=True) or {}
    algorithm = data.get("algorithm", "FCFS")
    with get_db() as conn:
        rows = conn.execute("SELECT id, name, arrival_time, burst_time, priority FROM patients WHERE status='Waiting' ORDER BY id").fetchall()
    try:
        result = schedule_patients([dict(row) for row in rows], algorithm)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/bankers", methods=["POST"])
def run_bankers():
    from flask import jsonify
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(bankers_algorithm(data["allocation"], data["maximum"], data["available"]))
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc) or "Invalid input matrices."}), 400


@app.route("/resources")
def resources():
    return render_template("resources.html")


init_db()

if __name__ == "__main__":
    app.run(debug=True)
