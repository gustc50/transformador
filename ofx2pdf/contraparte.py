"""Identificação de categoria, contraparte (nome + CPF/CNPJ), pagador e recebedor.

O padrão OFX não tem campos próprios para "pagador" e "recebedor": cada
banco coloca essas informações dentro de NAME (histórico) e MEMO
(complemento). Exemplos reais de formato:

Banco do Brasil
    NAME: "Pix - Recebido"           MEMO: "05/01 09:31 12345678909 FULANO DE TAL"
    MEMO: "Pix - Enviado - 03/01 10:22 Fulano de Tal"
    MEMO: "Transferido de 1234 56789-0 FULANO DE TAL"
Sicoob
    MEMO: "PIX RECEB.OUTRA IF     Recebimento Pix FULANO DE TAL"
    MEMO: "PIX EMIT.OUTRA IF | Pagamento Pix | EMPRESA X LTDA | 12.345.678/0001-90"
    MEMO: "DÉB.TÍT.COMPE EFETIVADO"

A estratégia é: dividir o texto em segmentos, descartar palavras que
descrevem o tipo de operação (PIX, RECEB, TED, DEB...), datas, horas,
agência/conta e extrair o que sobra como nome da contraparte. CPF/CNPJ
(formatados, mascarados ou só números com dígito verificador válido)
são extraídos à parte.
"""

from __future__ import annotations

import re
import unicodedata

from .models import Transacao

NAO_IDENTIFICADO = "Não identificado no OFX"


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento.upper()


# --------------------------------------------------------------------------
# Linhas de saldo (não são movimentação)
# --------------------------------------------------------------------------

_SALDO = re.compile(
    r"^\s*(S\s?A\s?L\s?D\s?O|SALDO\s+(ANTERIOR|DO\s+DIA|FINAL|ATUAL|INICIAL|EM\s+C/?C|TOTAL|DISPONIVEL))"
    r"[\s\-:.0-9/]*$"
)


def eh_linha_de_saldo(t: Transacao) -> bool:
    for texto in (t.historico, t.complemento):
        if texto and _SALDO.match(normalizar(texto)):
            outro = t.complemento if texto is t.historico else t.historico
            if not outro or _SALDO.match(normalizar(outro)):
                return True
    return False


# --------------------------------------------------------------------------
# Categorias
# --------------------------------------------------------------------------

_CATEGORIAS = [
    ("Estorno", r"\bESTORN|\bDEVOL"),
    ("PIX", r"\bPIX\b"),
    ("TED", r"\bTED\b|\bSTR\b"),
    ("DOC", r"\bDOC\b"),
    ("Tributo", r"\bDARF\b|\bGPS\b|\bDAS\b|\bGRU\b|\bFGTS\b|\bGARE\b|\bDAE\b|\bIPVA\b|\bIPTU\b|\bTRIBUT|\bIMPOSTO|\bSIMPLES\s+NACIONAL"),
    ("IOF", r"\bIOF\b"),
    ("Tarifa", r"\bTARIF|\bTAR\b|\bCESTA\b|\bPACOTE\s+(DE\s+)?SERV|\bANUIDADE|\bMANUT"),
    ("Juros/Encargos", r"\bJUROS\b|\bENCARG|\bMORA\b|\bMULTA\b|\bCHEQUE\s+ESPECIAL|\bLIMITE\b"),
    ("Cobrança", r"\bLIQUIDACAO\s+(DE\s+)?COBRANCA|\bCRED\.?\s*COBRANCA|\bCOBRANCA\b"),
    ("Boleto/Título", r"\bBOLETO|\bTIT\b|\bTITULO|\bCOMPE\b|\bCOD(IGO)?\.?\s*BARRAS"),
    ("Convênio/Débito automático", r"\bCONVENIO|\bDEB(ITO)?\.?\s*AUT|\bCONV\b|\bCONTA\s+DE\s+(LUZ|AGUA|TELEFONE|GAS)|\bPAGTO\s+CONTA"),
    ("Salário/Folha", r"\bSALARIO|\bFOLHA\b|\bPROVENTO|\bVENCIMENTO"),
    ("Rendimento", r"\bRENDIMENTO|\bREND\b|\bREMUNERACAO"),
    ("Resgate", r"\bRESG|\bRESGATE"),
    ("Aplicação", r"\bAPLIC|\bINVEST|\bPOUPANCA|\bRDB\b|\bCDB\b|\bLCI\b|\bLCA\b"),
    ("Empréstimo/Financiamento", r"\bEMPREST|\bFINANC|\bCREDITO\s+PESSOAL|\bCONSIGNADO|\bPARCELA"),
    ("Compra com cartão", r"\bCOMPRA|\bCARTAO|\bDEBITO\s+VISA|\bMAESTRO|\bELO\b|\bMASTERCARD|\bVISA\b|\bVISA\s+ELECTRON"),
    ("Saque", r"\bSAQUE"),
    ("Depósito", r"\bDEPOSITO|\bDEP\b"),
    ("Cheque", r"\bCHEQUE|\bCH\b|\bCOMPENSADO"),
    ("Transferência", r"\bTRANSF|\bTRANSFERENCIA|\bTRANSFERIDO|\bTEV\b"),
    ("Recebimento de vendas", r"\bCIELO|\bREDE\b|\bSTONE\b|\bGETNET|\bPAGSEGURO|\bSUMUP|\bSIPAG|\bVENDAS?\b"),
]
_CATEGORIAS_RE = [(nome, re.compile(p)) for nome, p in _CATEGORIAS]

