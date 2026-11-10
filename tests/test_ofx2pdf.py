import io
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from ofx2pdf.contraparte import extrair_contraparte, extrair_documento
from ofx2pdf.parser import ErroOFX, ler_ofx, ler_ofx_bytes, parse_data, parse_valor
from ofx2pdf.pdf import gerar_pdf, moeda

EX = Path(__file__).resolve().parent.parent / "exemplos"
TITULAR = "Empresa Teste Ltda"


def test_banco_do_brasil():
    arq = ler_ofx(str(EX / "banco_do_brasil_exemplo.ofx"), titular=TITULAR)
    e = arq.extratos[0]
    assert (e.banco_codigo, e.banco_nome) == ("001", "Banco do Brasil")
    assert (e.agencia, e.conta) == ("1234-5", "98765-4")
    assert len(e.transacoes) == 8
    assert len(e.lancamentos_saldo) == 2  # "Saldo Anterior" e "S A L D O" não entram na soma
    assert e.saldo_inicial_calculado == Decimal("4001.00")
    assert e.transacoes[-1].saldo_apos == e.saldo_final.valor

    pix = e.transacoes[0]
    assert pix.categoria == "PIX"
    assert pix.contraparte == "Maria Aparecida da Silva"
    assert pix.contraparte_doc == "CPF 529.982.247-25"
    assert pix.pagador.startswith("Maria Aparecida da Silva")
    assert pix.recebedor == TITULAR

    enviado = e.transacoes[1]
    assert enviado.pagador == TITULAR
    assert enviado.recebedor == "Padaria Pão Quente Ltda"  # acentos preservados (cp1252)

    tarifa = next(t for t in e.transacoes if t.categoria == "Tarifa")
    assert tarifa.recebedor == "Banco do Brasil"

    darf = next(t for t in e.transacoes if t.categoria == "Tributo")
    assert darf.recebedor == "Receita Federal"

    pj = e.transacoes[-1]
    assert pj.contraparte_doc == "CNPJ 11.222.333/0001-81"
    assert pj.contraparte == "TECNOLOGIA BETA SERVICOS LTDA"


def test_sicoob():
    e = ler_ofx(str(EX / "sicoob_exemplo.ofx")).extratos[0]
    assert (e.banco_codigo, e.banco_nome) == ("756", "Sicoob")
    assert e.saldo_inicial_calculado == Decimal("5000.00")
    valores = [t.valor for t in e.transacoes]
    assert Decimal("-1250.00") in valores  # "-1.250,00"
    por_fitid = {t.fitid: t for t in e.transacoes}
    assert por_fitid["756001"].contraparte == "JOAO PEREIRA DOS SANTOS"
    assert por_fitid["756001"].contraparte_doc == "CPF ***.456.789-**"
    assert por_fitid["756002"].recebedor == "DISTRIBUIDORA GAMA LTDA (CNPJ 12.345.678/0001-95)"
    assert por_fitid["756003"].recebedor == "Não identificado no OFX"
    assert por_fitid["756005"].categoria == "Tarifa"
    assert por_fitid["756006"].pagador == "ANA CAROLINA MENDES"
    assert por_fitid["756007"].recebedor == "FORNECEDORA DELTA EIRELI"
    assert por_fitid["756008"].categoria == "Rendimento"


def test_ofx2_xml_payee_e_conta_destino():
    e = ler_ofx(str(EX / "generico_ofx2_xml.ofx")).extratos[0]
    assert e.banco_nome == "Bradesco"
    assert e.saldo_disponivel.valor == Decimal("1250.00")
    assert e.transacoes[0].pagador == "Cliente & Filhos Ltda"
    assert e.transacoes[1].recebedor == "Banco 001 / Ag 9999 / Conta 11111-1"


def test_cartao_de_credito():
    ofx = b"""OFXHEADER:100
DATA:OFXSGML
<OFX><CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS><CURDEF>BRL
<CCACCTFROM><ACCTID>1234********5678</CCACCTFROM>
<BANKTRANLIST><DTSTART>20261001<DTEND>20261031
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261003<TRNAMT>-99.90<FITID>1<MEMO>NETFLIX.COM
</STMTTRN></BANKTRANLIST>
<LEDGERBAL><BALAMT>-99.90<DTASOF>20261031</LEDGERBAL>
</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>"""
    arq = ler_ofx_bytes(ofx, "cartao.ofx")
    e = arq.extratos[0]
    assert e.cartao
    assert e.transacoes[0].recebedor == "NETFLIX.COM"
    assert e.transacoes[0].saldo_apos is None
    assert gerar_pdf(arq).startswith(b"%PDF")


def test_arquivo_invalido():
    with pytest.raises(ErroOFX):
        ler_ofx_bytes(b"isto nao e um ofx")


@pytest.mark.parametrize("entrada,esperado", [
    ("20261031120000[-3:BRT]", (2026, 10, 31, 12)),
    ("20261031", (2026, 10, 31, 0)),
    ("20261031083000.000[-03:EST]", (2026, 10, 31, 8)),
])
def test_parse_data(entrada, esperado):
    d = parse_data(entrada)
    assert (d.year, d.month, d.day, d.hour) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    ("-1.250,00", "-1250.00"), ("1250.5", "1250.5"), ("1,234.56", "1234.56"), ("-45,00", "-45.00"),
])
def test_parse_valor(entrada, esperado):
    assert parse_valor(entrada) == Decimal(esperado)


def test_documentos():
    assert extrair_documento("PIX 11222333000181 FULANO")[0] == "CNPJ 11.222.333/0001-81"
    assert extrair_documento("conta 12345678901")[0] == ""  # CPF com DV inválido não é aceito
    assert extrair_contraparte("Pix - Enviado", "03/01 10:22 Fulano de Tal")[0] == "Fulano de Tal"
    assert extrair_contraparte("", "PIX RECEB.OUTRA IF")[0] == ""


def test_moeda():
    assert moeda(Decimal("1234.5")) == "R$ 1.234,50"
    assert moeda(Decimal("-0.99"), simbolo="") == "-0,99"
    assert moeda(Decimal("10"), sinal=True) == "+R$ 10,00"


def test_pdf_contem_informacoes():
    from pypdf import PdfReader
    arq = ler_ofx(str(EX / "sicoob_exemplo.ofx"), titular=TITULAR)
    pdf = gerar_pdf(arq, titular=TITULAR)
    texto = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    for trecho in ["Sicoob", "123456-7", "JOAO PEREIRA DOS SANTOS", "DISTRIBUIDORA GAMA LTDA",
                   "8.638,14", "Pagador", "Recebedor", TITULAR]:
        assert trecho in texto


def test_web():
    pytest.importorskip("flask")
    from ofx2pdf.web import app
    cliente = app.test_client()
    assert cliente.get("/").status_code == 200

    dados = (EX / "banco_do_brasil_exemplo.ofx").read_bytes()
    r = cliente.post("/converter", data={"arquivos": (io.BytesIO(dados), "bb.ofx")},
                     content_type="multipart/form-data")
    assert r.status_code == 200 and r.data.startswith(b"%PDF")

    r = cliente.post("/converter", data={"arquivos": [(io.BytesIO(dados), "a.ofx"), (io.BytesIO(dados), "b.ofx")]},
                     content_type="multipart/form-data")
    assert r.mimetype == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(r.data)).namelist()) == ["a.pdf", "b.pdf"]
