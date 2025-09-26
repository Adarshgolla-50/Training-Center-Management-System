from flask import Blueprint, render_template, request, redirect, url_for, flash, session,current_app
from models import get_connection  
from werkzeug.utils import secure_filename
import os
from datetime import datetime
from calendar import month_name

student_bp = Blueprint('student', __name__, template_folder="templates")

# ----------------------------
# Config
# ----------------------------
ASSIGNMENT_UPLOAD_FOLDER = 'static/uploads/assignments'
LEAVE_UPLOAD_FOLDER = 'static/uploads/leaves'
ALLOWED_ASSIGNMENT_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}
ALLOWED_LEAVE_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png'}

def allowed_file(filename, allowed_ext):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_ext

@student_bp.route('/dashboard')
def dashboard():
    if session.get('user_role') != 'student':
        return redirect(url_for('auth.login'))

    student_user_id = session.get('user_id')
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get student_id from students table
    cursor.execute("SELECT student_id FROM students WHERE user_id=%s", (student_user_id,))
    student = cursor.fetchone()
    if not student:
        conn.close()
        return "Student profile not found!", 404

    student_id = student['student_id']

    # ----------------------------
    # 1️⃣ Active batches
    cursor.execute("""
        SELECT b.batch_id, b.batch_name, c.course_name, b.start_date, b.end_date, 
               b.status, u.full_name as trainer_name
        FROM enrollments e
        JOIN batches b ON e.batch_id = b.batch_id
        JOIN courses c ON b.course_id = c.course_id
        LEFT JOIN users u ON b.trainer_id = u.user_id
        WHERE e.student_id=%s AND e.status='ACTIVE' AND b.is_active=TRUE
    """, (student_id,))
    batches = cursor.fetchall()

    # ----------------------------
    # 2️⃣ Assignment summary
    cursor.execute("""
        SELECT COUNT(*) as total_assignments,
               SUM(CASE WHEN s.status='GRADED' THEN 1 ELSE 0 END) as completed,
               SUM(CASE WHEN s.status='PENDING' OR s.status IS NULL THEN 1 ELSE 0 END) as pending
        FROM assignments a
        JOIN enrollments e ON e.batch_id = a.batch_id
        LEFT JOIN assignment_submissions s 
            ON s.assignment_id = a.assignment_id AND s.student_id=%s
        WHERE e.student_id=%s
    """, (student_id, student_id))
    assignment_summary = cursor.fetchone()

    # ----------------------------
    # 3️⃣ Pending assignments (for table)
    cursor.execute("""
        SELECT a.assignment_id, a.title, a.due_date, b.batch_name, c.course_name
        FROM assignments a
        JOIN batches b ON a.batch_id = b.batch_id
        JOIN courses c ON a.course_id = c.course_id
        LEFT JOIN assignment_submissions s 
            ON a.assignment_id = s.assignment_id AND s.student_id = %s
        JOIN enrollments e ON e.batch_id = a.batch_id AND e.student_id=%s
        WHERE s.status IS NULL OR s.status='PENDING'
        ORDER BY a.due_date ASC
    """, (student_id, student_id))
    pending_assignments = cursor.fetchall()

    # ----------------------------
    # 4️⃣ Leave summary
    cursor.execute("""
        SELECT COUNT(*) as total, 
               SUM(status='approved') as approved,
               SUM(status='pending') as pending,
               SUM(status='rejected') as rejected
        FROM leave_applications
        WHERE student_id=%s
    """, (student_id,))
    leave_summary = cursor.fetchone() or {'total':0,'approved':0,'pending':0,'rejected':0}

    # Remaining leave balance per type (with total days)
    cursor.execute("""
        SELECT lt.type_name, slb.available_days, lt.default_max_days AS total_days
        FROM student_leave_balances slb
        JOIN leave_types lt ON slb.type_id = lt.type_id
        WHERE slb.student_id=%s
    """, (student_id,))
    leave_balances = cursor.fetchall()

    # ----------------------------
    # 5️⃣ Attendance %
    cursor.execute("""
        SELECT COUNT(*) as total_days,
               SUM(CASE WHEN status='PRESENT' THEN 1 ELSE 0 END) as present_days
        FROM attendance
        WHERE student_id=%s
    """, (student_id,))
    attendance_data = cursor.fetchone()
    attendance_percentage = 0
    if attendance_data['total_days']:
        attendance_percentage = round((attendance_data['present_days']/attendance_data['total_days'])*100, 2)

    # ----------------------------
    # 6️⃣ Recent activities
    cursor.execute("""
        SELECT action, table_affected, timestamp
        FROM activity_logs
        WHERE user_id=%s
        ORDER BY timestamp DESC
        LIMIT 10
    """, (student_user_id,))
    recent_activities = cursor.fetchall()

    # Assignment marks per assignment (latest 10)
    cursor.execute("""
        SELECT a.title, IFNULL(s.marks_obtained, 0) AS marks_obtained, a.total_marks
        FROM assignments a
        JOIN enrollments e ON e.batch_id = a.batch_id
        LEFT JOIN assignment_submissions s ON s.assignment_id = a.assignment_id AND s.student_id=%s
        WHERE e.student_id=%s
        ORDER BY a.due_date ASC
        LIMIT 10
    """, (student_id, student_id))
    assignment_marks = cursor.fetchall()

    # Prepare data for chart
    marks_labels = [row['title'] for row in assignment_marks]
    marks_data = [float(row['marks_obtained']) if row['marks_obtained'] is not None else 0 for row in assignment_marks]
    marks_total = [float(row['total_marks']) for row in assignment_marks]

    # ----------------------------
    # Upcoming assignments (next 5, due date >= today)
    cursor.execute("""
    SELECT a.title, b.batch_name, c.course_name, a.due_date
    FROM assignments a
    JOIN batches b ON a.batch_id = b.batch_id
    JOIN courses c ON a.course_id = c.course_id
    JOIN enrollments e ON e.batch_id = a.batch_id
    WHERE e.student_id = %s AND a.due_date >= CURDATE()
    ORDER BY a.due_date ASC
    LIMIT 5
""", (student_id,))
    upcoming_assignments = cursor.fetchall()

    # Performance by topic (average marks per topic)
    cursor.execute("""
        SELECT t.topic_name,
               COALESCE(AVG(s.marks_obtained), 0) AS average_marks,
               COALESCE(MAX(a.total_marks), 100) AS total_marks
        FROM assignments a
        JOIN topics t ON a.topic_id = t.topic_id
        JOIN enrollments e ON e.batch_id = a.batch_id
        LEFT JOIN assignment_submissions s ON s.assignment_id = a.assignment_id AND s.student_id=%s
        WHERE e.student_id=%s
        GROUP BY t.topic_name
        ORDER BY t.topic_name
    """, (student_id, student_id))
    topic_performance = cursor.fetchall()
    topic_labels = [row['topic_name'] for row in topic_performance] if topic_performance else []
    topic_average_marks = [row['average_marks'] for row in topic_performance] if topic_performance else []
    topic_total_marks = [row['total_marks'] for row in topic_performance] if topic_performance else []

    # Get attendance records for the student
    cursor.execute("""
        SELECT MONTH(attendance_date) AS month, YEAR(attendance_date) AS year,
               COUNT(*) AS total_days,
               SUM(status = 'PRESENT') AS present_days
        FROM attendance
        WHERE student_id = %s
        GROUP BY year, month
        ORDER BY year, month
    """, (student_id,))
    attendance_rows = cursor.fetchall()

    attendance_month_labels = []
    attendance_month_percentages = []

    for row in attendance_rows:
        label = f"{month_name[row['month']][:3]} {row['year']}"
        percent = round((row['present_days'] / row['total_days']) * 100, 1) if row['total_days'] else 0
        attendance_month_labels.append(label)
        attendance_month_percentages.append(percent)

    # Overall average marks for assignments
    cursor.execute("""
        SELECT AVG(marks_obtained) AS avg_points
        FROM assignment_submissions
        WHERE student_id = %s AND marks_obtained IS NOT NULL
    """, (student['student_id'],))
    avg_points_row = cursor.fetchone()
    avg_points = round(avg_points_row['avg_points'], 2) if avg_points_row and avg_points_row['avg_points'] is not None else 0

    # Topic-wise average marks
    cursor.execute("""
        SELECT t.topic_name, ROUND(AVG(s.marks_obtained), 2) AS avg_marks
        FROM assignments a
        JOIN topics t ON a.topic_id = t.topic_id
        JOIN enrollments e ON e.batch_id = a.batch_id
        LEFT JOIN assignment_submissions s ON s.assignment_id = a.assignment_id AND s.student_id=%s
        WHERE e.student_id=%s
        GROUP BY t.topic_name
        ORDER BY t.topic_name
    """, (student_id, student_id))
    topic_performance = cursor.fetchall()
    topic_labels = [row['topic_name'] for row in topic_performance] if topic_performance else []
    topic_average_marks = [row['avg_marks'] or 0 for row in topic_performance] if topic_performance else []

    # Last 5 assignments (most recent)
    cursor.execute("""
        SELECT a.title, s.marks_obtained, a.total_marks
        FROM assignments a
        JOIN assignment_submissions s ON s.assignment_id = a.assignment_id
        WHERE s.student_id = %s
        ORDER BY s.submitted_at DESC
        LIMIT 5
    """, (student_id,))
    last5 = cursor.fetchall()
    last5_labels = [row['title'] for row in last5] if last5 else []
    last5_marks = [row['marks_obtained'] or 0 for row in last5] if last5 else []
    last5_total = [row['total_marks'] or 0 for row in last5] if last5 else []

    conn.close()

    return render_template(
        "student_dashboard.html",
        batches=batches,
        total_batches=len(batches),
        assignment_summary=assignment_summary,
        pending_assignments=pending_assignments,
        total_pending_assignments=len(pending_assignments),
        leave_summary=leave_summary,
        leave_balances=leave_balances,
        attendance_percentage=attendance_percentage,
        recent_activities=recent_activities,
        marks_labels=marks_labels,
        marks_data=marks_data,
        marks_total=marks_total,
        upcoming_assignments=upcoming_assignments,  # <-- Add this line
        topic_labels=topic_labels,
        topic_average_marks=topic_average_marks,
        last5_labels=last5_labels,
        last5_marks=last5_marks,
        last5_total=last5_total,
        attendance_month_labels=attendance_month_labels,
        attendance_month_percentages=attendance_month_percentages,
        avg_points=avg_points,
    )

