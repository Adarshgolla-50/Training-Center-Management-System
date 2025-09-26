from werkzeug.security import generate_password_hash, check_password_hash
from models.db import get_connection
import random
from models.email_utils import send_all_emails
from threading import Thread
from flask import current_app


app = current_app  # No need for _get_current_object()


# Create default admin if not exists
def create_super_admin():
    conn = get_connection()
    if conn is None:
        print("❌ No DB connection.")
        return

    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE role = 'super_admin' LIMIT 1")
    admin = cursor.fetchone()

    if not admin:
        default_email = "superadmin@gmail.com"
        default_password = "superadmin123"

        hashed_password = generate_password_hash(default_password)
        cursor.execute("""
            INSERT INTO users (email, password_hash, full_name, role, is_active)
            VALUES (%s, %s, %s, %s, %s)
        """, (default_email, hashed_password, "Super Admin", "super_admin", True))

        conn.commit()
        print("✅ Super admin created:", default_email)

    cursor.close()
    conn.close()


# Validate user login
def validate_user(email, password):
    conn = get_connection()
    if conn is None:
        return None

    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE email = %s AND is_active = TRUE", (email,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if user and check_password_hash(user["password_hash"], password):
        return user
    return None

def generate_user_password(full_name, phone):        
    name_parts = full_name.strip().split()   # Split name into parts

    if len(name_parts) > 1:       
        clean_name = "".join(name_parts)       # If more than one name part → combine without spaces
    else:      
        clean_name = name_parts[0]          # If only one name part → use it directly

    # Take first 4 digits of phone or fallback random
    phone_part = phone[:4] if phone else str(random.randint(1000, 9999))

    # Final password
    password = f"{clean_name}@{phone_part}"

    # Hash password
    hashed_password = generate_password_hash(password)

    return password, hashed_password


def create_user(full_name, email, role, phone, is_active=True):
    """Insert user and return (user_id, plain_password)"""
    conn = get_connection()
    cursor = conn.cursor()

    plain_password, hashed_password = generate_user_password(full_name, phone)

    cursor.execute("""
        INSERT INTO users (email, password_hash, full_name, phone, role, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (email, hashed_password, full_name, phone, role, is_active))

    conn.commit()
    user_id = cursor.lastrowid
    cursor.close()
    conn.close()

    return user_id, plain_password
# --------------------------------------------------------
# Create batch function
# --------------------------------------------------------
def create_batch(batch_name, course_id, start_date, end_date,
                 trainer_ids=None, max_students=30, status='Upcoming',
                 is_active=True, created_by=None):
    """
    Create a new batch and optionally assign trainers.
    trainer_ids can be None or a list of trainer IDs.
    
    Returns dict:
        Success: {'success': True, 'batch_id': int, 'duration_weeks': int}
        Failure: {'success': False, 'error': str}
    """
    if trainer_ids is None:
        trainer_ids = []

    conn = None
    try:
        conn = get_connection()
        if not conn:
            return {'success': False, 'error': 'Database connection failed'}

        cursor = conn.cursor(dictionary=True)

        # --- 0. Check for duplicate batch ---
        cursor.execute("""
            SELECT batch_id FROM batches
            WHERE batch_name = %s AND course_id = %s
        """, (batch_name, course_id))
        if cursor.fetchone():
            return {'success': False, 'error': f"A batch named '{batch_name}' already exists for this course."}

        # --- 1. Insert batch ---
        insert_query = """
            INSERT INTO batches (
                batch_name, course_id, start_date, end_date,
                max_students, status, is_active, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_query, (
            batch_name,
            course_id,
            start_date,
            end_date,
            max_students,
            status,
            1 if is_active else 0,
            created_by
        ))
        conn.commit()
        batch_id = cursor.lastrowid

        # --- 2. Assign trainers if provided ---
        if trainer_ids:
            insert_map = """
                INSERT INTO batch_trainers (batch_id, trainer_id, is_active)
                VALUES (%s, %s, %s)
            """
            for tid in trainer_ids:
                cursor.execute(insert_map, (batch_id, tid, 1))
            conn.commit()

        # --- 3. Fetch duration_weeks ---
        cursor.execute(
            "SELECT duration_weeks FROM batches WHERE batch_id = %s",
            (batch_id,)
        )
        row = cursor.fetchone()
        duration_weeks = row['duration_weeks'] if row else None

        return {'success': True, 'batch_id': batch_id, 'duration_weeks': duration_weeks}

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"[ERROR] Failed to create batch: {e}")
        return {'success': False, 'error': str(e)}

    finally:
        if conn:
            cursor.close()
            conn.close()





