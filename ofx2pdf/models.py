"""Estruturas de dados que representam o conteúdo de um arquivo OFX."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass
class Transacao:
    tipo: str                      # TRNTYPE (CREDIT, DEBIT, PAYMENT, XFER...)
    data: Optional[datetime]       # DTPOSTED
    valor: Decimal                 # TRNAMT (negativo = saída)
    fitid: str = ""                # identificador único da transação
    numero_documento: str = ""     # CHECKNUM
    referencia: str = ""           # REFNUM
    historico: str = ""            # NAME (ou PAYEE/NAME)
    complemento: str = ""          # MEMO
    payee_id: str = ""             # PAYEEID
    sic: str = ""                  # SIC
    data_usuario: Optional[datetime] = None   # DTUSER
    data_disponivel: Optional[datetime] = None  # DTAVAIL
    conta_destino: str = ""        # BANKACCTTO / CCACCTTO resumido
    extras: dict = field(default_factory=dict)  # demais campos não mapeados

    # Preenchidos pela análise (contraparte.py)
    contraparte: str = ""          # nome identificado da outra parte
    contraparte_doc: str = ""      # CPF/CNPJ da outra parte (se houver)
    pagador: str = ""
    recebedor: str = ""
    categoria: str = ""            # PIX, TED, Boleto, Tarifa...
    saldo_apos: Optional[Decimal] = None  # saldo calculado após a transação

    @property
    def is_credito(self) -> bool:
        return self.valor > 0

    @property
    def is_debito(self) -> bool:
        return self.valor < 0


@dataclass
class Saldo:
    valor: Decimal
    data: Optional[datetime]


@dataclass
class Extrato:
    banco_codigo: str = ""
    banco_nome: str = ""
    agencia: str = ""
    conta: str = ""
    tipo_conta: str = ""           # CHECKING, SAVINGS, CREDITCARD...
    moeda: str = "BRL"
    inicio: Optional[datetime] = None
    fim: Optional[datetime] = None
    saldo_final: Optional[Saldo] = None      # LEDGERBAL
    saldo_disponivel: Optional[Saldo] = None  # AVAILBAL
    transacoes: list[Transacao] = field(default_factory=list)
    lancamentos_saldo: list[Transacao] = field(default_factory=list)  # linhas "SALDO" informativas
    cartao: bool = False

    @property
    def total_creditos(self) -> Decimal:
        return sum((t.valor for t in self.transacoes if t.valor > 0), Decimal("0"))

    @property
    def total_debitos(self) -> Decimal:
        return sum((t.valor for t in self.transacoes if t.valor < 0), Decimal("0"))

    @property
    def qtd_creditos(self) -> int:
        return sum(1 for t in self.transacoes if t.valor > 0)

    @property
    def qtd_debitos(self) -> int:
        return sum(1 for t in self.transacoes if t.valor < 0)

    @property
    def saldo_inicial_calculado(self) -> Optional[Decimal]:
        if self.saldo_final is None:
            return None
        return self.saldo_final.valor - (self.total_creditos + self.total_debitos)


@dataclass
class ArquivoOFX:
    nome_arquivo: str = ""
    versao: str = ""
    instituicao_org: str = ""      # SIGNON/FI/ORG
    instituicao_fid: str = ""      # SIGNON/FI/FID
    data_geracao: Optional[datetime] = None  # SIGNON/DTSERVER
    idioma: str = ""
    extratos: list[Extrato] = field(default_factory=list)
