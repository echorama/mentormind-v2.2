from flask import Flask, request, jsonify, render_template, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from openai import OpenAI
from pinecone import Pinecone
import os
import traceback
import time
import uuid
import tiktoken


SYSTEM_PROMPT = """
You are a fintech regulatory advisor specializing in Turkish financial laws. You analyze legal questions based on current and historical Turkish regulations provided below.

🧠 MEMORY BEHAVIOR:
- Always remember and refer to the user's previous messages.
- Maintain context of past questions during the session.
- Do not repeat yourself unless asked.
- Ask clarifying questions if context is unclear.

🌍 LANGUAGE:
- Respond in the same language the user used. English if English,  Turkish if Turkish, Russian if russian.

📌 CORE RULES:
- Compare ALL relevant regulations.
- Use the MOST RECENT DATE unless told otherwise.
- If a topic has not been updated, use the oldest valid rule.

📊 NUMERIC RULES:
- For capital requirements, fees, penalties:
  → Extract ALL values with dates.
  → Use the MOST RECENT valid value.
  → If values changed, EXPLAIN the change clearly (e.g. from 2.000.000 TL to 5.000.000 TL).

📚 CITATION RULE:
- Always cite like: '[Document Name]-[DD/MM/YYYY]-Madde[Number]'
- Format all money like: 'X.XXX TL'

💬 IF NON-REGULATORY:
- Be friendly and helpful like a smart consultant friend.
"""



app = Flask(__name__)

# === Database Configuration ===
uri = os.getenv("DATABASE_URL")
if uri and uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = uri or "sqlite:///chat_memory.db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CORS(app, resources={r"/chat": {"origins": "*"}})
app.secret_key = "my_super_secret_key_1234"

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True
}


db = SQLAlchemy(app)

class ChatMemory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# Environment variables or keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

if not OPENAI_API_KEY or not PINECONE_API_KEY:
    raise ValueError("Missing API keys. Please set OPENAI_API_KEY and PINECONE_API_KEY as environment variables.")

client = OpenAI()
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index("turkish-fintech-regulations")

@app.after_request
def apply_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response
    




@app.route("/chat", methods=["OPTIONS"])
def chat_options():
    return '', 204

def get_or_create_session_id():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return session["session_id"]

def search_pinecone(query, top_k=80, max_words_per_match=200):
    try:
        print(f"🟡 Embedding query:{query}")
        response = client.embeddings.create(
            model="text-embedding-ada-002",
            input=query
        )
        query_embedding = response.data[0].embedding

        print("🟡 Querying Pinecone index...")

        search_results = index.query(
            vector=query_embedding,
            top_k=top_k * 2,  # request more to account for skipped long ones
            include_metadata=True,
            namespace=""
        )

        matches = search_results['matches']
        print(f"🔎 Pinecone returned {len(matches)} matches.")

        regulations = []
        for match in matches:
            text = match['metadata']['text']
            word_count = len(text.split())

            if word_count <= max_words_per_match:
                regulations.append(text)

            if len(regulations) >= top_k:
                break  # stop once we’ve collected enough valid matches

        print(f"🔍 Accepted {len(regulations)} short-enough regulations from Pinecone.")

        if regulations:
            print(f"📘 Example regulation preview:\n{regulations[0][:300]}...\n")
        else:
            print("⚠️ No usable regulations found (all too long).")

        return regulations

    except Exception as e:
        print("❌ ERROR in Pinecone Search")
        traceback.print_exc()
        return ["Error retrieving regulations: " + str(e)]

    except Exception as e:
        print("❌ ERROR in Pinecone Search")
        traceback.print_exc()
        return ["Error retrieving regulations: " + str(e)]


def update_chat_memory(session_id, user_message, bot_response):
    chat_user = ChatMemory(session_id=session_id, role="user", content=user_message)
    chat_bot = ChatMemory(session_id=session_id, role="assistant", content=bot_response)

    db.session.add_all([chat_user, chat_bot])
    db.session.commit()

def get_chat_memory(session_id):
    two_hours_ago = datetime.utcnow() - timedelta(hours=2)
    try:
        chats = ChatMemory.query.filter(
            ChatMemory.session_id == session_id,
            ChatMemory.timestamp >= two_hours_ago
        ).order_by(ChatMemory.timestamp).all()
        return [{"role": c.role, "content": c.content} for c in chats]
    except Exception as e:
        print("❌ Failed to retrieve chat memory:", e)
        return []

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message", "").strip()
    incoming_session_id = request.json.get("session_id", "").strip()

    if not user_message or not incoming_session_id:
        return jsonify({"error": "Missing message or session ID"}), 400

    # Store session ID for consistency
    session["session_id"] = incoming_session_id

    print(f"🟢 USER MESSAGE ({incoming_session_id}): {user_message}")

    try:
        regulations = search_pinecone(user_message)
        regulations_text = "\n\n".join(regulations)

        print(f"📄 Sending regulations to GPT. First 500 chars:\n{regulations_text[:500]}")

        chat_history = get_chat_memory(incoming_session_id)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ] + chat_history + [
            {"role": "user", "content": user_message},
            {"role": "system", "content": (
                "Here are the regulations retrieved from the database:\n\n"
                f"{regulations_text}\n\n"
                "🧠 INSTRUCTIONS FOR INTERPRETATION:\n"
                "- Carefully examine all retrieved regulations.\n"
                "- For capital requirements and similar numeric values:\n"
                "    → Extract **all relevant values** mentioned in regulations.\n"
                "    → Sort them by date [DD/MM/YYYY].\n"
                "    → Use the **most recent applicable value**.\n"
                "⚠️ VERY IMPORTANT:\n"
                "- Always verify and use the **most recent and legally binding regulation**, especially for numeric obligations like minimum capital requirements.\n"
                "📚 Always cite like this: '[Document Name]-[DD/MM/YYYY]-Madde[Number]'\n"
                "💸 Format all amounts like: 'X.XXX TL'\n"
                "🌐 Use the same language as the user_message."
            )}
        ]

        time.sleep(0.1)

        print("📤 GPT Prompt Messages Preview:")
        for msg in messages:
            print(f"{msg['role']}:\n{msg['content'][:300]}...\n")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=1000,
            temperature=0.7,
        )

        reply = response.choices[0].message.content.strip()
        print(f"🟣 BOT RESPONSE: {reply}")

        update_chat_memory(incoming_session_id, user_message, reply)
        return jsonify({"response": reply})

    except Exception as e:
        print("=== ERROR in /chat ===")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/clear_memory", methods=["POST"])
def clear_memory():
    session_id = get_or_create_session_id()
    ChatMemory.query.filter_by(session_id=session_id).delete()
    db.session.commit()
    return jsonify({"response": "✅ Chat memory cleared."})
    
def cleanup_old_memory():
    threshold = datetime.utcnow() - timedelta(hours=2)
    try:
        deleted = ChatMemory.query.filter(ChatMemory.timestamp < threshold).delete()
        db.session.commit()
        print(f"🧹 Deleted {deleted} old messages.")
    except Exception as e:
        print("❌ Cleanup failed:", e)

def start_cleanup_thread():
    def run():
        while True:
            time.sleep(3600)  # every hour
            cleanup_old_memory()
    threading.Thread(target=run, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    start_cleanup_thread()
    app.run(host="0.0.0.0", port=port, debug=True)
