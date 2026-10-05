# Fidelidade dos operadores contra o Forge (Fase 3)

Gerado por `scripts/forge_oracle.py`. Cada caso: um estado montado no Forge,
a habilidade resolvida uma vez (só o efeito, sem pagar custo), e o Δ medido
dos recursos comparado com o Δ previsto apenas a partir dos operadores
compilados. Passa quando todo recurso previsto ou alterado está a ≤ 10%.

## Resultado

**Avaliação separada:** semente 11, excluídas as 1095 cartas da amostra de desenvolvimento (semente 3) usada para ajustar o preditor. Tipos de efeito pequenos já foram inteiros para o desenvolvimento, então esta amostra cobre os maiores.

| | |
|---|---:|
| cartas no escopo do preditor (1ª ordem, quantidades numéricas) | 2442 |
| amostradas | 375 |
| erro do próprio oráculo (estado/habilidade não montou) | 0 |
| julgadas | 375 |
| **previsão correta** | **370 (98.7%)** |

**Veredito:** ACEITE (critério: ≥ 85%).

## Por tipo de efeito

| API | pass | miss | erro do oráculo |
|---|---:|---:|---:|
| `ChangeZone.Battlefield>Exile` | 23 | 0 | 0 |
| `ChangeZone.Battlefield>Hand` | 21 | 0 | 0 |
| `ChangeZone.Battlefield>Library` | 2 | 0 | 0 |
| `DealDamage` | 40 | 0 | 0 |
| `Destroy` | 40 | 0 | 0 |
| `Discard` | 4 | 0 | 0 |
| `Draw` | 39 | 1 | 0 |
| `GainLife` | 40 | 0 | 0 |
| `Mana` | 40 | 0 | 0 |
| `Mill` | 5 | 0 | 0 |
| `Pump` | 37 | 3 | 0 |
| `Tap` | 40 | 0 | 0 |
| `Token` | 39 | 1 | 0 |

## Erros de previsão

| carta | API | recurso: previsto → medido |
|---|---|---|
| Sky Crier | `Draw` | p1.hand: 0 → 1; p1.library: 0 → -1 |
| Rites of Reaping | `Pump` | p0.power: 0 → 3; p0.toughness: 0 → 3 |
| Zelyon Sword | `Pump` | p0.power: 2 → 0 |
| Agony Warp | `Pump` | p0.power: -5 → -2 |
| The Circle of Loyalty | `Token` | p1.power: 2 → 3; p1.toughness: 2 → 3 |

## Erros do oráculo

| carta | API | detalhe |
|---|---|---|
