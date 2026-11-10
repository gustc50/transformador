# Conversor OFX → PDF

Transforma extratos bancários no formato **OFX** em relatórios **PDF** organizados, com:

- dados da conta (banco, agência, conta, tipo, titular, período, data de geração);
- resumo com **saldo inicial**, **total de entradas**, **total de saídas**, **resultado** e **saldo final/disponível**;
- tabela de lançamentos com **data, histórico, complemento, categoria, pagador, recebedor, CPF/CNPJ, nº do documento, referência, ID (FITID), valor e saldo após cada lançamento**;
- **resumo por categoria** (PIX, TED, Boleto, Tarifa, Tributo, Cartão, Rendimento etc.);
- rodapé com “Página X de Y” e data de geração.

Funciona com **qualquer banco** que exporte OFX (versões 1.x SGML e 2.x XML, contas correntes, poupança e cartão de crédito),
com tratamento específico para os formatos do **Banco do Brasil** e do **Sicoob**.

## Como usar

### Opção 1 — Interface no navegador (mais fácil)

1. Instale o [Python 3.9 ou mais novo](https://www.python.org/downloads/) — no Windows, marque **“Add Python to PATH”**
   na primeira tela da instalação. (Se esquecer, o atalho abaixo avisa e abre a página de download.)
2. **Windows:** dê dois cliques em **`Abrir Conversor OFX.bat`**.
   **Linux/macOS:** rode `./iniciar.sh`.
   Na primeira vez ele cria um ambiente isolado na pasta `.venv` e instala os componentes (precisa de internet);
   nas próximas, abre direto.
3. O navegador abre em `http://127.0.0.1:5000`. Arraste um ou mais arquivos `.ofx`, informe (opcionalmente)
   o nome do titular e clique em **Gerar PDF**. Vários arquivos são baixados juntos em um `.zip`.
4. Mantenha a janela preta aberta enquanto usa; feche-a para encerrar. Se clicar no atalho com o conversor
   já aberto, ele apenas abre o navegador de novo.

Dica: para ter o atalho na área de trabalho, clique com o botão direito em `Abrir Conversor OFX.bat` →
**Enviar para** → **Área de trabalho (criar atalho)**.

### Opção 2 — Linha de comando

```bash
pip install -r requirements.txt
```

```bash
python -m ofx2pdf extrato.ofx
```

```bash
python -m ofx2pdf extrato.ofx -o relatorio.pdf --titular "Empresa Exemplo Ltda"
```

```bash
python -m ofx2pdf pasta_com_ofx/ -o pasta_de_saida/
```

### Opção 3 — Dentro de outro programa Python

```python
from ofx2pdf import ler_ofx, gerar_pdf

arquivo = ler_ofx("extrato.ofx", titular="Empresa Exemplo Ltda")
gerar_pdf(arquivo, "extrato.pdf")
```

## Como pagador e recebedor são identificados

O padrão OFX **não tem campos próprios** para pagador e recebedor: cada banco escreve essas informações dentro
do histórico (`NAME`) e do complemento (`MEMO`). O sistema:

1. extrai **CPF/CNPJ** (formatado, mascarado como `***.456.789-**` ou só números com dígito verificador válido);
2. remove datas, horas, agência/conta e as palavras que descrevem a operação (`PIX`, `RECEB.`, `OUTRA IF`, `TED`, `DÉB.`…);
3. o que sobra é o nome da outra parte (contraparte);
4. em **créditos**, a contraparte é o **pagador** e o titular é o **recebedor**; em **débitos**, o inverso;
5. tarifas, juros, IOF e rendimentos têm o próprio banco como contraparte; DARF, GPS, DAS, FGTS recebem o órgão correspondente;
6. se o banco não enviar a informação, aparece **“Não identificado no OFX”** — o texto original do banco é sempre mantido
   na coluna *Histórico / Complemento*, então nenhuma informação se perde.

O nome do titular normalmente não vem no OFX; informe-o com `--titular` (ou no campo da tela) para que apareça no PDF.

| Banco | Exemplo de lançamento no OFX | Resultado |
|---|---|---|
| Banco do Brasil | `NAME: Pix - Recebido` / `MEMO: 01/10 09:31 52998224725 Maria Aparecida da Silva` | Pagador: Maria Aparecida da Silva (CPF 529.982.247-25) |
| Banco do Brasil | `NAME: Transferência recebida` / `MEMO: 10/10 1234 56789-0 COMERCIAL ALFA LTDA` | Pagador: COMERCIAL ALFA LTDA |
| Sicoob | `MEMO: PIX RECEB.OUTRA IF  Recebimento Pix JOAO PEREIRA ***.456.789-**` | Pagador: JOAO PEREIRA (CPF ***.456.789-**) |
| Sicoob | `MEMO: PIX EMIT.OUTRA IF \| Pagamento Pix \| DISTRIBUIDORA GAMA LTDA \| 12.345.678/0001-95` | Recebedor: DISTRIBUIDORA GAMA LTDA (CNPJ …) |

Linhas que são apenas informativas de saldo (ex.: `Saldo Anterior`, `S A L D O`, comuns no Banco do Brasil) não são somadas como movimentação.

## Segurança

O conversor foi feito para rodar **somente no seu computador**:

- a interface escuta apenas em `127.0.0.1` — outros computadores da rede não conseguem acessá-la;
- requisições com endereço (cabeçalho *Host*) diferente de `localhost`/`127.0.0.1` são recusadas,
  o que impede que um site malicioso aberto no navegador use o conversor (*DNS rebinding*);
- os arquivos enviados são processados **em memória** e nunca gravados em disco nem enviados à internet;
- nomes de arquivo são saneados (sem `..\`, `/` ou caracteres especiais) antes de irem para o PDF/ZIP;
- textos vindos do OFX são escapados antes de entrar no PDF e na página (sem injeção de HTML/markup);
- arquivos malformados ou maliciosos (aninhamento excessivo, comentários sem fim, valores `NaN`/`Infinity`,
  campos gigantes) geram uma mensagem de erro em vez de travar ou derrubar o programa;
- limite de 50 MB por envio, cabeçalhos HTTP de segurança (CSP, `X-Frame-Options`, `nosniff`) e modo debug desligado.

Verificações feitas: análise estática com [bandit](https://bandit.readthedocs.io/) (nenhum problema) e
[pip-audit](https://pypi.org/project/pip-audit/) nas dependências (nenhuma vulnerabilidade conhecida). Para repetir:

```bash
pip install bandit pip-audit && bandit -r ofx2pdf && pip-audit -r requirements.txt
```

## Exemplos e testes

A pasta `exemplos/` tem arquivos OFX **fictícios** no formato do Banco do Brasil, do Sicoob e de um OFX 2.x (XML) genérico.

```bash
python -m ofx2pdf exemplos/ -o saida/
```

```bash
pip install pytest pypdf && python -m pytest
```

## Estrutura

```
ofx2pdf/
  parser.py       leitura do OFX (SGML/XML, codificações, datas, valores)
  contraparte.py  categorias, CPF/CNPJ, pagador e recebedor
  bancos.py       códigos e cores dos bancos
  pdf.py          montagem do relatório PDF (ReportLab)
  web.py          interface web local (Flask)
  __main__.py     linha de comando
```
