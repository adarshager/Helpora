# Helpora - AI Customer Support Agent
chatbot that answers ONLY from a company knowledge base (RAG). When it cannot answer, or the customer
asks for a person, it creates a **support ticket**, **alerts a human agent instantly** (Telegram / Slack / email),
sends the customer a **confirmation email**, and gives the agent a **dashboard** to track status and reply.

```
Customer -> Chat UI -> Flask API -> Knowledge base search -> AI (or offline) answer
                              |
                  "I couldn't find this" / "Talk to human"
                              |
              Ticket saved in database (SQLite or Postgres)
                              |
   Telegram / Slack / email alert  ->  Agent opens /agent  ->  changes status, replies by email
```

---------------------------------------------------------------------------------------------------

## 1. Run it on your computer (VS Code)

=======
A chatbot that answers customer questions ONLY from a company knowledge base (RAG), says so when it
does not know, and hands over to a human agent by creating a support ticket.

## Run in VS Code
>>>>>>> dc31bff7ead649512dafebfa2281b97529237360
1. Open this folder in VS Code, then open a terminal (Ctrl + `).
2. Create and activate a virtual environment
   - Windows: `python -m venv .venv` then `.venv\Scripts\activate`
   - Mac/Linux: `python3 -m venv .venv` then `source .venv/bin/activate`

3. Install the packages: `pip install -r requirements.txt`
4. Copy `.env.example` to a new file named `.env` (never upload `.env` to GitHub).
5. In `.env`, set at least: `AGENT_PASSWORD=choose-a-strong-password`
6. Start the app: `python app.py`
7. Open http://127.0.0.1:5000 for the chat, and http://127.0.0.1:5000/agent for the agent desk.

With nothing else configured the bot works in offline mode, tickets are saved in a local file `helpora.db`,
and no alerts or emails are sent. Now add the features below one by one.

## 2. Settings reference (`.env` locally, Environment tab on Render)

| Variable | Needed? | What it does |
|---|---|---|
| `AGENT_PASSWORD` | Yes, for the agent desk | Password for `/agent` (there is no default) |
| `AGENT_USERNAME` | No | Agent login name, default `agent` |
| `SECRET_KEY` | Recommended | Long random text that protects login cookies. Render can generate it for you |
| `BASE_URL` | Recommended | Your site address, used for the link inside alerts, e.g. `https://helpora.onrender.com` |
| `DATABASE_URL` | For production | Postgres connection string. Empty = local SQLite file |
| `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` | No | Turns on AI mode (add only one) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | No | Instant phone alert to the agent |
| `SLACK_WEBHOOK_URL` | No | Alert in a Slack channel |
| `BREVO_API_KEY`, `MAIL_FROM_EMAIL`, `MAIL_FROM_NAME` | No | Sends emails (customer confirmation + agent replies) |
| `AGENT_NOTIFY_EMAIL` | No | Email address that receives a copy of every new-ticket alert, and is used as reply-to |

## 3. Agent login

1. Set `AGENT_PASSWORD` (and optionally `AGENT_USERNAME`) in `.env`, then restart the app.
2. Go to `/agent` and log in. You see all tickets with the counts for Open, In progress and Resolved.
3. Click a ticket to see the customer, the chat transcript and the replies. Change the status, or write a reply.
4. If email is configured, "Send reply by email" emails the customer; otherwise the reply is saved only.
5. The list refreshes itself every minute.

## 4. Telegram alert (recommended, free, instant)

1. In Telegram, search for **@BotFather** and send `/newbot`. Choose a name and a username ending in `bot`.
2. BotFather replies with a **token**. Put it in `TELEGRAM_BOT_TOKEN`.
3. Open your new bot in Telegram and send it any message (for example `hi`).
4. In your browser open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` (replace `<YOUR_TOKEN>`).
   Find `"chat":{"id":123456789,...}` in the result. That number is your **chat id**. Put it in `TELEGRAM_CHAT_ID`.
   (For a group, add the bot to the group, send a message there, and use the group's id, which starts with a minus sign.)
5. Restart the app, create a test ticket with Talk to human, and the alert arrives on your phone within seconds.

## 5. Slack alert (optional)

1. Go to https://api.slack.com/apps, create an app, enable **Incoming Webhooks**, and add a webhook to a channel.
2. Copy the webhook URL into `SLACK_WEBHOOK_URL`.

## 6. Email with Brevo (customer confirmation and agent replies)

Why an email API and not SMTP? Free Render services block the normal SMTP ports, so Helpora sends email over HTTPS.

1. Create a free account at https://www.brevo.com.
2. Add and verify a **sender**: an email address you can open (menu names may differ slightly, look for "Senders").
   Brevo sends you a verification link.
3. Create an **API key** (look for "SMTP & API", then "API keys") and put it in `BREVO_API_KEY`.
4. Set `MAIL_FROM_EMAIL` to the verified sender address exactly, and optionally `MAIL_FROM_NAME`.
5. Set `AGENT_NOTIFY_EMAIL` to the address where the agent wants alerts (can be the same address).
6. Restart and test. Check the spam folder: mail sent from a free Gmail address is often marked as spam.
   A business domain with proper email authentication fixes this in a real deployment.
7. Check Brevo's current free daily sending limit on their pricing page before relying on it.

## 7. Database

- **Local testing:** leave `DATABASE_URL` empty. Tickets go to the file `helpora.db` (ignored by git).
- **Online (Render):** the Render free disk is wiped when the service restarts, so use a hosted Postgres:
  1. Create a free project at https://neon.tech (Supabase also works).
  2. Copy the connection string. It looks like `postgresql://user:password@host/dbname?sslmode=require`.
  3. Put it in `DATABASE_URL`. The tables are created automatically on startup.
  4. Check the provider's current free limits.

## 8. Deploy free on Render

1. Push this folder to a new GitHub repository (check that `.env` and `helpora.db` are not included).
2. On https://render.com choose **New > Blueprint**, pick your repository. Render reads `render.yaml`.
   (Manual alternative: New > Web Service, build `pip install -r requirements.txt`, start `gunicorn app:app --timeout 120`, Free instance.)
3. Fill in the values it asks for. At minimum: `AGENT_PASSWORD` and `DATABASE_URL`. Leave the rest empty if unused.
4. After the first deploy, copy your site address (like `https://helpora-xxxx.onrender.com`) and set it as `BASE_URL` in the Environment tab.
5. Add the Telegram / Brevo / AI keys you want in the same Environment tab. Render restarts the service by itself.
6. Free services sleep after 15 minutes without visitors, and the first request afterwards takes about a minute.
   Open the link yourself before showing it to anyone.

## 9. Test checklist

1. Chat: ask "How long does delivery take?" and see an answer with a source label.
2. Ask "Write me a poem" and see the not-found message with Yes/No buttons.
3. Click Yes, connect me, fill in the form, and submit. You get a ticket ID.
4. Check: Telegram/Slack message arrives, the customer receives the confirmation email, a ticket appears in `/agent`.
5. In `/agent`, open the ticket, send a reply, then set the status to Resolved.
6. Anti-spam: more than 5 tickets per hour from one network are refused.

## 10. Use your own company's knowledge base

Edit or add `.md` files in `knowledge_base/`: a `# Section title` line, then pairs of lines
`Q: question` and `A: answer` (each on ONE line). Restart the app. Also change the sample store name `COMPANY`
near the top of `app.py` and the "ShopEase" wording in `notify.py`.

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| Cannot log in at `/agent` | `AGENT_PASSWORD` is not set, or the username/password is wrong. Restart after changing `.env` |
| No Telegram alert | Send your bot a message first, recheck the chat id, and see the Render logs for "Telegram error" |
| No emails | Verify the sender in Brevo, make sure `MAIL_FROM_EMAIL` matches it exactly, check spam, see logs for "Brevo error" |
| Tickets disappear on Render | You are using SQLite. Set `DATABASE_URL` to a Postgres database |
| Alert has no link | Set `BASE_URL` |
| Page unstyled | Tailwind loads from a CDN, so the browser needs internet |
| Badge says "Offline mode (AI unavailable)" | Hover it to see the reason; check the AI key |

## Structure
- `app.py` - chat API, retrieval, AI call, tickets, agent desk routes
- `notify.py` - Telegram / Slack alerts and Brevo email
- `knowledge_base/` - policy documents
- `templates/index.html` - chat UI; `login.html`, `agent.html`, `ticket.html` - agent desk
=======
3. `pip install -r requirements.txt`
4. `python app.py` and open http://127.0.0.1:5000

## Enable AI mode (optional)
Copy `.env.example` to `.env` and add ONE key: `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` (free from Google AI Studio).
Without a key the bot runs in offline mode and returns the best-matching FAQ answer.

## Use your own company's knowledge base
Edit or add `.md` files in `knowledge_base/`. Format: a `# Section title` line, then pairs of lines
`Q: question` and `A: answer` (each on ONE line). Restart the app after editing.

## Support tickets
"Talk to human" saves a ticket (name, email, chat transcript) to `tickets.json`. Set `ADMIN_KEY` in `.env`
and open `/admin/tickets?key=YOUR_KEY` to view them. On Render's free tier files reset when the service
restarts, so tickets there are for demo only.

## Deploy free on Render
Push to GitHub, then Render > New > Blueprint (uses `render.yaml`) and choose the Free instance.
Start command: `gunicorn app:app --timeout 120`. Free services sleep after 15 minutes idle.

## Structure
- `app.py` - Flask API, retrieval, AI call, offline answers, tickets
- `knowledge_base/` - policy documents (shipping, returns, refunds, payments, cancellation, FAQs)
- `templates/index.html` - chat UI (Tailwind via CDN, needs internet)
>>>>>>> dc31bff7ead649512dafebfa2281b97529237360
- `docs/Project_Report.docx` - project report
