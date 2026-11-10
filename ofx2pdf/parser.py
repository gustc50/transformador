"""Leitor de arquivos OFX (versões 1.x SGML e 2.x XML).

O parser é tolerante: aceita tags sem fechamento (padrão SGML usado por
Banco do Brasil, Sicoob e a maioria dos bancos brasileiros), tags fechadas
(XML), codificações UTF-8 / Windows-1252 / Latin-1 e valores com vírgula
decimal. Também é defensivo contra arquivos malformados ou maliciosos:
limita profundidade, tamanho de campos e magnitude de valores, e qualquer
problema vira ErroOFX (nunca um travamento ou erro inesperado).
"""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from .bancos import identificar_banco
from .contraparte import analisar_transacao, eh_linha_de_saldo
from .models import ArquivoOFX, Extrato, Saldo, Transacao

PROFUNDIDADE_MAXIMA = 64          # um OFX real tem ~8 níveis
TAMANHO_MAXIMO_CAMPO = 2000       # caracteres por valor (MEMO, NAME...)
VALOR_MAXIMO = Decimal("1e15")    # R$ 1 quatrilhão


class ErroOFX(ValueError):
    """Arquivo não pôde ser interpretado como OFX."""


# --------------------------------------------------------------------------
# Árvore de elementos
# --------------------------------------------------------------------------

class No:
    __slots__ = ("nome", "valor", "filhos")

    def __init__(self, nome: str, valor: Optional[str] = None):
        self.nome = nome
        self.valor = valor
        self.filhos: list[No] = []

    def achar(self, caminho: str) -> Optional["No"]:
        """Filho pelo caminho 'A/B/C' (cada parte é procurada entre os filhos diretos)."""
        atual: Optional[No] = self
        for parte in caminho.upper().split("/"):
            atual = next((f for f in atual.filhos if f.nome == parte), None)
            if atual is None:
                return None
        return atual

    def texto(self, caminho: str, padrao: str = "") -> str:
        no = self.achar(caminho)
        return no.valor if no is not None and no.valor else padrao

    def todos(self, nome: str) -> list["No"]:
        """Todos os descendentes (qualquer profundidade) com o nome informado, em ordem."""
        res, pilha = [], list(reversed(self.filhos))
        while pilha:
            n = pilha.pop()
            if n.nome == nome:
                res.append(n)
            pilha.extend(reversed(n.filhos))
        return res


_TOKEN = re.compile(r"<(/?)([A-Za-z0-9_.]+)\s*>([^<]*)")
# \Z garante tempo linear mesmo com "<!--" sem fechamento
_COMENTARIO = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_ESPACOS = re.compile(r"\s{3,}")


def _decodificar(dados: bytes) -> str:
    if dados.startswith(b"\xef\xbb\xbf"):
        dados = dados[3:]
    for codificacao in ("utf-8", "cp1252"):
        try:
            return dados.decode(codificacao)
        except UnicodeDecodeError:
            pass
    return dados.decode("latin-1")  # nunca falha


def _limpar_valor(bruto: str) -> str:
    # mantém espaço duplo (separador usado pelo Sicoob) e limita o tamanho do campo
    return _ESPACOS.sub("  ", html.unescape(bruto.strip()))[:TAMANHO_MAXIMO_CAMPO]


def _montar_arvore(texto: str) -> No:
    inicio = texto.upper().find("<OFX>")
    if inicio < 0:
        raise ErroOFX("Arquivo não contém a tag <OFX>; não parece ser um extrato OFX.")
    corpo = _COMENTARIO.sub("", texto[inicio:])

    raiz = No("#RAIZ")
    pilha = [raiz]
    for m in _TOKEN.finditer(corpo):
        fechamento, nome, resto = m.group(1), m.group(2).upper(), m.group(3)
        if fechamento:
            # fecha até o elemento correspondente (tolera tags SGML sem fechamento)
            for i in range(len(pilha) - 1, 0, -1):
                if pilha[i].nome == nome:
                    del pilha[i:]
                    break
            continue
        valor = _limpar_valor(resto)
        if valor:  # elemento folha, com ou sem tag de fechamento
            pilha[-1].filhos.append(No(nome, valor))
            continue
        if len(pilha) > PROFUNDIDADE_MAXIMA:
            raise ErroOFX("Estrutura OFX inválida: aninhamento de tags profundo demais.")
        no = No(nome)
        pilha[-1].filhos.append(no)
        pilha.append(no)

    ofx = raiz.achar("OFX")
    if ofx is None:
        raise ErroOFX("Estrutura OFX inválida.")
    return ofx


