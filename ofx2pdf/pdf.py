"""Geração do relatório PDF a partir de um ArquivoOFX."""

from __future__ import annotations

from collections import defaultdict
import html
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import BinaryIO, Optional, Union

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .bancos import cores_do_banco
from .models import ZERO, ArquivoOFX, Extrato, Transacao

PAGINA = landscape(A4)
MARGEM = 12 * mm
LARGURA = PAGINA[0] - 2 * MARGEM

COR_TEXTO = colors.HexColor("#1D2733")
COR_CREDITO = colors.HexColor("#1B7F3B")
COR_DEBITO = colors.HexColor("#C0392B")
COR_TEXTO_SUAVE = colors.HexColor("#5F6B7A")
COR_LINHA = colors.HexColor("#D9DEE5")
COR_ZEBRA = colors.HexColor("#F5F7FA")
COR_CABECALHO = colors.HexColor("#2B3A4A")

TIPOS_CONTA = {
    "CHECKING": "Conta corrente",
    "SAVINGS": "Poupança",
    "MONEYMRKT": "Conta investimento",
    "CREDITLINE": "Linha de crédito",
    "CREDITCARD": "Cartão de crédito",
    "CD": "Certificado de depósito",
}

TIPOS_TRANSACAO = {
    "CREDIT": "Crédito", "DEBIT": "Débito", "INT": "Juros", "DIV": "Dividendos",
    "FEE": "Tarifa", "SRVCHG": "Taxa de serviço", "DEP": "Depósito", "ATM": "Caixa eletrônico",
    "POS": "Compra (POS)", "XFER": "Transferência", "CHECK": "Cheque", "PAYMENT": "Pagamento",
    "CASH": "Saque", "DIRECTDEP": "Depósito direto", "DIRECTDEBIT": "Débito automático",
    "REPEATPMT": "Pagamento recorrente", "HOLD": "Bloqueio", "OTHER": "Outros",
}

# --------------------------------------------------------------------------
# Estilos (criados uma única vez)
# --------------------------------------------------------------------------

_BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=7.4, leading=9, textColor=COR_TEXTO)


