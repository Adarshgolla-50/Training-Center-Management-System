from threading import Thread
from flask_mail import Message

def send_email_async(app, msg, mail):
    """
    Send email inside the Flask app context.
    """
    with app.app_context():
        try:
            mail.send(msg)
            print(f"📧 Email successfully sent to {msg.recipients}")
        except Exception as e:
            print(f"❌ Failed to send email to {msg.recipients}: {e}")

def send_user_email(full_name, email, role, password, app, mail):
    """
    Send account credentials to the new user asynchronously, including login link.
    """
    # Construct login URL dynamically
    login_url = f"{app.config.get('BASE_URL', 'http://127.0.0.1:5000')}/auth/login"

    msg = Message(
        subject="Your Account Credentials",
        sender=app.config['MAIL_USERNAME'],
        recipients=[email],
        body=f"Hello {full_name},\n\n"
             f"Your account has been created:\n"
             f"Role: {role}\n"
             f"Temporary Password: {password}\n\n"
             f"Please login using the link below and change your password immediately:\n"
             f"{login_url}\n\n"
             "Thank you."
    )

    # Send email asynchronously
    Thread(target=send_email_async, args=(app, msg, mail)).start()


def send_admin_email(admin_email, full_name, user_email, role, app, mail):
    """
    Send notification to the admin about the new user asynchronously.
    """
    msg = Message(
        subject="New User Created",
        sender=app.config['MAIL_USERNAME'],
        recipients=[admin_email],
        body=f"Hello Admin,\n\n"
             f"A new user has been created:\n"
             f"Full Name: {full_name}\n"
             f"Email: {user_email}\n"
             f"Role: {role}\n\n"
             "This is an automated notification."
    )
    # Pass the real app instance using _get_current_object()
    Thread(target=send_email_async, args=(app, msg, mail)).start()

def send_all_emails(full_name, user_email, role, password, admin_email, app, mail):
    """
    Send both user and admin emails asynchronously.
    """
    send_user_email(full_name, user_email, role, password, app, mail)
    send_admin_email(admin_email, full_name, user_email, role, app, mail)
    print("✅ Both emails triggered for sending.")

def send_password_reset_email(full_name, email, reset_link, app, mail):
    """
    Send password reset email asynchronously.
    """
    msg = Message(
        subject="Password Reset Request - TCMS",
        sender=app.config['MAIL_USERNAME'],
        recipients=[email],
        body=f"Hi {full_name},\n\nClick the link below to reset your password:\n{reset_link}\n\nThis link is valid for 1 hour.\n\nIf you didn't request this, ignore this email."
    )
    Thread(target=send_email_async, args=(app, msg, mail)).start()

def send_password_reset_success_email(full_name, email, app, mail):
    """
    Send a confirmation email after successful password reset.
    """
    msg = Message(
        subject="Your TCMS Password Has Been Reset",
        sender=app.config['MAIL_USERNAME'],
        recipients=[email],
        body=f"Hi {full_name},\n\nYour password has been successfully reset.\n\nIf you did not perform this action, please contact support immediately.\n\nThank you."
    )
    Thread(target=send_email_async, args=(app, msg, mail)).start()