# ----------------------------
# Assignments List
# ----------------------------
@student_bp.route('/assignments')
def assignments():
    if session.get("user_role") != "student":
        return redirect(url_for("auth.login"))

    student_user_id = session.get('user_id')
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get student_id
    cursor.execute("SELECT student_id FROM students WHERE user_id=%s", (student_user_id,))
    student = cursor.fetchone()
    if not student:
        conn.close()
        flash("Student profile not found.", "danger")
        return redirect(url_for('auth.login'))

    student_id = student['student_id']

    # Filters & sorting
    topic_id = request.args.get('topic_id')
    sort_by = request.args.get('sort_by', 'due_date')

    query = """
    SELECT a.assignment_id, a.title, a.description, a.due_date, a.attachment,
           t.topic_name, c.course_name,
           s.status AS submission_status,
           s.grade,
           s.marks_obtained,
           a.total_marks,
           -- Overdue: only if not submitted or pending
           CASE WHEN a.due_date < CURDATE() AND (s.status IS NULL OR s.status != 'GRADED') THEN TRUE ELSE FALSE END AS is_overdue,
           -- Due soon: only if not submitted or pending
           CASE WHEN a.due_date >= CURDATE() 
                    AND a.due_date <= DATE_ADD(CURDATE(), INTERVAL 3 DAY)
                    AND (s.status IS NULL OR s.status = 'PENDING')
                THEN TRUE ELSE FALSE END AS is_due_soon
    FROM assignments a
    JOIN batches b ON a.batch_id = b.batch_id
    JOIN courses c ON a.course_id = c.course_id
    JOIN topics t ON a.topic_id = t.topic_id
    JOIN enrollments e ON e.batch_id = a.batch_id
    LEFT JOIN assignment_submissions s 
        ON s.assignment_id = a.assignment_id AND s.student_id = %s
    WHERE e.student_id = %s
    """

    params = [student_id, student_id]

    # Apply topic filter
    if topic_id:
        query += " AND t.topic_id = %s"
        params.append(topic_id)

    # Apply sorting
    if sort_by in ["due_date", "title", "topic_name"]:
        query += f" ORDER BY {sort_by}"
    else:
        query += " ORDER BY due_date"

    cursor.execute(query, tuple(params))
    assignments = cursor.fetchall()

    # Group assignments by course
    courses = {}
    for a in assignments:
        course = a['course_name']
        if course not in courses:
            courses[course] = []
        courses[course].append(a)

    # Fetch topics for filter dropdown
    cursor.execute("""
        SELECT DISTINCT t.topic_id, t.topic_name
        FROM topics t
        JOIN assignments a ON a.topic_id = t.topic_id
        JOIN batches b ON a.batch_id = b.batch_id
        JOIN enrollments e ON e.batch_id = b.batch_id
        WHERE e.student_id = %s
        ORDER BY t.topic_name
    """, (student_id,))
    topics = cursor.fetchall()

    conn.close()

    return render_template(
        'student_assignments.html',
        courses=courses,
        topics=topics,
        selected_topic=topic_id or '',
        sort_by=sort_by
    )





