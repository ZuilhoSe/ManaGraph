import sqlite3
import os
import chromadb
from chromadb.utils import embedding_functions

# ==== CONFIGURAÇÃO DE CAMINHOS ABSOLUTOS ====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")

# Nova pasta para guardar os vetores do ChromaDB
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
os.makedirs(CHROMA_DIR, exist_ok=True)
# ============================================

def gerar_embeddings():
    print("A inicializar o modelo de embeddings (isto pode demorar na primeira vez para fazer o download)...")
    
    # O modelo all-MiniLM-L6-v2 é excelente: muito rápido, leve e compreende bem a semântica do inglês
    emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    
    print(f"A ligar ao ChromaDB em: {CHROMA_DIR}")
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    
    # Cria (ou obtém) uma "collection" que é equivalente a uma tabela no SQLite
    collection = chroma_client.get_or_create_collection(
        name="oracle_cards",
        embedding_function=emb_fn,
        metadata={"description": "Vetorização das regras do Magic"}
    )
    
    print("A extrair cartas da base de dados local (SQLite)...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Vamos ignorar terrenos básicos puros, pois não acrescentam valor semântico em RAG
    cursor.execute("""
        SELECT id, name, type_line, oracle_text, color_identity 
        FROM cards 
        WHERE type_line NOT LIKE '%Basic Land%'
    """)
    cards = cursor.fetchall()
    conn.close()
    
    print(f"Encontradas {len(cards)} cartas para vetorizar.")
    print("A processar em lotes (isto vai exigir um pouco do seu CPU)...")
    
    batch_size = 1000 # O ChromaDB prefere lotes para não sobrecarregar a memória
    
    for i in range(0, len(cards), batch_size):
        batch = cards[i : i + batch_size]
        
        ids = []
        documents = []
        metadatas = []
        
        for card in batch:
            card_id, name, type_line, oracle_text, color_identity = card
            
            # Limpeza rápida
            if not oracle_text:
                oracle_text = "Vanilla creature / No abilities."
                
            # O SEGREDO DO RAG: Criar um documento rico em contexto para a IA ler!
            # Juntamos o nome, o tipo e o texto da carta numa única string lógica.
            document_text = f"{name} - {type_line}. Effect: {oracle_text}"
            
            ids.append(card_id)
            documents.append(document_text)
            
            # Guardamos a identidade de cor no metadata para permitir filtros híbridos no futuro
            metadatas.append({
                "name": name,
                "color_identity": color_identity
            })
            
        # Inserir no ChromaDB (gera os vetores automaticamente!)
        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas
        )
        
        print(f"Lote concluído: {min(i + batch_size, len(cards))} / {len(cards)} cartas vetorizadas.")
        
    print("\n✅ Vetorização concluída com sucesso! O ChromaDB está pronto.")

if __name__ == "__main__":
    gerar_embeddings()