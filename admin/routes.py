from flask import render_template,request,session,jsonify,url_for,redirect,current_app,Blueprint,json,send_file
from models import get_connection,create_batch,create_student
from flask import flash
from datetime import date
from models.user_model import generate_user_password
import io
import csv
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import os 
from werkzeug.utils import secure_filename
from flask_mail import Mail
from flask import current_app
import uuid





admin_bp = Blueprint('admin',__name__,template_folder='templates')

# ---------- Helper: check admin ----------
def require_admin():
    if session.get('user_role') != 'admin':
        return False
    return True

@admin_bp.route('/dashboard')
def admin_dashboard():
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    admin_id = session.get('user_id')

   # ✅ Total students (all active students linked to this admin’s courses via course_admins)
    cursor.execute("""
    SELECT COUNT(DISTINCT s.student_id) AS total
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    JOIN batches b ON e.batch_id = b.batch_id
    JOIN course_admins ca ON b.course_id = ca.course_id
    WHERE s.is_active = TRUE
      AND ca.admin_id = %s
      AND ca.is_active = TRUE
    """, (admin_id,))
    total_students = cursor.fetchone()['total']

    # ✅ Active batches
    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM batches b
        JOIN course_admins ca ON b.course_id = ca.course_id
        WHERE b.is_active = TRUE
        AND ca.admin_id = %s
        AND ca.is_active = TRUE
    """, (admin_id,))
    active_batches = cursor.fetchone()['total']

    # ✅ Total courses
    cursor.execute("""
        SELECT COUNT(DISTINCT c.course_id) AS total
        FROM courses c
        JOIN course_admins ca ON c.course_id = ca.course_id
        WHERE ca.admin_id = %s
        AND ca.is_active = TRUE
    """, (admin_id,))
    total_courses = cursor.fetchone()['total']

    # ✅ Pending leave requests
    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM leave_applications la
        JOIN students s ON la.student_id = s.student_id
        JOIN enrollments e ON s.student_id = e.student_id
        JOIN batches b ON e.batch_id = b.batch_id
        JOIN course_admins ca ON b.course_id = ca.course_id
        WHERE la.status = 'pending'
        AND ca.admin_id = %s
        AND ca.is_active = TRUE
    """, (admin_id,))
    pending_leaves = cursor.fetchone()['total']

    # 📊 Attendance Trends (last 6 months, grouped by month)
    cursor.execute("""
        SELECT DATE_FORMAT(a.attendance_date, '%Y-%m') AS month,
            SUM(CASE WHEN a.status = 'PRESENT' THEN 1 ELSE 0 END) AS present_count,
            SUM(CASE WHEN a.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent_count
        FROM attendance a
        JOIN batches b ON a.batch_id = b.batch_id
        JOIN course_admins ca ON b.course_id = ca.course_id
        WHERE ca.admin_id = %s
        AND ca.is_active = TRUE
        GROUP BY month
        ORDER BY month DESC
        LIMIT 6
    """, (admin_id,))
    attendance_data = cursor.fetchall()

    # 📊 Course-wise Enrollment
    cursor.execute("""
        SELECT c.course_name,
            COUNT(DISTINCT e.student_id) AS student_count
        FROM enrollments e
        JOIN batches b ON e.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        JOIN course_admins ca ON c.course_id = ca.course_id
        WHERE ca.admin_id = %s
        AND ca.is_active = TRUE
        GROUP BY c.course_id, c.course_name
        ORDER BY student_count DESC
    """, (admin_id,))
    enrollment_data = cursor.fetchall()


    # ✅ Recent Activities
    cursor.execute("""
        SELECT al.timestamp, u.full_name AS user, al.action, al.new_values AS details
        FROM activity_logs al
        LEFT JOIN users u ON al.user_id = u.user_id
        WHERE u.role IN ('student','trainer')
        ORDER BY al.timestamp DESC
        LIMIT 10
    """)
    recent_activities = cursor.fetchall()

    # Monthly Enrollments Trend (last 6 months)
    from datetime import datetime, timedelta

    monthly_labels = []
    monthly_counts = []
    today = datetime.today()

    for i in range(5, -1, -1):
        month_date = today - timedelta(days=i*30)
        month_name = month_date.strftime("%b %Y")
        month_number = month_date.month
        year_number = month_date.year

        cursor.execute("""
            SELECT COUNT(*) AS total 
            FROM enrollments e
            JOIN batches b ON e.batch_id = b.batch_id
            JOIN course_admins ca ON b.course_id = ca.course_id
            WHERE MONTH(e.enrolled_on)=%s AND YEAR(e.enrolled_on)=%s
              AND ca.admin_id = %s AND ca.is_active = TRUE
        """, (month_number, year_number, admin_id))
        monthly_counts.append(cursor.fetchone()['total'])
        monthly_labels.append(month_name)

    # Leave Applications Trend (last 6 months)
    leave_months = []
    leave_approved = []
    leave_rejected = []

    for i in range(5, -1, -1):
        month_date = today - timedelta(days=i*30)
        month_name = month_date.strftime("%b %Y")
        month_number = month_date.month
        year_number = month_date.year

        # Approved
        cursor.execute("""
            SELECT COUNT(*) AS total 
            FROM leave_applications la
            JOIN students s ON la.student_id = s.student_id
            JOIN enrollments e ON s.student_id = e.student_id
            JOIN batches b ON e.batch_id = b.batch_id
            JOIN course_admins ca ON b.course_id = ca.course_id
            WHERE la.status='approved' AND MONTH(la.start_date)=%s AND YEAR(la.start_date)=%s
              AND ca.admin_id = %s AND ca.is_active = TRUE
        """, (month_number, year_number, admin_id))
        leave_approved.append(cursor.fetchone()['total'])

        # Rejected
        cursor.execute("""
            SELECT COUNT(*) AS total 
            FROM leave_applications la
            JOIN students s ON la.student_id = s.student_id
            JOIN enrollments e ON s.student_id = e.student_id
            JOIN batches b ON e.batch_id = b.batch_id
            JOIN course_admins ca ON b.course_id = ca.course_id
            WHERE la.status='rejected' AND MONTH(la.start_date)=%s AND YEAR(la.start_date)=%s
              AND ca.admin_id = %s AND ca.is_active = TRUE
        """, (month_number, year_number, admin_id))
        leave_rejected.append(cursor.fetchone()['total'])

        leave_months.append(month_name)

    cursor.close()
    conn.close()

    return render_template(
        'admin_dashboard.html',
        total_students=total_students,
        active_batches=active_batches,
        total_courses=total_courses,
        pending_leaves=pending_leaves,
        attendance_data=attendance_data,
        enrollment_data=enrollment_data,
        recent_activities=recent_activities,
        monthly_labels=monthly_labels,
        monthly_counts=monthly_counts,
        leave_months=leave_months,
        leave_approved=leave_approved,
        leave_rejected=leave_rejected
    )