@student_bp.route('/student/assignment/<int:assignment_id>')
def assignment_details(assignment_id):
    # Ensure the user is a student
    if session.get('user_role') != 'student':
        return redirect(url_for('auth.login'))

    student_user_id = session.get('user_id')

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # 1️⃣ Get student_id from students table
    cursor.execute("SELECT student_id FROM students WHERE user_id=%s", (student_user_id,))
    student = cursor.fetchone()
    if not student:
        return "Student record not found.", 404
    student_id = student['student_id']

    # 2️⃣ Get assignment details with course, batch, and topic
    cursor.execute("""
        SELECT a.*, 
               b.batch_name, 
               c.course_name, 
               t.topic_name
        FROM assignments a
        JOIN batches b ON a.batch_id = b.batch_id
        JOIN courses c ON a.course_id = c.course_id
        JOIN topics t ON a.topic_id = t.topic_id
        WHERE a.assignment_id = %s
    """, (assignment_id,))
    assignment = cursor.fetchone()
    if not assignment:
        return "Assignment not found.", 404

    # 3️⃣ Get student submission (if exists)
    cursor.execute("""
        SELECT * 
        FROM assignment_submissions
        WHERE assignment_id = %s AND student_id = %s
    """, (assignment_id, student_id))
    submission = cursor.fetchone()  # Can be None if not submitted

    cursor.close()
    conn.close()

    return render_template(
        'assignment_details.html',
        assignment=assignment,
        submission=submission
    )


