"""Identificação de categoria, contraparte (nome + CPF/CNPJ), pagador e recebedor.

O padrão OFX não tem campos próprios para "pagador" e "recebedor": cada
banco coloca essas informações dentro de NAME (histórico) e MEMO
(complemento). Exemplos de formato:

Banco do Brasil
    NAME: "Pix - Recebido"           MEMO: "05/01 09:31 12345678909 FULANO DE TAL"
    MEMO: "Pix - Enviado - 03/01 10:22 Fulano de Tal"
    MEMO: "Transferido de 1234 56789-0 FULANO DE TAL"
Sicoob
    MEMO: "PIX RECEB.OUTRA IF     Recebimento Pix FULANO DE TAL"
    MEMO: "PIX EMIT.OUTRA IF | Pagamento Pix | EMPRESA X LTDA | 12.345.678/0001-90"
    MEMO: "DÉB.TÍT.COMPE EFETIVADO"

Estratégia: dividir o texto em segmentos, descartar palavras que descrevem
o tipo de operação (PIX, RECEB, TED, DEB...), datas, horas, agência/conta,
e usar o que sobra como nome da contraparte. CPF/CNPJ (formatados,
mascarados ou só números com dígito verificador válido) são extraídos à parte.
"""

from __future__ import annotations

import re
import unicodedata

from .models import Transacao

NAO_IDENTIFICADO = "Não identificado no OFX"
TAMANHO_MAXIMO_NOME = 150


def normalizar(texto: str) -> str:
    """Maiúsculas e sem acentos."""
    decomposto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in decomposto if not unicodedata.combining(c)).upper()


# --------------------------------------------------------------------------
# Linhas de saldo (não são movimentação)
# --------------------------------------------------------------------------

_SALDO = re.compile(
    r"^\s*(S\s?A\s?L\s?D\s?O|SALDO\s+(ANTERIOR|DO\s+DIA|FINAL|ATUAL|INICIAL|EM\s+C/?C|TOTAL|DISPONIVEL))"
    r"[\s\-:.0-9/]*$"
)


def eh_linha_de_saldo(t: Transacao) -> bool:
    """True quando todos os textos preenchidos são apenas 'Saldo ...' (ex.: BB 'S A L D O')."""
    textos = [x for x in (t.historico, t.complemento) if x]
    return bool(textos) and all(_SALDO.match(normalizar(x)) for x in textos)


# --------------------------------------------------------------------------
# Categorias (a primeira que casar vence, por isso a ordem importa)
# --------------------------------------------------------------------------

