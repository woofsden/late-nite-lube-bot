import os
import smtplib
from email.mime.text import MIMEText

# This function will send order confirmation emails to Late Nite Lube
def send_order_email(order_text: str):
    """
    Sends an email with the order details to Late Nite Lube notifications.
    """

    # Load settings from environment (set these in Replit secrets)
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")

    recipient = "orders@latenitelube.com"

    if not smtp_user or not smtp_password:
        raise ValueError("SMTP_USER and SMTP_PASSWORD environment variables must be set!")

    # Build the email
    msg = MIMEText(order_text)
    msg["Subject"] = "New Late Nite Lube Order"
    msg["From"] = smtp_user
    msg["To"] = recipient

    # Send via SMTP
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        print("✅ Order email sent to Late Nite Lube")
    except Exception as e:
        print(f"❌ Failed to send order email: {e}")