# -------------------------------------------------------------------------------------------------------------------
#                                                       Batch Management
# -------------------------------------------------------------------------------------------------------------------
@admin_bp.route('/add_batch', methods=['GET', 'POST'])
def add_batch():
    # --- Access Control ---
    if session.get('user_role') != 'admin':
        if request.method == 'POST':
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        return redirect(url_for('auth.login'))

    admin_id = session.get('user_id')

    # --- GET: Render Add Batch Page ---
    if request.method == 'GET':
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT c.course_id, c.course_name
            FROM courses c
            JOIN course_admins ca ON c.course_id = ca.course_id
            WHERE c.is_active = TRUE
              AND ca.admin_id = %s
              AND ca.is_active = TRUE
        """, (admin_id,))
        courses = cursor.fetchall()

        cursor.close()
        conn.close()
        return render_template('add_batch.html', courses=courses)

    # --- POST: Handle AJAX batch creation ---
    if request.method == 'POST':
        try:
            batch_name = request.form.get('batch_name')
            course_id = int(request.form['course_id'])
            trainers = request.form.getlist("trainers")
            start_date = request.form['start_date']
            end_date = request.form['end_date']
            max_students = int(request.form.get('max_students', 30))
            is_active = bool(int(request.form.get('is_active', 1)))

            # --- New leave fields ---
            personal_leaves = int(request.form.get('personal_leaves', 5))  # default 5
            medical_leaves = request.form.get('medical_leaves')
            educational_leaves = request.form.get('educational_leaves')

            # Convert optional values to None if empty
            medical_leaves = int(medical_leaves) if medical_leaves else None
            educational_leaves = int(educational_leaves) if educational_leaves else None

            conn = get_connection()
            cursor = conn.cursor(dictionary=True)

            # --- 1. Check for duplicate batch ---
            cursor.execute("""
                SELECT batch_id FROM batches
                WHERE batch_name = %s AND course_id = %s
            """, (batch_name, course_id))
            if cursor.fetchone():
                return jsonify({'success': False, 'error': f"A batch named '{batch_name}' already exists for this course."})

            # --- 2. Insert batch (with new fields) ---
            cursor.execute("""
                INSERT INTO batches (
                    batch_name, course_id, start_date, end_date,
                    max_students, is_active, created_by,
                    personal_leaves, medical_leaves, educational_leaves
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                batch_name, course_id, start_date, end_date,
                max_students, 1 if is_active else 0, admin_id,
                personal_leaves, medical_leaves, educational_leaves
            ))
            conn.commit()
            batch_id = cursor.lastrowid

            # --- 3. Assign trainers ---
            if trainers:
                for tid in trainers:
                    cursor.execute("""
                        INSERT INTO batch_trainers (batch_id, trainer_id, is_active)
                        VALUES (%s, %s, %s)
                    """, (batch_id, int(tid), 1))
                conn.commit()

            # --- 4. Calculate duration_weeks ---
            cursor.execute("""
                SELECT TIMESTAMPDIFF(WEEK, start_date, end_date) AS duration_weeks
                FROM batches WHERE batch_id = %s
            """, (batch_id,))
            row = cursor.fetchone()
            duration_weeks = row['duration_weeks'] if row else None

            cursor.close()
            conn.close()

            return jsonify({
                'success': True,
                'batch_id': batch_id,
                'duration_weeks': duration_weeks
            })

        except Exception as e:
            if 'conn' in locals() and conn:
                conn.rollback()
                cursor.close()
                conn.close()
            return jsonify({'success': False, 'error': f"An unexpected error occurred: {str(e)}"})



# -------------------------------------------------------
# Get trainers 
# -------------------------------------------------------