_CATEGORIAS = [(nome, re.compile(padrao)) for nome, padrao in [
    ("Estorno", r"\bESTORN|\bDEVOL"),
    ("PIX", r"\bPIX\b"),
    ("TED", r"\bTED\b|\bSTR\b"),
    ("DOC", r"\bDOC\b"),
    ("Tributo", r"\b(DARF|GPS|DAS|GRU|FGTS|GARE|DAE|IPVA|IPTU)\b|\bTRIBUT|\bIMPOSTO|\bSIMPLES\s+NACIONAL"),
    ("IOF", r"\bIOF\b"),
    ("Tarifa", r"\bTARIF|\bTAR\b|\bCESTA\b|\bPACOTE\s+(DE\s+)?SERV|\bANUIDADE|\bMANUT"),
    ("Juros/Encargos", r"\b(JUROS|MORA|MULTA|LIMITE)\b|\bENCARG|\bCHEQUE\s+ESPECIAL"),
    ("Cobrança", r"\bCOBRANCA\b"),
    ("Boleto/Título", r"\bBOLETO|\bTIT\b|\bTITULO|\bCOMPE\b|\bCOD(IGO)?\.?\s*BARRAS"),
    ("Convênio/Débito automático", r"\bCONVENIO|\bCONV\b|\bDEB(ITO)?\.?\s*AUT|\bCONTA\s+DE\s+(LUZ|AGUA|TELEFONE|GAS)|\bPAGTO\s+CONTA"),
    ("Salário/Folha", r"\bSALARIO|\bFOLHA\b|\bPROVENTO|\bVENCIMENTO"),
    ("Rendimento", r"\bREND\b|\bRENDIMENTO|\bREMUNERACAO"),
    ("Resgate", r"\bRESG"),
    ("Aplicação", r"\bAPLIC|\bINVEST|\bPOUPANCA|\b(RDB|CDB|LCI|LCA)\b"),
    ("Empréstimo/Financiamento", r"\bEMPREST|\bFINANC|\bCREDITO\s+PESSOAL|\bCONSIGNADO|\bPARCELA"),
    ("Compra com cartão", r"\bCOMPRA|\bCARTAO|\b(VISA|MAESTRO|ELO|MASTERCARD)\b"),
    ("Saque", r"\bSAQUE"),
    ("Depósito", r"\bDEPOSITO|\bDEP\b"),
    ("Cheque", r"\bCHEQUE|\bCH\b|\bCOMPENSADO"),
    ("Transferência", r"\bTRANSF|\bTEV\b"),
    ("Recebimento de vendas", r"\b(CIELO|REDE|STONE|GETNET|PAGSEGURO|SUMUP|SIPAG|VENDAS?)\b"),
]]

# Categoria a partir do TRNTYPE, quando o texto não indica nada
_CATEGORIA_POR_TIPO = {
    "FEE": "Tarifa", "SRVCHG": "Tarifa", "INT": "Juros/Encargos", "DIV": "Rendimento",
    "ATM": "Saque", "CASH": "Saque", "POS": "Compra com cartão", "CHECK": "Cheque",
    "XFER": "Transferência", "PAYMENT": "Pagamento", "DIRECTDEP": "Depósito",
    "DEP": "Depósito", "DIRECTDEBIT": "Convênio/Débito automático",
    "REPEATPMT": "Convênio/Débito automático",
}

# Categorias em que a contraparte é a própria instituição financeira
_CONTRAPARTE_BANCO = {"Tarifa", "IOF", "Juros/Encargos", "Rendimento", "Empréstimo/Financiamento"}

# Contrapartes padrão quando o OFX não traz o nome
_CONTRAPARTE_PADRAO = [(re.compile(p), rotulo) for p, rotulo in [
    (r"\bDARF\b", "Receita Federal (DARF)"),
    (r"\bGPS\b|\bINSS\b", "INSS (GPS)"),
    (r"\bDAS\b|\bSIMPLES\s+NACIONAL", "Simples Nacional (DAS)"),
    (r"\bFGTS\b", "FGTS (Caixa Econômica Federal)"),
    (r"\bGRU\b", "União (GRU)"),
    (r"\bSAQUE", "Saque em espécie"),
    (r"\bDEPOSITO\b.*\b(DINHEIRO|ESPECIE)|\bDEP\.?\s*DINHEIRO", "Depósito em espécie"),
]]


def categorizar(texto_norm: str, tipo: str) -> str:
    for nome, padrao in _CATEGORIAS:
        if padrao.search(texto_norm):
            return nome
    return _CATEGORIA_POR_TIPO.get(tipo, "Outros")


# --------------------------------------------------------------------------
# CPF / CNPJ
# --------------------------------------------------------------------------

_DOC_FORMATADO = [
    (re.compile(r"(?<![\d*])[\d*]{2}\.[\d*]{3}\.[\d*]{3}/[\d*]{4}-[\d*]{2}(?![\d*])"), "CNPJ"),
    (re.compile(r"(?<![\d*/])[\d*]{3}\.[\d*]{3}\.[\d*]{3}-[\d*]{2}(?![\d*])"), "CPF"),
]
_NUM_LONGO = re.compile(r"(?<![\d.\-/])(\d{14}|\d{11})(?![\d.\-/])")


