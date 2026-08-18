import sqlite3
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(DATA_DIR, exist_ok=True)

DB_NAME = os.path.join(DATA_DIR, "managraph.db")

class CommanderValidator:
    def __init__(self):
        self.conn = sqlite3.connect(DB_NAME)
        self.cursor = self.conn.cursor()

    def get_card_info(self, card_name):
        """Busca as informações vitais de uma carta no banco Oracle."""
        self.cursor.execute("""
            SELECT color_identity, oracle_text, type_line 
            FROM cards 
            WHERE name = ?
        """, (card_name,))
        
        row = self.cursor.fetchone()
        if row:
            return {
                "color_identity": json.loads(row[0]), # Retorna lista ex: ['R', 'B']
                "oracle_text": row[1] if row[1] else "",
                "type_line": row[2] if row[2] else ""
            }
        return None

    def validate_deck(self, commander_name, deck_list):
        """
        Recebe o nome do comandante e um dicionário do deck.
        Ex: {"Sol Ring": 1, "Mountain": 10, "Relentless Rats": 4}
        Retorna um relatório de regras quebradas.
        """
        # 1. Identifica a cor do Comandante
        cmd_info = self.get_card_info(commander_name)
        if not cmd_info:
            return {"erro": f"Comandante '{commander_name}' não encontrado no banco Oracle."}
            
        cmd_identity = set(cmd_info["color_identity"])
        
        singleton_violations = []
        color_violations = []
        cards_not_found = []

        for card_name, qty in deck_list.items():
            card_info = self.get_card_info(card_name)
            
            if not card_info:
                cards_not_found.append(card_name)
                continue
                
            # --- VALIDAÇÃO 1: Identidade de Cor ---
            # A identidade da carta deve ser um subconjunto da identidade do comandante.
            # Cartas incolores (identidade vazia) sempre passam.
            card_identity = set(card_info["color_identity"])
            if not card_identity.issubset(cmd_identity):
                color_violations.append(
                    f"{card_name} (Cores: {list(card_identity)} | Permitido: {list(cmd_identity)})"
                )
                
            # --- VALIDAÇÃO 2: Regra de Singleton ---
            if qty > 1:
                # Exceção A: É um Terreno Básico?
                if "Basic Land" in card_info["type_line"] or "Basic Snow Land" in card_info["type_line"]:
                    continue
                    
                # Exceção B: A carta possui texto permitindo múltiplas cópias?
                # Cobre Relentless Rats, Nazgûl, Slime Against Humanity, etc.
                if "A deck can have any number of cards named" in card_info["oracle_text"]:
                    continue
                    
                # Se não caiu nas exceções, quebrou a regra
                singleton_violations.append(f"{card_name} ({qty} cópias)")
                
        # Gera o relatório final
        is_valid = len(singleton_violations) == 0 and len(color_violations) == 0
        
        return {
            "valido": is_valid,
            "comandante": commander_name,
            "identidade_comandante": list(cmd_identity),
            "erros_de_cor": color_violations,
            "erros_de_singleton": singleton_violations,
            "cartas_desconhecidas": cards_not_found
        }

    def fechar_conexao(self):
        self.conn.close()


if __name__ == "__main__":
    print("Iniciando validação de regras do Commander com dados do Banco...\n")
    
    validador = CommanderValidator()
    
    meu_comandante = "Krenko, Mob Boss"
    nome_do_deck_no_banco = "deck_krenko"  

    validador.cursor.execute("""
        SELECT card_name, total_quantity, allocations 
        FROM inventory
    """)
    
    deck_real = {}
    for row in validador.cursor.fetchall():
        card_name = row[0]
        allocations = json.loads(row[2])
        
        if nome_do_deck_no_banco in allocations:
            deck_real[card_name] = allocations[nome_do_deck_no_banco]
            
    if not deck_real:
        print(f"⚠️ Nenhum card encontrado no banco para a localização '{nome_do_deck_no_banco}'.")
        print("Certifique-se de rodar o 'import_inventory.py' primeiro para carregar o arquivo txt.")
    else:
        relatorio = validador.validate_deck(meu_comandante, deck_real)
        
        print(f"Comandante: {relatorio['comandante']} (Cores: {relatorio['identidade_comandante']})")
        print(f"Deck Válido? {'✅ SIM' if relatorio['valido'] else '❌ NÃO'}")
        
        if not relatorio['valido']:
            print("\n--- PROBLEMAS ENCONTRADOS ---")
            if relatorio['erros_de_cor']:
                print("🚨 Violações de Identidade de Cor:")
                for erro in relatorio['erros_de_cor']:
                    print(f"  - {erro}")
                    
            if relatorio['erros_de_singleton']:
                print("\n🚨 Violações da Regra Singleton (Máx 1):")
                for erro in relatorio['erros_de_singleton']:
                    print(f"  - {erro}")
                    
            if relatorio['cartas_desconhecidas']:
                print("\n⚠️ Cartas não encontradas no Oracle:")
                for carta in relatorio['cartas_desconhecidas']:
                    print(f"  - {carta}")

    validador.fechar_conexao()