# ----------------------------
# Assignment Submission
# ----------------------------
@student_bp.route('/assignment/<int:assignment_id>/feedback')
def view_feedback(assignment_id):
    if session.get('user_role') != 'student':
        return redirect(url_for('auth.login'))

    student_user_id = session.get('user_id')
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get student_id
    cursor.execute("SELECT student_id FROM students WHERE user_id=%s", (student_user_id,))
    student = cursor.fetchone()
    if not student:
        conn.close()
        return "Student profile not found!", 404
    student_id = student['student_id']

    # Get assignment details
    cursor.execute("""
        SELECT a.*, b.batch_name, c.course_name, t.topic_name
        FROM assignments a
        JOIN batches b ON a.batch_id = b.batch_id
        JOIN courses c ON a.course_id = c.course_id
        JOIN topics t ON a.topic_id = t.topic_id
        WHERE a.assignment_id = %s
    """, (assignment_id,))
    assignment = cursor.fetchone()
    if not assignment:
        conn.close()
        return "Assignment not found.", 404

    # Get submission with feedback
    cursor.execute("""
        SELECT * FROM assignment_submissions
        WHERE assignment_id = %s AND student_id = %s
    """, (assignment_id, student_id))
    submission = cursor.fetchone()

    cursor.close()
    conn.close()

    return render_template('assignment_feedback.html', assignment=assignment, submission=submission)


