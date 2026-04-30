import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import os
import json
from datetime import datetime

# shop@propertya.co.jp IMAP設定
SHOP_IMAP = {
    "host": "mail1044.onamae.ne.jp",
    "port": 993,
    "user": "shop@propertya.co.jp",
    "password": "proper@7285"
}
SHOP_SMTP = {
    "host": "mail1044.onamae.ne.jp",
    "port": 465,
    "user": "shop@propertya.co.jp",
    "password": "proper@7285"
}

def decode_str(s):
    if s is None:
        return ""
    decoded = decode_header(s)
    result = ""
    for part, enc in decoded:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="replace")
        else:
            result += part
    return result

def fetch_emails_imap(account="shop", limit=20):
    """IMAPでメールを取得"""
    cfg = SHOP_IMAP
    try:
        mail = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
        mail.login(cfg["user"], cfg["password"])
        mail.select("INBOX")
        _, data = mail.search(None, "ALL")
        ids = data[0].split()
        ids = ids[-limit:][::-1]
        messages = []
        for mid in ids:
            _, msg_data = mail.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
            messages.append({
                "id": mid.decode(),
                "from": decode_str(msg["From"]),
                "subject": decode_str(msg["Subject"]),
                "date": decode_str(msg["Date"]),
                "body": body[:2000],
                "account": account
            })
        mail.logout()
        return messages, None
    except Exception as e:
        return [], str(e)

def send_email_smtp(to, subject, body, account="shop"):
    """SMTPでメール送信"""
    cfg = SHOP_SMTP
    try:
        msg = MIMEMultipart()
        msg["From"] = cfg["user"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"]) as server:
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)
