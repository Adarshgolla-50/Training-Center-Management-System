# =========================
# Imports and Config
# =========================
import matplotlib
matplotlib.use('Agg')  # Fix for Flask (no GUI needed)
import matplotlib.pyplot as plt
import io
import base64
from matplotlib.ticker import MaxNLocator
from flask import render_template, request, session, jsonify, url_for, redirect, current_app, Blueprint, flash, send_file
from models import get_connection
from datetime import datetime, date, timedelta
from werkzeug.utils import secure_filename
import os
import zipfile
import uuid
import json

trainer_bp = Blueprint('trainer', __name__, template_folder='templates')

# ----------------------------
# File Upload Config
# ----------------------------
ASSIGNMENT_UPLOAD_FOLDER = 'static/uploads/assignments'
FEEDBACK_UPLOAD_FOLDER = 'static/uploads/feedback'
ALLOWED_ASSIGNMENT_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}
ALLOWED_FEEDBACK_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}

# Ensure upload folders exist at startup
for folder in [ASSIGNMENT_UPLOAD_FOLDER, FEEDBACK_UPLOAD_FOLDER]:
    os.makedirs(os.path.join(os.getcwd(), folder), exist_ok=True)

def allowed_file(filename, allowed_ext):
    return bool(filename and '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_ext)

def working_days_between(start_date, end_date, holidays=[]):
    """
    Returns the number of working days (Mon-Fri) between two dates, excluding holidays.
    """
    day_count = 0
    current_day = start_date
    while current_day <= end_date:
        if current_day.weekday() < 5 and current_day not in holidays:  # Mon-Fri & not holiday
            day_count += 1
        current_day += timedelta(days=1)
    return day_count



# ------------------ Helper: check trainer login ------------------
def trainer_required():
    if session.get('user_role') != 'trainer':
        flash("Trainer login required!", "warning")
        return False
    return True

# ============================================================
# DASHBOARD
# ============================================================
@trainer_bp.route('/dashboard')
def trainer_dashboard():
    """
    Trainer dashboard: stats, charts, recent activities, upcoming assignments.
    """
    if not trainer_required():
        return redirect(url_for('auth.login'))

    trainer_id = session.get('user_id')
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # 1. Total Assigned Courses
    cursor.execute("""
        SELECT COUNT(DISTINCT course_id) AS total_courses
        FROM course_trainers
        WHERE trainer_id=%s AND is_active=1
    """, (trainer_id,))
    total_assigned_courses = cursor.fetchone()['total_courses']

    # 2. Active Batches
    cursor.execute("""
        SELECT COUNT(DISTINCT b.batch_id) AS active_batches
        FROM batches b
        JOIN course_trainers ct ON ct.course_id = b.course_id
        WHERE ct.trainer_id=%s AND b.status IN ('Upcoming','Ongoing') AND b.is_active=1
    """, (trainer_id,))
    active_batches = cursor.fetchone()['active_batches']

    # 3. Total Students
    cursor.execute("""
        SELECT COUNT(DISTINCT e.student_id) AS total_students
        FROM enrollments e
        JOIN batches b ON e.batch_id = b.batch_id
        JOIN course_trainers ct ON ct.course_id = b.course_id
        WHERE ct.trainer_id=%s
    """, (trainer_id,))
    total_students = cursor.fetchone()['total_students']

    # 4. Pending Leave Requests
    cursor.execute("""
        SELECT COUNT(*) AS pending_leaves
        FROM leave_applications la
        JOIN students s ON la.student_id = s.student_id
        JOIN enrollments e ON s.student_id=e.student_id
        JOIN batches b ON e.batch_id=b.batch_id
        WHERE b.trainer_id=%s AND la.status='pending'
    """, (trainer_id,))
    pending_leave_requests = cursor.fetchone()['pending_leaves']

    # 5. Recent Activities
    cursor.execute("""
        SELECT * FROM activity_logs
        WHERE user_id=%s
        ORDER BY timestamp DESC
        LIMIT 10
    """, (trainer_id,))
    recent_activities = cursor.fetchall()

    # 6. Course-wise Topics, Assignments, and Student Performance
    cursor.execute("""
        SELECT c.course_id, c.course_name,
            (SELECT COUNT(*) FROM topics t WHERE t.course_id=c.course_id AND t.trainer_id=%s) AS topics_count,
            (SELECT COUNT(*) FROM assignments a WHERE a.course_id=c.course_id AND a.trainer_id=%s) AS assignments_count,
            (SELECT COUNT(DISTINCT asub.student_id) 
             FROM assignments a
             LEFT JOIN assignment_submissions asub ON a.assignment_id=asub.assignment_id
             WHERE a.course_id=c.course_id AND a.trainer_id=%s AND asub.status='SUBMITTED') AS students_completed,
            (SELECT COUNT(DISTINCT e.student_id) 
             FROM enrollments e
             JOIN batches b ON e.batch_id=b.batch_id
             WHERE b.course_id=c.course_id AND b.trainer_id=%s) AS total_students_course,
            (SELECT AVG(COALESCE(asub.marks_obtained,0))
             FROM assignments a
             LEFT JOIN assignment_submissions asub ON asub.assignment_id=a.assignment_id
             WHERE a.course_id=c.course_id AND a.trainer_id=%s) AS avg_marks
        FROM courses c
        JOIN course_trainers ct ON ct.course_id=c.course_id
        WHERE ct.trainer_id=%s AND ct.is_active=1
    """, (trainer_id, trainer_id, trainer_id, trainer_id, trainer_id, trainer_id))
    course_stats = cursor.fetchall()

    
    

    # 9. Assignment Marks Distribution (for all assignments by this trainer)
    cursor.execute("""
        SELECT asub.marks_obtained
        FROM assignments a
        JOIN assignment_submissions asub ON a.assignment_id = asub.assignment_id
        WHERE a.trainer_id = %s AND asub.status IN ('SUBMITTED', 'GRADED') AND asub.marks_obtained IS NOT NULL
    """, (trainer_id,))
    marks = [row['marks_obtained'] for row in cursor.fetchall()]

    

    # 8. Helper function to generate Matplotlib charts
    def generate_chart(x_labels, y_values_list, y_labels, title):
        fig, ax = plt.subplots(figsize=(10,5))
        for y_values, label in zip(y_values_list, y_labels):
            ax.bar(x_labels, y_values, label=label, alpha=0.7)
        ax.set_xticks(range(len(x_labels)))
        ax.set_xticklabels(x_labels, rotation=30, ha='right')
        ax.set_ylabel("Count / Marks")
        ax.set_title(title)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.legend()
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', transparent=True)
        buf.seek(0)
        img_base64 = base64.b64encode(buf.getvalue()).decode('utf8')
        plt.close(fig)
        return img_base64

    # Course-wise chart: Topics & Assignments
    if course_stats:
        course_chart = generate_chart(
            [c['course_name'] for c in course_stats],
            [[c['topics_count'] or 0 for c in course_stats],
             [c['assignments_count'] or 0 for c in course_stats]],
            ['Topics', 'Assignments'],
            'Course-wise Topics & Assignments'
        )
        # Student completion chart
        completion_chart = generate_chart(
            [c['course_name'] for c in course_stats],
            [[c['students_completed'] or 0 for c in course_stats],
             [c['total_students_course'] or 0 for c in course_stats]],
            ['Completed', 'Total Students'],
            'Student Assignment Completion'
        )
        # Average marks chart
        performance_chart = generate_chart(
            [c['course_name'] for c in course_stats],
            [[c['avg_marks'] or 0 for c in course_stats]],
            ['Avg Marks'],
            'Student Performance (Average Marks)'
        )
    else:
        course_chart = completion_chart = performance_chart = None

    # 9. Assignment Marks Distribution (for all assignments by this trainer)
    cursor.execute("""
        SELECT asub.marks_obtained
        FROM assignments a
        JOIN assignment_submissions asub ON a.assignment_id = asub.assignment_id
        WHERE a.trainer_id = %s AND asub.status IN ('SUBMITTED', 'GRADED') AND asub.marks_obtained IS NOT NULL
    """, (trainer_id,))
    marks = [row['marks_obtained'] for row in cursor.fetchall()]

    # Compute distribution: bins of 10 (0-10, 11-20, ..., 91-100)
    bins = [i for i in range(0, 101, 10)]
    labels = [f"{b+1}-{b+10}" if b != 0 else "0-10" for b in bins[:-1]]
    counts = [0] * len(labels)
    for m in marks:
        idx = min(int(m) // 10, 9)
        counts[idx] += 1

    # Pass as JSON for Chart.js
    marks_labels_json = json.dumps(labels)
    marks_counts_json = json.dumps(counts)

    # For donut chart: submissions vs to grade per course
    donut_data = []
    for c in course_stats:
        course_id = c['course_id']
        # Total submissions for this course
        cursor.execute("""
            SELECT COUNT(DISTINCT asub.student_id) AS submitted
            FROM assignments a
            LEFT JOIN assignment_submissions asub ON a.assignment_id=asub.assignment_id
            WHERE a.course_id=%s AND a.trainer_id=%s AND asub.status IN ('SUBMITTED', 'GRADED')
        """, (course_id, trainer_id))
        submitted = cursor.fetchone()['submitted'] or 0

        # Submissions that are not yet graded
        cursor.execute("""
            SELECT COUNT(DISTINCT asub.student_id) AS to_grade
            FROM assignments a
            LEFT JOIN assignment_submissions asub ON a.assignment_id=asub.assignment_id
            WHERE a.course_id=%s AND a.trainer_id=%s AND asub.status='SUBMITTED'
        """, (course_id, trainer_id))
        to_grade = cursor.fetchone()['to_grade'] or 0

        donut_data.append({
            'course_name': c['course_name'],
            'submitted': submitted,
            'to_grade': to_grade
        })

    donut_labels = [d['course_name'] for d in donut_data]
    donut_submitted = [d['submitted'] for d in donut_data]
    donut_to_grade = [d['to_grade'] for d in donut_data]

    total_submitted = sum(donut_submitted)
    total_to_grade = sum(donut_to_grade)

    # 10. Absentees Today 

    today = date.today()
    cursor.execute("""
        SELECT COUNT(DISTINCT a.student_id) AS absentees_today
        FROM attendance a
        JOIN batches b ON a.batch_id = b.batch_id
        WHERE a.attendance_date = %s
          AND a.status = 'ABSENT'
          AND b.trainer_id = %s
    """, (today, trainer_id))
    absentees_today = cursor.fetchone()['absentees_today']

    # 11. Average Grade for Trainer's Assignments
    cursor.execute("""
        SELECT AVG(COALESCE(asub.marks_obtained, 0)) AS avg_grade
        FROM assignments a
        LEFT JOIN assignment_submissions asub ON a.assignment_id = asub.assignment_id
        WHERE a.trainer_id = %s
    """, (trainer_id,))
    avg_grade = cursor.fetchone()['avg_grade']

    # Example: Fetch assigned courses for the trainer
    cursor.execute("""
        SELECT c.course_name
        FROM course_trainers ct
        JOIN courses c ON ct.course_id = c.course_id
        WHERE ct.trainer_id = %s
    """, (trainer_id,))
    assigned_courses = [row['course_name'] for row in cursor.fetchall()]

    

    # Get average marks per student for this trainer's assignments
    cursor.execute("""
        SELECT s.student_id, AVG(asub.marks_obtained) AS avg_marks
        FROM assignments a
        JOIN assignment_submissions asub ON a.assignment_id = asub.assignment_id
        JOIN students s ON asub.student_id = s.student_id
        WHERE a.trainer_id = %s AND asub.status IN ('SUBMITTED', 'GRADED') AND asub.marks_obtained IS NOT NULL
        GROUP BY s.student_id
    """, (trainer_id,))
    student_marks = [row['avg_marks'] for row in cursor.fetchall()]

    # Bin averages into ranges (0-10, 11-20, ..., 91-100)
    bins = [i for i in range(0, 101, 10)]
    labels = [f"{b+1}-{b+10}" if b != 0 else "0-10" for b in bins[:-1]]
    counts = [0] * len(labels)
    for m in student_marks:
        idx = min(int(m) // 10, 9)
        counts[idx] += 1

    marks_labels_json = json.dumps(labels)
    marks_counts_json = json.dumps(counts)

    cursor.close()
    conn.close()

    return render_template(
        'trainer_dashboard.html',
        total_assigned_courses=len(assigned_courses),
        assigned_courses=assigned_courses,
        avg_grade=avg_grade,
        active_batches=active_batches,
        total_students=total_students,
        pending_leave_requests=pending_leave_requests,
        recent_activities=recent_activities,
        course_chart=course_chart,
        completion_chart=completion_chart,
        performance_chart=performance_chart,        
        course_stats=course_stats,
        marks_labels_json=marks_labels_json,
        marks_counts_json=marks_counts_json,
        donut_labels=donut_labels,
        donut_submitted=donut_submitted,
        donut_to_grade=donut_to_grade,   
        total_submitted=total_submitted,
        total_to_grade=total_to_grade,
        absentees_today=absentees_today
    )

# ============================================================
# COURSES & COURSE DETAILS
# ============================================================

# --- My Courses List ---
@trainer_bp.route('/my_courses')
def my_courses():
    """
    List all courses assigned to the trainer, with their batches.
    """
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    trainer_id = session.get('user_id')
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get all courses assigned to this trainer
    cursor.execute("""
        SELECT c.course_id, c.course_name, c.description
        FROM courses c
        JOIN course_trainers ct ON c.course_id = ct.course_id
        WHERE ct.trainer_id = %s
    """, (trainer_id,))
    courses = cursor.fetchall()

    # For each course, get batches (no course_batches table needed)
    for course in courses:
        cursor.execute("""
            SELECT batch_id, batch_name
            FROM batches
            WHERE course_id = %s
        """, (course['course_id'],))
        course['batches'] = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template('my_courses.html', courses=courses)

# ============================================================
# TOPICS & SUBTOPICS
# ============================================================

# ------------------ List Topics & Subtopics ------------------
@trainer_bp.route('/course/<int:course_id>/topics')
def course_topics(course_id):
    batch_id = request.args.get('batch_id', type=int)
    if not trainer_required():
        return redirect(url_for('auth.login'))

    trainer_id = session.get('user_id')
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get course
    cursor.execute("SELECT * FROM courses WHERE course_id = %s", (course_id,))
    course = cursor.fetchone()
    if not course:
        flash("Course not found.", "danger")
        cursor.close()
        conn.close()
        return redirect(url_for('trainer.my_courses'))

    # Get batch if batch_id is provided
    batch = None
    if batch_id:
        cursor.execute("SELECT * FROM batches WHERE batch_id = %s", (batch_id,))
        batch = cursor.fetchone()

    # Get all topics for this trainer & course
    cursor.execute("""
        SELECT * FROM topics
        WHERE course_id = %s AND trainer_id = %s
        ORDER BY sequence_order
    """, (course_id, trainer_id))
    topics = cursor.fetchall()

    # Get all subtopics for these topics in one query (more efficient)
    topic_ids = [topic['topic_id'] for topic in topics]
    subtopics_by_topic = {}
    if topic_ids:
        format_strings = ','.join(['%s'] * len(topic_ids))
        cursor.execute(f"""
            SELECT * FROM subtopics
            WHERE topic_id IN ({format_strings})
            ORDER BY topic_id, sequence_order
        """, tuple(topic_ids))
        subtopics = cursor.fetchall()
        for sub in subtopics:
            subtopics_by_topic.setdefault(sub['topic_id'], []).append(sub)
    else:
        subtopics = []

    # Attach subtopics to each topic
    for topic in topics:
        topic['subtopics'] = subtopics_by_topic.get(topic['topic_id'], [])

    cursor.close()
    conn.close()
    return render_template('course_topics.html', course=course, topics=topics, batch=batch)

# ------------------ View Batches for a Course ------------------
@trainer_bp.route('/course/<int:course_id>/batches')
def course_batch(course_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    trainer_id = session.get('user_id')
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch course info
    cursor.execute("SELECT * FROM courses WHERE course_id = %s", (course_id,))
    course = cursor.fetchone()

    # Get all batches of this course where trainer is assigned
    cursor.execute("""
        SELECT b.batch_id, b.batch_name, b.start_date, b.end_date, b.status
        FROM batches b
        JOIN course_trainers ct ON ct.course_id = b.course_id
        WHERE b.course_id = %s AND ct.trainer_id = %s
    """, (course_id, trainer_id))
    
    batches = cursor.fetchall()
    cursor.close()
    conn.close()

    # ✅ Pass course to the template
    return render_template('course_batch.html', course=course, batches=batches)


# ============================================================
# TOPICS
# ============================================================
@trainer_bp.route('/course/<int:course_id>/topics/add', methods=['POST'])
def add_topic(course_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    topic_name = request.form['topic_name']
    description = request.form.get('description')
    sequence_order = request.form.get('sequence_order', 1)
    due_date = request.form.get('due_date') or None
    batch_id = request.form.get('batch_id')
    trainer_id = session.get('user_id')

    try:
        sequence_order = int(sequence_order)
    except ValueError:
        sequence_order = 1

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO topics (topic_name, description, course_id, trainer_id, batch_id, sequence_order, due_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (topic_name, description, course_id, trainer_id, batch_id, sequence_order, due_date))
        conn.commit()
        flash("✅ Topic added successfully.", "success")
        new_topic_id = cursor.lastrowid
        return redirect(url_for('trainer.course_topics', course_id=course_id, batch_id=batch_id, open_topic=new_topic_id))
    except Exception as e:
        conn.rollback()
        flash(f"❌ Error adding topic: {e}", "danger")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('trainer.course_topics', course_id=course_id, batch_id=batch_id))
# ------------------ Edit Topic ------------------
@trainer_bp.route('/topic/<int:topic_id>/edit', methods=['POST'])
def edit_topic(topic_id):
    if session.get('user_role') != 'trainer':
        flash("Trainer login required!", "warning")
        return redirect(url_for('trainer.trainer_dashboard'))

    topic_name = request.form['topic_name']
    description = request.form.get('description')
    sequence_order = request.form.get('sequence_order', 1)
    due_date = request.form.get('due_date', None)
    batch_id = request.form.get('batch_id')

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT course_id, batch_id FROM topics WHERE topic_id=%s", (topic_id,))
    topic = cursor.fetchone()
    course_id = topic['course_id'] if topic else None
    batch_id = batch_id or (topic['batch_id'] if topic else None)

    cursor.execute("""
        UPDATE topics SET topic_name=%s, description=%s, sequence_order=%s, due_date=%s, batch_id=%s
        WHERE topic_id=%s
    """, (topic_name, description, sequence_order, due_date, batch_id, topic_id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Topic updated successfully.", "success")
    if course_id and batch_id:
        return redirect(url_for('trainer.course_topics', course_id=course_id, batch_id=batch_id, open_topic=topic_id))
    elif course_id:
        return redirect(url_for('trainer.course_topics', course_id=course_id, open_topic=topic_id))
    else:
        return redirect(url_for('trainer.trainer_dashboard'))

# ------------------ Delete Topic (with cascade) ------------------
@trainer_bp.route('/topic/<int:topic_id>/delete', methods=['POST'])
def delete_topic(topic_id):
    if not trainer_required():
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT course_id, batch_id FROM topics WHERE topic_id = %s", (topic_id,))
    topic = cursor.fetchone()
    course_id = topic['course_id'] if topic else None
    batch_id = topic['batch_id'] if topic else None

    # Manually delete subtopics and assignments to avoid FK errors
    cursor.execute("DELETE FROM assignments WHERE topic_id = %s", (topic_id,))
    cursor.execute("DELETE FROM subtopics WHERE topic_id = %s", (topic_id,))
    cursor.execute("DELETE FROM topics WHERE topic_id = %s", (topic_id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Topic and its subtopics/assignments deleted successfully.", "success")
    if course_id and batch_id:
        return redirect(url_for('trainer.course_topics', course_id=course_id, batch_id=batch_id))
    elif course_id:
        return redirect(url_for('trainer.course_topics', course_id=course_id))
    return redirect(url_for('trainer.my_courses'))


# ============================================================
# SUBTOPICS
# ============================================================


# ------------------ Add Subtopic ------------------
@trainer_bp.route('/topic/<int:topic_id>/subtopics/add', methods=['POST'])
def add_subtopic(topic_id):
    if not trainer_required():
        return redirect(url_for('auth.login'))

    subtopic_name = request.form['subtopic_name']
    description = request.form.get('description')
    sequence_order = request.form.get('sequence_order', 1)
    due_date = request.form.get('due_date', None)
    batch_id = request.form.get('batch_id')

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT course_id, batch_id FROM topics WHERE topic_id=%s", (topic_id,))
    topic = cursor.fetchone()
    if not topic:
        flash("Parent topic not found.", "danger")
        cursor.close()
        conn.close()
        return redirect(url_for('trainer.my_courses'))

    course_id = topic['course_id']
    batch_id = batch_id or topic['batch_id']

    cursor.execute("""
        INSERT INTO subtopics (topic_id, subtopic_name, description, sequence_order, due_date)
        VALUES (%s, %s, %s, %s, %s)
    """, (topic_id, subtopic_name, description, sequence_order, due_date))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Subtopic added successfully.", "success")
    return redirect(url_for('trainer.course_topics', course_id=course_id, batch_id=batch_id, open_topic=topic_id))

# ------------------ Edit Subtopic ------------------
@trainer_bp.route('/subtopic/<int:subtopic_id>/edit', methods=['POST'])
def edit_subtopic(subtopic_id):
    if not trainer_required():
        return redirect(url_for('auth.login'))

    subtopic_name = request.form['subtopic_name']
    description = request.form.get('description')
    sequence_order = request.form.get('sequence_order', 1)
    due_date = request.form.get('due_date', None)

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT topic_id FROM subtopics WHERE subtopic_id=%s", (subtopic_id,))
    subtopic = cursor.fetchone()
    if not subtopic:
        flash("Subtopic not found.", "danger")
        cursor.close()
        conn.close()
        return redirect(url_for('trainer.my_courses'))

    topic_id = subtopic['topic_id']
    cursor.execute("SELECT course_id, batch_id FROM topics WHERE topic_id=%s", (topic_id,))
    topic = cursor.fetchone()
    course_id = topic['course_id'] if topic else None
    batch_id = topic['batch_id'] if topic else None

    cursor.execute("""
        UPDATE subtopics SET subtopic_name=%s, description=%s, sequence_order=%s, due_date=%s
        WHERE subtopic_id=%s
    """, (subtopic_name, description, sequence_order, due_date, subtopic_id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Subtopic updated successfully.", "success")
    if course_id and batch_id:
        return redirect(url_for('trainer.course_topics', course_id=course_id, batch_id=batch_id, open_topic=topic_id))
    elif course_id:
        return redirect(url_for('trainer.course_topics', course_id=course_id, open_topic=topic_id))
    return redirect(url_for('trainer.my_courses'))

# ------------------ Delete Subtopic ------------------
@trainer_bp.route('/subtopic/<int:subtopic_id>/delete', methods=['POST'])
def delete_subtopic(subtopic_id):
    if not trainer_required():
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT topic_id FROM subtopics WHERE subtopic_id=%s", (subtopic_id,))
    subtopic = cursor.fetchone()
    if subtopic:
        topic_id = subtopic['topic_id']
        cursor.execute("SELECT course_id, batch_id FROM topics WHERE topic_id=%s", (topic_id,))
        topic = cursor.fetchone()
        course_id = topic['course_id'] if topic else None
        batch_id = topic['batch_id'] if topic else None

        # Manually delete assignments for this subtopic to avoid FK errors
        cursor.execute("DELETE FROM assignments WHERE subtopic_id=%s", (subtopic_id,))
        cursor.execute("DELETE FROM subtopics WHERE subtopic_id=%s", (subtopic_id,))
        conn.commit()
        flash("Subtopic and its assignments deleted successfully.", "success")
    else:
        flash("Subtopic not found.", "danger")
        course_id = None
        topic_id = None
        batch_id = None

    cursor.close()
    conn.close()
    if course_id and topic_id and batch_id:
        return redirect(url_for('trainer.course_topics', course_id=course_id, batch_id=batch_id, open_topic=topic_id))
    elif course_id and topic_id:
        return redirect(url_for('trainer.course_topics', course_id=course_id, open_topic=topic_id))
    elif course_id:
        return redirect(url_for('trainer.course_topics', course_id=course_id))
    return redirect(url_for('trainer.my_courses'))

@trainer_bp.route('/subtopic/<int:subtopic_id>/add_assignment', methods=['POST'])
def add_assignment(subtopic_id):
    if session.get('user_role') != 'trainer':
        flash("Unauthorized access.", "danger")
        return redirect(url_for('auth.login'))

    conn = None
    cursor = None
    batch_id = None
    try:
        # Collect form data safely
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        course_id = int(request.form.get('course_id'))
        topic_id = int(request.form.get('topic_id'))
        total_marks = int(request.form.get('total_marks', 100))

        # Parse due_date
        due_date_str = request.form.get('due_date')
        due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date() if due_date_str else None

        # File upload
        file = request.files.get('attachment')
        filename = None
        if file and allowed_file(file.filename, ALLOWED_ASSIGNMENT_EXTENSIONS):
            filename = secure_filename(file.filename)
            os.makedirs(ASSIGNMENT_UPLOAD_FOLDER, exist_ok=True)
            file.save(os.path.join(ASSIGNMENT_UPLOAD_FOLDER, filename))

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        # Fetch batch & trainer
        cursor.execute("""
            SELECT batch_id, trainer_id FROM topics
            WHERE topic_id = %s AND course_id = %s
        """, (topic_id, course_id))
        topic_info = cursor.fetchone()

        if not topic_info:
            flash("Topic not found or not linked to this course.", "danger")
            return redirect(url_for('trainer.course_topics', course_id=course_id, open_topic=topic_id))

        batch_id = topic_info['batch_id']

        # Insert assignment
        cursor.execute("""
            INSERT INTO assignments (
                batch_id, course_id, trainer_id, topic_id, subtopic_id,
                title, description, due_date, total_marks, attachment
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            batch_id, course_id, topic_info['trainer_id'], topic_id, subtopic_id,
            title, description, due_date, total_marks, filename
        ))
        conn.commit()
        flash("✅ Assignment added successfully!", "success")

    except Exception as e:
        if conn: conn.rollback()
        flash(f"❌ Error adding assignment: {str(e)}", "danger")

    finally:
        if cursor: cursor.close()
        if conn: conn.close()

    # Always pass batch_id in the redirect
    return redirect(url_for('trainer.course_topics', course_id=course_id, batch_id=batch_id, open_topic=topic_id)) 


# @trainer_bp.route('/assignment/<int:assignment_id>/edit', methods=['GET','POST'])
# def edit_assignment(assignment_id):
#     """
#     Edit an existing assignment.
#     """
#     if session.get('user_role') != 'trainer':
#         return redirect(url_for('auth.login'))

#     conn = get_connection()
#     cursor = conn.cursor(dictionary=True)

#     # Fetch assignment
#     cursor.execute("SELECT * FROM assignments WHERE assignment_id=%s", (assignment_id,))
#     assignment = cursor.fetchone()
#     if not assignment:
#         flash("Assignment not found.", "danger")
#         cursor.close()
#         conn.close()
#         return redirect(url_for('trainer.my_courses'))

#     if request.method == 'POST':
#         title = request.form['title']
#         description = request.form.get('description')
#         due_date = request.form.get('due_date')
#         total_marks = request.form.get('total_marks', 100)

#         # File upload
#         file = request.files.get('attachment')
#         filename = assignment['file_path']
#         if file and allowed_file(file.filename, ALLOWED_ASSIGNMENT_EXTENSIONS):
#             filename = secure_filename(file.filename)
#             os.makedirs(ASSIGNMENT_UPLOAD_FOLDER, exist_ok=True)
#             file.save(os.path.join(ASSIGNMENT_UPLOAD_FOLDER, filename))

#         cursor.execute("""
#             UPDATE assignments
#             SET title=%s, description=%s, due_date=%s, total_marks=%s, file_path=%s
#             WHERE assignment_id=%s
#         """, (title, description, due_date, total_marks, filename, assignment_id))
#         conn.commit()
#         cursor.close()
#         conn.close()
#         flash("Assignment updated successfully!", "success")
#         # Pass batch_id in redirect
#         return redirect(url_for('trainer.course_topics', course_id=assignment['course_id'], batch_id=assignment['batch_id'], open_topic=assignment['topic_id']))

#     cursor.close()
#     conn.close()
#     return render_template('edit_assignment.html', assignment=assignment)

# @trainer_bp.route('/assignment/<int:assignment_id>/delete', methods=['POST'])
# def delete_assignment(assignment_id):
#     """
#     Delete an assignment.
#     """
#     if session.get('user_role') != 'trainer':
#         return redirect(url_for('auth.login'))

#     conn = get_connection()
#     cursor = conn.cursor(dictionary=True)

#     # Get course_id, batch_id, topic_id for redirect and file path
#     cursor.execute("SELECT course_id, batch_id, topic_id, file_path FROM assignments WHERE assignment_id=%s", (assignment_id,))
#     assignment = cursor.fetchone()
#     if assignment:
#         # Delete file if exists
#         if assignment['file_path']:
#             file_path = os.path.join(ASSIGNMENT_UPLOAD_FOLDER, assignment['file_path'])
#             if os.path.exists(file_path):
#                 os.remove(file_path)

#         cursor.execute("DELETE FROM assignments WHERE assignment_id=%s", (assignment_id,))
#         conn.commit()
#         flash("Assignment deleted successfully!", "success")
#         course_id = assignment['course_id']
#         batch_id = assignment['batch_id']
#         topic_id = assignment['topic_id']
#     else:
#         flash("Assignment not found.", "danger")
#         course_id = None
#         batch_id = None
#         topic_id = None

#     cursor.close()
#     conn.close()
#     if course_id and batch_id and topic_id:
#         return redirect(url_for('trainer.course_topics', course_id=course_id, batch_id=batch_id, open_topic=topic_id))
#     elif course_id and batch_id:
#         return redirect(url_for('trainer.course_topics', course_id=course_id, batch_id=batch_id))
#     elif course_id:
#         return redirect(url_for('trainer.course_topics', course_id=course_id))
#     return redirect(url_for('trainer.my_courses'))

@trainer_bp.route('/subtopic/<int:subtopic_id>/assignments')
def view_assignments(subtopic_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    course_id = request.args.get('course_id', type=int)
    batch_id = request.args.get('batch_id', type=int)

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch assignments for this subtopic
    cursor.execute("""
        SELECT * FROM assignments
        WHERE subtopic_id = %s
        ORDER BY due_date
    """, (subtopic_id,))
    assignments = cursor.fetchall()

    # Fetch course and batch info for heading/breadcrumb
    course = None
    batch = None
    if course_id:
        cursor.execute("SELECT * FROM courses WHERE course_id = %s", (course_id,))
        course = cursor.fetchone()
    if batch_id:
        cursor.execute("SELECT * FROM batches WHERE batch_id = %s", (batch_id,))
        batch = cursor.fetchone()

    # After fetching assignments, also fetch subtopic:
    cursor.execute("SELECT subtopic_name FROM subtopics WHERE subtopic_id = %s", (subtopic_id,))
    subtopic = cursor.fetchone()

    cursor.close()
    conn.close()

    return render_template(
        'view_assignments.html',
        assignments=assignments,
        subtopic_id=subtopic_id,
        course=course,
        batch=batch,
        subtopic=subtopic  # <-- add this
    )

@trainer_bp.route('/assignment/<int:assignment_id>')
def view_assignment(assignment_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM assignments WHERE assignment_id=%s", (assignment_id,))
    assignment = cursor.fetchone()
    cursor.close()
    conn.close()

    if not assignment:
        flash("Assignment not found.", "danger")
        return redirect(url_for('trainer.my_courses'))

    return render_template('view_assignment.html', assignment=assignment)

# ------------------ Edit Assignment ------------------
@trainer_bp.route('/assignment/<int:assignment_id>/edit', methods=['GET', 'POST'])
def edit_assignment(assignment_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch assignment
    cursor.execute("SELECT * FROM assignments WHERE assignment_id=%s", (assignment_id,))
    assignment = cursor.fetchone()
    if not assignment:
        flash("Assignment not found.", "danger")
        cursor.close()
        conn.close()
        return redirect(url_for('trainer.my_courses'))

    if request.method == 'POST':
        title = request.form['title']
        description = request.form.get('description')
        due_date = request.form.get('due_date')
        total_marks = request.form.get('total_marks', 100)

        # File upload
        file = request.files.get('attachment')
        filename = assignment.get('attachment')
        if file and allowed_file(file.filename, ALLOWED_ASSIGNMENT_EXTENSIONS):
            filename = secure_filename(file.filename)
            os.makedirs(ASSIGNMENT_UPLOAD_FOLDER, exist_ok=True)
            file.save(os.path.join(ASSIGNMENT_UPLOAD_FOLDER, filename))

        cursor.execute("""
            UPDATE assignments
            SET title=%s, description=%s, due_date=%s, total_marks=%s, attachment=%s
            WHERE assignment_id=%s
        """, (title, description, due_date, total_marks, filename, assignment_id))
        conn.commit()
        cursor.close()
        conn.close()
        flash("Assignment updated successfully!", "success")
        # Redirect to assignments list for the subtopic
        return redirect(url_for('trainer.view_assignments',
                                subtopic_id=assignment['subtopic_id'],
                                course_id=assignment['course_id'],
                                batch_id=assignment['batch_id']))

    cursor.close()
    conn.close()
    return render_template('edit_assignment.html', assignment=assignment)

# ------------------ Delete Assignment ------------------
@trainer_bp.route('/assignment/<int:assignment_id>/delete', methods=['POST'])
def delete_assignment(assignment_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get assignment info for redirect and file deletion
    cursor.execute("SELECT * FROM assignments WHERE assignment_id=%s", (assignment_id,))
    assignment = cursor.fetchone()
    if assignment:
        # Delete file if exists
        if assignment.get('attachment'):
            file_path = os.path.join(ASSIGNMENT_UPLOAD_FOLDER, assignment['attachment'])
            if os.path.exists(file_path):
                os.remove(file_path)

        cursor.execute("DELETE FROM assignments WHERE assignment_id=%s", (assignment_id,))
        conn.commit()
        flash("Assignment deleted successfully!", "success")
        subtopic_id = assignment['subtopic_id']
        course_id = assignment['course_id']
        batch_id = assignment['batch_id']
    else:
        flash("Assignment not found.", "danger")
        subtopic_id = None
        course_id = None
        batch_id = None

    cursor.close()
    conn.close()
    if subtopic_id and course_id and batch_id:
        return redirect(url_for('trainer.view_assignments',
                                subtopic_id=subtopic_id,
                                course_id=course_id,
                                batch_id=batch_id))
    return redirect(url_for('trainer.my_courses'))

@trainer_bp.route('/assignment/<int:assignment_id>/submissions')
def view_submissions(assignment_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Get assignment info
    cursor.execute("SELECT * FROM assignments WHERE assignment_id=%s", (assignment_id,))
    assignment = cursor.fetchone()
    if not assignment:
        flash("Assignment not found.", "danger")
        cursor.close()
        conn.close()
        return redirect(url_for('trainer.my_courses'))

    # Get student submissions with student name (join students and users)
    cursor.execute("""
        SELECT a.*, u.full_name
        FROM assignment_submissions a
        JOIN students s ON a.student_id = s.student_id
        JOIN users u ON s.user_id = u.user_id
        WHERE a.assignment_id=%s
    """, (assignment_id,))
    submissions = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('view_submissions.html', assignment=assignment, submissions=submissions)



@trainer_bp.route('/assignment/<int:assignment_id>/download_submissions')
def download_submissions(assignment_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT s.file_path, u.full_name
        FROM assignment_submissions s
        JOIN students st ON s.student_id = st.student_id
        JOIN users u ON st.user_id = u.user_id
        WHERE s.assignment_id = %s AND s.file_path IS NOT NULL AND s.status IN ('SUBMITTED', 'GRADED')
    """, (assignment_id,))
    files = cursor.fetchall()
    cursor.close()
    conn.close()

    # Create ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for f in files:
            file_path = f['file_path']
            # If file_path is not absolute, prepend the upload folder
            if not os.path.isabs(file_path):
                file_path = os.path.join(ASSIGNMENT_UPLOAD_FOLDER, file_path)
            student_name = f['full_name'].replace(' ', '_')
            if os.path.exists(file_path):
                zipf.write(file_path, arcname=f"{student_name}_{os.path.basename(file_path)}")
    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f"assignment_{assignment_id}_submissions.zip"
    )

# ------------------- Grade Submission -------------------
@trainer_bp.route('/submission/<int:submission_id>/grade', methods=['POST'])
def grade_submission(submission_id):
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    marks_obtained = request.form['marks_obtained']    
    feedback = request.form.get('feedback')

    conn = get_connection()
    cursor = conn.cursor()

    # Get assignment_id for redirect
    cursor.execute("SELECT assignment_id FROM assignment_submissions WHERE submission_id=%s", (submission_id,))
    assignment_id = cursor.fetchone()[0]

    cursor.execute("""
        UPDATE assignment_submissions
        SET marks_obtained=%s, feedback=%s, status='GRADED', graded_by=%s, graded_at=NOW()
        WHERE submission_id=%s
    """, (marks_obtained, feedback, session.get('user_id'), submission_id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Submission graded successfully!", "success")
    return redirect(url_for('trainer.view_submissions', assignment_id=assignment_id))

# ============================================================
# AJAX ENDPOINTS
# ============================================================
@trainer_bp.route("/get_batches/<int:course_id>")
def get_batches(course_id):
    """
    AJAX: Get batches for a course assigned to this trainer.
    """
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    trainer_id = session.get('user_id')

    cursor.execute("""
        SELECT b.batch_id, b.batch_name
        FROM batches b
        JOIN course_trainers ct ON ct.course_id = b.course_id
        WHERE b.course_id=%s AND ct.trainer_id=%s AND ct.is_active=1
    """, (course_id, trainer_id))
    batches = cursor.fetchall()

    cursor.close()
    conn.close()
    return jsonify(batches)

@trainer_bp.route("/get_topics/<int:course_id>/<int:batch_id>")
def get_topics(course_id, batch_id):
    """
    AJAX: Get topics for a course assigned to this trainer.
    """
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    trainer_id = session.get('user_id')

    cursor.execute("""
        SELECT t.topic_id, t.topic_name
        FROM topics t
        WHERE t.course_id=%s AND t.trainer_id=%s
        ORDER BY t.sequence_order
    """, (course_id, trainer_id))
    topics = cursor.fetchall()

    cursor.close()
    conn.close()
    return jsonify(topics)

# ============================================================
# PROFILE
# ============================================================
@trainer_bp.route('/profile', methods=['GET', 'POST'])
def profile():
    """
    Trainer profile view and update.
    """
    if session.get('user_role') != 'trainer':
        return redirect(url_for('auth.login'))

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    user_id = session.get('user_id')

    # Fetch trainer info joined with users for name/email/phone
    cursor.execute("""
        SELECT t.*, u.full_name, u.email, u.phone
        FROM trainers t
        JOIN users u ON t.user_id = u.user_id
        WHERE t.user_id=%s
    """, (user_id,))
    trainer = cursor.fetchone()
    
    if not trainer:
        conn.close()
        flash("Trainer profile not found.", "danger")
        return redirect(url_for('trainer.trainer_dashboard'))

    if request.method == 'POST':
        dob = request.form.get('dob') or None
        gender = request.form.get('gender') or None
        address = request.form.get('address') or None
        city = request.form.get('city') or None
        state = request.form.get('state') or None
        country = request.form.get('country') or None
        zip_code = request.form.get('zip_code') or None
        qualifications = request.form.get('qualifications') or None
        experience_years = request.form.get('experience_years') or 0
        specialization = request.form.get('specialization') or None
        emergency_name = request.form.get('emergency_contact_name') or None
        emergency_phone = request.form.get('emergency_contact_phone') or None

        # Profile photo upload
        profile_photo = trainer.get('profile_photo')
        file = request.files.get('profile_photo')
        if file and file.filename:
            ext = os.path.splitext(secure_filename(file.filename))[1]
            unique_name = f"{user_id}_{uuid.uuid4().hex}{ext}"
            upload_dir = os.path.join(current_app.root_path, 'static/uploads/profiles')
            os.makedirs(upload_dir, exist_ok=True)
            file.save(os.path.join(upload_dir, unique_name))
            profile_photo = unique_name

        # Update trainer
        cursor.execute("""
            UPDATE trainers
            SET dob=%s, gender=%s, address=%s, city=%s, state=%s, country=%s, zip_code=%s,
                qualifications=%s, experience_years=%s, specialization=%s,
                emergency_contact_name=%s, emergency_contact_phone=%s, profile_photo=%s
            WHERE trainer_id=%s
        """, (
            dob, gender, address, city, state, country, zip_code,
            qualifications, experience_years, specialization,
            emergency_name, emergency_phone, profile_photo, trainer['trainer_id']
        ))
        conn.commit()

        # Update dict for template
        trainer.update({
            'dob': dob,
            'gender': gender,
            'address': address,
            'city': city,
            'state': state,
            'country': country,
            'zip_code': zip_code,
            'qualifications': qualifications,
            'experience_years': experience_years,
            'specialization': specialization,
            'emergency_contact_name': emergency_name,
            'emergency_contact_phone': emergency_phone,
            'profile_photo': profile_photo
        })

        session['profile_image'] = profile_photo if profile_photo else None
        flash("Profile updated successfully", "success")

    # Profile completion calculation
    fields = [
        'dob', 'gender', 'address', 'city', 'state', 'country', 'zip_code',
        'qualifications', 'experience_years', 'specialization',
        'emergency_contact_name', 'emergency_contact_phone', 'profile_photo'
    ]
    filled = sum(1 for f in fields if trainer.get(f))
    completion = int((filled / len(fields)) * 100)

    conn.close()
    return render_template('trainer_profile.html', trainer=trainer, completion=completion)



