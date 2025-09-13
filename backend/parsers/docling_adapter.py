# backend/parsers/docling_adapter.py
import io, os, tempfile
from typing import List

def _split_markdown(md_text: str, target_chars: int = 2000) -> List[str]:
    md_text = (md_text or "").strip()
    if not md_text:
        return []
    # separadores de página comunes
    for sep in ("\n\\pagebreak\n", "\n---\n", "\n# Página ", "\n# Page "):
        if sep in md_text:
            parts = [p.strip() for p in md_text.split(sep) if p.strip()]
            return parts
    # fallback: troceo por longitud
    parts, start, n = [], 0, len(md_text)
    while start < n:
        end = min(start + target_chars, n)
        parts.append(md_text[start:end])
        start = end
    return parts

def _fallback_pdf(buffer: bytes) -> List[str]:
    try:
        from pypdf import PdfReader
    except Exception:
        return []
    reader = PdfReader(io.BytesIO(buffer))
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception:
            pages.append("")
    return pages

def _fallback_docx(buffer: bytes) -> List[str]:
    try:
        import docx  # python-docx
    except Exception:
        return []
    with tempfile.NamedTemporaryFile(suffix=".docx") as tmp:
        tmp.write(buffer); tmp.flush()
        d = docx.Document(tmp.name)
    return ["\n".join([p.text for p in d.paragraphs])]

def extract_pages_and_formulas(buffer: bytes, ext: str) -> tuple[List[str], List[dict]]:
    """
    Extrae páginas y fórmulas de un documento usando Docling.

    Returns:
        tuple: (pages, formulas)
        - pages: Lista de strings con el contenido de cada página
        - formulas: Lista de diccionarios con información de fórmulas
    """
    ext = (ext or "").lower().lstrip(".")
    use_docling = os.getenv("DOC_PARSER", "docling").lower() == "docling"

    print(f"[docling] Processing {ext} file, use_docling={use_docling}")
    formulas = []

    if use_docling:
        try:
            print("[docling] Attempting to use Docling...")
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions

            with tempfile.NamedTemporaryFile(suffix=f".{ext or 'bin'}") as tmp:
                tmp.write(buffer); tmp.flush()
                print(f"[docling] Created temp file: {tmp.name}")

                if ext == "pdf":
                    do_ocr = os.getenv("DOCLING_OCR", "on").lower() in ("on", "true", "1")
                    langs = os.getenv("DOCLING_OCR_LANGS", "auto").split(",")
                    force_full = os.getenv("DOCLING_FORCE_FULL_OCR", "true").lower() in ("on", "true", "1")
                    do_formula_enrichment = os.getenv("DOCLING_FORMULA_ENRICHMENT", "false").lower() in ("on", "true", "1")

                    print(f"[docling] PDF config: OCR={do_ocr}, langs={langs}, force_full={force_full}, formula_enrichment={do_formula_enrichment}")

                    pipe = PdfPipelineOptions(
                        do_ocr=do_ocr,
                        force_full_page_ocr=force_full,
                        do_formula_enrichment=do_formula_enrichment,
                        ocr_options=TesseractCliOcrOptions(
                            lang=langs,
                            force_full_page_ocr=force_full,
                        ),
                        artifacts_path=os.getenv("DOCLING_ARTIFACTS_PATH"),
                        # Optimizaciones para reducir uso de memoria
                        max_pages=20,  # Limitar páginas para evitar sobrecarga
                        max_chars_per_page=25000,  # Reducir caracteres por página
                    )
                    converter = DocumentConverter(
                        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipe)}
                    )
                else:
                    # DOCX / otros: configuración básica con formula enrichment si está habilitado
                    do_formula_enrichment = os.getenv("DOCLING_FORMULA_ENRICHMENT", "false").lower() in ("on", "true", "1")
                    print(f"[docling] {ext.upper()} config: formula_enrichment={do_formula_enrichment}")

                    if do_formula_enrichment:
                        pipe = PdfPipelineOptions(
                            do_formula_enrichment=do_formula_enrichment,
                            # Optimizaciones para reducir uso de memoria
                            max_pages=20,
                            max_chars_per_page=25000,
                        )
                        converter = DocumentConverter(format_options={
                            InputFormat.DOCX: PdfFormatOption(pipeline_options=pipe)
                        })
                    else:
                        converter = DocumentConverter()

                print("[docling] Converting document...")
                conv = converter.convert(tmp.name)
                print(f"[docling] Conversion result: {conv}")

                # Extraer fórmulas del documento
                formula_count = 0
                for item in conv.document.texts:
                    if hasattr(item, 'label') and str(item.label).upper() == 'FORMULA':
                        formula_info = {
                            'page_number': getattr(item, 'page_no', 0),
                            'latex_code': getattr(item, 'text', ''),
                            'original_text': getattr(item, 'original_text', None),
                            'confidence_score': getattr(item, 'confidence', None),
                        }
                        formulas.append(formula_info)
                        formula_count += 1

                md_text = conv.document.export_to_markdown() or ""
                print(f"[docling] Markdown length: {len(md_text)}, formulas found: {formula_count}")

                pages = _split_markdown(md_text)
                print(f"[docling] Split into {len(pages)} pages")

                if pages:
                    if formula_count > 0:
                        print(f"[docling] Successfully processed with Docling (including {formula_count} formulas)")
                    else:
                        print("[docling] Successfully processed with Docling")
                    return pages, formulas
                else:
                    print("[docling] Docling returned empty pages, falling back")
        except Exception as e:
            print(f"[docling] Error using Docling: {e}")
            import traceback
            traceback.print_exc()
            # fallback silencioso
            pass

    print(f"[docling] Using fallback for {ext}")
    if ext == "pdf":
        pages = _fallback_pdf(buffer)
    if ext == "docx":
        pages = _fallback_docx(buffer)
    else:
        pages = []
    return pages, []


def extract_pages(buffer: bytes, ext: str) -> List[str]:
    """
    Función de compatibilidad que mantiene la interfaz original.
    Usa Docling (API Python) y habilita OCR para PDFs si DOCLING_OCR=on/true/1.
    Habilita formula enrichment si DOCLING_FORMULA_ENRICHMENT=on/true/1.
    Si Docling falla o devuelve vacío, cae a fallbacks nativos (pypdf/docx).
    """
    pages, _ = extract_pages_and_formulas(buffer, ext)
    return pages