def _dv_valido(numero: str, pesos_dv1: list[int]) -> bool:
    """Valida os 2 dígitos verificadores (módulo 11) de CPF ou CNPJ."""
    if numero == numero[0] * len(numero):
        return False
    for pesos in (pesos_dv1, [pesos_dv1[0] + 1] + pesos_dv1):
        n = len(pesos)
        resto = sum(int(d) * p for d, p in zip(numero[:n], pesos)) % 11
        if int(numero[n]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def _cpf_valido(cpf: str) -> bool:
    return len(cpf) == 11 and _dv_valido(cpf, list(range(10, 1, -1)))


def _cnpj_valido(cnpj: str) -> bool:
    return len(cnpj) == 14 and _dv_valido(cnpj, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])


def extrair_documento(texto: str) -> tuple[str, str]:
    """Retorna (documento formatado, texto sem o documento)."""
    for padrao, rotulo in _DOC_FORMATADO:
        m = padrao.search(texto)
        if m and re.search(r"\d", m.group(0)):
            return f"{rotulo} {m.group(0)}", texto[: m.start()] + " " + texto[m.end():]
    for m in _NUM_LONGO.finditer(texto):
        d = m.group(1)
        if _cnpj_valido(d):
            doc = f"CNPJ {d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
        elif _cpf_valido(d):
            doc = f"CPF {d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
        else:
            continue
        return doc, texto[: m.start()] + " " + texto[m.end():]
    return "", texto


# --------------------------------------------------------------------------
# Nome da contraparte
# --------------------------------------------------------------------------

# Palavras que descrevem a operação (comparadas sem acento, em maiúsculas, sem pontuação)
_PALAVRAS_OPERACAO = frozenset("""
PIX RECEB RECEBIDO RECEBIDA RECEBIMENTO RECEBTO ENVIADO ENVIADA ENVIO EMITIDO EMITIDA EMIT
TRANSF TRANSFERENCIA TRANSFERENCIAS TRANSFERIDO TRANSFERIDA TEV TED DOC STR
CRED CREDITO DEB DEBITO C D
PAGTO PGTO PAGAMENTO PAGAMENTOS PAG PAGO
BOLETO BOLETOS TIT TITULO TITULOS COMPE EFETIVADO EFETIVADA LIQUIDACAO LIQ COBRANCA COB
OUTRA OUTRO MESMA MESMO IF IFS INST INSTITUICAO BANCO BCO AGENCIA AG CONTA CONTAS CC CORRENTE CTA
ONLINE INTERNET APP MOBILE CELULAR AUTOATENDIMENTO CAIXA ELETRONICO TAA BB SICOOB
DE DA DO DAS DOS PARA P EM COM POR VIA REF REFERENTE A AO E
QR QRCODE CODE CHAVE AGENDADO AGENDAMENTO PROGRAMADO DEVOLUCAO DEVOLVIDO ESTORNO
CONVENIO CONV AUT AUTOMATICO AUTOMATICA SERVICOS SERVICO PACOTE TARIFA TARIFAS TAR CESTA MENSAL
COMPRA COMPRAS CARTAO VISA ELECTRON MASTERCARD MAESTRO ELO NACIONAL INTERNACIONAL
SAQUE DEPOSITO DEP DINHEIRO ESPECIE CHEQUE CH COMPENSADO
APLICACAO APLIC RESGATE RESG RENDIMENTO REND POUPANCA INVEST INVESTIMENTO
JUROS IOF ENCARGOS MORA MULTA LIMITE ESPECIAL
SALARIO FOLHA PROVENTOS
LUZ AGUA TELEFONE GAS ENERGIA
DARF GPS DAS GRU GARE DAE FGTS IMPOSTO IMPOSTOS TRIBUTO TRIBUTOS
ENTRE PROPRIA PROPRIAS PROPRIO PROPRIOS TITULARIDADE TITULAR
OPERACAO OPER LANCAMENTO LANC MOVIMENTACAO DOCUMENTO DOCTO NR NUM NUMERO NO N
""".split())

