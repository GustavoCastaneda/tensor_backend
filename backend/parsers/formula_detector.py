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
        
        # Patrones matemáticos comunes
        math_patterns = [
            # LaTeX patterns
            r'\\[a-zA-Z]+\{[^}]*\}',  # \frac{}{}, \sqrt{}, etc.
            r'\\[a-zA-Z]+',           # \alpha, \beta, \sum, \int, etc.
            r'\$[^$]+\$',             # Math mode $...$
            r'\$\$[^$]+\$\$',         # Display math $$...$$
            
            # Símbolos matemáticos
            r'[∑∫∏∮∝∞±×÷≤≥≠≈≡]',      # Símbolos Unicode
            r'[αβγδεζηθικλμνξοπρστυφχψω]',  # Letras griegas
            
            # Patrones de ecuaciones
            r'[a-zA-Z]\s*[=]\s*[a-zA-Z0-9\+\-\*/\(\)]+',  # x = y + z
            r'[a-zA-Z]\s*[=]\s*[a-zA-Z0-9\+\-\*/\(\)]+\s*[=]\s*[a-zA-Z0-9\+\-\*/\(\)]+',  # x = y = z
            
            # Fracciones visuales
            r'[0-9]+\s*/\s*[0-9]+',  # 1/2, 3/4, etc.
            
            # Exponentes y subíndices
            r'[a-zA-Z0-9]+\^[a-zA-Z0-9]+',  # x^2, a^b
            r'[a-zA-Z0-9]+_[a-zA-Z0-9]+',   # x_1, a_i
            
            # Funciones matemáticas
            r'\b(sin|cos|tan|log|ln|exp|sqrt|abs|max|min)\s*\(',
            
            # Notación científica
            r'[0-9]+\.[0-9]+[eE][+-]?[0-9]+',  # 1.23e-4
            
            # Matrices (patrones básicos)
            r'\[[^]]*\]\s*\[[^]]*\]',  # [a b][c d]
        ]
        
        found_patterns = []
        has_formulas = False
        
        for pattern in math_patterns:
            matches = re.findall(pattern, all_text, re.IGNORECASE)
            if matches:
                found_patterns.extend(matches[:3])  # Limitar a 3 ejemplos por patrón
                has_formulas = True
        
        # Criterio adicional: densidad de símbolos matemáticos
        math_symbols = re.findall(r'[∑∫∏∮∝∞±×÷≤≥≠≈≡αβγδεζηθικλμνξοπρστυφχψω]', all_text)
        if len(math_symbols) >= 3:  # Si hay 3+ símbolos matemáticos
            has_formulas = True
            found_patterns.extend(math_symbols[:3])
        
        # Criterio adicional: densidad de LaTeX
        latex_patterns = re.findall(r'\\[a-zA-Z]+', all_text)
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
            
            # Patrones matemáticos similares a PDF
            math_patterns = [
                r'[∑∫∏∮∝∞±×÷≤≥≠≈≡]',
                r'[αβγδεζηθικλμνξοπρστυφχψω]',
                r'[a-zA-Z]\s*[=]\s*[a-zA-Z0-9\+\-\*/\(\)]+',
                r'[0-9]+\s*/\s*[0-9]+',
                r'[a-zA-Z0-9]+\^[a-zA-Z0-9]+',
                r'[a-zA-Z0-9]+_[a-zA-Z0-9]+',
            ]
            
            found_patterns = []
            has_formulas = False
            
            for pattern in math_patterns:
                matches = re.findall(pattern, all_text, re.IGNORECASE)
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