# --------------------------------------------------------------------------
# Conversões de valores
# --------------------------------------------------------------------------

_DATA = re.compile(
    r"^(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2})?(?:\.\d+)?)?\s*(?:\[\s*([+-]?\d{1,2}(?:\.\d+)?)\s*(?::[^\]]*)?\])?"
)


def parse_data(valor: str) -> Optional[datetime]:
    m = _DATA.match((valor or "").strip())
    if not m:
        return None
    a, mes, d, h, mi, s, tz = m.groups()
    try:
        dt = datetime(int(a), int(mes), int(d), int(h or 0), int(mi or 0), int(s or 0))
        if tz:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=float(tz))))
    except ValueError:
        return None
    return dt


def parse_valor(valor: str) -> Decimal:
    v = (valor or "").strip().replace(" ", "")
    if not v:
        return Decimal("0")
    if "," in v and "." in v:
        # 1.234,56 -> 1234.56   |   1,234.56 -> 1234.56
        v = v.replace(".", "").replace(",", ".") if v.rfind(",") > v.rfind(".") else v.replace(",", "")
    else:
        v = v.replace(",", ".")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", v):  # rejeita NaN, Infinity, notação científica...
        raise ErroOFX(f"Valor monetário inválido: {valor[:30]!r}")
    numero = Decimal(v)
    if abs(numero) >= VALOR_MAXIMO:
        raise ErroOFX(f"Valor monetário fora do limite aceito: {valor[:30]!r}")
    return numero


# --------------------------------------------------------------------------
# Leitura do extrato
# --------------------------------------------------------------------------

_CAMPOS_CONHECIDOS = {
    "TRNTYPE", "DTPOSTED", "DTUSER", "DTAVAIL", "TRNAMT", "FITID", "CHECKNUM",
    "REFNUM", "NAME", "MEMO", "PAYEEID", "SIC", "PAYEE", "BANKACCTTO", "CCACCTTO",
}


def _ler_transacao(no: No) -> Transacao:
    nome = no.texto("NAME")
    extras = {}
    payee = no.achar("PAYEE")
    if payee is not None:
        payee_nome = payee.texto("NAME")
        if payee_nome:
            extras["PAYEE"] = payee_nome
            nome = nome or payee_nome
        endereco = ", ".join(filter(None, (payee.texto(c) for c in ("ADDR1", "ADDR2", "CITY", "STATE", "POSTALCODE"))))
        if endereco:
            extras["Endereço do favorecido"] = endereco

    conta_destino = ""
    destino = no.achar("BANKACCTTO") or no.achar("CCACCTTO")
    if destino is not None:
        partes = [(rotulo, destino.texto(tag)) for rotulo, tag in
                  (("Banco", "BANKID"), ("Ag", "BRANCHID"), ("Conta", "ACCTID"))]
        conta_destino = " / ".join(f"{rotulo} {valor}" for rotulo, valor in partes if valor)

    extras.update({f.nome: f.valor for f in no.filhos if f.nome not in _CAMPOS_CONHECIDOS and f.valor})

    return Transacao(
        tipo=no.texto("TRNTYPE").upper(),
        data=parse_data(no.texto("DTPOSTED")),
        valor=parse_valor(no.texto("TRNAMT")),
        fitid=no.texto("FITID"),
        numero_documento=no.texto("CHECKNUM"),
        referencia=no.texto("REFNUM"),
        historico=nome,
        complemento=no.texto("MEMO"),
        payee_id=no.texto("PAYEEID"),
        sic=no.texto("SIC"),
        data_usuario=parse_data(no.texto("DTUSER")),
        data_disponivel=parse_data(no.texto("DTAVAIL")),
        conta_destino=conta_destino,
        extras=extras,
    )