_DATA_HORA = re.compile(r"^\d{1,2}/\d{1,2}(/\d{2,4})?$|^\d{1,2}:\d{2}(:\d{2})?$|^\d{1,2}H\d{2}$")
_SEPARADORES = re.compile(r"\s+[-–—|;>]\s+|\s*[|;]\s*|\s{2,}")


def _limpar_token(tok: str) -> str:
    return normalizar(tok).strip(".,:;()[]{}-–*'\"")


def _eh_operacao(tok: str) -> bool:
    """'RECEB.OUTRA' -> True (todas as partes são palavras de operação)."""
    partes = [p for p in re.split(r"[.\-/]", _limpar_token(tok)) if p]
    return bool(partes) and all(p in _PALAVRAS_OPERACAO for p in partes)


def _nome_do_segmento(segmento: str) -> str:
    # remove datas, horas e tokens sem letras (agência, conta, números) em qualquer posição
    uteis = [tok for tok in segmento.split()
             if re.search(r"[A-Z]", _limpar_token(tok)) and not _DATA_HORA.match(_limpar_token(tok))]
    # descarta palavras de operação nas pontas ("Recebimento Pix FULANO", "FULANO - OUTRA IF")
    while uteis and _eh_operacao(uteis[0]):
        uteis.pop(0)
    while uteis and _eh_operacao(uteis[-1]):
        uteis.pop()
    nome = " ".join(uteis).strip(" -–|;:,")[:TAMANHO_MAXIMO_NOME]
    return nome if len(re.sub(r"[^A-Za-zÀ-ÿ]", "", nome)) >= 3 else ""


def extrair_contraparte(historico: str, complemento: str) -> tuple[str, str]:
    """Retorna (nome, documento) da contraparte a partir do NAME e MEMO."""
    documento, nome = "", ""
    # MEMO primeiro (é onde BB e Sicoob colocam o favorecido), depois NAME
    for texto in (complemento, historico):
        if not texto:
            continue
        if not documento:
            documento, texto = extrair_documento(texto)
        if not nome:
            nome = next(filter(None, map(_nome_do_segmento, _SEPARADORES.split(texto.strip()))), "")
    return nome, documento


# --------------------------------------------------------------------------
# Análise completa
# --------------------------------------------------------------------------

def _nome_padrao(t: Transacao, texto_norm: str, nome_banco: str) -> str:
    """Contraparte quando o texto do banco não traz um nome."""
    if t.categoria in _CONTRAPARTE_BANCO:
        return nome_banco or "Instituição financeira"
    if t.categoria in ("Aplicação", "Resgate"):
        return f"Investimentos - {nome_banco}" if nome_banco else "Investimentos (mesmo titular)"
    for padrao, rotulo in _CONTRAPARTE_PADRAO:
        if padrao.search(texto_norm):
            return rotulo
    return t.conta_destino


def analisar_transacao(t: Transacao, nome_banco: str, titular: str) -> None:
    texto_norm = normalizar(f"{t.historico} {t.complemento}")
    t.categoria = categorizar(texto_norm, t.tipo)

    nome, doc = extrair_contraparte(t.historico, t.complemento)
    # favorecido informado explicitamente no OFX (<PAYEE><NAME>) tem prioridade
    nome = t.extras.get("PAYEE") or nome or _nome_padrao(t, texto_norm, nome_banco)

    t.contraparte, t.contraparte_doc = nome, doc
    if nome and doc:
        outra_parte = f"{nome} ({doc})"
    else:
        outra_parte = nome or doc or NAO_IDENTIFICADO

    if t.valor >= 0:
        t.pagador, t.recebedor = outra_parte, titular
    else:
        t.pagador, t.recebedor = titular, outra_parte
