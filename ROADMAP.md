# Roadmap

## Próxima versão: menu central por tipo de aparelho

Hoje o painel mostra todos os aparelhos juntos. Com tomadas e ar-condicionado chegando, ele passa
a abrir num **menu central com um card por tipo de aparelho**. Clicar num card abre a aba daquele
tipo, com os controles certos para ele.

### Como vai funcionar

1. **Tela inicial (menu central):** um card para cada tipo que tenha pelo menos um aparelho
   cadastrado. Um tipo sem aparelhos não aparece. Ao cadastrar a primeira tomada, surge o card
   Tomadas.
2. **Clique no card:** abre a aba do tipo.
   - **Luzes** abre exatamente o painel que existe hoje: blocos das lâmpadas, cor, brilho, efeitos
     e velocidade.
   - **Ar-condicionado** abre os controles do ar: temperatura, modo, ventilação, oscilar.
   - **Tomadas** abre os blocos de liga/desliga.
3. **Voltar:** a seta no topo da aba volta ao menu central.

```
┌ SmartDevices ──────────── ● 3 conectados ┐      ┌ ‹ Luzes ─────────── 2 ligadas ┐
│                                          │      │ [A  Ligada ›] [B  Ligada ›]   │
│  ┌──────────────┐  ┌──────────────┐      │      │ Controles · Todos             │
│  │ ▯ Luzes      │  │ ⏻ Tomadas    │      │ ───▶ │ Cor   ● ● ● ● ● ● ● ● ●       │
│  │ 2 de 2 ligadas│  │ 1 de 3 ligada│      │      │ Brilho ━━━━━━━○──── 60%       │
│  └──────────────┘  └──────────────┘      │      │ Efeito [Rainbow Cycle    ▾]   │
│  ┌──────────────┐                        │      │ Velocidade ━━━━○────── 30%    │
│  │ ❄ Ar         │                        │      └───────────────────────────────┘
│  │ 24 °C · Frio │                        │
│  └──────────────┘                        │
│ 🔍 Procurar dispositivos        ⚙  ⏻     │
└──────────────────────────────────────────┘
```

### Detalhes de comportamento

- **Resumo no card:** cada card mostra um resumo do tipo ("2 de 2 ligadas", "24 °C · Frio").
- **Desligar sem abrir:** um botão de liga/desliga no próprio card age sobre todos os aparelhos
  daquele tipo, no estilo das Configurações Rápidas do Windows 11.
- **Um tipo só:** com apenas um tipo cadastrado (como hoje, só luzes), o menu central é pulado e a
  aba abre direto. Ninguém precisa de um clique extra à toa.
- **Última aba lembrada:** o painel reabre na última aba usada.
- **Rodapé igual em todas as telas:** Procurar dispositivos, Configurações e Desligar tudo.
- **Configurações:** a lista de aparelhos passa a ser agrupada por tipo.

### Como se encaixa no código atual

A base já está pronta: cada driver declara `category` (`light`, `plug`, `climate`, `sensor`,
`other`) e o snapshot enviado ao painel já inclui esse campo.

| Parte | Mudança |
|---|---|
| `core/device.py` | `CATEGORIES` vira um registro com nome de exibição, ícone e ordem de cada tipo, para um tipo novo entrar sem mexer no painel |
| `ui/index.html` | Nova tela `home` (cards por categoria). A tela atual vira a aba de uma categoria: a mesma renderização genérica, filtrada pelos aparelhos daquele tipo. "Controlar todos" passa a valer dentro da aba |
| Resumo do card | Novo método opcional `category_summary(devices)` por categoria (ex.: clima mostra temperatura e modo); o padrão conta quantos estão ligados |
| `core/hub.py` | Sem mudança na lógica; `power_all` ganha filtro opcional por categoria |
| Terminal (`smart.py`) | O menu "Controlar" pergunta primeiro o tipo, depois o aparelho |

O princípio continua o mesmo: nada específico de um aparelho fica fixo na interface. Uma aba é
montada a partir de `actions()` dos aparelhos daquele tipo. Se um tipo precisar de um visual
próprio no futuro (ex.: um mostrador grande de temperatura para o ar), ele entra como uma dica
opcional declarada pelo driver, sem quebrar os outros.

## Depois

- **Ar-condicionado por infravermelho.** O PC não tem emissor IR. Caminhos avaliados:
  - **Broadlink RM4 mini** (recomendado): emissor IR com Wi-Fi, controlado pela rede local, sem
    nuvem (`python-broadlink`). Aprende os botões do controle original.
  - **ESP32 + LED infravermelho**: mais barato; a biblioteca IRremoteESP8266 conhece os protocolos
    de ar de várias marcas e manda o estado completo (temperatura, modo, ventilação).
  - **Ar com Wi-Fi de fábrica**: driver direto pela API do fabricante, sem hardware extra.

  Qualquer um deles entra como um transporte novo (`core/wifi.py` ou `core/serial.py`) mais um
  driver com `category = "climate"`.
- **Tomadas inteligentes** (Tuya/Smart Life via `tinytuya`, ou modelos Bluetooth).
- **Executável único** (`SmartDevices.exe` com PyInstaller), para instalar sem Python.
