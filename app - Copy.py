# app.py
from flask import Flask, request, jsonify, render_template, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from openai import OpenAI
from pinecone import Pinecone
from docx import Document
import fitz  # PyMuPDF
from werkzeug.utils import secure_filename
import os
import traceback
import time
import uuid
import urllib.parse

app = Flask(__name__)

# === Database Configuration ===
uri = os.getenv("DATABASE_URL")
if uri and uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = uri or "sqlite:///chat_memory.db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CORS(app, resources={r"/chat": {"origins": "*"}, r"/upload": {"origins": "*"}})
app.secret_key = "my_super_secret_key_1234"

db = SQLAlchemy(app)

class ChatMemory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# Environment variables or keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or "sk-..."
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY") or "pcsk-..."

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

def search_pinecone(query, top_k=20):
    try:
        response = client.embeddings.create(
            model="text-embedding-ada-002",
            input=query
        )
        query_embedding = response.data[0].embedding

        search_results = index.query(
            vector=query_embedding,
            top_k=top_k,
            include_metadata=True,
            namespace=""
        )

        regulations = [match['metadata']['text'] for match in search_results['matches']]
        return regulations

    except Exception as e:
        print("=== ERROR in Pinecone Search ===")
        traceback.print_exc()
        return ["Error retrieving regulations: " + str(e)]

def update_chat_memory(user_message, bot_response):
    session_id = get_or_create_session_id()

    chat_user = ChatMemory(session_id=session_id, role="user", content=user_message)
    chat_bot = ChatMemory(session_id=session_id, role="assistant", content=bot_response)

    db.session.add_all([chat_user, chat_bot])
    db.session.commit()

def get_chat_memory():
    session_id = get_or_create_session_id()
    chats = ChatMemory.query.filter_by(session_id=session_id).order_by(ChatMemory.timestamp).all()
    return [{"role": c.role, "content": c.content} for c in chats[-10:]]

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message", "").strip()
    print(f"🟢 USER MESSAGE: {user_message}")

    if not user_message:
        return jsonify({"error": "Message cannot be empty"}), 400

    try:
        regulations = search_pinecone(user_message)
        regulations_text = "\n\n".join(regulations)

        chat_history = get_chat_memory()

        formatted_history = "\n".join(
            f"{'You' if msg['role'] == 'user' else 'Assistant'}: {msg['content']}"
            for msg in chat_history
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a fintech expert specializing in Turkish financial regulations. "
                    "If the user is not talking about business, just chat casually and helpfully. "
                    "You should also remember what the user said earlier in the conversation and refer to it when relevant."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Please remember the conversation below. It may help you answer the next question:\n\n"
                    f"{formatted_history}\n\n"
                    f"Now the user asks: {user_message}\n\n"
                    f"Here are some regulations that might help:\n\n"
                    f"{regulations_text}\n\n"
                    f"Please respond clearly and naturally, referring to both the memory and the regulations."
                )
            }
        ]

        time.sleep(1)

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=1500,
            temperature=0.7,
        )

        reply = response.choices[0].message.content.strip()
        print(f"🟣 BOT RESPONSE: {reply}")

        update_chat_memory(user_message, reply)
        return jsonify({"response": reply})

    except Exception as e:
        print("=== ERROR in /chat ===")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/upload", methods=["POST"])
def upload_file():
    try:
        uploaded_file = request.files.get("file")
        if not uploaded_file:
            return jsonify({"response": "No file uploaded."}), 400

        filename = secure_filename(uploaded_file.filename)

        if filename.endswith(".docx"):
            doc = Document(uploaded_file)
            full_text = "\n".join([para.text for para in doc.paragraphs])
            preview = full_text[:500] or "[File is empty or has no readable text]"
            return jsonify({
                "response": f"✅ File '{filename}' received.\n\nExtracted text:\n\n{preview}"
            })

        elif filename.endswith(".pdf"):
            doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
            text = ""
            for page in doc:
                text += page.get_text()
            preview = text[:500] or "[PDF has no readable text]"
            return jsonify({
                "response": f"✅ File '{filename}' received.\n\nExtracted text:\n\n{preview}"
            })

        else:
            content = uploaded_file.read().decode("utf-8", errors="ignore")
            return jsonify({
                "response": f"✅ File '{filename}' received.\n\nFirst 300 characters:\n\n{content[:300]}"
            })

    except Exception as e:
        print("=== ERROR in /upload ===")
        traceback.print_exc()
        return jsonify({"response": "❌ Error processing file: " + str(e)}), 500

@app.route("/clear_memory", methods=["POST"])
def clear_memory():
    session_id = get_or_create_session_id()
    ChatMemory.query.filter_by(session_id=session_id).delete()
    db.session.commit()
    return jsonify({"response": "✅ Chat memory cleared."})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
