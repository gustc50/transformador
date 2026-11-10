"""Linha de comando.

Exemplos:
    python -m ofx2pdf extrato.ofx
    python -m ofx2pdf extrato.ofx -o relatorio.pdf --titular "Empresa X Ltda"
    python -m ofx2pdf pasta_com_ofx/ -o saida/
    python -m ofx2pdf --web            (abre a interface no navegador)
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from .parser import ErroOFX, ler_ofx
from .pdf import gerar_pdf


def _coletar(entradas: list[str]) -> list[str]:
    arquivos = []
    for e in entradas:
        if os.path.isdir(e):
            for ext in ("*.ofx", "*.OFX", "*.Ofx"):
                arquivos += glob.glob(os.path.join(e, ext))
        else:
            arquivos += glob.glob(e) or [e]
    vistos, unicos = set(), []
    for a in arquivos:
        chave = os.path.abspath(a)
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(a)
    return sorted(unicos)


def converter(caminho: str, saida: str | None, titular: str = "") -> str:
    arquivo = ler_ofx(caminho, titular=titular)
    if saida is None:
        destino = os.path.splitext(caminho)[0] + ".pdf"
    elif os.path.isdir(saida) or saida.endswith(os.sep):
        os.makedirs(saida, exist_ok=True)
        destino = os.path.join(saida, os.path.splitext(os.path.basename(caminho))[0] + ".pdf")
    else:
        destino = saida
    gerar_pdf(arquivo, destino, titular=titular)
    return destino


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ofx2pdf", description="Converte extratos OFX em relatórios PDF.")
    ap.add_argument("entradas", nargs="*", help="arquivo(s) .ofx ou pasta(s) contendo arquivos .ofx")
    ap.add_argument("-o", "--saida", help="arquivo PDF de saída (1 entrada) ou pasta de saída (várias)")
    ap.add_argument("-t", "--titular", default="", help="nome do titular da conta (o OFX normalmente não traz)")
    ap.add_argument("--web", action="store_true", help="inicia a interface web")
    ap.add_argument("--porta", type=int, default=5000, help="porta da interface web (padrão 5000)")
    args = ap.parse_args(argv)

    if args.web:
        from .web import executar
        executar(porta=args.porta)
        return 0

    arquivos = _coletar(args.entradas)
    if not arquivos:
        ap.print_help()
        return 1

    saida = args.saida
    if saida and len(arquivos) > 1 and not saida.lower().endswith(".pdf"):
        os.makedirs(saida, exist_ok=True)
    elif saida and len(arquivos) > 1:
        print("Com vários arquivos de entrada, --saida deve ser uma pasta.", file=sys.stderr)
        return 1

    erros = 0
    for caminho in arquivos:
        try:
            destino = converter(caminho, saida, args.titular)
            print(f"OK  {caminho} -> {destino}")
        except (ErroOFX, OSError) as exc:
            erros += 1
            print(f"ERRO {caminho}: {exc}", file=sys.stderr)
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