# Categorias em que a contraparte é a própria instituição financeira
_CONTRAPARTE_BANCO = {"Tarifa", "IOF", "Juros/Encargos", "Rendimento", "Empréstimo/Financiamento"}

# Contrapartes padrão quando o OFX não traz o nome
_CONTRAPARTE_PADRAO = [
    (re.compile(r"\bDARF\b"), "Receita Federal (DARF)"),
    (re.compile(r"\bGPS\b|\bINSS\b"), "INSS (GPS)"),
    (re.compile(r"\bDAS\b|\bSIMPLES\s+NACIONAL"), "Simples Nacional (DAS)"),
    (re.compile(r"\bFGTS\b"), "FGTS (Caixa Econômica Federal)"),
    (re.compile(r"\bGRU\b"), "União (GRU)"),
    (re.compile(r"\bSAQUE"), "Saque em espécie"),
    (re.compile(r"\bDEPOSITO\b.*\b(DINHEIRO|ESPECIE)|\bDEP\.?\s*DINHEIRO"), "Depósito em espécie"),
]


def categorizar(texto_norm: str, tipo: str) -> str:
    for nome, padrao in _CATEGORIAS_RE:
        if padrao.search(texto_norm):
            return nome
    mapa_tipo = {
        "FEE": "Tarifa", "SRVCHG": "Tarifa", "INT": "Juros/Encargos", "DIV": "Rendimento",
        "ATM": "Saque", "CASH": "Saque", "POS": "Compra com cartão", "CHECK": "Cheque",
        "XFER": "Transferência", "PAYMENT": "Pagamento", "DIRECTDEP": "Depósito",
        "DEP": "Depósito", "DIRECTDEBIT": "Convênio/Débito automático",
        "REPEATPMT": "Convênio/Débito automático",
    }
    return mapa_tipo.get(tipo, "Outros")


# --------------------------------------------------------------------------
# CPF / CNPJ
# --------------------------------------------------------------------------

_CNPJ_FMT = re.compile(r"(?<![\d*])[\d*]{2}\.[\d*]{3}\.[\d*]{3}/[\d*]{4}-[\d*]{2}(?![\d*])")
_CPF_FMT = re.compile(r"(?<![\d*/])[\d*]{3}\.[\d*]{3}\.[\d*]{3}-[\d*]{2}(?![\d*])")
_NUM_LONGO = re.compile(r"(?<![\d.\-/])(\d{14}|\d{11})(?![\d.\-/])")


