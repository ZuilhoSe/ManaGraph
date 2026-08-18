import sqlite3
import os
import chromadb
from embeddings import MiniLMStrategy

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
os.makedirs(CHROMA_DIR, exist_ok=True)


def generate_embeddings():
    print("Initializing the embedding provider...")

    embedding_provider = MiniLMStrategy().get_function()

    print(f"Connecting to ChromaDB at: {CHROMA_DIR}")
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

    collection = chroma_client.get_or_create_collection(
        name="oracle_cards",
        embedding_function=embedding_provider,
        metadata={"description": "Vectorized Magic: The Gathering oracle text"}
    )

    print("Reading cards from the local SQLite database...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, type_line, oracle_text, color_identity FROM cards WHERE type_line NOT LIKE '%Basic Land%'"
    )
    cards = cursor.fetchall()
    conn.close()

    print(f"Found {len(cards)} cards. Processing in batches...")
    batch_size = 1000

    for i in range(0, len(cards), batch_size):
        batch = cards[i : i + batch_size]
        ids, documents, metadatas = [], [], []

        for card in batch:
            card_id, name, type_line, oracle_text, color_identity = card
            oracle_text = oracle_text if oracle_text else "Vanilla creature / No abilities."

            document_text = f"{name} - {type_line}. Effect: {oracle_text}"

            ids.append(card_id)
            documents.append(document_text)
            metadatas.append({"name": name, "color_identity": color_identity})

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        print(f"Batch {i//batch_size + 1}: {min(i + batch_size, len(cards))} / {len(cards)} cards.")

    print("\nVectorization complete.")


if __name__ == "__main__":
    generate_embeddings()