# ✅ New AJAX route for fetching trainers by course
@admin_bp.route('/get_trainers/<int:course_id>')
def get_trainers(course_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT u.user_id AS trainer_id, u.full_name AS trainer_name
        FROM course_trainers ct
        JOIN users u ON ct.trainer_id = u.user_id
        WHERE ct.is_active = TRUE
          AND ct.course_id = %s
    """, (course_id,))
    trainers = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify(trainers)
@admin_bp.route('/batches', methods=['GET'])
def batches():
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))

    batch_id = request.args.get('batch_id', type=int)
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    if batch_id:
        # --- AJAX: Return batch details for modal ---
        # Fetch batch info with course and creator
        cursor.execute("""
            SELECT b.*, c.course_name, u.full_name AS created_by_name
            FROM batches b
            LEFT JOIN courses c ON b.course_id = c.course_id
            LEFT JOIN users u ON b.created_by = u.user_id
            WHERE b.batch_id = %s
        """, (batch_id,))
        batch = cursor.fetchone()

        if not batch:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Batch not found'})

        # Fetch trainers for this batch
        cursor.execute("""
            SELECT u.user_id, u.full_name AS trainer_name, bt.is_active
            FROM batch_trainers bt
            JOIN users u ON bt.trainer_id = u.user_id
            WHERE bt.batch_id = %s
        """, (batch_id,))
        trainers = cursor.fetchall()
        batch['trainers'] = trainers

        cursor.close()
        conn.close()
        return jsonify({'success': True, 'batch': batch})

    else:
        # --- Normal page load: render all batches ---
        cursor.execute("""
            SELECT b.*, c.course_name
            FROM batches b
            LEFT JOIN courses c ON b.course_id = c.course_id
            ORDER BY b.batch_id DESC
        """)
        batches = cursor.fetchall()

        # Fetch all trainers for these batches
        batch_ids = [b['batch_id'] for b in batches]
        trainers_map = {}
        if batch_ids:
            format_strings = ','.join(['%s'] * len(batch_ids))
            cursor.execute(f"""
                SELECT bt.batch_id, u.user_id, u.full_name AS trainer_name, bt.is_active
                FROM batch_trainers bt
                JOIN users u ON bt.trainer_id = u.user_id
                WHERE bt.batch_id IN ({format_strings})
            """, tuple(batch_ids))
            trainers = cursor.fetchall()
            for t in trainers:
                trainers_map.setdefault(t['batch_id'], []).append({
                    'trainer_id': t['user_id'],
                    'trainer_name': t['trainer_name'],
                    'is_active': t['is_active']
                })

        for b in batches:
            b['trainers'] = trainers_map.get(b['batch_id'], [])

        cursor.close()
        conn.close()
        return render_template('batches.html', batches=batches)


# -------------------------------------------------------
# Edit/Delete Batch 
# -------------------------------------------------------
@admin_bp.route('/edit_batch/<int:batch_id>', methods=['GET', 'POST'])
def edit_batch(batch_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    # Get batch details
    cursor.execute("SELECT * FROM batches WHERE batch_id = %s", (batch_id,))
    batch = cursor.fetchone()
    if not batch:
        cursor.close()
        conn.close()
        return "Batch not found", 404

    # Get assigned trainers for this batch
    cursor.execute("""
        SELECT u.user_id AS trainer_id, u.full_name AS trainer_name
        FROM batch_trainers bt
        JOIN users u ON bt.trainer_id = u.user_id
        WHERE bt.batch_id = %s AND bt.is_active = TRUE
    """, (batch_id,))
    batch['trainers'] = cursor.fetchall()

    # Get all active courses
    cursor.execute("SELECT course_id, course_name FROM courses WHERE is_active = TRUE")
    courses = cursor.fetchall()

    if request.method == 'POST':
        batch_name = request.form['batch_name']
        course_id = int(request.form['course_id'])
        trainers = request.form.getlist("trainers")
        start_date = request.form['start_date']
        end_date = request.form['end_date']
        max_students = int(request.form.get('max_students', 30))        
        is_active = bool(int(request.form.get('is_active', 1)))

        # Leaves
        personal_leaves = int(request.form.get('personal_leaves', 5))  # Fixed at 5
        medical_leaves = request.form.get('medical_leaves')
        educational_leaves = request.form.get('educational_leaves')

        # Convert blank to None (NULL in DB)
        medical_leaves = int(medical_leaves) if medical_leaves not in (None, '') else None
        educational_leaves = int(educational_leaves) if educational_leaves not in (None, '') else None

        # Update batch
        cursor.execute("""
            UPDATE batches SET
                batch_name=%s, course_id=%s, start_date=%s, end_date=%s,
                max_students=%s, is_active=%s,
                personal_leaves=%s, medical_leaves=%s, educational_leaves=%s
            WHERE batch_id=%s
        """, (
            batch_name, course_id, start_date, end_date,
            max_students, is_active,
            personal_leaves, medical_leaves, educational_leaves,
            batch_id
        ))

        # Update trainers: remove all, then add selected
        cursor.execute("DELETE FROM batch_trainers WHERE batch_id=%s", (batch_id,))
        for tid in trainers:
            cursor.execute("""
                INSERT INTO batch_trainers (batch_id, trainer_id, is_active)
                VALUES (%s, %s, %s)
            """, (batch_id, int(tid), 1))

        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'redirect': url_for('admin.batches')})

    cursor.close()
    conn.close()
    return render_template('edit_batch.html', batch=batch, courses=courses)



@admin_bp.route('/delete_batch/<int:batch_id>', methods=['GET', 'POST'])
def delete_batch(batch_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM batches WHERE batch_id = %s", (batch_id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash('Batch deleted successfully!', 'success')
    return redirect(url_for('admin.batches'))


# -------------------------------------------------------
# Toggle Batch Active Status
# -------------------------------------------------------


@admin_bp.route('/toggle_batch_status/<int:batch_id>', methods=['POST'])
def toggle_batch_status(batch_id):
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.get_json() or {}
    val = str(data.get('is_active', "1")).lower()
    is_active = 1 if val in ("1", "true", "yes", "on") else 0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE batches SET is_active=%s WHERE batch_id=%s",
            (is_active, batch_id)
        )
        conn.commit()
        return jsonify({'success': True, 'is_active': bool(is_active)})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        cursor.close()
        conn.close()



#####################
#Student management



# -------------------------------------------------------
# Add Student
# -------------------------------------------------------
# GET route → show the Add Student form
@admin_bp.route('/batches/<int:batch_id>/add_student', methods=['GET'])
def show_add_student(batch_id):
    if session.get("user_role") != "admin":
        return redirect(url_for("auth.login"))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get batch info along with course
    cursor.execute("""
        SELECT b.*, c.course_name
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        WHERE b.batch_id = %s
    """, (batch_id,))
    batch = cursor.fetchone()

    cursor.close()
    conn.close()

    if not batch:
        flash("Batch not found.", "danger")
        return redirect(url_for("admin.courses"))

    return render_template("add_student.html", batch=batch, current_date=date.today().isoformat())


# POST route → handle Add Student form submission
@admin_bp.route('/batches/<int:batch_id>/add_student_submit', methods=['POST'])
def submit_add_student(batch_id):
    if session.get("user_role") != "admin":
        return jsonify(success=False, message="Unauthorized access")

    try:
        full_name = request.form.get("full_name")
        admission_no = request.form.get("admission_no")
        dob = request.form.get("dob")
        guardian_name = request.form.get("guardian_name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        enrollment_date = request.form.get("enrollment_date")
        is_active = 1 if request.form.get("is_active") in ["1", "true", "True"] else 0

        # Validate required fields
        if not full_name or not email or not admission_no or not dob:
            return jsonify(success=False, message="Full Name, Email, Admission No, and DOB are required.")

        # --- Add these lines ---
        app = current_app._get_current_object()
        mail = Mail(app)
        admin_email = session.get("user_email") or app.config.get("MAIL_USERNAME")
        # ------------------------

        # Create student (now with email sending)
        student_id, user_id, plain_password = create_student(
            full_name=full_name,
            email=email,
            phone=phone,
            admission_no=admission_no,
            dob=dob,
            guardian_name=guardian_name,
            enrollment_date=enrollment_date,
            is_active=is_active,
            app=app,
            mail=mail,
            admin_email=admin_email
        )

        # Enroll student into the batch
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO enrollments (student_id, batch_id, status, enrolled_on)
            VALUES (%s, %s, 'ACTIVE', CURRENT_DATE)
        """, (student_id, batch_id))
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify(success=True, message="Student added successfully!")

    except Exception as e:
        return jsonify(success=False, message=str(e))