def create_student(full_name, email, phone, admission_no, dob, guardian_name, enrollment_date, is_active=True, app=None, mail=None, admin_email=None):
    """Insert user (role='student'), then student record. Return (student_id, user_id, plain_password)"""
    from datetime import date
    conn = get_connection()
    cursor = conn.cursor()

    # Generate password and create user
    role = 'student'
    plain_password, hashed_password = generate_user_password(full_name, phone)
    cursor.execute("""
        INSERT INTO users (email, password_hash, full_name, phone, role, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (email, hashed_password, full_name, phone, role, is_active))
    user_id = cursor.lastrowid

    # Insert student record
    cursor.execute("""
        INSERT INTO students (user_id, admission_no, dob, guardian_name, enrollment_date, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (user_id, admission_no, dob, guardian_name, enrollment_date or date.today(), is_active))
    student_id = cursor.lastrowid

    conn.commit()
    cursor.close()
    conn.close()

    # Send emails if app and mail are provided
    if app and mail and admin_email:
        from models.email_utils import send_all_emails
        Thread(target=send_all_emails, args=(full_name, email, role, plain_password, admin_email, app, mail)).start()

    return student_id, user_id, plain_password



def create_trainer(full_name, email, phone, dob=None, qualifications=None, experience_years=0, specialization=None, is_active=True, app=None, mail=None, admin_email=None):
    """Insert user (role='trainer'), then trainer record. Return (trainer_id, user_id, plain_password)"""
    conn = get_connection()
    cursor = conn.cursor()

    # Generate password and create user
    role = 'trainer'
    plain_password, hashed_password = generate_user_password(full_name, phone)
    cursor.execute("""
        INSERT INTO users (email, password_hash, full_name, phone, role, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (email, hashed_password, full_name, phone, role, is_active))
    user_id = cursor.lastrowid

    # Insert trainer record (remove is_active if not present in table)
    cursor.execute("""
        INSERT INTO trainers (user_id, dob, qualifications, experience_years, specialization)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, dob, qualifications, experience_years, specialization))
    trainer_id = cursor.lastrowid

    conn.commit()
    cursor.close()
    conn.close()

    # Send emails if app and mail are provided
    if app and mail and admin_email:
        from models.email_utils import send_all_emails
        Thread(target=send_all_emails, args=(full_name, email, role, plain_password, admin_email, app, mail)).start()

    return trainer_id, user_id, plain_password


def create_admin(full_name, email, phone, is_active=True, app=None, mail=None, admin_email=None):
    """
    Create an admin user, insert into users and admins tables, and send email.
    Returns (admin_id, user_id, plain_password)
    """
    from werkzeug.security import generate_password_hash
    from models.email_utils import send_all_emails

    conn = get_connection()
    cursor = conn.cursor()

    # Generate password
    plain_password, hashed_password = generate_user_password(full_name, phone)

    # Insert into users table
    cursor.execute("""
        INSERT INTO users (email, password_hash, full_name, phone, role, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (email, hashed_password, full_name, phone, "admin", is_active))
    conn.commit()
    user_id = cursor.lastrowid

    # Insert into admins table (only user_id, rest can be updated later)
    cursor.execute("""
        INSERT INTO admins (user_id)
        VALUES (%s)
    """, (user_id,))
    conn.commit()
    admin_id = cursor.lastrowid

    cursor.close()
    conn.close()

    # Send email if app/mail/admin_email provided
    if app and mail and admin_email:
        send_all_emails(full_name, email, "admin", plain_password, admin_email, app, mail)

    return admin_id, user_id, plain_password


def get_course_counts():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM courses WHERE is_active = True")
    active = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM courses WHERE is_active = False")
    inactive = cursor.fetchone()[0]
    conn.close()
    return active, inactive
