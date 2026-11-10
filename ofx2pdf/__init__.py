"""ofx2pdf - converte extratos bancários OFX em relatórios PDF."""

from .parser import ler_ofx, ler_ofx_bytes
from .pdf import gerar_pdf

__all__ = ["ler_ofx", "ler_ofx_bytes", "gerar_pdf"]
__version__ = "1.0.0"
