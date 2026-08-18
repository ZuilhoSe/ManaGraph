import json
import sqlite3
import urllib.request
import urllib.error
import gzip
import os


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_DIR = os.path.dirname(SCRIPT_DIR)

DB_NAME = os.path.join(BASE_DIR, "managraph.db")

SCRYFALL_BULK_ALL_URL = "https://api.scryfall.com/bulk-data"

def download_and_process_scryfall():
    print("Consultando a API do Scryfall para localizar o catálogo de dados...")
    
    headers = {
        'User-Agent': 'ManaGraph-Agent/1.0',
        'Accept': 'application/json'
    }
    
    req_info = urllib.request.Request(SCRYFALL_BULK_ALL_URL, headers=headers)
    
    try:
        with urllib.request.urlopen(req_info) as response:
            bulk_list = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Erro ao acessar a API: {e.code} - {e.reason}")
        return

    download_uri = None
    for item in bulk_list.get("data", []):
        if item.get("type") == "oracle_cards":
            download_uri = item.get("jsonl_download_uri")
            break
            
    if not download_uri:
        print("Erro: Não foi possível obter a URI de download do formato JSONL.")
        return

    print(f"Link encontrado! Baixando dados comprimidos em JSONL...")
    print(f"URL: {download_uri}")
    print("(Isso consome cerca de ~24MB de download e processa os cards via stream)")
    
    req_data = urllib.request.Request(download_uri, headers=headers)
    
    try:
        # Faz o download e abre o stream diretamente da memória compactada usando gzip
        with urllib.request.urlopen(req_data) as response:
            with gzip.open(response, 'rt', encoding='utf-8') as f:
                
                print("Conectando ao banco de dados local...")
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cards (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        mana_cost TEXT,
                        cmc REAL,
                        oracle_text TEXT,
                        color_identity TEXT,
                        type_line TEXT,
                        legalities TEXT
                    )
                """)

                batch_data = []
                count = 0
                
                for line in f:
                    if not line.strip():
                        continue
                    
                    card = json.loads(line)
                    
                    batch_data.append((
                        card.get("id"),
                        card.get("name"),
                        card.get("mana_cost", ""),
                        card.get("cmc", 0.0),
                        card.get("oracle_text", ""),
                        json.dumps(card.get("color_identity", [])),
                        card.get("type_line", ""),
                        json.dumps(card.get("legalities", {}))
                    ))
                    
                    count += 1
                    
                    # Salva em lotes de 2000 em 2000 cartas para melhor performance no SQLite
                    if len(batch_data) >= 2000:
                        cursor.executemany("""
                            INSERT OR REPLACE INTO cards 
                            (id, name, mana_cost, cmc, oracle_text, color_identity, type_line, legalities)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, batch_data)
                        batch_data = []
                        print(f"{count} cartas processadas...")

                if batch_data:
                    cursor.executemany("""
                        INSERT OR REPLACE INTO cards 
                        (id, name, mana_cost, cmc, oracle_text, color_identity, type_line, legalities)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, batch_data)

                conn.commit()
                conn.close()
                print(f"\nSucesso! {count} cartas inseridas/atualizadas.")
                print(f"Banco de dados '{DB_NAME}' populado com sucesso!")

    except urllib.error.HTTPError as e:
        print(f"Erro ao baixar o arquivo: {e.code} - {e.reason}")
    except Exception as e:
        print(f"Ocorreu um erro inesperado no processamento: {e}")

if __name__ == "__main__":
    download_and_process_scryfall()