# ----------------------------------------
#  courses list (for assigning students)
# ----------------------------------------

@admin_bp.route('/courses')
def courses():
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM courses ORDER BY course_name")
    courses = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('admin_courses.html', courses=courses)


# ------------------------------------------
# course batches 
# ------------------------------------------

@admin_bp.route('/courses/<int:course_id>/batches')
def course_batches(course_id):
    if session.get("user_role") != "admin":
        return redirect(url_for("auth.login"))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get course details
    cursor.execute("SELECT * FROM courses WHERE course_id = %s", (course_id,))
    course = cursor.fetchone()

    if not course:
        cursor.close()
        conn.close()
        flash("Course not found", "danger")
        return redirect(url_for("admin.courses"))

    # Get batches for this course
    cursor.execute("""
        SELECT * 
        FROM batches
        WHERE course_id = %s
        ORDER BY start_date DESC
    """, (course_id,))
    batches = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "course_batches.html",
        course=course,
        batches=batches
    )

# ---------------------------------------------
# 
# ---------------------------------------------

@admin_bp.route('/batches/<int:batch_id>/students')
def batch_students(batch_id):
    if session.get("user_role") != "admin":
        return redirect(url_for("auth.login"))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get batch info along with course name
    cursor.execute("""
        SELECT b.*, c.course_name
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        WHERE b.batch_id = %s
    """, (batch_id,))
    batch = cursor.fetchone()

    if not batch:
        cursor.close()
        conn.close()
        flash("Batch not found", "danger")
        return redirect(url_for("admin.courses"))

    # Get students for this batch
    cursor.execute("""
        SELECT s.*
        FROM students s
        JOIN enrollments e ON e.student_id = s.student_id
        WHERE e.batch_id = %s
        ORDER BY s.full_name
    """, (batch_id,))
    students = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("batch_students.html", batch=batch, students=students)



@admin_bp.route('/students', methods=['GET'])
def students():
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # ✅ Fetch all students with their course & batch
    cursor.execute("""
        SELECT s.student_id, s.admission_no, s.dob, s.guardian_name, s.enrollment_date, s.is_active,
               u.full_name, u.email, u.phone,
               c.course_name, b.batch_name
        FROM students s
        JOIN users u ON s.user_id = u.user_id
        LEFT JOIN enrollments e ON s.student_id = e.student_id AND e.status = 'ACTIVE'
        LEFT JOIN batches b ON e.batch_id = b.batch_id
        LEFT JOIN courses c ON b.course_id = c.course_id
        ORDER BY s.student_id DESC
    """)
    students = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template('students.html', students=students)

@admin_bp.route('/edit_student/<int:student_id>', methods=['GET', 'POST'])
def edit_student(student_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # ✅ Fetch student with user + enrollment + course/batch info
    cursor.execute("""
        SELECT s.student_id, s.admission_no, s.dob, s.guardian_name, s.enrollment_date, s.is_active,
               u.user_id, u.full_name, u.email, u.phone,
               e.batch_id, b.course_id
        FROM students s
        JOIN users u ON s.user_id = u.user_id
        LEFT JOIN enrollments e ON s.student_id = e.student_id AND e.status='ACTIVE'
        LEFT JOIN batches b ON e.batch_id = b.batch_id
        WHERE s.student_id = %s
    """, (student_id,))
    student = cursor.fetchone()

    # ✅ Fetch active courses for this admin (via course_admins mapping table)
    admin_id = session.get('user_id')
    cursor.execute("""
        SELECT c.course_id, c.course_name
        FROM courses c
        JOIN course_admins ca ON c.course_id = ca.course_id
        WHERE c.is_active = TRUE AND ca.admin_id = %s AND ca.is_active = TRUE
    """, (admin_id,))
    courses = cursor.fetchall()

    # ✅ Prefill batches if student already has a course
    batches = []
    if student and student.get("course_id"):
        cursor.execute("""
            SELECT batch_id, batch_name
            FROM batches
            WHERE course_id = %s AND is_active = TRUE
        """, (student["course_id"],))
        batches = cursor.fetchall()

    errors = {}

    # ✅ Handle POST request (AJAX)
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        admission_no = request.form.get('admission_no', '').strip()
        dob = request.form.get('dob')
        guardian_name = request.form.get('guardian_name')
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip()
        enrollment_date = request.form.get('enrollment_date')
        is_active = bool(int(request.form.get('is_active', 1)))

        course_id = request.form.get('course_id')
        batch_id = request.form.get('batch_id')

        # ✅ Validation
        if not full_name:
            errors['full_name'] = 'Full name is required'
        if not admission_no:
            errors['admission_no'] = 'Admission No is required'
        if not course_id:
            errors['course_id'] = 'Course is required'
        if not batch_id:
            errors['batch_id'] = 'Batch is required'

        if errors:
            cursor.close()
            conn.close()
            return jsonify(success=False, errors=errors, message="Validation error")

        try:
            # ✅ Update users table
            cursor.execute("""
                UPDATE users 
                SET full_name=%s, email=%s, phone=%s, is_active=%s
                WHERE user_id=%s
            """, (full_name, email, phone, is_active, student['user_id']))

            # ✅ Update students table
            cursor.execute("""
                UPDATE students 
                SET admission_no=%s, dob=%s, guardian_name=%s, enrollment_date=%s, is_active=%s
                WHERE student_id=%s
            """, (admission_no, dob, guardian_name, enrollment_date, is_active, student_id))

            # ✅ Upsert enrollment
            cursor.execute("""
                INSERT INTO enrollments (student_id, batch_id, status, enrolled_on)
                VALUES (%s, %s, 'ACTIVE', CURRENT_DATE)
                ON DUPLICATE KEY UPDATE 
                    batch_id = VALUES(batch_id),
                    status = 'ACTIVE',
                    enrolled_on = CURRENT_DATE,
                    exited_on = NULL,
                    remarks = NULL
            """, (student_id, batch_id))

            conn.commit()
            return jsonify(success=True)

        except Exception as e:
            conn.rollback()
            return jsonify(success=False, message=str(e))

        finally:
            cursor.close()
            conn.close()

    # ✅ Render template for GET request
    cursor.close()
    conn.close()
    return render_template(
        'edit_student.html',
        student=student,
        courses=courses,
        batches=batches,
        errors=errors
    )

@admin_bp.route('/delete_student/<int:student_id>', methods=['POST'])
def delete_student(student_id):
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Get user_id for this student
        cursor.execute("SELECT user_id FROM students WHERE student_id=%s", (student_id,))
        result = cursor.fetchone()
        if result:
            user_id = result[0]
            # Delete from students table
            cursor.execute("DELETE FROM students WHERE student_id=%s", (student_id,))
            # Optionally delete from users table
            cursor.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
            conn.commit()
            cursor.close()
            conn.close()
            return jsonify({'success': True})
        else:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Student not found'})
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'error': str(e)})

