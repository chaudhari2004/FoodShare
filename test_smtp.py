import os
import smtplib
import socket
import ssl
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("SMTP_HOST", "smtp.gmail.com")
port = int(os.getenv("SMTP_PORT", "587"))
username = os.getenv("SMTP_USERNAME")
password = os.getenv("SMTP_PASSWORD")

print("Testing SMTP connection...")

try:
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        # smtp.set_debuglevel(1)
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        print("TLS connection successful.")

        smtp.login(username, password)
        print("Gmail authentication successful.")

except (smtplib.SMTPException, OSError, socket.error) as error:
    print("SMTP test failed:", type(error).__name__, str(error))