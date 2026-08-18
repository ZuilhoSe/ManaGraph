import sqlite3
import json
import os
import chromadb
from chromadb.utils import embedding_functions
from embeddings import MiniLMStrategy

# ==== CONFIGURAÇÃO DE CAMINHOS ABSOLUTOS ====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_NAME = os.path.join(DATA_DIR, "managraph.db")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
# ============================================

class RAGSearcher:
    def __init__(self):
        print("Carregando o modelo de linguagem e conectando aos bancos...")
        # Usa exatamente o mesmo modelo que usamos para vetorizar
        self.emb_fn = MiniLMStrategy().get_function()        
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
        self.chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
        
        # Puxamos a coleção que você acabou de criar
        self.collection = self.chroma_client.get_collection(
            name="oracle_cards", 
            embedding_function=self.emb_fn
        )
        self.conn = sqlite3.connect(DB_NAME)
        self.cursor = self.conn.cursor()

    def buscar_cartas(self, query, cores_permitidas, apenas_inventario=False, limite=5):
        print(f"\nBuscando por: '{query}'")
        print(f"Filtros -> Cores: {cores_permitidas} | Apenas na Coleção: {apenas_inventario}")
        
        # 1. BUSCA VETORIAL: Trazemos 50 resultados para dar margem aos filtros
        resultados = self.collection.query(
            query_texts=[query],
            n_results=50 
        )
        
        cartas_encontradas = []
        
        ids = resultados["ids"][0]
        documentos = resultados["documents"][0]
        metadados = resultados["metadatas"][0]
        # Distância: Quão "longe" o texto está da sua pergunta. Quanto menor, melhor.
        distancias = resultados["distances"][0] 

        cores_permitidas_set = set(cores_permitidas)

        # 2. ABRIR CONEXÃO LOCAL (Thread-safe para o LangGraph)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            # 3. FILTRAGEM SQL E REGRAS:
            for i in range(len(ids)):
                nome = metadados[i]["name"]
                
                # Filtro A: Identidade de Cor
                cor_carta_str = metadados[i].get("color_identity", "[]")
                cor_carta = set(json.loads(cor_carta_str))
                
                if not cor_carta.issubset(cores_permitidas_set):
                    continue # Corta a carta se não servir pro deck
                    
                # Filtro B: Você tem a carta?
                qtd_total = 0
                onde_esta = {}
                
                if apenas_inventario:
                    cursor.execute("SELECT total_quantity, allocations FROM inventory WHERE card_name = ?", (nome,))
                    row = cursor.fetchone()
                    
                    if not row or row[0] <= 0:
                        continue # Corta a carta se você não tem nenhuma
                        
                    qtd_total = row[0]
                    onde_esta = json.loads(row[1])
                
                # Passou no pente fino! Adiciona aos resultados:
                cartas_encontradas.append({
                    "nome": nome,
                    "texto": documentos[i],
                    "distancia": distancias[i],
                    "quantidade": qtd_total,
                    "alocacao": onde_esta
                })
                
                # Para a busca quando acharmos o número de cartas que você pediu
                if len(cartas_encontradas) == limite:
                    break
        finally:
            # Garante que a conexão do SQLite será sempre fechada, mesmo se ocorrer um erro
            conn.close()
            
        return cartas_encontradas

    def fechar(self):
        self.conn.close()

if __name__ == "__main__":
    searcher = RAGSearcher()
    
    minha_pergunta = "deal damage to all creatures" # Ex: "causar dano em todas as criaturas"
    identidade_deck = ["R", "U"] # Ex: Deck Izzet do Comandante
    
    resultados = searcher.buscar_cartas(
        query=minha_pergunta,
        cores_permitidas=identidade_deck,
        apenas_inventario=True, # Mude para True para ver só o que você tem de verdade!
        limite=1000
    )
    
    print("\n--- RESULTADOS DA BUSCA ---")
    if not resultados:
        print("Nenhuma carta encontrada que atenda a todos os filtros.")
    else:
        for i, carta in enumerate(resultados):
            print(f"\n{i+1}. {carta['nome']} (Score de dist.: {carta['distancia']:.3f})")
            print(f"   Efeito: {carta['texto']}")
            if carta['quantidade'] > 0:
                print(f"   Você possui: {carta['quantidade']} cópias -> {carta['alocacao']}")
            else:
                print("   Status: Não possui na coleção.")
            
    searcher.fechar()