@admin_bp.route('/toggle_student_status/<int:student_id>', methods=['POST'])
def toggle_student_status(student_id):
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    data = request.get_json()
    is_active = bool(int(data.get('is_active', 1)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Update both students and users table for consistency
        cursor.execute("UPDATE students SET is_active=%s WHERE student_id=%s", (is_active, student_id))
        cursor.execute("""
            UPDATE users SET is_active=%s
            WHERE user_id = (SELECT user_id FROM students WHERE student_id=%s)
        """, (is_active, student_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'error': str(e)})



@admin_bp.route('/get_batches_by_course/<int:course_id>')
def get_batches_by_course(course_id):
    if session.get('user_role') != 'admin':
        return jsonify({'batches': []})
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT batch_id, batch_name FROM batches
        WHERE course_id = %s AND is_active = TRUE
    """, (course_id,))
    batches = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify({'batches': batches})

@admin_bp.route('/pending_leave_requests')
def pending_leave_requests():
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT l.*, u.full_name, s.admission_no, c.course_name, b.batch_name
        FROM leaves l
        JOIN students s ON l.student_id = s.student_id
        JOIN users u ON s.user_id = u.user_id
        JOIN batches b ON l.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        WHERE l.status = 'PENDING'
        ORDER BY l.requested_on DESC
    """)
    leaves = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('pending_leave_requests.html', leaves=leaves)

# Admin Dashboard: List pending leave applications
@admin_bp.route('/leave_dashboard')
def leave_dashboard():
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT l.*, s.student_id, u.full_name AS student_name, t.type_name
        FROM leave_applications l
        JOIN students s ON l.student_id = s.student_id
        JOIN users u ON s.user_id = u.user_id
        JOIN leave_types t ON l.type_id = t.type_id
        WHERE l.status = 'pending'
        ORDER BY l.applied_at DESC
    """)
    pending_leaves = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('admin_leave_dashboard.html', pending_leaves=pending_leaves)

# Admin Review Leave
@admin_bp.route('/review_leave/<int:leave_id>', methods=['GET', 'POST'])
def review_leave(leave_id):
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    # Fetch leave
    cursor.execute("""
        SELECT l.*, s.student_id, u.full_name AS student_name, t.type_name, l.type_id
        FROM leave_applications l
        JOIN students s ON l.student_id = s.student_id
        JOIN users u ON s.user_id = u.user_id
        JOIN leave_types t ON l.type_id = t.type_id
        WHERE l.leave_id = %s
    """, (leave_id,))
    leave = cursor.fetchone()

    if request.method == 'POST':
        action = request.form.get('action')
        admin_comments = request.form.get('admin_comments', '')
        admin_id = session.get('user_id')

        # Update leave status
        cursor.execute("""
            UPDATE leave_applications
            SET status=%s, reviewed_by=%s, reviewed_at=NOW(), admin_comments=%s
            WHERE leave_id=%s
        """, (action, admin_id, admin_comments, leave_id))

        # Insert log
        cursor.execute("""
            INSERT INTO leave_application_logs (leave_id, previous_status, new_status, action_by, comment)
            VALUES (%s, %s, %s, %s, %s)
        """, (leave_id, leave['status'], action, admin_id, admin_comments))

        # Reduce leave balance if approved
        if action == 'approved':
            days_taken = (leave['end_date'] - leave['start_date']).days + 1
            cursor.execute("""
                UPDATE student_leave_balances
                SET available_days = available_days - %s, updated_at = NOW()
                WHERE student_id = %s AND type_id = %s
            """, (days_taken, leave['student_id'], leave['type_id']))

        conn.commit()
        cursor.close()
        conn.close()
        flash(f'Leave {action} successfully!', 'success')
        return redirect(url_for('admin.leave_dashboard'))

    cursor.close()
    conn.close()
    return render_template('review_leave.html', leave=leave)


@admin_bp.route('/leave_history')
def leave_history():
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT l.leave_id,
               l.start_date,
               l.end_date,
               l.reason,
               l.status,
               l.applied_at,
               l.reviewed_at,
               l.admin_comments,
               s.student_id,
               u.full_name AS student_name,
               t.type_name,
               a.full_name AS reviewed_by_name
        FROM leave_applications l
        JOIN students s ON l.student_id = s.student_id
        JOIN users u ON s.user_id = u.user_id        -- student name
        JOIN leave_types t ON l.type_id = t.type_id  -- leave type
        LEFT JOIN users a ON l.reviewed_by = a.user_id  -- admin/reviewer name
        ORDER BY l.applied_at DESC
    """)
    leaves = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("admin_leave_history.html", leaves=leaves)