def _estilo(nome: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(nome, parent=kw.pop("parent", _BASE), **kw)


E_BASE = _BASE
E_SUAVE = _estilo("suave", fontSize=6.4, leading=7.8, textColor=COR_TEXTO_SUAVE)
E_DIREITA = _estilo("direita", alignment=TA_RIGHT)
E_CAB = _estilo("cab", fontName="Helvetica-Bold", textColor=colors.white)
E_CAB_DIR = _estilo("cab_dir", parent=E_CAB, alignment=TA_RIGHT)
E_SECAO = _estilo("secao", fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceBefore=4, spaceAfter=4)
E_ROTULO = _estilo("rotulo", fontSize=6.6, leading=8, textColor=COR_TEXTO_SUAVE)
E_INFO = _estilo("info", fontName="Helvetica-Bold", fontSize=8.6, leading=10.5)
E_NOTA = _estilo("nota", fontSize=6.8, leading=8.5, textColor=COR_TEXTO_SUAVE)
E_CARD = {cor: _estilo(f"card{i}", fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=cor)
          for i, cor in enumerate((COR_TEXTO, COR_CREDITO, COR_DEBITO))}
E_VALOR = {cor: _estilo(f"valor{i}", parent=E_DIREITA, fontName="Helvetica-Bold", textColor=cor)
           for i, cor in enumerate((COR_TEXTO, COR_CREDITO, COR_DEBITO))}
E_NUMERO = {cor: _estilo(f"num{i}", parent=E_DIREITA, textColor=cor)
            for i, cor in enumerate((COR_TEXTO, COR_CREDITO, COR_DEBITO))}

# Espaçamento interno padrão das tabelas
_PADDING = [("LEFTPADDING", (0, 0), (-1, -1), 1.6 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 1.6 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 1.4 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.4 * mm)]


# --------------------------------------------------------------------------
# Formatação
# --------------------------------------------------------------------------

def moeda(valor: Optional[Decimal], sinal: bool = False, simbolo: str = "R$") -> str:
    if valor is None:
        return "—"
    v = Decimal(valor).quantize(Decimal("0.01"))
    texto = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    prefixo = "-" if v < 0 else ("+" if sinal and v > 0 else "")
    return f"{prefixo}{simbolo} {texto}" if simbolo else f"{prefixo}{texto}"


def data_br(d: Optional[datetime], hora: bool = False) -> str:
    if d is None:
        return "—"
    return d.strftime("%d/%m/%Y %H:%M" if hora and (d.hour or d.minute) else "%d/%m/%Y")


def _e(texto: str) -> str:
    """Escapa texto vindo do OFX antes de usá-lo em um Paragraph (evita injeção de markup)."""
    return html.escape(texto or "", quote=False)


def _cor(valor: Optional[Decimal]):
    if valor is None or valor == 0:
        return COR_TEXTO
    return COR_CREDITO if valor > 0 else COR_DEBITO


# --------------------------------------------------------------------------
# Canvas com "Página X de Y" no rodapé
# --------------------------------------------------------------------------

def _fabrica_canvas(rodape: str):
    class CanvasNumerado(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._paginas = []

        def showPage(self):
            self._paginas.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._paginas)
            for estado in self._paginas:
                self.__dict__.update(estado)
                self.saveState()
                self.setStrokeColor(COR_LINHA)
                self.setLineWidth(0.5)
                self.line(MARGEM, 9 * mm, PAGINA[0] - MARGEM, 9 * mm)
                self.setFont("Helvetica", 6.8)
                self.setFillColor(COR_TEXTO_SUAVE)
                self.drawString(MARGEM, 5.5 * mm, rodape)
                self.drawRightString(PAGINA[0] - MARGEM, 5.5 * mm, f"Página {self._pageNumber} de {total}")
                self.restoreState()
                super().showPage()
            super().save()

    return CanvasNumerado


# --------------------------------------------------------------------------
# Blocos do relatório
# --------------------------------------------------------------------------

def _faixa_titulo(extrato: Extrato) -> Table:
    cor1, cor2 = cores_do_banco(extrato.banco_codigo)
    titulo = "Fatura de Cartão de Crédito" if extrato.cartao else "Extrato Bancário"
    banco = extrato.banco_nome + (f" ({extrato.banco_codigo})" if extrato.banco_codigo else "")
    esquerda = Paragraph(
        f'<font size="16" color="white"><b>{titulo}</b></font><br/>'
        f'<font size="10.5" color="white">{_e(banco)}</font>',
        _estilo("t", leading=19),
    )
    direita = Paragraph(
        f'<font size="7.5" color="white">PERÍODO</font><br/>'
        f'<font size="11" color="white"><b>{data_br(extrato.inicio)} a {data_br(extrato.fim)}</b></font>',
        _estilo("p", leading=14, alignment=TA_RIGHT),
    )
    t = Table([[esquerda, direita]], colWidths=[LARGURA * 0.65, LARGURA * 0.35], rowHeights=[17 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(cor1)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("LINEBELOW", (0, 0), (-1, -1), 3, colors.HexColor(cor2)),
    ]))
    return t


def _bloco_info(extrato: Extrato, arquivo: ArquivoOFX) -> Table:
    campos = [
        ("Banco", extrato.banco_nome + (f" — {extrato.banco_codigo}" if extrato.banco_codigo else "")),
        ("Agência", extrato.agencia),
        ("Cartão" if extrato.cartao else "Conta", extrato.conta),
        ("Tipo de conta", TIPOS_CONTA.get(extrato.tipo_conta, extrato.tipo_conta)),
        ("Titular", arquivo.titular or "Não informado no OFX"),
        ("Moeda", extrato.moeda),
        ("Arquivo OFX", arquivo.nome_arquivo),
        ("Gerado pelo banco em", data_br(arquivo.data_geracao, hora=True)),
    ]
    celulas = [[Paragraph(_e(rotulo.upper()), E_ROTULO), Paragraph(_e(valor or "—"), E_INFO)]
               for rotulo, valor in campos]
    t = Table([celulas[:4], celulas[4:]], colWidths=[LARGURA / 4] * 4)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2 * mm),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, COR_LINHA),
        ("BOX", (0, 0), (-1, -1), 0.6, COR_LINHA),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FBFCFD")),
    ]))
    return t


