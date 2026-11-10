"""Interface web simples: envie um ou mais arquivos OFX e baixe o PDF (ou um ZIP)."""

from __future__ import annotations

import io
import os
import zipfile

from flask import Flask, render_template, request, send_file

from .parser import ErroOFX, ler_ofx_bytes
from .pdf import gerar_pdf

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB por envio


def _nome_pdf(nome_ofx: str) -> str:
    base = os.path.splitext(os.path.basename(nome_ofx or "extrato"))[0] or "extrato"
    return f"{base}.pdf"


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/converter")
def converter():
    arquivos = [f for f in request.files.getlist("arquivos") if f and f.filename]
    titular = request.form.get("titular", "").strip()
    if not arquivos:
        return render_template("index.html", erro="Selecione pelo menos um arquivo OFX."), 400

    gerados, erros = [], []
    for f in arquivos:
        try:
            ofx = ler_ofx_bytes(f.read(), os.path.basename(f.filename), titular=titular)
            gerados.append((_nome_pdf(f.filename), gerar_pdf(ofx, titular=titular)))
        except ErroOFX as exc:
            erros.append(f"{f.filename}: {exc}")

    if not gerados:
        return render_template("index.html", erro="Nenhum arquivo pôde ser convertido.", detalhes=erros), 400

    if len(gerados) == 1 and not erros:
        nome, pdf = gerados[0]
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=nome)

    buffer = io.BytesIO()
    usados = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, pdf in gerados:
            final, n = nome, 2
            while final in usados:
                final = nome.replace(".pdf", f"_{n}.pdf")
                n += 1
            usados.add(final)
            z.writestr(final, pdf)
        if erros:
            z.writestr("ERROS.txt", "\n".join(erros))
    buffer.seek(0)
    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name="extratos_pdf.zip")


@app.errorhandler(413)
def muito_grande(_):
    return render_template("index.html", erro="Envio muito grande (limite de 50 MB)."), 413


def executar(porta: int = 5000, abrir_navegador: bool = True) -> None:
    url = f"http://127.0.0.1:{porta}"
    print(f"Interface disponível em {url}  (Ctrl+C para encerrar)")
    if abrir_navegador:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=porta, debug=False)


if __name__ == "__main__":
    executar()