@admin_bp.route('/leave_types', methods=['GET'])
def leave_types():
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch leave types from batches
    # Assuming your batches table has columns like: batch_name, personal_leave, education_leave, medical_leave
    cursor.execute("""
        SELECT 
            batch_id,
            batch_name,
            personal_leaves AS personal_max_days,
            educational_leaves AS education_max_days,
            medical_leaves AS medical_max_days
        FROM batches
        ORDER BY batch_name
    """)
    leave_types = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template('admin_leave_types.html', leave_types=leave_types)
@admin_bp.route('/leave_balances')
def leave_balances():
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # 🔹 Ensure every student has balances for all leave types
    cursor.execute("""
        INSERT INTO student_leave_balances (student_id, type_id, available_days)
        SELECT s.student_id, t.type_id, t.default_max_days
        FROM students s
        CROSS JOIN leave_types t
        WHERE NOT EXISTS (
            SELECT 1 FROM student_leave_balances b
            WHERE b.student_id = s.student_id AND b.type_id = t.type_id
        )
    """)
    conn.commit()

    # 🔹 Fetch balances along with used and pending leaves, include batch
    cursor.execute("""
    SELECT 
        s.student_id,
        u.full_name AS student_name,
        t.type_name,
        lb.available_days,
        COALESCE((
            SELECT SUM(DATEDIFF(end_date, start_date)+1)
            FROM leave_applications la
            WHERE la.student_id = s.student_id
              AND la.type_id = t.type_id
              AND la.status='approved'
        ),0) AS used_days,
        COALESCE((
            SELECT SUM(DATEDIFF(end_date, start_date)+1)
            FROM leave_applications la
            WHERE la.student_id = s.student_id
              AND la.type_id = t.type_id
              AND la.status='pending'
        ),0) AS pending_days,
        b.batch_name
    FROM student_leave_balances lb
    JOIN students s ON lb.student_id = s.student_id
    JOIN enrollments e ON s.student_id = e.student_id AND e.status='ACTIVE'
    JOIN batches b ON e.batch_id = b.batch_id
    JOIN users u ON s.user_id = u.user_id
    JOIN leave_types t ON lb.type_id = t.type_id
    ORDER BY b.batch_name,
             CASE t.type_name
                 WHEN 'Personal Leave' THEN 1
                 WHEN 'Educational Leave' THEN 2
                 WHEN 'Medical Leave' THEN 3
                 ELSE 4
             END
""")
    balances = cursor.fetchall()


    # 🔹 Calculate remaining / taken
    for b in balances:
        if b['type_name'] == 'Personal Leave':
            b['remaining'] = b['available_days'] - (b['used_days'] + b['pending_days'])
        else:
            # Educational / Medical: show only used/taken
            b['remaining'] = b['used_days']  # this will be displayed as "Taken" in template

    cursor.close()
    conn.close()

    return render_template("admin_leave_balances.html", balances=balances)




# ---------- Attendance Marking Route ----------from datetime import date


@admin_bp.route('/attendance/mark', methods=['GET', 'POST'])
def mark_attendance_page():
    if not require_admin():
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    admin_id = session.get('user_id')
    attendance = {}
    remarks = {}
    leave_students = set()  # students with approved leave on selected_date

    # ---------- POST: Save Attendance ----------
    if request.method == 'POST':
        batch_id = request.form.get('batch_id')
        attendance_date = request.form.get('attendance_date')

        # Get students of this batch
        cursor.execute("""
            SELECT s.student_id
            FROM enrollments e
            JOIN students s ON e.student_id = s.student_id
            WHERE e.batch_id = %s
        """, (batch_id,))
        students = cursor.fetchall()

        for s in students:
            student_id = s['student_id']
            status = request.form.get(f'status_{student_id}')
            remark = request.form.get(f'remarks_{student_id}', '')

            if not status:
                continue

            # Save or update attendance
            cursor.execute("""
                INSERT INTO attendance (student_id, batch_id, attendance_date, status, remarks, marked_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    status = VALUES(status),
                    remarks = VALUES(remarks),
                    marked_by = VALUES(marked_by),
                    marked_at = CURRENT_TIMESTAMP
            """, (student_id, batch_id, attendance_date, status, remark, admin_id))

        # Log the bulk marking
        cursor.execute("""
            INSERT INTO attendance_logs (action_type, performed_by, batch_id, attendance_date, details)
            VALUES (%s, %s, %s, %s, %s)
        """, ("MARK_BULK", admin_id, batch_id, attendance_date,
              json.dumps({"students": len(students)})))

        conn.commit()
        flash("✅ Attendance saved successfully!", "success")
        return redirect(url_for('admin.mark_attendance_page'))

    # ---------- GET: Load Page ----------
    selected_batch = request.args.get('batch_id')
    selected_date = request.args.get('attendance_date')

    # Get active batches
    cursor.execute("""
        SELECT b.batch_id, b.batch_name AS name
        FROM batches b
        JOIN courses c ON b.course_id = c.course_id
        WHERE b.is_active = TRUE
    """)
    batches = cursor.fetchall()

    students = []
    if selected_batch and selected_date:
        # Fetch students of batch
        cursor.execute("""
            SELECT s.student_id, u.full_name AS name, s.admission_no AS roll_no
            FROM enrollments e
            JOIN students s ON e.student_id = s.student_id
            JOIN users u ON s.user_id = u.user_id
            WHERE e.batch_id = %s
            ORDER BY s.admission_no
        """, (selected_batch,))
        students = cursor.fetchall()

        # Fetch already marked attendance
        cursor.execute("""
            SELECT student_id, status, remarks
            FROM attendance
            WHERE batch_id = %s AND attendance_date = %s
        """, (selected_batch, selected_date))
        rows = cursor.fetchall()
        attendance = {row['student_id']: row['status'] for row in rows}
        remarks = {row['student_id']: row['remarks'] for row in rows}

        # Fetch students with approved leave for the selected date
        cursor.execute("""
            SELECT DISTINCT student_id
            FROM leave_applications
            WHERE status='approved'
              AND %s BETWEEN start_date AND end_date
        """, (selected_date,))
        leave_students = {row['student_id'] for row in cursor.fetchall()}

        # Auto mark ABSENT for students on approved leave
        for s in students:
            if s['student_id'] in leave_students:
                attendance[s['student_id']] = 'ABSENT'

    cursor.close()
    conn.close()

    return render_template(
        "mark_attendance_bulk.html",
        batches=batches,
        students=students,
        selected_batch=int(selected_batch) if selected_batch else None,
        selected_date=selected_date,
        attendance=attendance,
        remarks=remarks,
        leave_students=leave_students,
        default_date=date.today().isoformat()
    )