@student_bp.route('/assignment/<int:assignment_id>/submit', methods=['GET', 'POST'])
def submit_assignment(assignment_id):
    student_user_id = session.get('user_id')
    if not student_user_id or session.get('user_role') != 'student':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get student_id
    cursor.execute("SELECT student_id FROM students WHERE user_id=%s", (student_user_id,))
    student = cursor.fetchone()
    if not student:
        conn.close()
        flash("Student profile not found.", "danger")
        return redirect(url_for('auth.login'))
    student_id = student['student_id']

    # Get assignment info
    cursor.execute("""
        SELECT a.*, b.batch_name, c.course_name, t.topic_name
        FROM assignments a
        JOIN batches b ON a.batch_id = b.batch_id
        JOIN courses c ON a.course_id = c.course_id
        JOIN topics t ON a.topic_id = t.topic_id
        WHERE a.assignment_id=%s
    """, (assignment_id,))
    assignment = cursor.fetchone()
    if not assignment:
        conn.close()
        flash("Assignment not found.", "danger")
        return redirect(url_for('student.assignments'))

    # Check if submission exists
    cursor.execute("""
        SELECT * FROM assignment_submissions
        WHERE assignment_id=%s AND student_id=%s
    """, (assignment_id, student_id))
    submission = cursor.fetchone()

    if request.method == 'POST':
        comments = request.form.get('comments')
        file = request.files.get('assignment_file')
        file_name = submission['file_path'] if submission else None

        # Save uploaded file
        if file and allowed_file(file.filename, ALLOWED_ASSIGNMENT_EXTENSIONS):
            filename = secure_filename(f"{student_id}_{assignment_id}_{file.filename}")
            uploads_dir = os.path.join(current_app.root_path, ASSIGNMENT_UPLOAD_FOLDER)
            os.makedirs(uploads_dir, exist_ok=True)
            file.save(os.path.join(uploads_dir, filename))
            file_name = filename

        is_late = datetime.now().date() > assignment['due_date']

        if submission:
            cursor.execute("""
                UPDATE assignment_submissions
                SET comments=%s, file_path=%s, submitted_at=%s, status='SUBMITTED',
                    is_late=%s, submission_version=submission_version+1
                WHERE submission_id=%s
            """, (comments, file_name, datetime.now(), is_late, submission['submission_id']))
        else:
            cursor.execute("""
                INSERT INTO assignment_submissions
                (assignment_id, student_id, comments, file_path, status, is_late)
                VALUES (%s, %s, %s, %s, 'SUBMITTED', %s)
            """, (assignment_id, student_id, comments, file_name, is_late))

        conn.commit()
        conn.close()
        flash("Assignment submitted successfully!", "success")
        return redirect(url_for('student.assignments'))

    conn.close()
    return render_template('submit_assignment.html', assignment=assignment, submission=submission)

