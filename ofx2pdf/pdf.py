"""Geração do relatório PDF a partir de um ArquivoOFX."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import Optional, Union
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .bancos import cores_do_banco
from .models import ArquivoOFX, Extrato, Transacao

PAGINA = landscape(A4)
MARGEM = 12 * mm

COR_CREDITO = colors.HexColor("#1B7F3B")
COR_DEBITO = colors.HexColor("#C0392B")
COR_TEXTO_SUAVE = colors.HexColor("#5F6B7A")
COR_LINHA = colors.HexColor("#D9DEE5")
COR_ZEBRA = colors.HexColor("#F5F7FA")

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
# Formatação
# --------------------------------------------------------------------------

def moeda(valor: Optional[Decimal], sinal: bool = False, simbolo: str = "R$") -> str:
    if valor is None:
        return "—"
    v = Decimal(valor).quantize(Decimal("0.01"))
    texto = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    prefixo = ""
    if v < 0:
        prefixo = "-"
    elif sinal and v > 0:
        prefixo = "+"
    return f"{prefixo}{simbolo} {texto}".strip() if simbolo else f"{prefixo}{texto}"


def data_br(d: Optional[datetime], hora: bool = False) -> str:
    if d is None:
        return "—"
    if hora and (d.hour or d.minute):
        return d.strftime("%d/%m/%Y %H:%M")
    return d.strftime("%d/%m/%Y")


def _e(texto: str) -> str:
    return escape(texto or "")


# --------------------------------------------------------------------------
# Estilos
# --------------------------------------------------------------------------

def _estilos():
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=7.4, leading=9, textColor=colors.HexColor("#1D2733"))
    return {
        "base": base,
        "negrito": ParagraphStyle("negrito", parent=base, fontName="Helvetica-Bold"),
        "suave": ParagraphStyle("suave", parent=base, fontSize=6.4, leading=7.8, textColor=COR_TEXTO_SUAVE),
        "direita": ParagraphStyle("direita", parent=base, alignment=TA_RIGHT),
        "centro": ParagraphStyle("centro", parent=base, alignment=TA_CENTER),
        "cab": ParagraphStyle("cab", parent=base, fontName="Helvetica-Bold", textColor=colors.white, fontSize=7.4),
        "cab_dir": ParagraphStyle("cab_dir", parent=base, fontName="Helvetica-Bold", textColor=colors.white,
                                  fontSize=7.4, alignment=TA_RIGHT),
        "titulo_secao": ParagraphStyle("titulo_secao", parent=base, fontName="Helvetica-Bold", fontSize=10.5,
                                       leading=13, spaceBefore=4, spaceAfter=4),
        "rotulo": ParagraphStyle("rotulo", parent=base, fontSize=6.6, leading=8, textColor=COR_TEXTO_SUAVE),
        "info": ParagraphStyle("info", parent=base, fontName="Helvetica-Bold", fontSize=8.6, leading=10.5),
        "card_valor": ParagraphStyle("card_valor", parent=base, fontName="Helvetica-Bold", fontSize=11.5,
                                     leading=14),
        "nota": ParagraphStyle("nota", parent=base, fontSize=6.8, leading=8.5, textColor=COR_TEXTO_SUAVE,
                               alignment=TA_LEFT),
    }


# --------------------------------------------------------------------------
# Canvas com "Página X de Y" e cabeçalho colorido
# --------------------------------------------------------------------------

class _CanvasNumerado(rl_canvas.Canvas):
    rodape_texto = ""

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
            self._rodape(total)
            super().showPage()
        super().save()

    def _rodape(self, total: int):
        largura, _ = self._pagesize
        self.saveState()
        self.setStrokeColor(COR_LINHA)
        self.setLineWidth(0.5)
        self.line(MARGEM, 9 * mm, largura - MARGEM, 9 * mm)
        self.setFont("Helvetica", 6.8)
        self.setFillColor(COR_TEXTO_SUAVE)
        self.drawString(MARGEM, 5.5 * mm, self.rodape_texto)
        self.drawRightString(largura - MARGEM, 5.5 * mm, f"Página {self._pageNumber} de {total}")
        self.restoreState()


def _fabrica_canvas(rodape: str):
    class C(_CanvasNumerado):
        rodape_texto = rodape
    return C


# --------------------------------------------------------------------------
# Blocos do relatório
# --------------------------------------------------------------------------

def _faixa_titulo(extrato: Extrato, arquivo: ArquivoOFX, est) -> Table:
    cor1, cor2 = cores_do_banco(extrato.banco_codigo)
    cor_texto = "#FFFFFF"
    titulo = "Fatura de Cartão de Crédito" if extrato.cartao else "Extrato Bancário"
    banco = extrato.banco_nome + (f" ({extrato.banco_codigo})" if extrato.banco_codigo else "")
    esquerda = Paragraph(
        f'<font size="16" color="{cor_texto}"><b>{_e(titulo)}</b></font><br/>'
        f'<font size="10.5" color="{cor_texto}">{_e(banco)}</font>',
        ParagraphStyle("t", fontName="Helvetica", leading=19),
    )
    periodo = f"{data_br(extrato.inicio)} a {data_br(extrato.fim)}"
    direita = Paragraph(
        f'<font size="7.5" color="{cor_texto}">PERÍODO</font><br/>'
        f'<font size="11" color="{cor_texto}"><b>{periodo}</b></font>',
        ParagraphStyle("p", fontName="Helvetica", leading=14, alignment=TA_RIGHT),
    )
    largura = PAGINA[0] - 2 * MARGEM
    t = Table([[esquerda, direita]], colWidths=[largura * 0.65, largura * 0.35], rowHeights=[17 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(cor1)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("LINEBELOW", (0, 0), (-1, -1), 3, colors.HexColor(cor2)),
    ]))
    return t


def _bloco_info(extrato: Extrato, arquivo: ArquivoOFX, titular: str, est) -> Table:
    def cel(rotulo, valor):
        return [Paragraph(_e(rotulo.upper()), est["rotulo"]), Paragraph(_e(valor or "—"), est["info"])]

    tipo = TIPOS_CONTA.get(extrato.tipo_conta, extrato.tipo_conta or "—")
    campos = [
        ("Banco", f"{extrato.banco_nome}" + (f" — {extrato.banco_codigo}" if extrato.banco_codigo else "")),
        ("Agência", extrato.agencia),
        ("Cartão" if extrato.cartao else "Conta", extrato.conta),
        ("Tipo de conta", tipo),
        ("Titular", titular or "Não informado no OFX"),
        ("Moeda", extrato.moeda),
        ("Arquivo OFX", arquivo.nome_arquivo or "—"),
        ("Gerado pelo banco em", data_br(arquivo.data_geracao, hora=True)),
    ]
    linhas = []
    for i in range(0, len(campos), 4):
        grupo = campos[i:i + 4]
        linhas.append([cel(r, v) for r, v in grupo])
    largura = PAGINA[0] - 2 * MARGEM
    t = Table(linhas, colWidths=[largura / 4] * 4)
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


def _cards_resumo(extrato: Extrato, est) -> Table:
    resultado = extrato.total_creditos + extrato.total_debitos
    cards = []
    if not extrato.cartao:
        cards.append(("Saldo inicial (calculado)", moeda(extrato.saldo_inicial_calculado), None, ""))
    cards += [
        ("Entradas", moeda(extrato.total_creditos), COR_CREDITO, f"{extrato.qtd_creditos} lançamento(s)"),
        ("Saídas", moeda(extrato.total_debitos), COR_DEBITO, f"{extrato.qtd_debitos} lançamento(s)"),
        ("Resultado do período", moeda(resultado, sinal=True),
         COR_CREDITO if resultado >= 0 else COR_DEBITO, ""),
    ]
    if extrato.saldo_final is not None:
        rot = "Saldo da fatura" if extrato.cartao else "Saldo final"
        cards.append((rot, moeda(extrato.saldo_final.valor), None, f"em {data_br(extrato.saldo_final.data)}"))
    if extrato.saldo_disponivel is not None:
        cards.append(("Saldo disponível", moeda(extrato.saldo_disponivel.valor), None,
                      f"em {data_br(extrato.saldo_disponivel.data)}"))

    celulas = []
    for rotulo, valor, cor, sub in cards:
        estilo_valor = ParagraphStyle("cv", parent=est["card_valor"],
                                      textColor=cor or colors.HexColor("#1D2733"))
        celulas.append([
            Paragraph(_e(rotulo.upper()), est["rotulo"]),
            Paragraph(_e(valor), estilo_valor),
            Paragraph(_e(sub) or "&nbsp;", est["suave"]),
        ])
    largura = PAGINA[0] - 2 * MARGEM
    gap = 2.5 * mm
    n = len(celulas)
    w = (largura - gap * (n - 1)) / n
    linha, larguras = [], []
    for i, c in enumerate(celulas):
        interno = Table([[x] for x in c], colWidths=[w])
        interno.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, COR_LINHA),
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("TOPPADDING", (0, 0), (0, 0), 2.5 * mm),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 2.5 * mm),
            ("TOPPADDING", (0, 1), (-1, -1), 0.6 * mm),
        ]))
        linha.append(interno)
        larguras.append(w)
        if i < n - 1:
            linha.append("")
            larguras.append(gap)
    t = Table([linha], colWidths=larguras)
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def _celula_historico(t: Transacao, est) -> list:
    partes = []
    titulo = t.historico or t.complemento or "(sem descrição)"
    partes.append(f"<b>{_e(titulo)}</b>")
    if t.historico and t.complemento and t.complemento != t.historico:
        partes.append(_e(t.complemento))
    detalhes = []
    tipo = TIPOS_TRANSACAO.get(t.tipo, t.tipo)
    if tipo:
        detalhes.append(f"Tipo OFX: {tipo}")
    if t.conta_destino:
        detalhes.append(f"Conta destino: {t.conta_destino}")
    if t.data_usuario and t.data and t.data_usuario.date() != t.data.date():
        detalhes.append(f"Data da operação: {data_br(t.data_usuario)}")
    if t.data_disponivel and t.data and t.data_disponivel.date() != t.data.date():
        detalhes.append(f"Disponível em: {data_br(t.data_disponivel)}")
    if t.payee_id:
        detalhes.append(f"ID favorecido: {t.payee_id}")
    if t.sic:
        detalhes.append(f"SIC: {t.sic}")
    for chave, valor in t.extras.items():
        if chave == "PAYEE" and valor == t.historico:
            continue
        rotulo = "Favorecido" if chave == "PAYEE" else chave
        detalhes.append(f"{rotulo}: {valor}")
    texto = "<br/>".join(partes)
    if detalhes:
        texto += f'<br/><font size="6.2" color="#5F6B7A">{_e(" · ".join(detalhes))}</font>'
    return Paragraph(texto, est["base"])


def _celula_ids(t: Transacao, est) -> Paragraph:
    linhas = []
    if t.numero_documento and t.numero_documento.strip("0"):
        linhas.append(f"Doc: {t.numero_documento}")
    if t.referencia and t.referencia.strip("0"):
        linhas.append(f"Ref: {t.referencia}")
    if t.fitid:
        linhas.append(f"ID: {t.fitid}")
    return Paragraph(_e("\n".join(linhas)).replace("\n", "<br/>") or "—", est["suave"])


def _tabela_transacoes(extrato: Extrato, est) -> Table:
    com_saldo = any(t.saldo_apos is not None for t in extrato.transacoes)
    largura = PAGINA[0] - 2 * MARGEM
    # Data | Histórico | Categoria | Pagador | Recebedor | Identificação | Valor | Saldo
    if com_saldo:
        props = [0.065, 0.235, 0.075, 0.155, 0.155, 0.11, 0.0975, 0.1075]
    else:
        props = [0.07, 0.26, 0.08, 0.165, 0.165, 0.12, 0.14]
    larguras = [largura * p for p in props]

    cab = ["Data", "Histórico / Complemento", "Categoria", "Pagador", "Recebedor", "Nº doc. / ID", "Valor"]
    if com_saldo:
        cab.append("Saldo")
    dados = [[Paragraph(c, est["cab_dir"] if c in ("Valor", "Saldo") else est["cab"]) for c in cab]]

    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2B3A4A")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1.6 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.6 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 1.4 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.4 * mm),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, COR_LINHA),
    ]

    for i, t in enumerate(extrato.transacoes, start=1):
        cor = COR_CREDITO if t.valor > 0 else COR_DEBITO if t.valor < 0 else colors.black
        estilo_valor = ParagraphStyle("v", parent=est["direita"], textColor=cor, fontName="Helvetica-Bold")
        linha = [
            Paragraph(data_br(t.data), est["base"]),
            _celula_historico(t, est),
            Paragraph(_e(t.categoria), est["base"]),
            Paragraph(_e(t.pagador), est["base"]),
            Paragraph(_e(t.recebedor), est["base"]),
            _celula_ids(t, est),
            Paragraph(moeda(t.valor, sinal=True, simbolo=""), estilo_valor),
        ]
        if com_saldo:
            cor_saldo = COR_DEBITO if (t.saldo_apos or 0) < 0 else colors.HexColor("#1D2733")
            linha.append(Paragraph(moeda(t.saldo_apos, simbolo=""),
                                   ParagraphStyle("s", parent=est["direita"], textColor=cor_saldo)))
        dados.append(linha)
        if i % 2 == 0:
            estilo.append(("BACKGROUND", (0, i), (-1, i), COR_ZEBRA))

    if not extrato.transacoes:
        vazio = [Paragraph("Nenhuma movimentação no período.", est["suave"])] + [""] * (len(cab) - 1)
        dados.append(vazio)
        estilo.append(("SPAN", (0, 1), (-1, 1)))
    else:
        total = extrato.total_creditos + extrato.total_debitos
        rodape = [Paragraph("", est["base"]),
                  Paragraph(f"<b>TOTAL DO PERÍODO</b> — {len(extrato.transacoes)} lançamento(s)", est["base"]),
                  "", Paragraph(f"Entradas: <b>{moeda(extrato.total_creditos)}</b>", est["base"]),
                  Paragraph(f"Saídas: <b>{moeda(extrato.total_debitos)}</b>", est["base"]), "",
                  Paragraph(f"<b>{moeda(total, sinal=True, simbolo='')}</b>", est["direita"])]
        if com_saldo:
            final = extrato.transacoes[-1].saldo_apos
            rodape.append(Paragraph(f"<b>{moeda(final, simbolo='')}</b>", est["direita"]))
        dados.append(rodape)
        n = len(dados) - 1
        estilo += [("BACKGROUND", (0, n), (-1, n), colors.HexColor("#E8EDF3")),
                   ("LINEABOVE", (0, n), (-1, n), 0.8, colors.HexColor("#2B3A4A"))]

    tabela = Table(dados, colWidths=larguras, repeatRows=1)
    tabela.setStyle(TableStyle(estilo))
    return tabela


def _tabela_categorias(extrato: Extrato, est) -> Optional[Table]:
    if not extrato.transacoes:
        return None
    grupos = defaultdict(lambda: [Decimal("0"), Decimal("0"), 0])
    for t in extrato.transacoes:
        g = grupos[t.categoria or "Outros"]
        if t.valor > 0:
            g[0] += t.valor
        else:
            g[1] += t.valor
        g[2] += 1
    dados = [[Paragraph(c, est["cab"] if i == 0 else est["cab_dir"]) for i, c in
              enumerate(["Categoria", "Qtd.", "Entradas (R$)", "Saídas (R$)", "Líquido (R$)"])]]
    for cat, (ent, sai, qtd) in sorted(grupos.items(), key=lambda kv: -(abs(kv[1][0]) + abs(kv[1][1]))):
        dados.append([
            Paragraph(_e(cat), est["base"]),
            Paragraph(str(qtd), est["direita"]),
            Paragraph(moeda(ent, simbolo="") if ent else "—", ParagraphStyle("e", parent=est["direita"], textColor=COR_CREDITO)),
            Paragraph(moeda(sai, simbolo="") if sai else "—", ParagraphStyle("s", parent=est["direita"], textColor=COR_DEBITO)),
            Paragraph(moeda(ent + sai, sinal=True, simbolo=""), est["direita"]),
        ])
    largura = PAGINA[0] - 2 * MARGEM
    t = Table(dados, colWidths=[largura * 0.2, largura * 0.06, largura * 0.12, largura * 0.12, largura * 0.12],
              hAlign="LEFT", repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2B3A4A")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, COR_LINHA),
        ("TOPPADDING", (0, 0), (-1, -1), 1.2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2 * mm),
    ]
    for i in range(2, len(dados), 2):
        estilo.append(("BACKGROUND", (0, i), (-1, i), COR_ZEBRA))
    t.setStyle(TableStyle(estilo))
    return t


def _notas(extrato: Extrato, est) -> list:
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
    return [Paragraph("• " + _e(n), est["nota"]) for n in notas]


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------

def gerar_pdf(arquivo: ArquivoOFX, destino: Union[str, BytesIO, None] = None, titular: str = "") -> bytes:
    """Gera o PDF. `destino` pode ser um caminho, um BytesIO ou None. Retorna os bytes do PDF."""
    est = _estilos()
    buffer = BytesIO()
    titulo_doc = "Extrato OFX"
    if arquivo.extratos:
        e0 = arquivo.extratos[0]
        titulo_doc = f"Extrato {e0.banco_nome} - Ag {e0.agencia} Conta {e0.conta}"
    doc = SimpleDocTemplate(
        buffer, pagesize=PAGINA, leftMargin=MARGEM, rightMargin=MARGEM,
        topMargin=MARGEM, bottomMargin=14 * mm, title=titulo_doc, author="ofx2pdf",
        subject="Extrato bancário convertido de OFX",
    )

    historia = []
    for idx, extrato in enumerate(arquivo.extratos):
        if idx:
            historia.append(PageBreak())
        historia.append(_faixa_titulo(extrato, arquivo, est))
        historia.append(Spacer(1, 4 * mm))
        historia.append(_bloco_info(extrato, arquivo, titular, est))
        historia.append(Spacer(1, 4 * mm))
        historia.append(_cards_resumo(extrato, est))
        historia.append(Spacer(1, 5 * mm))
        historia.append(Paragraph("Lançamentos", est["titulo_secao"]))
        historia.append(_tabela_transacoes(extrato, est))
        cat = _tabela_categorias(extrato, est)
        if cat is not None:
            historia.append(Spacer(1, 6 * mm))
            historia.append(KeepTogether([Paragraph("Resumo por categoria", est["titulo_secao"]), cat]))
        historia.append(Spacer(1, 5 * mm))
        historia.append(KeepTogether(_notas(extrato, est)))

    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    rodape = f"Gerado em {agora} a partir de {arquivo.nome_arquivo or 'arquivo OFX'}"
    doc.build(historia, canvasmaker=_fabrica_canvas(rodape))
    pdf = buffer.getvalue()

    if isinstance(destino, str):
        with open(destino, "wb") as f:
            f.write(pdf)
    elif destino is not None:
        destino.write(pdf)
    return pdf
