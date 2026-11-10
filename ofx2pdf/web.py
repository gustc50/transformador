"""Interface web local: envie um ou mais arquivos OFX e baixe o PDF (ou um ZIP).

Segurança (uso local):
- escuta somente em 127.0.0.1 (não fica acessível por outros computadores da rede);
- recusa requisições com cabeçalho Host diferente de localhost/127.0.0.1 (proteção contra DNS rebinding);
- arquivos são processados em memória, nunca gravados em disco;
- nomes de arquivo enviados são saneados antes de ir para o ZIP / download;
- limite de 50 MB por envio; erros internos não expõem detalhes técnicos;
- cabeçalhos HTTP de segurança (CSP, X-Frame-Options, nosniff, no-referrer).
"""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile

from flask import Flask, abort, render_template, request, send_file

from .parser import ErroOFX, ler_ofx_bytes
from .pdf import gerar_pdf

HOST = "127.0.0.1"
HOSTS_PERMITIDOS = {"127.0.0.1", "localhost"}
LIMITE_MB = 50

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = LIMITE_MB * 1024 * 1024
log = logging.getLogger(__name__)


def _nome_seguro(nome: str, extensao: str) -> str:
    """'..\\..\\x<y>.ofx' -> 'x_y_.pdf' (sem caminhos nem caracteres especiais)."""
    base = os.path.splitext(re.split(r"[\\/]", nome or "")[-1])[0]
    base = re.sub(r"[^\w\- ]", "_", base).strip(" ._")[:80]
    return f"{base or 'extrato'}{extensao}"


def _pagina(erro: str = "", detalhes: list[str] | None = None, status: int = 200):
    return render_template("index.html", erro=erro, detalhes=detalhes or [], limite=LIMITE_MB), status


@app.before_request
def _verificar_host():
    if request.host.rsplit(":", 1)[0].strip("[]") not in HOSTS_PERMITIDOS:
        abort(403)


@app.after_request
def _cabecalhos_seguranca(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Aplicacao"] = "ofx2pdf"  # permite reconhecer uma instância já aberta
    return resp


@app.get("/")
def index():
    return _pagina()


@app.post("/converter")
def converter():
    arquivos = [f for f in request.files.getlist("arquivos") if f and f.filename]
    titular = request.form.get("titular", "").strip()[:200]
    if not arquivos:
        return _pagina("Selecione pelo menos um arquivo OFX.", status=400)

    gerados, erros = [], []
    for f in arquivos:
        nome = _nome_seguro(f.filename, ".ofx")
        try:
            gerados.append((_nome_seguro(f.filename, ".pdf"), gerar_pdf(ler_ofx_bytes(f.read(), nome, titular))))
        except ErroOFX as exc:
            erros.append(f"{nome}: {exc}")
        except Exception:  # noqa: BLE001 - qualquer falha vira mensagem amigável, sem expor detalhes
            log.exception("Falha inesperada ao converter %s", nome)
            erros.append(f"{nome}: não foi possível converter este arquivo.")

    if not gerados:
        return _pagina("Nenhum arquivo pôde ser convertido.", erros, status=400)

    if len(gerados) == 1 and not erros:
        nome, pdf = gerados[0]
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=nome)

    buffer = io.BytesIO()
    usados: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, pdf in gerados:
            final, n = nome, 2
            while final in usados:
                final, n = nome[:-4] + f"_{n}.pdf", n + 1
            usados.add(final)
            z.writestr(final, pdf)
        if erros:
            z.writestr("ERROS.txt", "\n".join(erros))
    buffer.seek(0)
    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name="extratos_pdf.zip")


@app.errorhandler(413)
def _muito_grande(_):
    return _pagina(f"Envio muito grande (limite de {LIMITE_MB} MB).", status=413)


def _ja_aberto(url: str) -> bool:
    """True se o próprio conversor já está rodando nesse endereço."""
    from urllib.request import urlopen

    try:
        # a URL é sempre http://127.0.0.1:<porta>, montada aqui mesmo
        with urlopen(url, timeout=2) as resp:  # nosec B310
            return resp.headers.get("X-Aplicacao") == "ofx2pdf"
    except OSError:
        return False


def executar(porta: int = 5000, abrir_navegador: bool = True) -> int:
    import socket
    import threading
    import webbrowser

    url = f"http://{HOST}:{porta}"
    with socket.socket() as s:
        porta_ocupada = s.connect_ex((HOST, porta)) == 0
    if porta_ocupada:
        if _ja_aberto(url):
            print(f"O conversor já está aberto em {url}")
            if abrir_navegador:
                webbrowser.open(url)
            return 0
        print(f"A porta {porta} está sendo usada por outro programa.")
        print("Feche o outro programa ou use outra porta, por exemplo: --porta 5050")
        return 1

    print(f"Conversor OFX -> PDF disponível em {url}")
    print("Mantenha esta janela aberta enquanto usa. Para encerrar, feche-a ou pressione Ctrl+C.")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # esconde o log de cada requisição
    if abrir_navegador:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=porta, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
