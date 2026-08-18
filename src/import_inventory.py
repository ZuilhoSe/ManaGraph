import sqlite3
import json
import re
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_DIR = os.path.dirname(SCRIPT_DIR)

DB_NAME = os.path.join(BASE_DIR, "managraph.db")

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            card_name TEXT PRIMARY KEY,
            total_quantity INTEGER,
            allocations TEXT
        )
    """)
    conn.commit()
    return conn

def importar_lista(caminho_ficheiro, location_name):
    conn = init_db()
    cursor = conn.cursor()

    padrao = re.compile(r"^(\d+)x?\s+(.+)$")
    cards_to_add = {} 
    
    try:
        with open(caminho_ficheiro, 'r', encoding='utf-8') as f:
            for linha in f:
                linha = linha.strip()
                if not linha:
                    continue
                    
                match = padrao.match(linha)
                if match:
                    qtd = int(match.group(1))
                    nome = match.group(2).strip()
                    cards_to_add[nome] = cards_to_add.get(nome, 0) + qtd
    except FileNotFoundError:
        print(f"Erro: O arquivo '{caminho_ficheiro}' não foi encontrado.")
        return

    print(f"A processar {len(cards_to_add)} cartas diferentes para a localização '{location_name}'...")

    for nome, nova_qtd in cards_to_add.items():
        cursor.execute("SELECT allocations FROM inventory WHERE card_name = ?", (nome,))
        row = cursor.fetchone()
        
        if row:
            allocations = json.loads(row[0])
            allocations[location_name] = allocations.get(location_name, 0) + nova_qtd
            total_quantity = sum(allocations.values())
            
            cursor.execute("""
                UPDATE inventory 
                SET total_quantity = ?, allocations = ?
                WHERE card_name = ?
            """, (total_quantity, json.dumps(allocations), nome))
        else:
            allocations = {location_name: nova_qtd}
            total_quantity = nova_qtd
            
            cursor.execute("""
                INSERT INTO inventory (card_name, total_quantity, allocations)
                VALUES (?, ?, ?)
            """, (nome, total_quantity, json.dumps(allocations)))

    conn.commit()
    conn.close()
    print(f"Sucesso! Inventário atualizado com as cartas de '{location_name}'.\n")


if __name__ == "__main__":
    caminho_pool = os.path.join(BASE_DIR, "data", "teste_pool.txt")
    caminho_deck = os.path.join(BASE_DIR, "data", "teste_deck.txt")
    
    importar_lista(caminho_pool, "pool_livre")
    importar_lista(caminho_deck, "deck_krenko")