"""Leitor de arquivos OFX (versões 1.x SGML e 2.x XML).

O parser é tolerante: aceita tags sem fechamento (padrão SGML usado por
Banco do Brasil, Sicoob e a maioria dos bancos brasileiros), tags fechadas
(XML), codificações UTF-8 / Windows-1252 / Latin-1 e valores com vírgula
decimal.
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
        """Busca o primeiro descendente pelo caminho 'A/B/C' (busca em profundidade no 1º nível)."""
        partes = caminho.upper().split("/")
        atual: Optional[No] = self
        for parte in partes:
            if atual is None:
                return None
            atual = next((f for f in atual.filhos if f.nome == parte), None)
        return atual

    def texto(self, caminho: str, padrao: str = "") -> str:
        no = self.achar(caminho)
        if no is None or no.valor is None:
            return padrao
        return no.valor

    def todos(self, nome: str) -> list["No"]:
        """Todos os descendentes (qualquer profundidade) com o nome informado."""
        nome = nome.upper()
        res = []
        pilha = list(reversed(self.filhos))
        while pilha:
            n = pilha.pop()
            if n.nome == nome:
                res.append(n)
            pilha.extend(reversed(n.filhos))
        return res

    def __repr__(self) -> str:  # pragma: no cover
        return f"<No {self.nome}={self.valor!r} filhos={len(self.filhos)}>"


_TOKEN = re.compile(r"<(/?)([A-Za-z0-9_.]+)\s*>([^<]*)", re.S)


def _decodificar(dados: bytes) -> str:
    cabecalho = dados[:600].decode("ascii", "ignore").upper()
    candidatos = []
    if "UTF-8" in cabecalho or "ENCODING:UTF" in cabecalho:
        candidatos.append("utf-8")
    candidatos += ["utf-8", "cp1252", "latin-1"]
    if dados.startswith(b"\xef\xbb\xbf"):
        dados = dados[3:]
    for cod in candidatos:
        try:
            return dados.decode(cod)
        except UnicodeDecodeError:
            continue
    return dados.decode("latin-1", "replace")


def _montar_arvore(texto: str) -> No:
    inicio = texto.upper().find("<OFX>")
    if inicio < 0:
        raise ErroOFX("Arquivo não contém a tag <OFX>; não parece ser um extrato OFX.")
    corpo = texto[inicio:]
    # remove comentários e instruções de processamento
    corpo = re.sub(r"<!--.*?-->", "", corpo, flags=re.S)
    corpo = re.sub(r"<\?.*?\?>", "", corpo, flags=re.S)

    raiz = No("#RAIZ")
    pilha = [raiz]
    for m in _TOKEN.finditer(corpo):
        fechamento, nome, resto = m.group(1), m.group(2).upper(), m.group(3)
        if fechamento:
            # fecha até encontrar o elemento correspondente (tolerante a tags sem fechamento)
            for i in range(len(pilha) - 1, 0, -1):
                if pilha[i].nome == nome:
                    del pilha[i:]
                    break
            continue
        valor = html.unescape(resto.strip())
        if valor:
            # elemento folha (com ou sem tag de fechamento)
            pilha[-1].filhos.append(No(nome, valor))
        else:
            no = No(nome)
            pilha[-1].filhos.append(no)
            pilha.append(no)

    # Elementos abertos sem valor e sem filhos (ex.: <MEMO></MEMO>) viram folhas vazias.
    def ajustar(n: No):
        for f in n.filhos:
            if not f.filhos and f.valor is None:
                f.valor = ""
            ajustar(f)
    ajustar(raiz)

    ofx = raiz.achar("OFX")
    if ofx is None:
        raise ErroOFX("Estrutura OFX inválida.")
    return ofx


# --------------------------------------------------------------------------
# Conversões de valores
# --------------------------------------------------------------------------

_DATA = re.compile(
    r"^(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2})?(?:\.\d+)?)?\s*(?:\[\s*([+-]?\d+(?:\.\d+)?)\s*(?::[^\]]*)?\])?"
)


def parse_data(valor: str) -> Optional[datetime]:
    if not valor:
        return None
    m = _DATA.match(valor.strip())
    if not m:
        return None
    a, mes, d, h, mi, s, tz = m.groups()
    try:
        dt = datetime(int(a), int(mes), int(d), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None
    if tz:
        try:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=float(tz))))
        except ValueError:
            pass
    return dt


def parse_valor(valor: str) -> Decimal:
    if valor is None:
        return Decimal("0")
    v = valor.strip().replace(" ", "")
    if not v:
        return Decimal("0")
    if "," in v and "." in v:
        # 1.234,56 -> 1234.56   |   1,234.56 -> 1234.56
        if v.rfind(",") > v.rfind("."):
            v = v.replace(".", "").replace(",", ".")
        else:
            v = v.replace(",", "")
    elif "," in v:
        v = v.replace(",", ".")
    try:
        return Decimal(v)
    except InvalidOperation:
        raise ErroOFX(f"Valor monetário inválido: {valor!r}")


# --------------------------------------------------------------------------
# Leitura do extrato
# --------------------------------------------------------------------------

_CAMPOS_CONHECIDOS = {
    "TRNTYPE", "DTPOSTED", "DTUSER", "DTAVAIL", "TRNAMT", "FITID", "CHECKNUM",
    "REFNUM", "NAME", "MEMO", "PAYEEID", "SIC", "PAYEE", "BANKACCTTO", "CCACCTTO",
}


def _ler_transacao(no: No) -> Transacao:
    nome = no.texto("NAME")
    payee = no.achar("PAYEE")
    extras = {}
    if payee is not None:
        payee_nome = payee.texto("NAME")
        if payee_nome:
            extras["PAYEE"] = payee_nome
            if not nome:
                nome = payee_nome
        partes_end = [payee.texto(c) for c in ("ADDR1", "ADDR2", "CITY", "STATE", "POSTALCODE")]
        endereco = ", ".join(p for p in partes_end if p)
        if endereco:
            extras["Endereço do favorecido"] = endereco

    conta_destino = ""
    for tag in ("BANKACCTTO", "CCACCTTO"):
        dest = no.achar(tag)
        if dest is not None:
            bco = dest.texto("BANKID")
            ag = dest.texto("BRANCHID")
            cc = dest.texto("ACCTID")
            partes = []
            if bco:
                partes.append(f"Banco {bco}")
            if ag:
                partes.append(f"Ag {ag}")
            if cc:
                partes.append(f"Conta {cc}")
            conta_destino = " / ".join(partes)

    for f in no.filhos:
        if f.nome not in _CAMPOS_CONHECIDOS and f.valor:
            extras[f.nome] = f.valor

    return Transacao(
        tipo=no.texto("TRNTYPE").upper(),
        data=parse_data(no.texto("DTPOSTED")),
        valor=parse_valor(no.texto("TRNAMT", "0")),
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
    if no is None:
        return None
    valor = no.texto("BALAMT")
    if not valor:
        return None
    return Saldo(parse_valor(valor), parse_data(no.texto("DTASOF")))


def _ler_extrato(stmt: No, org: str, fid: str, titular: str = "") -> Extrato:
    cartao = stmt.nome == "CCSTMTRS"
    conta_no = stmt.achar("BANKACCTFROM") or stmt.achar("CCACCTFROM")
    bankid = agencia = conta = tipo = ""
    if conta_no is not None:
        bankid = conta_no.texto("BANKID")
        agencia = conta_no.texto("BRANCHID")
        conta = conta_no.texto("ACCTID")
        tipo = conta_no.texto("ACCTTYPE")
    if cartao:
        tipo = tipo or "CREDITCARD"
    codigo, nome_banco = identificar_banco(bankid, org, fid)

    lista = stmt.achar("BANKTRANLIST")
    extrato = Extrato(
        banco_codigo=codigo,
        banco_nome=nome_banco,
        agencia=agencia,
        conta=conta,
        tipo_conta=tipo.upper(),
        moeda=stmt.texto("CURDEF", "BRL") or "BRL",
        inicio=parse_data(lista.texto("DTSTART")) if lista is not None else None,
        fim=parse_data(lista.texto("DTEND")) if lista is not None else None,
        saldo_final=_ler_saldo(stmt.achar("LEDGERBAL")),
        saldo_disponivel=_ler_saldo(stmt.achar("AVAILBAL")),
        cartao=cartao,
    )

    if lista is not None:
        for no in lista.filhos:
            if no.nome != "STMTTRN":
                continue
            t = _ler_transacao(no)
            if eh_linha_de_saldo(t):
                extrato.lancamentos_saldo.append(t)
                continue
            extrato.transacoes.append(t)

    # Ordena por data mantendo a ordem original entre transações do mesmo dia
    extrato.transacoes.sort(key=lambda t: (t.data.replace(tzinfo=None) if t.data else datetime.min))

    titular_desc = titular.strip() or _descricao_titular(extrato)
    for t in extrato.transacoes:
        analisar_transacao(t, codigo, nome_banco, titular_desc)

    _calcular_saldos(extrato)
    return extrato


def _descricao_titular(extrato: Extrato) -> str:
    partes = []
    if extrato.agencia:
        partes.append(f"Ag {extrato.agencia}")
    if extrato.conta:
        partes.append(f"{'Cartão' if extrato.cartao else 'C/C'} {extrato.conta}")
    ref = " ".join(partes)
    return f"Titular da conta ({ref})" if ref else "Titular da conta"


def _calcular_saldos(extrato: Extrato) -> None:
    """Saldo após cada lançamento, calculado de trás para frente a partir do LEDGERBAL."""
    inicial = extrato.saldo_inicial_calculado
    if inicial is None or extrato.cartao:
        return
    saldo = inicial
    for t in extrato.transacoes:
        saldo += t.valor
        t.saldo_apos = saldo


def ler_ofx_bytes(dados: bytes, nome_arquivo: str = "", titular: str = "") -> ArquivoOFX:
    texto = _decodificar(dados)
    cabecalho = texto[: max(texto.upper().find("<OFX>"), 0)]
    versao = ""
    m = re.search(r"VERSION[:=]\s*\"?(\d+)", cabecalho)
    if m:
        versao = m.group(1)
    ofx = _montar_arvore(texto)

    fi = ofx.achar("SIGNONMSGSRSV1/SONRS/FI")
    org = fi.texto("ORG") if fi is not None else ""
    fid = fi.texto("FID") if fi is not None else ""
    arquivo = ArquivoOFX(
        nome_arquivo=nome_arquivo,
        versao=versao,
        instituicao_org=org,
        instituicao_fid=fid,
        data_geracao=parse_data(ofx.texto("SIGNONMSGSRSV1/SONRS/DTSERVER")),
        idioma=ofx.texto("SIGNONMSGSRSV1/SONRS/LANGUAGE"),
    )
    for stmt in ofx.todos("STMTRS") + ofx.todos("CCSTMTRS"):
        arquivo.extratos.append(_ler_extrato(stmt, org, fid, titular))
    if not arquivo.extratos:
        raise ErroOFX("Nenhum extrato (STMTRS/CCSTMTRS) encontrado no arquivo OFX.")
    return arquivo


def ler_ofx(caminho: str, titular: str = "") -> ArquivoOFX:
    with open(caminho, "rb") as f:
        dados = f.read()
    return ler_ofx_bytes(dados, os.path.basename(caminho), titular)