def _cards_resumo(extrato: Extrato) -> Table:
    cards = []
    if not extrato.cartao:
        cards.append(("Saldo inicial (calculado)", moeda(extrato.saldo_inicial_calculado), COR_TEXTO, ""))
    cards += [
        ("Entradas", moeda(extrato.total_creditos), COR_CREDITO, f"{len(extrato.creditos)} lançamento(s)"),
        ("Saídas", moeda(extrato.total_debitos), COR_DEBITO, f"{len(extrato.debitos)} lançamento(s)"),
        ("Resultado do período", moeda(extrato.resultado, sinal=True),
         COR_CREDITO if extrato.resultado >= 0 else COR_DEBITO, ""),
    ]
    for rotulo, saldo in (("Saldo da fatura" if extrato.cartao else "Saldo final", extrato.saldo_final),
                          ("Saldo disponível", extrato.saldo_disponivel)):
        if saldo is not None:
            cards.append((rotulo, moeda(saldo.valor), COR_TEXTO, f"em {data_br(saldo.data)}"))

    gap = 2.5 * mm
    largura_card = (LARGURA - gap * (len(cards) - 1)) / len(cards)
    linha, larguras = [], []
    for rotulo, valor, cor, sub in cards:
        card = Table([[Paragraph(_e(rotulo.upper()), E_ROTULO)], [Paragraph(valor, E_CARD[cor])],
                      [Paragraph(sub or "&nbsp;", E_SUAVE)]], colWidths=[largura_card])
        card.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, COR_LINHA),
            ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("TOPPADDING", (0, 0), (0, 0), 2.5 * mm),
            ("TOPPADDING", (0, 1), (-1, -1), 0.6 * mm),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 2.5 * mm),
        ]))
        linha += [card, ""]
        larguras += [largura_card, gap]
    t = Table([linha[:-1]], colWidths=larguras[:-1])
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def _celula_historico(t: Transacao) -> Paragraph:
    texto = f"<b>{_e(t.historico or t.complemento or '(sem descrição)')}</b>"
    if t.historico and t.complemento and t.complemento != t.historico:
        texto += "<br/>" + _e(t.complemento)

    detalhes = [f"Tipo OFX: {TIPOS_TRANSACAO.get(t.tipo, t.tipo)}"] if t.tipo else []
    if t.conta_destino:
        detalhes.append(f"Conta destino: {t.conta_destino}")
    for rotulo, data in (("Data da operação", t.data_usuario), ("Disponível em", t.data_disponivel)):
        if data and t.data and data.date() != t.data.date():
            detalhes.append(f"{rotulo}: {data_br(data)}")
    if t.payee_id:
        detalhes.append(f"ID favorecido: {t.payee_id}")
    if t.sic:
        detalhes.append(f"SIC: {t.sic}")
    for chave, valor in t.extras.items():
        if not (chave == "PAYEE" and valor == t.historico):
            detalhes.append(f"{'Favorecido' if chave == 'PAYEE' else chave}: {valor}")
    if detalhes:
        texto += f'<br/><font size="6.2" color="#5F6B7A">{_e(" · ".join(detalhes))}</font>'
    return Paragraph(texto, E_BASE)


def _celula_ids(t: Transacao) -> Paragraph:
    linhas = [f"{rotulo}: {valor}" for rotulo, valor in
              (("Doc", t.numero_documento), ("Ref", t.referencia), ("ID", t.fitid))
              if valor and (rotulo == "ID" or valor.strip("0"))]
    return Paragraph("<br/>".join(map(_e, linhas)) or "—", E_SUAVE)


