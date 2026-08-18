## 🗺️ Roadmap de Desenvolvimento

O desenvolvimento deste projeto está estruturado de forma incremental. Começamos estabelecendo a base de dados determinística e avançamos até a orquestração autônoma dos agentes e análises topológicas de sinergia.

### 📍 Épico 1: Fundação de Dados e Inventário (Data Ingestion)
O objetivo desta fase é criar a infraestrutura local para ler as cartas e as regras do jogo.
- [x] **Integração com Scryfall:** Script para baixar e processar o *Bulk Data* (JSON) do Scryfall, extraindo `Oracle Text`, `Mana Value`, `Color Identity` e `Legalities`.
- [x] **Parser de Inventário:** Criar o módulo que lê a coleção pessoal (arquivos `.csv` do ManaBox/Moxfield).
- [x] **Banco de Dados Relacional:** Implementar SQLite (ou DuckDB) para rastrear o estado da coleção (quais cartas estão livres, quais estão alocadas em decks).
- [x] **Validador de Regras Base:** Funções determinísticas em Python para checar identidade de cor de comandantes e restrição de Singleton.

### 📍 Épico 2: Pipeline RAG e Espaço Vetorial (Embeddings)
Aqui, o sistema ganha a capacidade de entender as cartas semanticamente, indo além da busca por palavras-chave.
- [x] **Geração de Embeddings:** Utilizar `sentence-transformers` (HuggingFace) para converter o *Oracle Text* das cartas em vetores densos.
- [x] **Setup do ChromaDB/FAISS:** Armazenar os embeddings das cartas do Scryfall e criar partições específicas para o inventário local.
- [x] **Busca Semântica (Retrieval):** Implementar a função que recebe uma query (ex: "proteger comandante de remoção global") e retorna as N cartas mais relevantes usando similaridade de cosseno.
- [x] **Filtragem Híbrida:** Combinar a busca vetorial (RAG) com filtros estritos do banco SQL (ex: *retornar similares, mas apenas se Color Identity == Izzet e Inventário > 0*).

### 📍 Épico 3: Orquestração Multi-Agente (LangGraph / CrewAI)
A inteligência principal do sistema. Aqui os agentes ganham "ferramentas" para interagir com a base construída nos épicos anteriores.
- [x] **Configuração do LLM:** Integrar a API do Gemini (ou modelo local) como motor de raciocínio.
- [x] **Desenvolvimento das *Tools*:** Encapsular as funções do Épico 1 e 2 no formato de ferramentas do LangChain (`@tool`).
- [ ] **Agente Gestor de Inventário:** Implementar o agente responsável por checar disponibilidade e "mover" cartas entre decks.
- [x] **Agente Arquiteto (RAG):** Implementar o agente focado em descobrir sinergias e consultar as regras no vector DB.
- [ ] **Agente Supervisor:** Implementar o orquestrador que avalia a curva de mana, recebe os inputs dos outros agentes e formata a lista final com 100 cartas.
- [ ] **Fluxo de Decisão (Graph):** Desenhar e rodar o grafo de execução conectando os três agentes de forma cíclica ou hierárquica.

### 📍 Épico 4: Interface e Usabilidade (Deploy Local)
Facilitar o uso do sistema para não precisar rodar tudo via terminal durante a montagem física dos decks.
- [ ] **CLI Interativa ou Streamlit:** Criar uma interface básica em Python para interagir com o chat dos agentes.
- [ ] **Visualização de Curva de Mana e Stats:** Gerar gráficos simples (`matplotlib` ou nativos do Streamlit) para o *output* do deck.
- [ ] **Exportação de Decklists:** Função para gerar um arquivo final formatado pronto para importar de volta no Moxfield.

### 🔬 Épico 5: Funcionalidades Avançadas (Pesquisa e Otimização)
*Stretch goals* focados em otimizações matemáticas mais densas do deckbuilding.
- [ ] **Análise Topológica de Sinergias (TDA):** Aplicar métricas topológicas sobre o espaço de embeddings para identificar "ilhas" isoladas no deck (cartas que não têm sinergia com o resto) ou redundâncias.
- [ ] **Algoritmo de *Cuts* (Cortes de Deck):** Otimizar o agente para, dada uma lista de 110 cartas, calcular matematicamente os 10 piores *slots* considerando redundância vetorial e peso na curva de mana.


---

## 🛠️ Guia de Utilização e Arquitetura do Projeto

O **ManaGraph** é um sistema inteligente de *deckbuilding* e busca semântica para Magic: The Gathering, estruturado com uma arquitetura modular orientada a dados (RAG) e orquestração multi-agente.

### 📂 Estrutura de Diretórios

*   `data/`: Armazena os bancos locais (`managraph.db` para o SQLite com inventário e regras estruturadas, e `chroma_db/` para o banco vetorial).
*   `notebooks/`: Contém cadernos interativos de análise exploratória de dados (ex: redução de dimensionalidade com UMAP e Plotly).
*   `src/`: Contém o código-fonte principal do sistema:
    *   `scryfall_download.py`: Importação e parsing dos dados oficiais do Scryfall.
    *   `import_inventory.py`: Gestão da sua coleção física de cartas.
    *   `embeddings.py`: Padrão *Strategy* para provedores de vetores (agnóstico a modelos).
    *   `vectorize_cards.py`: Processamento e indexação em lotes para o ChromaDB.
    *   `hybrid_search.py`: Motor de busca combinando similaridade vetorial e filtros relacionais (SQLite).
    *   `llm_factory.py`: Fábrica modular para troca de provedores de LLM via variáveis de ambiente.
    *   `tools.py`: Encapsulamento das buscas em ferramentas (`@tool`) para agentes de IA.
    *   `architect_agent.py`: O Agente Arquiteto orquestrado via LangGraph.
    *   `main_agent.py`: Script de execução e testes dos agentes.

---

### 🚀 Como Executar o Projeto

1. **Configuração do Ambiente:**
   Certifique-se de preencher o arquivo `.env` na raiz do projeto com a sua chave de API e provedor desejado (ex: Google Gemini):
   ```env
   LLM_PROVIDER=google
   LLM_MODEL=gemini-2.5-flash
   GOOGLE_API_KEY=sua_chave_aqui
   ```

2. **Instalação de Dependências:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Geração de Base Vetorial**
Caso precise reindexar as cartas do Magic a partir do banco relacional:
   ```bash
   python src/vectorize_cards.py
   ```

4. **Executando o Agente Arquiteto:**
Para testar as consultas inteligentes de sinergia e inventário:
    ```bash
    python src/main_agent.py   
    ```