import os
import time
import warnings
from dotenv import load_dotenv
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain_community.chat_models import ChatOpenAI
from langchain.chains import ConversationalRetrievalChain

warnings.filterwarnings("ignore")

# ─── Load environment ────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
load_dotenv(os.path.join(BASE_DIR, ".env"))

OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")

if not OPENAI_API_KEY or not PINECONE_INDEX_NAME:
    raise RuntimeError("Missing OPENAI_API_KEY or PINECONE_INDEX_NAME in .env")

# ─── Global chat history ─────────────────────────────────────────────────────────
chat_history = []

# ─── PROFESSIONAL SYSTEM PROMPT ──────────────────────────────────────────────────
# This tells the AI to structure text like a professional document
system_context = """
You are the Morgan State CS Navigator, a professional academic advisor.
Your goal is to provide clear, visually structured, and helpful guidance.

GUIDELINES FOR OUTPUT:
1. **Use Headers:** Start main sections with '### ' (e.g., ### Core Requirements).
2. **Use Bullet Points:** Always use bullet points (-) for lists or steps. Never write long comma-separated lists.
3. **Spacing:** Add a blank line between sections for readability.
4. **Links:** Format links as `[Link Text](URL)`.
5. **Tone:** Professional, concise, and direct. Avoid fluff.
6. **Formatting:** Use **bold** for credit counts, course codes (e.g., **COSC 101**), or deadlines.
"""

def main():
    # 1) Initialize the OpenAI embeddings and Pinecone-backed vector store
    embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    vectorstore = PineconeVectorStore(
        index_name=PINECONE_INDEX_NAME,
        embedding=embeddings
    )

    # 2) Build a retriever with Maximal Marginal Relevance (MMR) for diversity
    retriever = vectorstore.as_retriever(
        search_type="mmr",
        search_kwargs={"k": 5, "fetch_k": 15}
    )

    # 3) Create a streaming-capable ChatOpenAI instance
    chat = ChatOpenAI(
        model_name="gpt-3.5-turbo",
        temperature=0.0,
        streaming=True,
        callbacks=[StreamingStdOutCallbackHandler()],
        verbose=False
    )

    # 4) Assemble the conversational retrieval chain
    qa = ConversationalRetrievalChain.from_llm(
        llm=chat,
        chain_type="stuff",
        retriever=retriever
    )

    print("\nAI Chatbot is ready! Type 'exit' to quit.\n")

    while True:
        user_question = input("You: ").strip()
        if user_question.lower() in ("exit", "quit"):
            print("Goodbye!")
            break

        # 5) Build the prompt
        prompt = system_context + "\n\n" + user_question

        # 6) Time retrieval
        t0 = time.time()
        docs = retriever.get_relevant_documents(prompt)
        t1 = time.time()

        # 7) Stream the generation
        print("Bot: ", end="", flush=True)
        t2 = time.time()
        qa({"question": prompt, "chat_history": chat_history})
        t3 = time.time()
        print("\n")  # finish the streaming line

        # 8) Print timings
        print(f"[Retrieval: {t1-t0:.2f}s | Generation: {t3-t2:.2f}s]\n")

        # 9) Store a minimal placeholder in history
        chat_history.append((user_question, "<response streamed>"))

if __name__ == "__main__":
    main()