def _tabela_transacoes(extrato: Extrato) -> Table:
    com_saldo = any(t.saldo_apos is not None for t in extrato.transacoes)
    # Data | Histórico | Categoria | Pagador | Recebedor | Identificação | Valor | Saldo
    props = ([0.065, 0.235, 0.075, 0.155, 0.155, 0.11, 0.0975, 0.1075] if com_saldo
             else [0.07, 0.26, 0.08, 0.165, 0.165, 0.12, 0.14])
    cab = ["Data", "Histórico / Complemento", "Categoria", "Pagador", "Recebedor", "Nº doc. / ID", "Valor"]
    cab += ["Saldo"] if com_saldo else []
    dados = [[Paragraph(c, E_CAB_DIR if c in ("Valor", "Saldo") else E_CAB) for c in cab]]
    estilo = _PADDING + [
        ("BACKGROUND", (0, 0), (-1, 0), COR_CABECALHO),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, COR_LINHA),
    ]

    for i, t in enumerate(extrato.transacoes, start=1):
        linha = [
            Paragraph(data_br(t.data), E_BASE),
            _celula_historico(t),
            Paragraph(_e(t.categoria), E_BASE),
            Paragraph(_e(t.pagador), E_BASE),
            Paragraph(_e(t.recebedor), E_BASE),
            _celula_ids(t),
            Paragraph(moeda(t.valor, sinal=True, simbolo=""), E_VALOR[_cor(t.valor)]),
        ]
        if com_saldo:
            cor = COR_DEBITO if (t.saldo_apos or ZERO) < 0 else COR_TEXTO
            linha.append(Paragraph(moeda(t.saldo_apos, simbolo=""), E_NUMERO[cor]))
        dados.append(linha)
        if i % 2 == 0:
            estilo.append(("BACKGROUND", (0, i), (-1, i), COR_ZEBRA))

    if not extrato.transacoes:
        dados.append([Paragraph("Nenhuma movimentação no período.", E_SUAVE)] + [""] * (len(cab) - 1))
        estilo.append(("SPAN", (0, 1), (-1, 1)))
    else:
        total = [
            "", Paragraph(f"<b>TOTAL DO PERÍODO</b> — {len(extrato.transacoes)} lançamento(s)", E_BASE), "",
            Paragraph(f"Entradas: <b>{moeda(extrato.total_creditos)}</b>", E_BASE),
            Paragraph(f"Saídas: <b>{moeda(extrato.total_debitos)}</b>", E_BASE), "",
            Paragraph(moeda(extrato.resultado, sinal=True, simbolo=""), E_VALOR[COR_TEXTO]),
        ]
        if com_saldo:
            total.append(Paragraph(moeda(extrato.transacoes[-1].saldo_apos, simbolo=""), E_VALOR[COR_TEXTO]))
        dados.append(total)
        estilo += [("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E8EDF3")),
                   ("LINEABOVE", (0, -1), (-1, -1), 0.8, COR_CABECALHO)]

    # splitInRow: um lançamento com texto muito longo pode continuar na página seguinte
    tabela = Table(dados, colWidths=[LARGURA * p for p in props], repeatRows=1, splitInRow=1)
    tabela.setStyle(TableStyle(estilo))
    return tabela


