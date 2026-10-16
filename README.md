# Helpora - AI Customer Support Agent

A chatbot that answers customer questions ONLY from a company knowledge base (RAG), says so when it
does not know, and hands over to a human agent by creating a support ticket.

## Run in VS Code
1. Open this folder in VS Code, then open a terminal (Ctrl + `).
2. Create and activate a virtual environment
   - Windows: `python -m venv .venv` then `.venv\Scripts\activate`
   - Mac/Linux: `python3 -m venv .venv` then `source .venv/bin/activate`
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
- `docs/Project_Report.docx` - project report