def _cpf_valido(cpf: str) -> bool:
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(cpf[i]) * (n + 1 - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != int(cpf[n]):
            return False
    return True


def _cnpj_valido(cnpj: str) -> bool:
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        soma = sum(int(cnpj[i]) * pesos[i] for i in range(pos))
        dv = 0 if soma % 11 < 2 else 11 - soma % 11
        if dv != int(cnpj[pos]):
            return False
    return True


def formatar_cpf(d: str) -> str:
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def formatar_cnpj(d: str) -> str:
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def extrair_documento(texto: str) -> tuple[str, str]:
    """Retorna (documento formatado, texto sem o documento)."""
    for padrao, rotulo in ((_CNPJ_FMT, "CNPJ"), (_CPF_FMT, "CPF")):
        m = padrao.search(texto)
        if m and re.search(r"\d", m.group(0)):
            return f"{rotulo} {m.group(0)}", texto[: m.start()] + " " + texto[m.end():]
    for m in _NUM_LONGO.finditer(texto):
        num = m.group(1)
        if len(num) == 14 and _cnpj_valido(num):
            return f"CNPJ {formatar_cnpj(num)}", texto[: m.start()] + " " + texto[m.end():]
        if len(num) == 11 and _cpf_valido(num):
            return f"CPF {formatar_cpf(num)}", texto[: m.start()] + " " + texto[m.end():]
    return "", texto


# --------------------------------------------------------------------------
# Nome da contraparte
# --------------------------------------------------------------------------

# Palavras que descrevem a operação (comparadas sem acento, em maiúsculas, sem pontuação)
_PALAVRAS_OPERACAO = set("""
PIX RECEB RECEBIDO RECEBIDA RECEBIMENTO RECEBTO RECEB ENVIADO ENVIADA ENVIO EMITIDO EMITIDA EMIT
TRANSF TRANSFERENCIA TRANSFERENCIAS TRANSFERIDO TRANSFERIDA TEV TED DOC STR
CRED CREDITO CRÉD DEB DEBITO DÉB C D
PAGTO PGTO PAGAMENTO PAG PAGO PAGAMENTOS
BOLETO BOLETOS TIT TITULO TITULOS COMPE EFETIVADO EFETIVADA LIQUIDACAO LIQ COBRANCA COB
OUTRA OUTRO MESMA MESMO IF IFS INST INSTITUICAO BANCO BCO AGENCIA AG CONTA CONTAS CC CORRENTE C/C CTA
ONLINE INTERNET APP MOBILE CELULAR AUTOATENDIMENTO CAIXA ELETRONICO TAA BB SICOOB
DE DA DO DAS DOS PARA P/ EM COM POR VIA REF REFERENTE A AO E
QR QRCODE CODE CHAVE AGENDADO AGENDAMENTO PROGRAMADO DEVOLUCAO DEVOLVIDO ESTORNO
CONVENIO CONV AUT AUTOMATICO SERVICOS SERVICO PACOTE TARIFA TARIFAS TAR CESTA MENSAL
COMPRA COMPRAS CARTAO DEBITO VISA ELECTRON MASTERCARD MAESTRO ELO NACIONAL INTERNACIONAL
SAQUE DEPOSITO DEP DINHEIRO ESPECIE CHEQUE CH COMPENSADO
APLICACAO APLIC RESGATE RESG RENDIMENTO REND POUPANCA INVEST INVESTIMENTO AUTOMATICA AUTOMATICO
JUROS IOF ENCARGOS MORA MULTA LIMITE ESPECIAL
SALARIO FOLHA PROVENTOS
LUZ AGUA TELEFONE GAS ENERGIA
DARF GPS DAS GRU GARE DAE FGTS IMPOSTO IMPOSTOS TRIBUTO TRIBUTOS
ENTRE PROPRIA PROPRIAS PROPRIO PROPRIOS TITULARIDADE TITULAR
OPERACAO OPER LANCAMENTO LANC MOVIMENTACAO DOCUMENTO DOCTO NR NUM NUMERO Nº NO N
""".split())

_DATA_HORA = re.compile(r"^\d{1,2}/\d{1,2}(/\d{2,4})?$|^\d{1,2}:\d{2}(:\d{2})?$|^\d{1,2}H\d{2}$")
_SEPARADORES = re.compile(r"\s+[-–—|;>]\s+|\s*\|\s*|\s{2,}|\s*;\s*")


def _limpar_token(tok: str) -> str:
    return normalizar(tok).strip(".,:;()[]{}-–*'\"")


def _nome_do_segmento(segmento: str) -> str:
    tokens = segmento.split()
    # remove datas, horas, números e marcadores de conta em qualquer posição
    uteis = []
    for tok in tokens:
        limpo = _limpar_token(tok)
        if not limpo:
            continue
        if _DATA_HORA.match(limpo):
            continue
        if not re.search(r"[A-Z]", limpo):
            continue  # só números / pontuação (agência, conta, documento)
        uteis.append(tok)
    # descarta palavras de operação no início (ex.: "Recebimento Pix FULANO")
    while uteis:
        partes = [p for p in re.split(r"[.\-/]", _limpar_token(uteis[0])) if p]
        if partes and all(p in _PALAVRAS_OPERACAO for p in partes):
            uteis.pop(0)
        else:
            break
    # e no fim (ex.: "FULANO DE TAL - OUTRA IF")
    while uteis:
        partes = [p for p in re.split(r"[.\-/]", _limpar_token(uteis[-1])) if p]
        if partes and all(p in _PALAVRAS_OPERACAO for p in partes):
            uteis.pop()
        else:
            break
    nome = " ".join(uteis).strip(" -–|;:,")
    letras = re.sub(r"[^A-Za-zÀ-ÿ]", "", nome)
    if len(letras) < 3:
        return ""
    return nome


def extrair_contraparte(historico: str, complemento: str) -> tuple[str, str]:
    """Retorna (nome, documento) da contraparte a partir do NAME e MEMO."""
    documento = ""
    candidatos: list[str] = []
    # MEMO primeiro (é onde BB e Sicoob colocam o favorecido), depois NAME
    for texto in (complemento, historico):
        if not texto:
            continue
        if not documento:
            documento, texto = extrair_documento(texto)
        for seg in _SEPARADORES.split(texto.strip()):
            nome = _nome_do_segmento(seg)
            if nome:
                candidatos.append(nome)
    nome = candidatos[0] if candidatos else ""
    return nome, documento


# --------------------------------------------------------------------------
# Análise completa
# --------------------------------------------------------------------------

def analisar_transacao(t: Transacao, codigo_banco: str, nome_banco: str, titular: str) -> None:
    texto_norm = normalizar(f"{t.historico} {t.complemento}")
    t.categoria = categorizar(texto_norm, t.tipo)

    nome, doc = extrair_contraparte(t.historico, t.complemento)
    if t.extras.get("PAYEE"):
        # favorecido informado explicitamente no OFX (<PAYEE><NAME>) tem prioridade
        nome = t.extras["PAYEE"]
    if not nome and t.categoria in _CONTRAPARTE_BANCO:
        nome = nome_banco or "Instituição financeira"
    if not nome and t.categoria in ("Aplicação", "Resgate"):
        nome = f"Investimentos - {nome_banco}" if nome_banco else "Investimentos (mesmo titular)"
    if not nome:
        for padrao, rotulo in _CONTRAPARTE_PADRAO:
            if padrao.search(texto_norm):
                nome = rotulo
                break
    if not nome and t.conta_destino:
        nome = t.conta_destino

    t.contraparte = nome
    t.contraparte_doc = doc
    outra_parte = nome or NAO_IDENTIFICADO
    if doc:
        outra_parte = f"{outra_parte} ({doc})" if nome else doc

    if t.valor >= 0:
        t.pagador, t.recebedor = outra_parte, titular
    else:
        t.pagador, t.recebedor = titular, outra_parte
