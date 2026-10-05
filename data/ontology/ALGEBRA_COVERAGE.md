# Cobertura da álgebra de operadores (Fase 1)

Gerado por `scripts/forge_algebra_coverage.py` · álgebra `1.1.0` (`data/ontology/operator_algebra_v1.yaml`) · cardsfolder `C:\Users\segun\Documents\forge\forge-gui\res\cardsfolder`.

Nível medido: **abstraível** — todo tipo de habilidade da carta cabe na álgebra.
Fidelidade (o operador prevê o Δ do Forge) é a Fase 3.

## Resultado

| | cartas | % |
|---|---:|---:|
| legais em Commander (com script) | 31699 | 100% |
| legais no catálogo sem script no Forge (fora do denominador) | 131 | 0.4% do catálogo |
| strict | 31063 | 98.0% |
| strict + approx (dado/moeda, escolha fixável, minimax do oponente) | 31699 | 100.0% |
| fora | 0 | 0.0% |
| cota pessimista (invólucros genéricos contados como fora) | | 91.1% |

A cota pessimista trata como fora os átomos cujo significado depende dos
parâmetros (`generic:` no YAML: Animate, ReplaceEffect, Moved, Play, MayPlay, ...).
Ela é o piso honesto até a Fase 2 ler esses parâmetros.

**Veredito:** ACEITE (≥ 80%).

Átomos distintos nas cartas legais: 794; mapeados: 794.

## Classes com valor diferido

Semântica definida na álgebra, mas o valor depende de fases posteriores.
Dentro da álgebra não quer dizer resolvido: estas cartas só são avaliadas
de verdade quando a fase indicada existir.

| classe | cartas | quem resolve o valor |
|---|---:|---|
| `reference` | 788 | Fase 5 — resolver contra o grafo do deck: valor = melhor alvo da classe copiada |
| `layer` | 715 | Fase 2 (ler o atributo alterado) + Fase 5 (aplicar na ordem do CR 613) |
| `face_down` | 298 | Fase 2 — corpo 2/2 + custo de virar como alt_cost do operador real |
| `turn` | 201 | Fase 5 — multiplicador do bloco de operadores no horizonte de V(D) |
| `terminal` | 80 | Decisão em aberto 8.1 (recurso de vitória) + Fase 5 |
| `adversarial` | 76 | Fase 8 — modelo de oponente; até lá, minimax (pior opção para você) |
| `stochastic` | 215 | Fase 2 — valor esperado sobre a distribuição do dado/moeda |
| `param` | 351 | Fase 7 — o otimizador fixa a escolha junto com o deck |

## Famílias fora da álgebra

Uma carta pode estar em mais de uma família.

| família | cartas | exemplos |
|---|---:|---|

## Átomos que mais quebram cartas sozinhos

Cartas que ficariam abstraíveis se só esse átomo entrasse na álgebra —
a ordem de ataque para subir a cobertura.

| átomo | classe | cartas |
|---|---|---:|

## Átomos não mapeados (revisar)

| átomo | cartas |
|---|---:|