def _ler_saldo(no: Optional[No]) -> Optional[Saldo]:
    if no is None or not no.texto("BALAMT"):
        return None
    return Saldo(parse_valor(no.texto("BALAMT")), parse_data(no.texto("DTASOF")))


def _descricao_titular(extrato: Extrato) -> str:
    partes = []
    if extrato.agencia:
        partes.append(f"Ag {extrato.agencia}")
    if extrato.conta:
        partes.append(f"{'Cartão' if extrato.cartao else 'C/C'} {extrato.conta}")
    return f"Titular da conta ({' '.join(partes)})" if partes else "Titular da conta"


def _ler_extrato(stmt: No, org: str, fid: str, titular: str) -> Extrato:
    cartao = stmt.nome == "CCSTMTRS"
    conta = stmt.achar("BANKACCTFROM") or stmt.achar("CCACCTFROM") or No("")
    codigo, nome_banco = identificar_banco(conta.texto("BANKID"), org, fid)
    lista = stmt.achar("BANKTRANLIST") or No("")

    extrato = Extrato(
        banco_codigo=codigo,
        banco_nome=nome_banco,
        agencia=conta.texto("BRANCHID"),
        conta=conta.texto("ACCTID"),
        tipo_conta=(conta.texto("ACCTTYPE") or ("CREDITCARD" if cartao else "")).upper(),
        moeda=stmt.texto("CURDEF", "BRL"),
        inicio=parse_data(lista.texto("DTSTART")),
        fim=parse_data(lista.texto("DTEND")),
        saldo_final=_ler_saldo(stmt.achar("LEDGERBAL")),
        saldo_disponivel=_ler_saldo(stmt.achar("AVAILBAL")),
        cartao=cartao,
    )

    for no in lista.filhos:
        if no.nome == "STMTTRN":
            t = _ler_transacao(no)
            (extrato.lancamentos_saldo if eh_linha_de_saldo(t) else extrato.transacoes).append(t)

    # Ordena por data (sort estável: mantém a ordem original dentro do mesmo dia)
    extrato.transacoes.sort(key=lambda t: t.data.replace(tzinfo=None) if t.data else datetime.min)

    titular_desc = titular or _descricao_titular(extrato)
    for t in extrato.transacoes:
        analisar_transacao(t, nome_banco, titular_desc)

    # Saldo após cada lançamento, a partir do saldo inicial calculado com o LEDGERBAL
    saldo = extrato.saldo_inicial_calculado
    if saldo is not None and not cartao:
        for t in extrato.transacoes:
            saldo += t.valor
            t.saldo_apos = saldo
    return extrato


def ler_ofx_bytes(dados: bytes, nome_arquivo: str = "", titular: str = "") -> ArquivoOFX:
    titular = titular.strip()
    ofx = _montar_arvore(_decodificar(dados))
    fi = ofx.achar("SIGNONMSGSRSV1/SONRS/FI") or No("")
    arquivo = ArquivoOFX(
        nome_arquivo=nome_arquivo,
        titular=titular,
        instituicao_org=fi.texto("ORG"),
        instituicao_fid=fi.texto("FID"),
        data_geracao=parse_data(ofx.texto("SIGNONMSGSRSV1/SONRS/DTSERVER")),
    )
    for stmt in ofx.todos("STMTRS") + ofx.todos("CCSTMTRS"):
        arquivo.extratos.append(_ler_extrato(stmt, arquivo.instituicao_org, arquivo.instituicao_fid, titular))
    if not arquivo.extratos:
        raise ErroOFX("Nenhum extrato (STMTRS/CCSTMTRS) encontrado no arquivo OFX.")
    return arquivo


def ler_ofx(caminho: str, titular: str = "") -> ArquivoOFX:
    with open(caminho, "rb") as f:
        return ler_ofx_bytes(f.read(), os.path.basename(caminho), titular)
