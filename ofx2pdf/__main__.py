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
    """Expande pastas e curingas em uma lista ordenada e sem repetições de arquivos .ofx."""
    arquivos = set()
    for entrada in entradas:
        if os.path.isdir(entrada):
            arquivos.update(os.path.join(entrada, n) for n in os.listdir(entrada) if n.lower().endswith(".ofx"))
        else:
            arquivos.update(glob.glob(entrada) or [entrada])
    return sorted(arquivos)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ofx2pdf", description="Converte extratos OFX em relatórios PDF.")
    ap.add_argument("entradas", nargs="*", help="arquivo(s) .ofx ou pasta(s) contendo arquivos .ofx")
    ap.add_argument("-o", "--saida", help="arquivo PDF de saída (1 entrada) ou pasta de saída")
    ap.add_argument("-t", "--titular", default="", help="nome do titular da conta (o OFX normalmente não traz)")
    ap.add_argument("--web", action="store_true", help="inicia a interface web")
    ap.add_argument("--porta", type=int, default=5000, help="porta da interface web (padrão 5000)")
    ap.add_argument("--sem-navegador", action="store_true", help="não abre o navegador automaticamente")
    args = ap.parse_args(argv)

    if args.web:
        from .web import executar
        return executar(porta=args.porta, abrir_navegador=not args.sem_navegador)

    arquivos = _coletar(args.entradas)
    if not arquivos:
        ap.print_help()
        return 1

    # --saida é um arquivo .pdf apenas quando há uma única entrada; caso contrário é uma pasta
    pasta_saida = None
    if args.saida and (len(arquivos) > 1 or not args.saida.lower().endswith(".pdf")):
        if args.saida.lower().endswith(".pdf"):
            print("Com vários arquivos de entrada, --saida deve ser uma pasta.", file=sys.stderr)
            return 1
        pasta_saida = args.saida
        os.makedirs(pasta_saida, exist_ok=True)

    erros = 0
    for caminho in arquivos:
        base = os.path.splitext(os.path.basename(caminho))[0] + ".pdf"
        if pasta_saida:
            destino = os.path.join(pasta_saida, base)
        else:
            destino = args.saida or os.path.join(os.path.dirname(caminho), base)
        try:
            gerar_pdf(ler_ofx(caminho, titular=args.titular), destino)
            print(f"OK   {caminho} -> {destino}")
        except (ErroOFX, OSError) as exc:
            erros += 1
            print(f"ERRO {caminho}: {exc}", file=sys.stderr)
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