def _tabela_categorias(extrato: Extrato) -> Table:
    grupos = defaultdict(lambda: [ZERO, ZERO, 0])  # entradas, saídas, quantidade
    for t in extrato.transacoes:
        g = grupos[t.categoria or "Outros"]
        g[0 if t.valor > 0 else 1] += t.valor
        g[2] += 1
    dados = [[Paragraph(c, E_CAB if i == 0 else E_CAB_DIR) for i, c in
              enumerate(["Categoria", "Qtd.", "Entradas (R$)", "Saídas (R$)", "Líquido (R$)"])]]
    for cat, (ent, sai, qtd) in sorted(grupos.items(), key=lambda kv: -(abs(kv[1][0]) + abs(kv[1][1]))):
        dados.append([
            Paragraph(_e(cat), E_BASE),
            Paragraph(str(qtd), E_DIREITA),
            Paragraph(moeda(ent, simbolo="") if ent else "—", E_NUMERO[COR_CREDITO]),
            Paragraph(moeda(sai, simbolo="") if sai else "—", E_NUMERO[COR_DEBITO]),
            Paragraph(moeda(ent + sai, sinal=True, simbolo=""), E_DIREITA),
        ])
    t = Table(dados, colWidths=[LARGURA * p for p in (0.2, 0.06, 0.12, 0.12, 0.12)], hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle(_PADDING + [
        ("BACKGROUND", (0, 0), (-1, 0), COR_CABECALHO),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, COR_LINHA),
    ] + [("BACKGROUND", (0, i), (-1, i), COR_ZEBRA) for i in range(2, len(dados), 2)]))
    return t


def _notas(extrato: Extrato) -> list:
    notas = [
        "Pagador e recebedor são identificados a partir do histórico (NAME) e do complemento (MEMO) "
        "informados pelo banco no arquivo OFX. Quando o banco não envia essa informação, a parte é "
        "indicada como “Não identificado no OFX”.",
    ]
    if not extrato.cartao and extrato.saldo_final is not None:
        notas.append("O saldo após cada lançamento é calculado a partir do saldo final informado pelo banco "
                     "(LEDGERBAL), retrocedendo pelos lançamentos do período.")
    if extrato.lancamentos_saldo:
        notas.append(f"{len(extrato.lancamentos_saldo)} linha(s) informativa(s) de saldo presente(s) no OFX "
                     "(ex.: “Saldo Anterior”, “S A L D O”) não foram somadas como movimentação.")
    notas.append("Documento gerado automaticamente a partir de arquivo OFX; não substitui o extrato oficial do banco.")
    return [Paragraph("• " + _e(n), E_NOTA) for n in notas]


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------

def gerar_pdf(arquivo: ArquivoOFX, destino: Union[str, BinaryIO, None] = None) -> bytes:
    """Gera o PDF. `destino` pode ser um caminho, um arquivo aberto/BytesIO ou None. Retorna os bytes."""
    buffer = BytesIO()
    titulo_doc = "Extrato OFX"
    if arquivo.extratos:
        e0 = arquivo.extratos[0]
        titulo_doc = f"Extrato {e0.banco_nome} - Ag {e0.agencia} Conta {e0.conta}"
    doc = SimpleDocTemplate(
        buffer, pagesize=PAGINA, leftMargin=MARGEM, rightMargin=MARGEM, topMargin=MARGEM,
        bottomMargin=14 * mm, title=titulo_doc, author="ofx2pdf", subject="Extrato bancário convertido de OFX",
    )

    historia = []
    for idx, extrato in enumerate(arquivo.extratos):
        if idx:
            historia.append(PageBreak())
        historia += [
            _faixa_titulo(extrato), Spacer(1, 4 * mm),
            _bloco_info(extrato, arquivo), Spacer(1, 4 * mm),
            _cards_resumo(extrato), Spacer(1, 5 * mm),
            Paragraph("Lançamentos", E_SECAO), _tabela_transacoes(extrato),
        ]
        if extrato.transacoes:
            historia += [Spacer(1, 6 * mm),
                         KeepTogether([Paragraph("Resumo por categoria", E_SECAO), _tabela_categorias(extrato)])]
        historia += [Spacer(1, 5 * mm), KeepTogether(_notas(extrato))]

    rodape = f"Gerado em {datetime.now():%d/%m/%Y %H:%M} a partir de {arquivo.nome_arquivo or 'arquivo OFX'}"
    doc.build(historia, canvasmaker=_fabrica_canvas(rodape))
    pdf = buffer.getvalue()

    if isinstance(destino, str):
        with open(destino, "wb") as f:
            f.write(pdf)
    elif destino is not None:
        destino.write(pdf)
    return pdf
