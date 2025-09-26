from flask import render_template, request, session, jsonify, url_for, redirect, Blueprint, current_app
from models import get_connection, create_user, get_course_counts
from models.user_model import create_student, create_trainer, create_admin
from datetime import datetime,date
import json
import os
from werkzeug.utils import secure_filename
import uuid

# --- Activity Log Helper ---
def log_activity(user_id, action, table_affected, record_id=None, old_values=None, new_values=None):
    conn = get_connection()
    cursor = conn.cursor()
    ip_address = request.remote_addr
    cursor.execute("""
        INSERT INTO activity_logs (user_id, action, table_affected, record_id, old_values, new_values, ip_address)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        user_id,
        action,
        table_affected,
        record_id,
        json.dumps(old_values) if old_values else None,
        json.dumps(new_values) if new_values else None,
        ip_address
    ))
    conn.commit()
    cursor.close()
    conn.close()

# Helper function to safely serialize any datetime objects
def serialize_for_json(data):
    if isinstance(data, dict):
        return {k: serialize_for_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [serialize_for_json(item) for item in data]
    elif isinstance(data, datetime) or isinstance(data, date):
        return data.isoformat()  # convert datetime/date to ISO string
    else:
        return data

superadmin_bp = Blueprint("super_admin", __name__, template_folder="templates")
# ------------------------------
# Dashboard
# ------------------------------

@superadmin_bp.route("/dashboard")
def dashboard():
    if session.get("user_role") != "super_admin":
        return redirect(url_for("home"))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # -------------------- Stats Cards --------------------
    # Total Admins
    cursor.execute("SELECT COUNT(*) as total FROM users WHERE role='admin'")
    total_admins = cursor.fetchone()['total']

    # Total Trainers
    cursor.execute("SELECT COUNT(*) as total FROM users WHERE role='trainer'")
    total_trainers = cursor.fetchone()['total']

    # Total Students
    cursor.execute("SELECT COUNT(*) as total FROM students")
    total_students = cursor.fetchone()['total']

    # Total Courses
    cursor.execute("SELECT COUNT(*) as total FROM courses")
    total_courses = cursor.fetchone()['total']

    # Active Courses
    cursor.execute("SELECT COUNT(*) as total FROM courses WHERE is_active=1")
    active_courses = cursor.fetchone()['total']

    # Active / Inactive Courses (for chart)
    cursor.execute("SELECT COUNT(*) as active FROM courses WHERE is_active=1")
    active = cursor.fetchone()['active']
    cursor.execute("SELECT COUNT(*) as inactive FROM courses WHERE is_active=0")
    inactive = cursor.fetchone()['inactive']

    # Total Batches
    cursor.execute("SELECT COUNT(*) as total FROM batches")
    total_batches = cursor.fetchone()['total']

    # Pending Leaves
    cursor.execute("SELECT COUNT(*) as total FROM leave_applications WHERE status='pending'")
    pending_leaves = cursor.fetchone()['total']   

    # -------------------- Charts --------------------
    # Enrollments per Course
    cursor.execute("""
        SELECT c.course_name, COUNT(e.enroll_id) AS enrollments
        FROM courses c
        LEFT JOIN batches b ON b.course_id = c.course_id
        LEFT JOIN enrollments e ON e.batch_id = b.batch_id
        GROUP BY c.course_id
        ORDER BY c.course_name
    """)
    enrollments_result = cursor.fetchall()
    course_labels = [row['course_name'] for row in enrollments_result]
    course_enrollments = [row['enrollments'] for row in enrollments_result]

    
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
            LEFT JOIN batches b ON e.batch_id = b.batch_id
            WHERE MONTH(e.enrolled_on)=%s AND YEAR(e.enrolled_on)=%s
        """, (month_number, year_number))
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
            FROM leave_applications 
            WHERE status='approved' AND MONTH(start_date)=%s AND YEAR(start_date)=%s
        """, (month_number, year_number))
        leave_approved.append(cursor.fetchone()['total'])

        # Rejected
        cursor.execute("""
            SELECT COUNT(*) AS total 
            FROM leave_applications 
            WHERE status='rejected' AND MONTH(start_date)=%s AND YEAR(start_date)=%s
        """, (month_number, year_number))
        leave_rejected.append(cursor.fetchone()['total'])

        leave_months.append(month_name)

    cursor.execute("""
    SELECT role, COUNT(*) as count
    FROM users
    GROUP BY role
    """)
    role_result = cursor.fetchall()
    user_role_labels = [row['role'].capitalize() for row in role_result]
    user_role_counts = [row['count'] for row in role_result]
    

    # Recent Activities
    cursor.execute("""
        SELECT al.timestamp, u.full_name AS user, al.action, al.table_affected AS details
        FROM activity_logs al
        LEFT JOIN users u ON al.user_id = u.user_id
        ORDER BY al.timestamp DESC
        LIMIT 10
    """)
    recent_activities = cursor.fetchall()  



    cursor.close()
    conn.close()

    # -------------------- Render Template --------------------
    return render_template(
        "dashboard.html",
        total_admins=total_admins,
        total_trainers=total_trainers,
        total_students=total_students,
        total_courses=total_courses,
        active_courses=active_courses,
        active_courses_count=active,
        inactive_courses_count=inactive,
        total_batches=total_batches,
        pending_leaves=pending_leaves,        
        course_labels=course_labels,
        course_enrollments=course_enrollments, 
        monthly_labels=monthly_labels,
        monthly_counts=monthly_counts,
        user_role_labels=user_role_labels,
        user_role_counts=user_role_counts,
        leave_months=leave_months,
        leave_approved=leave_approved,
        leave_rejected=leave_rejected,
        recent_activities=recent_activities
    )


# # -------------------------------------------------------------------------------------------------------------------------------------------- 
#                                                   Courses Management Block
# # -------------------------------------------------------------------------------------------------------------------------------------------- 

@superadmin_bp.route('/courses')
def courses():
    if session.get('user_role') != 'super_admin':
        return redirect(url_for('auth.login'))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    search = request.args.get('search', '', type=str).strip()
    selected_status = request.args.get('status', '')
    offset = (page - 1) * per_page

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Build dynamic query
    sql = "SELECT * FROM courses WHERE 1"
    params = []

    if search:
        sql += " AND course_name LIKE %s"
        params.append(f"%{search}%")
    if selected_status == 'active':
        sql += " AND is_active = 1"
    elif selected_status == 'inactive':
        sql += " AND is_active = 0"

    # Count total courses for pagination
    sql_count = sql.replace("SELECT *", "SELECT COUNT(*) AS total")
    cursor.execute(sql_count, tuple(params))
    total = cursor.fetchone()['total']

    # Add order, limit, offset
    sql += " ORDER BY course_id DESC LIMIT %s OFFSET %s"
    params.extend([per_page, offset])
    cursor.execute(sql, tuple(params))
    courses = cursor.fetchall()

    total_pages = (total + per_page - 1) // per_page
    conn.close()

    return render_template(
        'courses.html',
        courses=courses,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
        search=search,
        total_courses=total,
        selected_status=selected_status
    )

# # -------------------------------------------------------------------------------------------------------------------------------------------- 
#                                                   Toggle Course Status [POST]
# # -------------------------------------------------------------------------------------------------------------------------------------------- 

@superadmin_bp.route("/courses/toggle_status/<int:course_id>", methods=["POST"])
def toggle_course_status(course_id):
    if session.get("user_role") != "super_admin":
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    try:
        data = request.get_json(force=True)
        is_active = data.get("is_active")

        # Convert directly → 0 or 1
        if str(is_active).lower() in ("1", "true", "yes", "on"):
            new_status = 1
        else:
            new_status = 0

        # Update DB
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE courses SET is_active=%s WHERE course_id=%s",
            (new_status, course_id)
        )
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Course status updated.",
            "is_active": bool(new_status),
            "status_label": "Active" if new_status else "Inactive"
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": f"Server error: {str(e)}"
        }), 500



# # -------------------------------------------------------------------------------------------------------------------------------------------- 
#                                                    Add Course Block[Get + POST]
# # -------------------------------------------------------------------------------------------------------------------------------------------- 

@superadmin_bp.route('/add_course', methods=['GET', 'POST'])
def add_course():
    if session.get('user_role') != 'super_admin':
        return redirect(url_for('auth.login'))

    if request.method == 'GET':        
        return render_template('add_course.html')

    # POST request handling
    course_name = request.form.get('course_name')
    description = request.form.get('description', '')
    is_active = True if request.form.get('is_active', '1') == '1' else False

    if not course_name:
        return jsonify({'success': False, 'error': 'Course name is required.'})

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute('''
            INSERT INTO courses
            (course_name, description, is_active, created_by)
            VALUES (%s, %s, %s, %s)
        ''', (course_name, description, is_active, session.get('user_id')))

        course_id = cursor.lastrowid
        conn.commit()
        cursor.close()
        conn.close()

        # Log activity
        log_activity(
            user_id=session.get("user_id"),
            action="Add Course",
            table_affected="courses",
            record_id=course_id,
            new_values={
                "course_name": course_name,
                "description": description,
                "is_active": is_active
            }
        )

        return jsonify({'success': True, 'message': 'Course added successfully!'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    


# # -------------------------------------------------------------------------------------------------------------------------------------------- 
#                                                    Edit Course Block [Get + POST]
# # -------------------------------------------------------------------------------------------------------------------------------------------- 

    

@superadmin_bp.route("/courses/edit/<int:course_id>", methods=["GET"])
def edit_course(course_id):
    if session.get("user_role") != "super_admin":
        return redirect(url_for("auth.login"))

    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM courses WHERE course_id=%s", (course_id,))
        course = cursor.fetchone()

        if not course:
            return "Course not found", 404

        return render_template("edit_course.html", course=course)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"An error occurred: {str(e)}", 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# # -------------------------------------------------------------------------------------------------------------------------------------------- 
#                                                    Update Course Block [POST]
# # -------------------------------------------------------------------------------------------------------------------------------------------- 

@superadmin_bp.route("/courses/update/<int:course_id>", methods=["POST"])
def update_course(course_id):
    conn = None
    cursor = None
    try:
        data = request.get_json(force=True)  # parse JSON only

        course_name = data.get("course_name")
        description = data.get("description", "")
        is_active = bool(data.get("is_active"))
        
        if not course_name:
            return jsonify({"success": False, "message": "Course name is required."})

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        # Check if course exists
        cursor.execute("SELECT * FROM courses WHERE course_id=%s", (course_id,))
        old_course = cursor.fetchone()
        if not old_course:
            return jsonify({"success": False, "message": "Course not found."})

        # Update course
        cursor.execute("""
            UPDATE courses
            SET course_name=%s, description=%s, is_active=%s
            WHERE course_id=%s
        """, (course_name, description, is_active, course_id))
        conn.commit()

        # Fetch updated course
        cursor.execute("SELECT * FROM courses WHERE course_id=%s", (course_id,))
        updated_course = cursor.fetchone()

        return jsonify({"success": True, "message": "Course updated successfully!", "course": updated_course})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Server error: {str(e)}"})

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



# ------------------------------
# Delete Course
# ------------------------------
@superadmin_bp.route("/courses/delete/<int:course_id>", methods=["GET"])
def delete_course(course_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM courses WHERE course_id=%s", (course_id,))
    old_course = cursor.fetchone()
    cursor.close()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM courses WHERE course_id=%s", (course_id,))
    conn.commit()
    cursor.close()
    conn.close()    
    return redirect(url_for("super_admin.courses"))



# ------------------------------
# Admin Management with Assigned Courses as list
# ------------------------------
@superadmin_bp.route('/admins')
def admins():
    if session.get("user_role") != "super_admin":
        return redirect(url_for("home"))

    # --- Get filters ---
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    search = request.args.get('search', '', type=str).strip()
    status = request.args.get('status', '', type=str)

    offset = (page - 1) * per_page

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # --- Build dynamic WHERE clause ---
    where_clauses = ["role='admin'"]
    params = []

    if search:
        where_clauses.append("full_name LIKE %s")
        params.append(f"%{search}%")

    if status == "active":
        where_clauses.append("is_active=1")
    elif status == "inactive":
        where_clauses.append("is_active=0")

    where_sql = " AND ".join(where_clauses)

    # --- Fetch admins ---
    query = f"""
        SELECT user_id, email, full_name, phone, role, is_active
        FROM users
        WHERE {where_sql}
        ORDER BY user_id DESC
        LIMIT %s OFFSET %s
    """
    cursor.execute(query, (*params, per_page, offset))
    admins = cursor.fetchall()

    # --- Fetch assigned courses for all admins at once ---
    if admins:
        admin_ids = [a['user_id'] for a in admins]
        format_ids = ','.join(str(i) for i in admin_ids)
        course_query = f"""
            SELECT ca.admin_id, c.course_id, c.course_name
            FROM course_admins ca
            JOIN courses c ON ca.course_id = c.course_id
            WHERE ca.admin_id IN ({format_ids}) AND ca.is_active=1
        """
        cursor.execute(course_query)
        course_rows = cursor.fetchall()

        # Map courses to each admin
        courses_map = {}
        for row in course_rows:
            courses_map.setdefault(row['admin_id'], []).append({'course_name': row['course_name']})

        for admin in admins:
            admin['courses'] = courses_map.get(admin['user_id'], [])

    # --- Count total admins ---
    count_query = f"SELECT COUNT(*) AS total FROM users WHERE {where_sql}"
    cursor.execute(count_query, params)
    total = cursor.fetchone()["total"]

    cursor.close()
    conn.close()

    total_pages = (total + per_page - 1) // per_page

    return render_template(
        "admins.html",
        admins=admins,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
        search=search,
        selected_status=status,
        total_admins=total
    )



# ------------------------------
# Add Admin 
# ------------------------------
from flask_mail import Mail

@superadmin_bp.route('/admins/add', methods=["GET", "POST"])
def add_admin():
    if session.get("user_role") != "super_admin":
        if request.method == "GET":
            return redirect(url_for("home"))
        else:
            return jsonify({"success": False, "error": "Unauthorized"}), 403

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == "GET":
        cursor.execute("SELECT course_id, course_name FROM courses WHERE is_active = 1")
        courses = cursor.fetchall()
        cursor.close()
        conn.close()
        return render_template("add_admin.html", courses=courses)

    # ---- POST ----
    full_name = request.form.get("full_name")
    email = request.form.get("email")
    phone = request.form.get("phone")
    is_active = request.form.get("is_active", 'True') == 'True'
    assigned_courses = request.form.getlist("courses")

    if not all([full_name, email]):
        return jsonify({"success": False, "error": "Missing required fields."})

    try:
        from flask import current_app
        from models.user_model import create_admin

        app = current_app._get_current_object()
        mail = Mail(app)
        admin_email = session.get("user_email") or app.config.get("MAIL_USERNAME")

        # Create the admin and send email
        admin_id, user_id, plain_password = create_admin(
            full_name=full_name,
            email=email,
            phone=phone,
            is_active=is_active,
            app=app,
            mail=mail,
            admin_email=admin_email
        )

        # Assign selected courses in course_admins
        for course_id in assigned_courses:
            cursor.execute(
                "INSERT INTO course_admins (course_id, admin_id) VALUES (%s, %s)",
                (course_id, user_id)
            )
        conn.commit()

        # Log activity
        log_activity(
            user_id=session.get("user_id"),
            action="Add Admin",
            table_affected="users",
            record_id=user_id,
            new_values={
                "full_name": full_name,
                "email": email,
                "role": "admin",
                "phone": phone,
                "is_active": is_active,
                "courses": assigned_courses
            }
        )

        return jsonify({
            "success": True,
            "message": "Admin created successfully!",
            "user": {
                "user_id": user_id,
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "role": "admin",
                "is_active": is_active,
                "courses": assigned_courses
            }
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)})
    finally:
        cursor.close()
        conn.close()



# ------------------------------
# Toggle Admin Status
# ------------------------------

@superadmin_bp.route("/toggle_admin_status/<int:user_id>", methods=["POST"])
def toggle_admin_status(user_id):
    data = request.get_json()
    new_status = bool(int(data.get("is_active", 0)))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_active = %s WHERE user_id = %s", (new_status, user_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True})


# ------------------------------
# Edit Admin
# ------------------------------

@superadmin_bp.route("/admins/edit/<int:user_id>", methods=["GET", "POST"])
def edit_admin(user_id):
    if session.get("user_role") != "super_admin":
        return redirect(url_for("home"))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # --- GET: Render edit form ---
    if request.method == "GET":
        # Fetch admin info
        cursor.execute("""
            SELECT user_id, full_name, email, phone, is_active, role
            FROM users
            WHERE user_id=%s
        """, (user_id,))
        admin = cursor.fetchone()
        if not admin:
            cursor.close()
            conn.close()
            return "Admin not found", 404

        # Fetch assigned courses
        cursor.execute("""
            SELECT course_id
            FROM course_admins
            WHERE admin_id=%s AND is_active=1
        """, (user_id,))
        assigned_courses = cursor.fetchall()
        admin['assigned_course_ids'] = [c['course_id'] for c in assigned_courses]

        # Fetch all courses
        cursor.execute("SELECT course_id, course_name FROM courses ORDER BY course_name")
        courses = cursor.fetchall()

        cursor.close()
        conn.close()
        return render_template("edit_admin.html", admin=admin, courses=courses)

    # --- POST: Update admin ---
    full_name = request.form.get("full_name")
    email = request.form.get("email")
    phone = request.form.get("phone")
    is_active = request.form.get("is_active", "True") == "True"
    assigned_course_ids = [int(cid) for cid in request.form.getlist("courses")]

    if not all([full_name, email]):
        cursor.close()
        conn.close()
        return "Missing required fields", 400

    # Fetch old admin data for logging
    cursor.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
    old_admin = cursor.fetchone()

    # Convert datetime objects to strings for JSON serialization
    old_admin_serializable = {}
    for k, v in old_admin.items():
        if isinstance(v, datetime):
            old_admin_serializable[k] = v.isoformat()
        else:
            old_admin_serializable[k] = v

    # Update users table
    cursor.execute("""
        UPDATE users
        SET full_name=%s, email=%s, phone=%s, is_active=%s
        WHERE user_id=%s
    """, (full_name, email, phone, is_active, user_id))
    conn.commit()

    # --- Update assigned courses ---
    # 1. Deactivate all existing assignments
    cursor.execute("UPDATE course_admins SET is_active=0 WHERE admin_id=%s", (user_id,))
    conn.commit()

    # 2. Insert new assignments or reactivate existing
    for cid in assigned_course_ids:
        cursor.execute("""
            INSERT INTO course_admins (course_id, admin_id, is_active)
            VALUES (%s, %s, 1)
            ON DUPLICATE KEY UPDATE is_active=1
        """, (cid, user_id))
    conn.commit()

    cursor.close()
    conn.close()

    # Log activity
    log_activity(
        user_id=session.get("user_id"),
        action="Edit Admin",
        table_affected="users",
        record_id=user_id,
        old_values=old_admin_serializable,
        new_values={
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "is_active": is_active,
            "assigned_courses": assigned_course_ids
        }
    )

    return redirect(url_for("super_admin.admins"))



# ------------------------------
# Delete Admin
# ------------------------------

@superadmin_bp.route("/admins/delete/<int:user_id>", methods=["GET"])
def delete_admin(user_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
    old_admin = cursor.fetchone()
    cursor.close()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()    
    return redirect(url_for("super_admin.admins"))

# ✅ Read-only page: render template (JS will fetch data)
@superadmin_bp.route('/assign_admin', methods=['GET'])
def assign_admin_page():
    if session.get('user_role') != 'super_admin':
        return redirect(url_for('auth.login'))

    return render_template('assign_admin.html')

# ✅ Return all assignments including unassigned admins
@superadmin_bp.route('/get_assigned_ajax', methods=['GET'])
def get_assigned_ajax():
    if session.get('user_role') != 'super_admin':
        return jsonify([])  # always return array

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        # Fetch all admins and their assigned courses (if any)
        cursor.execute("""
            SELECT u.user_id AS admin_id,
                   u.full_name AS admin_name,
                   c.course_id,
                   c.course_name
            FROM users u
            LEFT JOIN course_admins ca 
                ON u.user_id = ca.admin_id AND ca.is_active=1
            LEFT JOIN courses c 
                ON ca.course_id = c.course_id
            WHERE u.role='admin' AND u.is_active=1
            ORDER BY u.full_name, c.course_name
        """)
        assignments = cursor.fetchall()  # list of dicts

    except Exception as e:
        print("Error in get_assigned_ajax:", e)
        return jsonify([])

    finally:
        cursor.close()
        conn.close()

    return jsonify(assignments)




# # -------------------------------------------------------------------------------------------------------------------------------------------- 
#                                                    Trainer Management Block
# # --------------------------------------------------------------------------------------------------------------------------------------------     

# ------------------------------
# Add Trainer
# ------------------------------

@superadmin_bp.route('/add_trainer', methods=['GET', 'POST'])
def add_trainer():
    if session.get("user_role") != "super_admin":
        if request.method == "GET":
            return redirect(url_for("auth.login"))
        else:
            return jsonify({"success": False, "error": "Unauthorized"}), 403

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # ---- GET: Show form ----
    if request.method == "GET":
        try:
            cursor.execute("SELECT course_id, course_name FROM courses WHERE is_active=1")
            courses = cursor.fetchall()
        except Exception as e:
            print("Error fetching courses:", e)
            courses = []
        finally:
            cursor.close()
            conn.close()
        return render_template("add_trainer.html", courses=courses)

    # ---- POST: Create Trainer ----
    full_name = request.form.get("full_name")
    email = request.form.get("email")
    phone = request.form.get("phone")
    is_active = request.form.get("is_active", 'True') == 'True'
    assigned_courses = request.form.getlist("courses")

    if not all([full_name, email]):
        return jsonify({"success": False, "error": "Missing required fields."})

    try:
        from flask import current_app
        from models.user_model import create_trainer

        app = current_app._get_current_object()  # <-- FIXED: get the real app object
        mail = Mail(app)
        admin_email = session.get("user_email") or app.config.get("MAIL_USERNAME")

        trainer_id, user_id, plain_password = create_trainer(
            full_name=full_name,
            email=email,
            phone=phone,
            is_active=is_active,
            app=app,
            mail=mail,
            admin_email=admin_email
        )

        # Assign selected courses in course_trainers
        for course_id in assigned_courses:
            cursor.execute(
                """INSERT INTO course_trainers (course_id, trainer_id)
                   VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE is_active=TRUE, assigned_date=CURRENT_DATE""",
                (course_id, user_id)
            )
        conn.commit()

        # Log activity
        log_activity(
            user_id=session.get("user_id"),
            action="Add Trainer",
            table_affected="users",
            record_id=user_id,
            new_values={
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "role": "trainer",
                "is_active": is_active,
                "courses": assigned_courses
            }
        )

        return jsonify({
            "success": True,
            "message": "Trainer created successfully!",
            "user": {
                "user_id": user_id,
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "role": "trainer",
                "is_active": is_active,
                "courses": assigned_courses
            }
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)})
    finally:
        cursor.close()
        conn.close()

# ------------------------------
# View Trainers with Assigned Courses 
# ------------------------------

@superadmin_bp.route('/trainers')
def trainers():
    if session.get('user_role') != 'super_admin':
        return redirect(url_for('auth.login'))

    # --- Get query parameters ---
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    search = request.args.get('search', '', type=str).strip()
    status = request.args.get('status', '', type=str)
    offset = (page - 1) * per_page

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # --- Build WHERE clauses dynamically ---
    where_clauses = ["u.role = 'trainer'"]
    params = []

    if search:
        where_clauses.append("(u.full_name LIKE %s OR u.email LIKE %s OR u.phone LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    if status == "active":
        where_clauses.append("u.is_active = 1")
    elif status == "inactive":
        where_clauses.append("u.is_active = 0")

    where_sql = " AND ".join(where_clauses)

    # --- Fetch trainers with pagination ---
    query = f"""
        SELECT u.user_id, u.full_name, u.email, u.phone, u.role, u.is_active
        FROM users u
        WHERE {where_sql}
        ORDER BY u.user_id DESC
        LIMIT %s OFFSET %s
    """
    cursor.execute(query, (*params, per_page, offset))
    trainers = cursor.fetchall()

    # --- Count total trainers for pagination ---
    count_query = f"SELECT COUNT(*) AS total FROM users u WHERE {where_sql}"
    cursor.execute(count_query, params)
    total = cursor.fetchone()['total']

    # --- Fetch assigned courses for all trainers in one query ---
    trainer_ids = [t['user_id'] for t in trainers]
    course_map = {}
    if trainer_ids:
        format_strings = ",".join(["%s"] * len(trainer_ids))
        cursor.execute(f"""
            SELECT ct.trainer_id, c.course_name
            FROM course_trainers ct
            JOIN courses c ON ct.course_id = c.course_id
            WHERE ct.trainer_id IN ({format_strings}) AND ct.is_active = 1
        """, tuple(trainer_ids))
        for row in cursor.fetchall():
            course_map.setdefault(row['trainer_id'], []).append({"course_name": row['course_name']})

    # --- Attach courses to trainers ---
    for trainer in trainers:
        trainer['courses'] = course_map.get(trainer['user_id'], [])

    cursor.close()
    conn.close()

    # --- Calculate pagination ---
    total_pages = (total + per_page - 1) // per_page

    return render_template(
        'trainers.html',
        trainers=trainers,
        page=page,
        total_pages=total_pages,
        search=search,
        per_page=per_page,
        selected_status=status,
        total_trainers=total
    )


# ------------------------------
# Edit Trainer (GET)
# ------------------------------
@superadmin_bp.route('/trainers/edit/<int:trainer_id>', methods=['GET'])
def edit_trainer(trainer_id):
    if session.get('user_role') != 'super_admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch trainer info
    cursor.execute("""
        SELECT user_id, full_name, email, phone, role, is_active
        FROM users
        WHERE user_id=%s AND role='trainer'
    """, (trainer_id,))
    trainer = cursor.fetchone()

    if not trainer:
        cursor.close()
        conn.close()
        return "Trainer not found", 404

    # Fetch all active courses
    cursor.execute("SELECT course_id, course_name FROM courses WHERE is_active=1")
    courses = cursor.fetchall()

    # Fetch courses assigned to this trainer
    cursor.execute("""
        SELECT course_id
        FROM course_trainers
        WHERE trainer_id=%s AND is_active=1
    """, (trainer_id,))
    assigned_courses = [row['course_id'] for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    return render_template(
        'edit_trainer.html',
        trainer=trainer,
        courses=courses,
        assigned_courses=assigned_courses
    )


# ------------------------------
# Edit Trainer (POST)
# ------------------------------
@superadmin_bp.route('/trainers/edit/<int:trainer_id>', methods=['POST'])
def update_trainer(trainer_id):
    if session.get('user_role') != 'super_admin':
        return redirect(url_for('auth.login'))

    full_name = request.form.get('full_name')
    email = request.form.get('email')
    phone = request.form.get('phone')
    is_active = request.form.get('is_active', 'True') == 'True'
    courses = request.form.getlist('courses')

    if not all([full_name, email]):
        return "Missing required fields", 400

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch old trainer data for logging
    cursor.execute("SELECT * FROM users WHERE user_id=%s AND role='trainer'", (trainer_id,))
    old_trainer = cursor.fetchone()

    # Update trainer info
    cursor.execute("""
        UPDATE users
        SET full_name=%s, email=%s, phone=%s, is_active=%s
        WHERE user_id=%s AND role='trainer'
    """, (full_name, email, phone, is_active, trainer_id))

    # Deactivate all current course assignments
    cursor.execute("UPDATE course_trainers SET is_active=0 WHERE trainer_id=%s", (trainer_id,))

    # Assign selected courses
    for course_id in courses:
        cursor.execute("""
            INSERT INTO course_trainers (course_id, trainer_id, is_active, assigned_date)
            VALUES (%s, %s, TRUE, CURRENT_DATE)
            ON DUPLICATE KEY UPDATE is_active=TRUE, assigned_date=CURRENT_DATE
        """, (course_id, trainer_id))

    conn.commit()
    cursor.close()
    conn.close()

    # Example usage:
    log_activity(
    user_id=session.get("user_id"),
    action="Edit Trainer",
    table_affected="users",
    record_id=trainer_id,
    old_values=serialize_for_json(old_trainer),
    new_values=serialize_for_json({
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "is_active": is_active,
        "courses": courses
    })
    )

    return redirect(url_for('super_admin.trainers'))



# ------------------------------
# Delete Trainer
# ------------------------------
@superadmin_bp.route('/trainers/delete/<int:trainer_id>', methods=['GET'])
def delete_trainer(trainer_id):
    if session.get('user_role') != 'super_admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE user_id=%s AND role='trainer'", (trainer_id,))
    old_trainer = cursor.fetchone()
    cursor.close()

    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE user_id=%s AND role='trainer'", (trainer_id,))
    cursor.execute("UPDATE course_trainers SET is_active=0 WHERE trainer_id=%s", (trainer_id,))
    conn.commit()
    cursor.close()
    conn.close()

    # Serialize old_trainer to avoid datetime JSON issues
    old_trainer_serialized = serialize_for_json(old_trainer)

    # Log activity
    log_activity(
        user_id=session.get("user_id"),
        action="Delete Trainer",
        table_affected="users",
        record_id=trainer_id,
        old_values=old_trainer_serialized
    )

    return redirect(url_for('super_admin.trainers'))

# ------------------------------
# Assign Courses to Trainer (GET Page)
# ------------------------------
# ------------------------------
# Assign Courses to Trainer Page
# ------------------------------
@superadmin_bp.route('/assign_courses', methods=['GET'])
def assign_courses_page():
    # Only super admin can access
    if session.get('user_role') != 'super_admin':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Fetch all active trainers
        cursor.execute("""
            SELECT user_id, full_name 
            FROM users 
            WHERE role='trainer' AND is_active=1 
            ORDER BY full_name
        """)
        trainers = cursor.fetchall()

        # Fetch all active courses
        cursor.execute("""
            SELECT course_id, course_name 
            FROM courses 
            WHERE is_active=1 
            ORDER BY course_name
        """)
        courses = cursor.fetchall()

    finally:
        cursor.close()
        conn.close()

    # Render page: table will be dynamically populated via AJAX
    return render_template('assign_courses.html', trainers=trainers, courses=courses)

# ------------------------------
# Get All Trainer Assignments (AJAX GET)
# ------------------------------
@superadmin_bp.route('/get_trainer_assignments_ajax', methods=['GET'])
def get_trainer_assignments_ajax():
    if session.get('user_role') != 'super_admin':
        return jsonify([])

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Return trainer_id, trainer_name, course_id, course_name
    cursor.execute("""
        SELECT 
            u.user_id AS trainer_id,
            u.full_name AS trainer_name,
            c.course_id,
            c.course_name
        FROM users u
        LEFT JOIN course_trainers ct ON u.user_id = ct.trainer_id AND ct.is_active=1
        LEFT JOIN courses c ON ct.course_id = c.course_id
        WHERE u.role='trainer' AND u.is_active=1
        ORDER BY u.full_name, c.course_name
    """)
    assignments = cursor.fetchall()

    cursor.close()
    conn.close()
    return jsonify(assignments)


# ------------------------------
# Get all assignments (Admins + Trainers)
# ------------------------------
@superadmin_bp.route('/get_all_assignments_ajax', methods=['GET'])
def get_all_assignments_ajax():
    if session.get('user_role') != 'super_admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Admin assignments
        cursor.execute("""
            SELECT u.user_id AS admin_id, u.full_name AS admin_name, c.course_name
            FROM users u
            LEFT JOIN course_admins ca ON u.user_id = ca.admin_id AND ca.is_active=1
            LEFT JOIN courses c ON ca.course_id = c.course_id
            WHERE u.role='admin'
        """)
        admin_assignments = cursor.fetchall()

        # Trainer assignments
        cursor.execute("""
            SELECT u.user_id AS trainer_id, u.full_name AS trainer_name, c.course_name
            FROM users u
            LEFT JOIN course_trainers ct ON u.user_id = ct.trainer_id AND ct.is_active=1
            LEFT JOIN courses c ON ct.course_id = c.course_id
            WHERE u.role='trainer'
        """)
        trainer_assignments = cursor.fetchall()

        response = {
            "status": "success",
            "admins": admin_assignments,
            "trainers": trainer_assignments
        }
    except Exception as e:
        response = {"status": "error", "message": str(e)}

    cursor.close()
    conn.close()
    return jsonify(response)


@superadmin_bp.route('/admins/profile/<int:user_id>', methods=['GET'])
def view_admin_profile(user_id):
    if session.get("user_role") != "super_admin":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT user_id, full_name, email, phone, role, is_active, created_at
            FROM users
            WHERE user_id = %s AND role = 'admin'
        """, (user_id,))
        admin = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    if not admin:
        return jsonify({"success": False, "error": "Admin not found"}), 404
    return jsonify({"success": True, "admin": admin})

