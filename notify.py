"""Helpora notifications: Telegram / Slack alerts for agents and email through the Brevo HTTP API.
HTTP APIs are used (not SMTP) because free Render services block outbound SMTP ports."""
import os
import logging
import requests

log = logging.getLogger("helpora.notify")


def base_url():
    return os.getenv("BASE_URL", "").strip().rstrip("/")


def email_ready():
    return bool(os.getenv("BREVO_API_KEY", "").strip() and os.getenv("MAIL_FROM_EMAIL", "").strip())


def send_email(to, subject, body, reply_to=None):
    """Send a plain-text email. Returns (ok, message)."""
    if not email_ready():
        return False, "Email is not configured"
    payload = {"sender": {"name": os.getenv("MAIL_FROM_NAME", "Helpora Support"),
                          "email": os.getenv("MAIL_FROM_EMAIL").strip()},
               "to": [{"email": to}], "subject": subject, "textContent": body}
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    r = requests.post("https://api.brevo.com/v3/smtp/email", timeout=15, json=payload,
                      headers={"api-key": os.getenv("BREVO_API_KEY").strip(),
                               "content-type": "application/json", "accept": "application/json"})
    if r.ok:
        return True, "sent"
    log.warning("Brevo error %s: %s", r.status_code, r.text[:200])
    return False, f"Email provider error {r.status_code}"


def _telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat):
        return
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text}, timeout=10)
    if not r.ok:
        log.warning("Telegram error %s: %s", r.status_code, r.text[:200])


def _slack(text):
    url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if url:
        r = requests.post(url, json={"text": text}, timeout=10)
        if not r.ok:
            log.warning("Slack error %s: %s", r.status_code, r.text[:200])


def notify_new_ticket(t):
    """Alert the agent (Telegram, Slack, email) and send the customer a confirmation. Never raises."""
    link = f"{base_url()}/agent/ticket/{t['id']}" if base_url() else "(set BASE_URL to get a dashboard link)"
    msg = (f"New support request {t['id']}\nName: {t['name']}\nEmail: {t['email']}\n"
           f"Topic: {t['topic'] or 'General enquiry'}\nOpen: {link}")
    agent = os.getenv("AGENT_NOTIFY_EMAIL", "").strip()
    jobs = [("telegram", lambda: _telegram(msg)), ("slack", lambda: _slack(msg))]
    if agent:
        jobs.append(("agent email", lambda: send_email(agent, f"[Helpora] New support request {t['id']}", msg)))
    jobs.append(("customer email", lambda: send_email(
        t["email"], f"We received your request {t['id']}",
        f"Hi {t['name']},\n\nThanks for contacting ShopEase support. We received your request {t['id']} and a "
        f"support agent will reply to this email address within 24 hours.\n\nTopic: {t['topic'] or 'General enquiry'}\n\n"
        f"Regards,\nShopEase Support", reply_to=agent or None)))
    for name, fn in jobs:
        try:
            fn()
        except Exception as e:
            log.warning("%s notification failed: %s", name, e)