# ----------------------------
# Leave Management
# ----------------------------
@student_bp.route("/apply_leave", methods=["GET", "POST"])
def apply_leave():
    if session.get("user_role") != "student":
        return redirect(url_for("auth.login"))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get student_id
    cursor.execute("SELECT student_id FROM students WHERE user_id=%s", (session.get("user_id"),))
    student = cursor.fetchone()
    if not student:
        conn.close()
        flash("Student profile not found.", "danger")
        return redirect(url_for("student.dashboard"))
    student_id = student['student_id']

    # Get student's active batch
    cursor.execute("""
        SELECT b.batch_id, b.personal_leaves, b.medical_leaves, b.educational_leaves
        FROM enrollments e
        JOIN batches b ON e.batch_id = b.batch_id
        WHERE e.student_id=%s AND e.status='ACTIVE'
        LIMIT 1
    """, (student_id,))
    batch = cursor.fetchone()
    if not batch:
        conn.close()
        flash("No active batch found for your account.", "warning")
        return redirect(url_for("student.dashboard"))

    # Get leave types
    cursor.execute("SELECT * FROM leave_types WHERE is_active=1")
    leave_types = cursor.fetchall()

    # Calculate remaining leave days per type
    leave_limits = {
        "Personal Leave": batch['personal_leaves'],
        "Medical Leave": batch['medical_leaves'],
        "Educational Leave": batch['educational_leaves']
    }

    available_leave_types = []
    for lt in leave_types:
        max_days = leave_limits.get(lt['type_name'])
        if max_days is not None and max_days > 0:
            cursor.execute("""
                SELECT SUM(DATEDIFF(end_date, start_date) + 1) AS used_days
                FROM leave_applications
                WHERE student_id=%s AND type_id=%s AND status IN ('pending','approved')
            """, (student_id, lt['type_id']))
            used = cursor.fetchone()['used_days'] or 0
            remaining = max_days - used
            lt['remaining'] = remaining
            available_leave_types.append(lt)
        else:
            lt['remaining'] = '∞'
            available_leave_types.append(lt)

    # Sort leave types: Personal -> Educational -> Medical
    order = {"Personal Leave": 1, "Educational Leave": 2, "Medical Leave": 3}
    available_leave_types.sort(key=lambda x: order.get(x['type_name'], 99))

    if request.method == "POST":
        type_id = int(request.form.get("type_id"))
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")
        reason = request.form.get("reason")
        file = request.files.get("supporting_document")
        file_path = None

        # Check if type is allowed
        selected_leave = next((l for l in available_leave_types if l['type_id'] == type_id), None)
        if not selected_leave:
            flash("You cannot apply for this leave type anymore.", "danger")
            conn.close()
            return redirect(url_for("student.apply_leave"))

        # Handle file upload
        if file and allowed_file(file.filename, ALLOWED_LEAVE_EXTENSIONS):
            filename = secure_filename(f"{student_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
            os.makedirs(LEAVE_UPLOAD_FOLDER, exist_ok=True)
            file_path = os.path.join(LEAVE_UPLOAD_FOLDER, filename)
            file.save(file_path)
            file_path = file_path.replace("\\", "/")

        cursor.execute("""
            INSERT INTO leave_applications (student_id, type_id, start_date, end_date, reason, supporting_document)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (student_id, type_id, start_date, end_date, reason, file_path))
        conn.commit()
        conn.close()
        flash("Leave request submitted successfully!", "success")
        return redirect(url_for("student.my_leaves"))

    conn.close()
    return render_template("apply_leave.html", leave_types=available_leave_types)




@student_bp.route("/my_leaves")
def my_leaves():
    if session.get("user_role") != "student":
        return redirect(url_for("auth.login"))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT student_id FROM students WHERE user_id=%s", (session.get("user_id"),))
    student = cursor.fetchone()
    if not student:
        conn.close()
        return "Student profile not found!", 404
    student_id = student['student_id']

    cursor.execute("""
        SELECT la.leave_id, lt.type_name, la.start_date, la.end_date, la.reason, 
               la.status, la.applied_at, la.reviewed_at, la.admin_comments
        FROM leave_applications la
        JOIN leave_types lt ON la.type_id = lt.type_id
        WHERE la.student_id = %s
        ORDER BY la.applied_at DESC
    """, (student_id,))
    leaves = cursor.fetchall()
    conn.close()

    return render_template("my_leaves.html", leaves=leaves)


@student_bp.route('/profile', methods=['GET', 'POST'])
def profile():
    if session.get('user_role') != 'student':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    user_id = session.get('user_id')

    # Fetch student info
    cursor.execute("""
        SELECT s.*, u.phone, u.full_name, u.email 
        FROM students s 
        JOIN users u ON s.user_id = u.user_id 
        WHERE s.user_id=%s
    """, (user_id,))
    student = cursor.fetchone()
    if not student:
        conn.close()
        flash("Student profile not found.", "danger")
        return redirect(url_for('student.dashboard'))


    if request.method == 'POST':
        dob = request.form.get('dob') or None
        guardian_name = request.form.get('guardian_name') or None
        address = request.form.get('address') or None
        city = request.form.get('city') or None
        state = request.form.get('state') or None
        country = request.form.get('country') or None
        zip_code = request.form.get('zip_code') or None
        emergency_name = request.form.get('emergency_name') or None
        emergency_phone = request.form.get('emergency_phone') or None
        blood_group = request.form.get('blood_group') or None

        # Profile photo
        profile_photo = student.get('profile_photo')
        file = request.files.get('profile_photo')
        if file and file.filename:
            filename = secure_filename(file.filename)
            upload_dir = os.path.join(current_app.root_path, 'static/uploads/profiles')
            os.makedirs(upload_dir, exist_ok=True)
            file.save(os.path.join(upload_dir, filename))
            profile_photo = filename
    

        # Update DB
        cursor.execute("""
            UPDATE students 
            SET dob=%s, guardian_name=%s, address=%s, city=%s, state=%s,
                country=%s, zip_code=%s, emergency_contact_name=%s, emergency_contact_phone=%s,
                blood_group=%s, profile_photo=%s 
            WHERE student_id=%s
        """, (
            dob, guardian_name, address, city, state, country, zip_code,
            emergency_name, emergency_phone, blood_group, profile_photo, student['student_id']
        ))
        conn.commit()

        # Update student dict for template
        student.update({
            'dob': dob,
            'guardian_name': guardian_name,
            'address': address,
            'city': city,
            'state': state,
            'country': country,
            'zip_code': zip_code,
            'emergency_contact_name': emergency_name,
            'emergency_contact_phone': emergency_phone,
            'blood_group': blood_group,
            'profile_photo': profile_photo
        })

        # Update session variable for profile image
        if profile_photo:
            session['profile_image'] = profile_photo
        else:
            session['profile_image'] = None

        flash("Profile updated successfully", "success")

    # Profile completion
    fields = [
        'dob', 'guardian_name', 'address', 'city', 'state', 'country', 'zip_code',
        'emergency_contact_name', 'emergency_contact_phone', 'blood_group', 'profile_photo'
    ]
    filled = sum(1 for f in fields if student.get(f))
    completion = int((filled / len(fields)) * 100)

    conn.close()
    return render_template('profile.html', student=student, completion=completion)


@student_bp.route('/view-profile')
def view_profile():
    # Ensure student is logged in
    if session.get('user_role') != 'student':
        flash("Please login to access your profile", "warning")
        return redirect(url_for('auth.login'))

    user_id = session.get('user_id')
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch student info
    cursor.execute("""
        SELECT s.*, u.full_name, u.email, u.phone
        FROM students s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.user_id = %s
    """, (user_id,))
    student = cursor.fetchone()

    if not student:
        flash("Student profile not found.", "danger")
        conn.close()
        return redirect(url_for('student.dashboard'))

    # Calculate profile completion percentage
    fields = [
        'dob', 'guardian_name', 'address', 'city', 'state', 'country', 'zip_code',
        'emergency_contact_name', 'emergency_contact_phone', 'blood_group', 'profile_photo'
    ]
    filled = sum(1 for f in fields if student.get(f))
    completion = int((filled / len(fields)) * 100)

    # Update session variable for profile image
    if student.get('profile_photo'):
        session['profile_image'] = student['profile_photo']
    else:
        session['profile_image'] = None

    conn.close()
    return render_template('view_profile.html', student=student, completion=completion)