@superadmin_bp.route('/get_trainer_courses/<int:trainer_id>', methods=['GET'])
def get_trainer_courses(trainer_id):
    if session.get('user_role') != 'super_admin':
        return jsonify([]), 403

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT course_id FROM course_trainers
        WHERE trainer_id=%s AND is_active=1
    """, (trainer_id,))
    assigned_courses = [row['course_id'] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return jsonify(assigned_courses)

# ✅ Get current assignments
@superadmin_bp.route('/get_assignments_ajax', methods=['GET'])
def get_assignments_ajax():
    if session.get('user_role') != 'super_admin':
        return jsonify([]), 403

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT 
            u.user_id AS admin_id,
            u.full_name AS admin_name,
            c.course_id,
            c.course_name
        FROM users u
        LEFT JOIN courses c 
            ON c.assigned_admin_id = u.user_id AND c.is_active = 1
        WHERE u.role = 'admin' AND u.is_active = 1
        ORDER BY u.full_name, c.course_name
    """)
    assignments = cursor.fetchall()

    cursor.close()
    conn.close()
    return jsonify(assignments)


# Toggle Trainer Status (AJAX)
@superadmin_bp.route("/toggle_trainer_status/<int:trainer_id>", methods=["POST"])
def toggle_trainer_status(trainer_id):
    if session.get("user_role") != "super_admin":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    data = request.get_json()
    new_status = bool(int(data.get("is_active", 0)))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_active = %s WHERE user_id = %s AND role = 'trainer'", (new_status, trainer_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True})

def save_profile_photo(file, user_id):
    # Get the file extension
    ext = os.path.splitext(file.filename)[1]
    # Create a unique filename
    filename = f"user_{user_id}_{uuid.uuid4().hex}{ext}"
    filepath = os.path.join('static/uploads/profile_photos', filename)
    file.save(filepath)
    return filename  # Save this in the database
