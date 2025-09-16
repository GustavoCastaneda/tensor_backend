# backend/parsers/formula_detector.py
import io
import re
from typing import List, Tuple
from pypdf import PdfReader

def detect_formulas_in_pdf(buffer: bytes) -> Tuple[bool, List[str]]:
    """
    Detecta rápidamente si un PDF contiene fórmulas matemáticas usando pypdf.
    
    Args:
        buffer: Contenido del PDF en bytes
        
    Returns:
        Tuple[bool, List[str]]: (tiene_formulas, patrones_encontrados)
    """
    try:
        reader = PdfReader(io.BytesIO(buffer))
        all_text = ""
        
        # Muestreo inteligente: páginas distribuidas (inicio, 25%, 50%, 75%, final)
        total_pages = len(reader.pages)
        if total_pages <= 5:
            pages_to_read = list(range(total_pages))
        else:
            pages_to_read = [
                0,
                max(1, total_pages // 4),
                max(1, total_pages // 2),
                max(1, (3 * total_pages) // 4),
                total_pages - 1,
            ]

        # Eliminar duplicados y mantener orden
        seen = set()
        ordered_pages = []
        for p in pages_to_read:
            if p not in seen and 0 <= p < total_pages:
                seen.add(p)
                ordered_pages.append(p)

        for i in ordered_pages:
            try:
                page_text = reader.pages[i].extract_text() or ""
                all_text += page_text + " "
            except Exception:
                continue
        
        # Construir texto sanitizado para la detección (NO afecta contenido real)
        sanitized_text = _sanitize_for_detection(all_text)

        # Patrones matemáticos comunes (más específicos para evitar falsos positivos)
        math_patterns = [
            # LaTeX patterns
            r'\\[a-zA-Z]+\{[^}]*\}',  # \frac{}{}, \sqrt{}, etc.
            r'\\(alpha|beta|gamma|delta|sum|int|frac|sqrt|cdot|times|pm|leq|geq|neq|approx)\b',
            r'\$[^$]+\$',             # Math mode $...$
            r'\$\$[^$]+\$\$',         # Display math $$...$$
            
            # Símbolos matemáticos
            r'[∑∫∏∮∝∞±×÷≤≥≠≈≡]',      # Símbolos Unicode
            r'[αβγδεζηθικλμνξοπρστυφχψω]',  # Letras griegas
            
            # Patrones de ecuaciones
            r'\b[a-zA-Z]\s*[=]\s*[a-zA-Z0-9\+\-\*/\(\)]+\b',  # x = y + z
            r'\b[a-zA-Z]\s*[=]\s*[a-zA-Z0-9\+\-\*/\(\)]+\s*[=]\s*[a-zA-Z0-9\+\-\*/\(\)]+\b',  # x = y = z
            
            # Fracciones visuales
            r'\b[0-9]+\s*/\s*[0-9]+\b',  # 1/2, 3/4, etc.
            
            # Exponentes y subíndices (más estrictos)
            r'\b[a-zA-Z]\^[0-9]+\b',      # x^2, a^3
            r'\b[a-zA-Z]_[0-9]+\b',        # x_1, y_2
            r'\b[a-zA-Z]_[a-zA-Z]\b',      # x_i, a_j
            
            # Funciones matemáticas
            r'\b(sin|cos|tan|log|ln|exp|sqrt|abs|max|min)\s*\(',
            
            # Notación científica
            r'\b[0-9]+\.[0-9]+[eE][+-]?[0-9]+\b',  # 1.23e-4
            
            # Matrices (patrones básicos)
            r'\[[^]]*\]\s*\[[^]]*\]',  # [a b][c d]
        ]
        
        found_patterns = []
        has_formulas = False
        
        for pattern in math_patterns:
            matches = re.findall(pattern, sanitized_text, re.IGNORECASE)
            if matches:
                found_patterns.extend(matches[:3])  # Limitar a 3 ejemplos por patrón
                has_formulas = True
        
        # Criterio adicional: densidad de símbolos matemáticos
        math_symbols = re.findall(r'[∑∫∏∮∝∞±×÷≤≥≠≈≡αβγδεζηθικλμνξοπρστυφχψω]', sanitized_text)
        if len(math_symbols) >= 3:  # Si hay 3+ símbolos matemáticos
            has_formulas = True
            found_patterns.extend(math_symbols[:3])
        
        # Criterio adicional: densidad de LaTeX
        latex_patterns = re.findall(r'\\[a-zA-Z]+', sanitized_text)
        if len(latex_patterns) >= 2:  # Si hay 2+ comandos LaTeX
            has_formulas = True
            found_patterns.extend(latex_patterns[:3])
        
        return has_formulas, found_patterns[:5]  # Máximo 5 ejemplos
        
    except Exception as e:
        print(f"[formula_detector] Error detecting formulas: {e}")
        # En caso de error, asumir que tiene fórmulas (usar servicio pesado)
        return True, [f"Error: {str(e)}"]

def detect_formulas_in_docx(buffer: bytes) -> Tuple[bool, List[str]]:
    """
    Detecta fórmulas en documentos DOCX usando python-docx.
    
    Args:
        buffer: Contenido del DOCX en bytes
        
    Returns:
        Tuple[bool, List[str]]: (tiene_formulas, patrones_encontrados)
    """
    try:
        import tempfile
        import docx
        
        with tempfile.NamedTemporaryFile(suffix=".docx") as tmp:
            tmp.write(buffer)
            tmp.flush()
            
            doc = docx.Document(tmp.name)
            all_text = ""
            
            # Extraer texto de todos los párrafos
            for paragraph in doc.paragraphs:
                all_text += paragraph.text + " "
            
            # Buscar ecuaciones en el XML del documento
            # Las ecuaciones en Word suelen estar en elementos <m:> o <oMath:>
            if hasattr(doc, '_element'):
                xml_text = doc._element.xml
                if 'm:' in xml_text or 'oMath:' in xml_text:
                    return True, ["Word equation detected"]
            
            # Patrones matemáticos similares a PDF (más específicos)
            math_patterns = [
                r'[∑∫∏∮∝∞±×÷≤≥≠≈≡]',
                r'[αβγδεζηθικλμνξοπρστυφχψω]',
                r'\b[a-zA-Z]\s*[=]\s*[a-zA-Z0-9\+\-\*/\(\)]+\b',
                r'\b[0-9]+\s*/\s*[0-9]+\b',
                r'\b[a-zA-Z]\^[0-9]+\b',
                r'\b[a-zA-Z]_[0-9]+\b',
                r'\b[a-zA-Z]_[a-zA-Z]\b',
            ]
            
            found_patterns = []
            has_formulas = False
            
            sanitized_text = _sanitize_for_detection(all_text)
            for pattern in math_patterns:
                matches = re.findall(pattern, sanitized_text, re.IGNORECASE)
                if matches:
                    found_patterns.extend(matches[:3])
                    has_formulas = True
            
            return has_formulas, found_patterns[:5]
            
    except Exception as e:
        print(f"[formula_detector] Error detecting formulas in DOCX: {e}")
        return True, [f"Error: {str(e)}"]

def detect_formulas_in_document(buffer: bytes, ext: str) -> Tuple[bool, List[str]]:
    """
    Detecta fórmulas en cualquier tipo de documento.
    
    Args:
        buffer: Contenido del documento en bytes
        ext: Extensión del archivo (pdf, docx)
        
    Returns:
        Tuple[bool, List[str]]: (tiene_formulas, patrones_encontrados)
    """
    ext = (ext or "").lower().lstrip(".")
    
    if ext == "pdf":
        return detect_formulas_in_pdf(buffer)
    elif ext == "docx":
        return detect_formulas_in_docx(buffer)
    else:
        # Para otros formatos, asumir que tiene fórmulas (usar servicio pesado)
        return True, ["Unknown format"]


# --- Utilidades de detección compartidas ---

_EMAIL_RE = re.compile(r"\b[\w\.-]+@[\w\.-]+\.[a-zA-Z]{2,}\b")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_GENERIC_SNAKE_RE = re.compile(r"\b[a-z0-9]+_[a-z0-9]+\b", re.IGNORECASE)

def _sanitize_for_detection(text: str) -> str:
    """
    Sanea texto SOLO para la heurística de detección (no afecta contenido real).
    - Elimina emails, URLs y snake_case genérico que suele aparecer en correos/usernames.
    """
    if not text:
        return ""
    text = _EMAIL_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    # Ojo: mantenemos subíndices válidos como x_1, a_i con patrones más estrictos arriba
    text = _GENERIC_SNAKE_RE.sub(" ", text)
    return text

def count_math_signals(text: str) -> Tuple[int, List[str]]:
    """
    Cuenta señales matemáticas en un bloque de texto ya extraído.
    Devuelve (num_señales, ejemplos_encontrados).
    """
    if not text:
        return 0, []
    sanitized_text = _sanitize_for_detection(text)
    patterns = [
        r'\\[a-zA-Z]+\{[^}]*\}',
        r'\\(alpha|beta|gamma|delta|sum|int|frac|sqrt|cdot|times|pm|leq|geq|neq|approx)\b',
        r'\$[^$]+\$',
        r'\$\$[^$]+\$\$',
        r'[∑∫∏∮∝∞±×÷≤≥≠≈≡]',
        r'[αβγδεζηθικλμνξοπρστυφχψω]',
        r'\b[a-zA-Z]\s*[=]\s*[a-zA-Z0-9\+\-\*/\(\)]+\b',
        r'\b[0-9]+\s*/\s*[0-9]+\b',
        r'\b[a-zA-Z]\^[0-9]+\b',
        r'\b[a-zA-Z]_[0-9]+\b',
        r'\b[a-zA-Z]_[a-zA-Z]\b',
        r'\b[0-9]+\.[0-9]+[eE][+-]?[0-9]+\b',
    ]
    found: List[str] = []
    signals = 0
    for pat in patterns:
        m = re.findall(pat, sanitized_text, re.IGNORECASE)
        if m:
            signals += 1
            found.extend([str(m[0])][:1])
    return signals, found[:5]