@admin_bp.route('/attendance/student_history', methods=['GET'])
def student_attendance_history():
    if not require_admin():
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    student_id = request.args.get('student_id')
    batch_id = request.args.get('batch_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # Fetch students list for dropdown
    cursor.execute("SELECT s.student_id, u.full_name FROM students s JOIN users u ON s.user_id = u.user_id")
    students = cursor.fetchall()

    # Fetch batches list for dropdown
    cursor.execute("SELECT batch_id, batch_name FROM batches WHERE is_active = TRUE")
    batches = cursor.fetchall()

    history = []
    summary = {}
    if student_id:
        query = """
            SELECT a.attendance_date, a.status, a.remarks, b.batch_name
            FROM attendance a
            JOIN batches b ON a.batch_id = b.batch_id
            WHERE a.student_id = %s
        """
        params = [student_id]

        if batch_id:
            query += " AND a.batch_id = %s"
            params.append(batch_id)
        if start_date:
            query += " AND a.attendance_date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND a.attendance_date <= %s"
            params.append(end_date)

        query += " ORDER BY a.attendance_date ASC"
        cursor.execute(query, params)
        history = cursor.fetchall()

        # Summary calculation
        total = len(history)
        present = sum(1 for h in history if h['status'] == 'PRESENT')
        absent = sum(1 for h in history if h['status'] == 'ABSENT')
        percentage = (present / total * 100) if total > 0 else 0
        summary = {'total': total, 'present': present, 'absent': absent, 'percentage': round(percentage,2)}

    cursor.close()
    conn.close()
    return render_template("student_attendance_history.html",
                           students=students,
                           batches=batches,
                           history=history,
                           summary=summary,
                           selected_student=int(student_id) if student_id else None,
                           selected_batch=int(batch_id) if batch_id else None,
                           start_date=start_date,
                           end_date=end_date)


@admin_bp.route('/attendance/report', methods=['GET'])
def attendance_report():
    if not require_admin():
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get filters and pagination
    batch_id = request.args.get('batch_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))

    # Fetch active batches
    cursor.execute("SELECT batch_id, batch_name FROM batches WHERE is_active = TRUE")
    batches = cursor.fetchall()

    report = []
    batch_summary = {
        "total_classes": 0,
        "present_count": 0,
        "absent_count": 0,
        "attendance_percent": 0
    }
    total_records = 0
    total_pages = 1

    if batch_id and start_date and end_date:
        # Count total students for this batch & date range
        cursor.execute("""
            SELECT COUNT(DISTINCT s.student_id) AS total
            FROM students s
            JOIN enrollments e ON s.student_id = e.student_id
            WHERE e.batch_id = %s AND e.status='ACTIVE'
        """, (batch_id,))
        total_records = cursor.fetchone()['total']
        total_pages = (total_records + per_page - 1) // per_page  # ceiling division
        offset = (page - 1) * per_page

        # Fetch paginated attendance report
        cursor.execute("""
            SELECT 
                s.student_id,
                u.full_name AS name,
                s.admission_no AS roll_no,
                COUNT(a.attendance_id) AS total_classes,
                SUM(CASE WHEN a.status='PRESENT' THEN 1 ELSE 0 END) AS present_count,
                SUM(CASE WHEN a.status='ABSENT' THEN 1 ELSE 0 END) AS absent_count,
                ROUND(SUM(CASE WHEN a.status='PRESENT' THEN 1 ELSE 0 END) / 
                      NULLIF(COUNT(a.attendance_id),0) * 100, 2) AS attendance_percent
            FROM students s
            JOIN users u ON s.user_id = u.user_id
            LEFT JOIN attendance a 
                ON s.student_id = a.student_id 
                AND a.batch_id = %s 
                AND a.attendance_date BETWEEN %s AND %s
            WHERE EXISTS (
                SELECT 1 FROM enrollments e 
                WHERE e.student_id = s.student_id AND e.batch_id = %s AND e.status='ACTIVE'
            )
            GROUP BY s.student_id
            ORDER BY s.admission_no
            LIMIT %s OFFSET %s
        """, (batch_id, start_date, end_date, batch_id, per_page, offset))
        report = cursor.fetchall()

        # Calculate batch summary
        if report:
            total_classes = sum(r['total_classes'] for r in report)
            present_count = sum(r['present_count'] for r in report)
            absent_count = sum(r['absent_count'] for r in report)
            attendance_percent = round((present_count / total_classes * 100) if total_classes else 0, 2)

            batch_summary = {
                "total_classes": total_classes,
                "present_count": present_count,
                "absent_count": absent_count,
                "attendance_percent": attendance_percent
            }

    cursor.close()
    conn.close()

    return render_template(
        "attendance_report.html",
        batches=batches,
        report=report,
        batch_summary=batch_summary,
        batch_id=batch_id,
        start_date=start_date,
        end_date=end_date,
        page=page,
        per_page=per_page,
        total_pages=total_pages
    )




# ---------------- CSV Export -----------------
@admin_bp.route('/attendance/report/export', methods=['GET'])
def export_attendance_csv():
    if not require_admin():
        return redirect(url_for('auth.login'))

    batch_id = request.args.get('batch_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT u.full_name AS name, s.admission_no AS roll_no,
               COUNT(*) AS total_classes,
               SUM(CASE WHEN a.status = 'PRESENT' THEN 1 ELSE 0 END) AS present_count,
               SUM(CASE WHEN a.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent_count,
               ROUND(SUM(CASE WHEN a.status = 'PRESENT' THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS attendance_percent
        FROM attendance a
        JOIN students s ON a.student_id = s.student_id
        JOIN users u ON s.user_id = u.user_id
        WHERE a.batch_id = %s AND a.attendance_date BETWEEN %s AND %s
        GROUP BY a.student_id
    """, (batch_id, start_date, end_date))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Roll No', 'Student Name', 'Total Classes', 'Present', 'Absent', 'Attendance %'])
    for r in rows:
        writer.writerow([r['roll_no'], r['name'], r['total_classes'], r['present_count'], r['absent_count'], r['attendance_percent']])

    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv',
                     download_name=f'attendance_report_batch_{batch_id}.csv', as_attachment=True)


@admin_bp.route('/attendance/report/export_pdf', methods=['GET'])
def export_attendance_pdf():
    if not require_admin():
        return redirect(url_for('auth.login'))

    batch_id = request.args.get('batch_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch attendance report
    cursor.execute("""
        SELECT u.full_name AS name, s.admission_no AS roll_no,
               COUNT(*) AS total_classes,
               SUM(CASE WHEN a.status = 'PRESENT' THEN 1 ELSE 0 END) AS present_count,
               SUM(CASE WHEN a.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent_count,
               ROUND(SUM(CASE WHEN a.status = 'PRESENT' THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS attendance_percent
        FROM attendance a
        JOIN students s ON a.student_id = s.student_id
        JOIN users u ON s.user_id = u.user_id
        WHERE a.batch_id = %s AND a.attendance_date BETWEEN %s AND %s
        GROUP BY a.student_id
    """, (batch_id, start_date, end_date))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    # Generate PDF
    mem = io.BytesIO()
    pdf = canvas.Canvas(mem, pagesize=A4)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(40, 820, "Attendance Report")
    y = 800

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(40, y, "Roll No | Name | Total | Present | Absent | %")
    y -= 20

    pdf.setFont("Helvetica", 10)
    for r in rows:
        line = f"{r['roll_no']} | {r['name']} | {r['total_classes']} | {r['present_count']} | {r['absent_count']} | {r['attendance_percent']}%"
        pdf.drawString(40, y, line)
        y -= 14
        if y < 60:
            pdf.showPage()
            y = 800

    pdf.save()
    mem.seek(0)

    return send_file(mem, mimetype='application/pdf', as_attachment=True,
                     download_name=f'attendance_report_batch_{batch_id}.pdf')


# ------------------------------------------
# profile routes
# ------------------------------------------

@admin_bp.route('/profile', methods=['GET', 'POST'])
def profile():
    if session.get('user_role') != 'admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    user_id = session.get('user_id')

    # Fetch admin info (joined with users table for name/email/phone if needed)
    cursor.execute("""
        SELECT a.*, u.full_name, u.email 
        FROM admins a
        JOIN users u ON a.user_id = u.user_id
        WHERE a.user_id=%s
    """, (user_id,))
    admin = cursor.fetchone()
    if not admin:
        conn.close()
        flash("Admin profile not found.", "danger")
        return redirect(url_for('admin.admin_dashboard'))

    if request.method == 'POST':
        dob = request.form.get('dob') or None
        gender = request.form.get('gender') or None
        address = request.form.get('address') or None
        city = request.form.get('city') or None
        state = request.form.get('state') or None
        country = request.form.get('country') or None
        zip_code = request.form.get('zip_code') or None
        emergency_name = request.form.get('emergency_contact_name') or None
        emergency_phone = request.form.get('emergency_contact_phone') or None


        # Profile photo
        profile_photo = admin.get('profile_photo')
        file = request.files.get('profile_photo')
        if file and file.filename:
            ext = os.path.splitext(secure_filename(file.filename))[1]
            unique_name = f"{user_id}_{uuid.uuid4().hex}{ext}"
            upload_dir = os.path.join(current_app.root_path, 'static/uploads/profiles')
            os.makedirs(upload_dir, exist_ok=True)
            file.save(os.path.join(upload_dir, unique_name))
            profile_photo = unique_name

        # Update DB
        cursor.execute("""
            UPDATE admins
            SET dob=%s, gender=%s, address=%s, city=%s, state=%s, country=%s, zip_code=%s,
                emergency_contact_name=%s, emergency_contact_phone=%s, profile_photo=%s
            WHERE admin_id=%s
        """, (
            dob, gender, address, city, state, country, zip_code,
            emergency_name, emergency_phone, profile_photo, admin['admin_id']
        ))
        conn.commit()

        # Update admin dict for template
        admin.update({
            'dob': dob,
            'gender': gender,
            'address': address,
            'city': city,
            'state': state,
            'country': country,
            'zip_code': zip_code,
            'emergency_contact_name': emergency_name,
            'emergency_contact_phone': emergency_phone,
            'profile_photo': profile_photo
        })

        # Update session variable for profile image
        session['profile_image'] = profile_photo if profile_photo else None

        flash("Profile updated successfully", "success")

    # Profile completion calculation
    fields = [
        'dob', 'gender', 'address', 'city', 'state', 'country', 'zip_code',
        'emergency_contact_name', 'emergency_contact_phone', 'profile_photo'
    ]
    filled = sum(1 for f in fields if admin.get(f))
    completion = int((filled / len(fields)) * 100)

    conn.close()
    return render_template('profile.html', admin=admin, completion=completion)


@admin_bp.route('/view-profile')
def view_profile():
    # Ensure admin is logged in
    if session.get('user_role') != 'admin':
        flash("Please login to access your profile", "warning")
        return redirect(url_for('auth.login'))

    admin_id = session.get('user_id')  # assuming 'user_id' session stores admin id
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch admin info
    cursor.execute("""
        SELECT a.*, u.full_name, u.email
        FROM admins a
        JOIN users u ON a.user_id = u.user_id
        WHERE a.user_id = %s
    """, (admin_id,))
    admin = cursor.fetchone()

    if not admin:
        flash("Admin profile not found.", "danger")
        conn.close()
        return redirect(url_for('admin.admin_dashboard'))

    # Calculate profile completion percentage
    fields = [
        'dob', 'gender', 'address', 'city', 'state', 'country', 'zip_code',
        'emergency_contact_name', 'emergency_contact_phone', 'profile_photo'
    ]
    filled = sum(1 for f in fields if admin.get(f))
    completion = int((filled / len(fields)) * 100)

    # Update session variable for profile image
    if admin.get('profile_photo'):
        session['profile_image'] = admin['profile_photo']
    else:
        session['profile_image'] = None

    conn.close()
    return render_template('view_profile.html', admin=admin, completion=completion)