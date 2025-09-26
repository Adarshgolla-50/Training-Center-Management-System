from models.db import get_connection

conn = get_connection()
if conn:
    print("✅ Database connected successfully!")
    conn.close()
else:
    print("❌ Failed to connect.")

