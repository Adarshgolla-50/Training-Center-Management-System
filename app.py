from flask import Flask,redirect,render_template,url_for,session
import os
from models.user_model import create_super_admin,validate_user
from auth.routes import auth_bp
from admin.routes import admin_bp
from trainer.routes import trainer_bp
from super_admin.routes import superadmin_bp
from student.routes import student_bp
from flask_mail import Mail
from config import Config,SECRET_KEY,FLASK_DEBUG


app = Flask(__name__)
# Load configuration from Config class
app.config.from_object(Config)
app.config['SECRET_KEY'] = SECRET_KEY



# Initialize Mail
mail = Mail(app)

# Ensure default admin exists
with app.app_context():
    create_super_admin()

# Register the blueprint
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(superadmin_bp,url_prefix='/super_admin')
app.register_blueprint(trainer_bp,url_prefix='/trainer')
app.register_blueprint(student_bp,url_prefix='/student')


# Home route
@app.route('/')
def home():
    return redirect(url_for('auth.login'))

if __name__ == "__main__":
    app.run(debug=FLASK_DEBUG)