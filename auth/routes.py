from flask import render_template, request, session, jsonify, redirect, url_for,flash,Blueprint
from models import validate_user,get_connection
from werkzeug.security import generate_password_hash, check_password_hash
from models.email_utils import send_password_reset_email, send_password_reset_success_email

from flask import current_app



import secrets, datetime

auth_bp = Blueprint('auth', __name__, template_folder="templates")


@auth_bp.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        if request.is_json:
            data = request.get_json()
            email = data.get('email')
            password = data.get('password')
        else:
            email = request.form.get('email')
            password = request.form.get('password')

        user = validate_user(email, password)
        if user:
            session['user_id'] = user['user_id']
            session['user_role'] = user['role']
            session['user_name'] = user['full_name']
            session['user_email'] = user['email'] 

            if request.is_json:
                return jsonify({"status": "success", "message": "Login successful!", "role": user['role']})
            else:
                # redirect based on role (admin, trainer, student, etc.)
                return redirect(url_for(f"{user['role']}.dashboard"))

        # Invalid login
        if request.is_json:
            return jsonify({"status": "error", "message": "Invalid email or password"})
        else:
            return render_template("login.html", error="Invalid email or password")

    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()  # Clear all session data
    return redirect(url_for('auth.login'))




@auth_bp.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        flash("Please login first.", "warning")
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not current_password or not new_password or not confirm_password:
            flash("All fields are required.", "danger")
            return redirect(url_for('auth.change_password'))

        if new_password != confirm_password:
            flash("New password and confirm password do not match.", "danger")
            return redirect(url_for('auth.change_password'))

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT password_hash FROM users WHERE user_id=%s", (session['user_id'],))
        user = cursor.fetchone()

        if not user or not check_password_hash(user['password_hash'], current_password):
            flash("Current password is incorrect.", "change_password-danger")
            conn.close()
            return redirect(url_for('auth.change_password'))

        # Update password
        hashed = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password_hash=%s, is_password_changed=1 WHERE user_id=%s", 
                       (hashed, session['user_id']))
        conn.commit()
        conn.close()
        session['is_password_changed'] = 1
        flash("Password changed successfully!", "change_password-success")
        return redirect(url_for('admin.admin_dashboard'))  # redirect to dashboard after change

    return render_template('change_password.html')

@auth_bp.route('/register_admin', methods=['GET', 'POST'])
def register_admin():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')

        if not email or not password or not name:
            flash("All fields are required.", "danger")
            return redirect(url_for('auth.register_admin'))

        pw_hash = generate_password_hash(password)

        conn = get_connection()
        cursor = conn.cursor()

        # 1. Insert into users
        cursor.execute("INSERT INTO users (email, password_hash, full_name, role) VALUES (%s, %s, %s, 'admin')", (email, pw_hash, name))
        user_id = cursor.lastrowid

        # 2. Insert into admins
        cursor.execute("INSERT INTO admins (user_id) VALUES (%s)", (user_id,))

        conn.commit()
        conn.close()

        flash("Admin registered successfully!", "success")
        return redirect(url_for('auth.login'))

    return render_template('register_admin.html')

@auth_bp.route('/register_student', methods=['GET', 'POST'])
def register_student():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        admission_no = request.form.get('admission_no')

        if not email or not password or not name or not admission_no:
            flash("All fields are required.", "danger")
            return redirect(url_for('auth.register_student'))

        pw_hash = generate_password_hash(password)

        conn = get_connection()
        cursor = conn.cursor()

        # 1. Insert into users
        cursor.execute("INSERT INTO users (email, password_hash, full_name, role) VALUES (%s, %s, %s, 'student')", (email, pw_hash, name))
        user_id = cursor.lastrowid

        # 2. Insert into students
        cursor.execute("INSERT INTO students (user_id, full_name, admission_no) VALUES (%s, %s, %s)", (user_id, name, admission_no))

        conn.commit()
        conn.close()

        flash("Student registered successfully!", "success")
        return redirect(url_for('auth.login'))

    return render_template('register_student.html')


@auth_bp.route('/register_trainer', methods=['GET', 'POST'])
def register_trainer():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')

        if not email or not password or not name:
            flash("All fields are required.", "danger")
            return redirect(url_for('auth.register_trainer'))

        pw_hash = generate_password_hash(password)

        conn = get_connection()
        cursor = conn.cursor()

        # 1. Insert into users
        cursor.execute("INSERT INTO users (email, password_hash, full_name, role) VALUES (%s, %s, %s, 'trainer')", (email, pw_hash, name))
        user_id = cursor.lastrowid

        # 2. Insert into trainers
        cursor.execute("INSERT INTO trainers (user_id) VALUES (%s)", (user_id,))

        conn.commit()
        conn.close()

        flash("Trainer registered successfully!", "success")
        return redirect(url_for('auth.login'))

    return render_template('register_trainer.html')


@auth_bp.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    from app import mail
    if request.method == 'GET':
        return render_template("forgot_password.html")

    email = request.form.get('email')
    if not email:
        return render_template("forgot_password.html", error="Email is required.")

    # Generate reset token and expiry
    token = secrets.token_urlsafe(32)
    expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=1)

    # Set token in DB and get user info
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE email=%s", (email,))
    user = cursor.fetchone()
    if not user:
        cursor.close()
        conn.close()
        return render_template("forgot_password.html", error="Email not found.")

    cursor.execute("UPDATE users SET reset_token=%s, reset_expiry=%s WHERE email=%s",
                   (token, expiry, email))
    conn.commit()
    cursor.close()
    conn.close()

    # Send reset email
    reset_link = url_for('auth.reset_password', token=token, _external=True)
    send_password_reset_email(
        full_name=user['full_name'],
        email=email,
        reset_link=reset_link,
        app=current_app._get_current_object(),
        mail=mail
    )

    return render_template("forgot_password.html", success="Password reset link has been sent to your email.")

@auth_bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    from app import mail
    if request.method == 'GET':
        return render_template("reset_password.html", token=token)

    new_password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')

    if not new_password or not confirm_password:
        return render_template("reset_password.html", token=token, error="Both password fields are required.")

    if new_password != confirm_password:
        return render_template("reset_password.html", token=token, error="Passwords do not match.")

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE reset_token=%s", (token,))
    user = cursor.fetchone()
    if not user:
        cursor.close()
        conn.close()
        return render_template("reset_password.html", token=token, error="Invalid or expired token.")

    if user['reset_expiry'] < datetime.datetime.utcnow():
        cursor.close()
        conn.close()
        return render_template("reset_password.html", token=token, error="Token expired.")

    # Update password
    hashed_password = generate_password_hash(new_password)
    cursor.execute("UPDATE users SET password_hash=%s, reset_token=NULL, reset_expiry=NULL WHERE user_id=%s",
                   (hashed_password, user['user_id']))
    conn.commit()
    cursor.close()
    conn.close()

    # Send reset success email
    send_password_reset_success_email(
        full_name=user['full_name'],
        email=user['email'],
        app=current_app._get_current_object(),
        mail=mail
    )

    return render_template("reset_success.html", success="Your password has been reset successfully!")
