"""Cadastro de bancos brasileiros (código COMPE) e cores usadas no relatório."""

from __future__ import annotations

import re

BANCOS = {
    "001": "Banco do Brasil",
    "003": "Banco da Amazônia",
    "004": "Banco do Nordeste",
    "021": "Banestes",
    "025": "Banco Alfa",
    "033": "Santander",
    "036": "Bradesco BBI",
    "037": "Banpará",
    "041": "Banrisul",
    "047": "Banese",
    "070": "BRB - Banco de Brasília",
    "077": "Banco Inter",
    "084": "Uniprime",
    "085": "Ailos",
    "089": "Credisan",
    "097": "Credisis",
    "104": "Caixa Econômica Federal",
    "133": "Cresol",
    "136": "Unicred",
    "197": "Stone",
    "208": "BTG Pactual",
    "212": "Banco Original",
    "218": "Banco BS2",
    "237": "Bradesco",
    "246": "Banco ABC Brasil",
    "260": "Nubank",
    "290": "PagBank (PagSeguro)",
    "318": "Banco BMG",
    "323": "Mercado Pago",
    "336": "C6 Bank",
    "341": "Itaú Unibanco",
    "380": "PicPay",
    "389": "Banco Mercantil do Brasil",
    "403": "Cora",
    "422": "Banco Safra",
    "623": "Banco Pan",
    "633": "Banco Rendimento",
    "637": "Banco Sofisa",
    "655": "Banco Votorantim",
    "707": "Banco Daycoval",
    "741": "Banco Ribeirão Preto",
    "745": "Citibank",
    "746": "Banco Modal",
    "748": "Sicredi",
    "752": "BNP Paribas",
    "756": "Sicoob",
}

# Cor principal / cor de destaque de cada banco (usadas apenas no cabeçalho do PDF)
CORES = {
    "001": ("#003DA5", "#FCE600"),   # Banco do Brasil
    "756": ("#003641", "#00AE9D"),   # Sicoob
    "748": ("#3FA110", "#FFFFFF"),   # Sicredi
    "104": ("#005CA9", "#F39200"),   # Caixa
    "237": ("#CC092F", "#FFFFFF"),   # Bradesco
    "341": ("#EC7000", "#003399"),   # Itaú
    "033": ("#EC0000", "#FFFFFF"),   # Santander
    "260": ("#820AD1", "#FFFFFF"),   # Nubank
    "077": ("#FF7A00", "#FFFFFF"),   # Inter
}
COR_PADRAO = ("#1F3A5F", "#4FA3D1")

# Termos presentes em SIGNON/ORG ou no FID que identificam o banco quando
# o BANKID vem vazio ou fora do padrão.
_APELIDOS = [
    (re.compile(r"banco do brasil|\bbb\b", re.I), "001"),
    (re.compile(r"sicoob|bancoob", re.I), "756"),
    (re.compile(r"sicredi", re.I), "748"),
    (re.compile(r"caixa|\bcef\b", re.I), "104"),
    (re.compile(r"bradesco", re.I), "237"),
    (re.compile(r"ita[uú]", re.I), "341"),
    (re.compile(r"santander", re.I), "033"),
    (re.compile(r"nubank|nu pagamentos", re.I), "260"),
    (re.compile(r"\binter\b", re.I), "077"),
]


def normalizar_codigo(bankid: str) -> str:
    """'0756', '756-0', '001' -> '756', '001'.

    Alguns bancos preenchem o BANKID com o ISPB (8 dígitos). Nesse caso
    devolvemos o valor original, que será resolvido pelos apelidos.
    """
    if not bankid:
        return ""
    digitos = re.sub(r"\D", "", bankid.split("-")[0])
    if not digitos:
        return ""
    if len(digitos) > 4:
        return digitos
    return digitos.lstrip("0").zfill(3)


def identificar_banco(bankid: str, org: str = "", fid: str = "") -> tuple[str, str]:
    """Retorna (codigo, nome) do banco."""
    codigo = normalizar_codigo(bankid)
    if codigo in BANCOS:
        return codigo, BANCOS[codigo]
    # ISPB conhecidos
    ispb = {"00000000": "001", "02038232": "756", "00360305": "104", "60746948": "237",
            "60701190": "341", "90400888": "033", "18236120": "260", "01181521": "748",
            "00416968": "077"}
    if codigo in ispb:
        c = ispb[codigo]
        return c, BANCOS[c]
    for texto in (org, fid):
        fid_cod = normalizar_codigo(texto) if texto and texto.strip().isdigit() else ""
        if fid_cod in BANCOS:
            return fid_cod, BANCOS[fid_cod]
        for padrao, c in _APELIDOS:
            if texto and padrao.search(texto):
                return c, BANCOS[c]
    nome = org.strip() if org else ""
    return codigo, nome or (f"Banco {codigo}" if codigo else "Banco não identificado")


def cores_do_banco(codigo: str) -> tuple[str, str]:
    return CORES.get(codigo, COR_PADRAO)
