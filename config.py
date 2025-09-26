import os
from dotenv import load_dotenv

# Load values from .env file
load_dotenv()

FLASK_DEBUG = os.getenv("FLASK_DEBUG", "True") == "True"
SECRET_KEY = os.getenv("SECRET_KEY","adarsh-secret-key")

# Database connection settings
class Config:
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "Adarsh50#")
    DB_NAME = os.getenv("DB_NAME", "training_center_db")
    DB_PORT = int(os.getenv("DB_PORT", 3306))

    # Optional: Email settings for Flask-Mail
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "True") == "True"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "trainingcentermanagement@gmail.com")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "ridi lnat ffil qzyr")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "trainingcentermanagement@gmail.com")
    MAIL_DEBUG = os.getenv("MAIL_DEBUG", "False") =="True"
