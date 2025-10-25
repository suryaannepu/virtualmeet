from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_pymongo import PyMongo
from werkzeug.security import generate_password_hash, check_password_hash
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib, os, uuid, datetime
from dotenv import load_dotenv
from bson.objectid import ObjectId

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

# ---------------- MongoDB Setup ----------------
app.config["MONGO_URI"] = os.getenv("MONGO_URI")
mongo = PyMongo(app)

# ---------------- SMTP Email Setup ----------------
SENDER_EMAIL = os.getenv("EMAIL_SENDER")
SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")

# ---------------- Routes ----------------
@app.route("/")
def index():
    return redirect(url_for("login"))

# ---------------- Signup ----------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "").strip()

        if not all([name, email, password, role]):
            flash("All fields are required.", "danger")
            return redirect(url_for("signup"))

        if mongo.db.users.find_one({"email": email}):
            flash("Email already registered. Please login.", "warning")
            return redirect(url_for("login"))

        hashed_pw = generate_password_hash(password)
        mongo.db.users.insert_one({
            "name": name,
            "email": email,
            "password": hashed_pw,
            "role": role
        })

        flash("Account created successfully! Please login.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")

# ---------------- Login ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not email or not password:
            flash("Please enter both email and password.", "danger")
            return redirect(url_for("login"))

        user = mongo.db.users.find_one({"email": email})
        if user and check_password_hash(user["password"], password):
            session["user_id"] = str(user["_id"])
            session["email"] = user["email"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            flash("Login successful!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

    return render_template("login.html")

# ---------------- Logout ----------------
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

# ---------------- Doctor Form ----------------
@app.route("/doctor-form", methods=["GET", "POST"])
def doctor_form():
    if "email" not in session or session["role"] != "doctor":
        flash("Unauthorized access.", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        specialization = request.form.get("specialization", "").strip()
        experience = request.form.get("experience", "").strip()
        slots = request.form.get("slots", "").strip()

        if not all([specialization, experience, slots]):
            flash("All fields are required.", "danger")
            return redirect(url_for("doctor_form"))

        mongo.db.doctors.update_one(
            {"email": session["email"]},
            {"$set": {
                "name": session["name"],
                "specialization": specialization,
                "experience": experience,
                "slots": [slot.strip() for slot in slots.split(",") if slot.strip()]
            }},
            upsert=True
        )
        flash("Doctor profile saved successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("doctor_form.html")

# ---------------- Dashboard ----------------
@app.route("/dashboard")
def dashboard():
    if "email" not in session:
        return redirect(url_for("login"))

    role = session["role"]
    email = session["email"]

    if role == "doctor":
        doctor_profile = mongo.db.doctors.find_one({"email": email})
        bookings = list(mongo.db.bookings.find({"doctor_email": email}))
        return render_template("dashboard.html", role=role, bookings=bookings, doctor_profile=doctor_profile)
    else:
        doctors = list(mongo.db.doctors.find())
        patient_bookings = list(mongo.db.bookings.find({"patient_email": email}))
        return render_template("dashboard.html", role=role, doctors=doctors, patient_bookings=patient_bookings)

# ---------------- Booking ----------------
@app.route("/book/<doctor_email>", methods=["GET", "POST"])
def book(doctor_email):
    if "email" not in session or session["role"] != "patient":
        flash("Only patients can book appointments.", "danger")
        return redirect(url_for("login"))

    doctor = mongo.db.doctors.find_one({"email": doctor_email})
    if not doctor:
        flash("Doctor not found.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        slot = request.form.get("slot", "").strip()
        if not slot:
            flash("Please select a time slot.", "danger")
            return redirect(url_for("book", doctor_email=doctor_email))

        # Check if slot is already booked
        existing_booking = mongo.db.bookings.find_one({
            "doctor_email": doctor_email,
            "slot": slot
        })
        if existing_booking:
            flash("This time slot is already booked. Please choose another.", "danger")
            return redirect(url_for("book", doctor_email=doctor_email))

        room_name = f"consult-{uuid.uuid4().hex}"
        jitsi_link = f"https://meet.jit.si/{room_name}"
        booking_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        mongo.db.bookings.insert_one({
            "doctor_email": doctor_email,
            "patient_email": session["email"],
            "patient_name": session["name"],
            "doctor_name": doctor["name"],
            "specialization": doctor["specialization"],
            "slot": slot,
            "jitsi_link": jitsi_link,
            "created_at": booking_time,
            "status": "confirmed"
        })

        try:
            send_booking_emails(doctor, session["email"], session["name"], slot, jitsi_link)
            flash("✅ Booking confirmed! Check your email for meeting details.", "success")
        except Exception as e:
            flash("✅ Booking confirmed! But there was an issue sending email notifications.", "warning")
            print(f"Email error: {e}")

        return redirect(url_for("dashboard"))

    return render_template("book.html", doctor=doctor)

# ---------------- Email Notifications ----------------
def send_booking_emails(doctor, patient_email, patient_name, slot, jitsi_link):
    doctor_name = doctor["name"]
    specialization = doctor["specialization"]
    doctor_email = doctor["email"]

    # Email to Patient
    subject_patient = f"Appointment Confirmed with Dr. {doctor_name} ({specialization})"
    body_patient = f"""
Hello {patient_name},

Your appointment with Dr. {doctor_name} ({specialization}) is confirmed.

🕒 Time Slot: {slot}
🔗 Jitsi Meeting Link: {jitsi_link}

Please join the meeting 5 minutes before your scheduled time.

Best Regards,  
Telemedicine Support
"""

    # Email to Doctor
    subject_doctor = f"Upcoming Appointment - {specialization}"
    body_doctor = f"""
Hello Dr. {doctor_name},

You have a new appointment scheduled with a patient.

🕒 Time Slot: {slot}
👤 Patient Name: {patient_name}
📧 Patient Email: {patient_email}
🔗 Jitsi Meeting Link: {jitsi_link}

Please be ready for the consultation at the scheduled time.

Best Regards,  
Telemedicine Support
"""

    send_email(patient_email, subject_patient, body_patient)
    send_email(doctor_email, subject_doctor, body_doctor)

# ---------------- Email Function ----------------
def send_email(to, subject, body):
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("Email credentials not configured. Skipping email send.")
        return

    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        print(f"📧 Email sent to {to}")
    except Exception as e:
        print(f"Failed to send email to {to}: {e}")
        raise e

@app.route("/test-mongo")
def test_mongo():
    try:
        mongo.db.command('ping')
        return "MongoDB connected successfully!"
    except Exception as e:
        return f"MongoDB connection error: {e}"

# ---------------- Run App ----------------
if __name__ == "__main__":
    app.run(debug=True)