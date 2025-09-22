import os
from typing import List


def generate_query_variants(original_query: str, fanout: int, model_fallback: str = "gpt-5") -> List[str]:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = f"""Genera {fanout} reformulaciones distintas para mejorar la búsqueda:
Consulta: "{original_query}"
Estrategias: definición/metodología/alcance/anexo; tabla/fórmula/ejemplo; expandir acrónimos; sinónimos técnicos.
Devuelve solo {fanout} líneas, sin numeración."""
        # Intentar Responses API con razonamiento bajo
        ANSWER_MODEL = os.getenv("ANSWER_MODEL", model_fallback)
        REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low")
        try:
            resp = client.responses.create(
                model=ANSWER_MODEL,
                reasoning={"effort": REASONING_EFFORT},
                input=prompt,
                max_output_tokens=200,
                text={"format": {"type": "text"}}
            )
            # Intentar diferentes formas de acceder al contenido
            content = getattr(resp, "output_text", None)
            if not content:
                content = getattr(resp, "text", None)
            if not content and hasattr(resp, "output") and resp.output:
                first = resp.output[0]
                if getattr(first, "content", None):
                    part = first.content[0]
                    content = getattr(part, "text", None)
            if content:
                variants = [line.strip() for line in content.strip().split("\n") if line.strip()]
                return variants[:fanout]
        except Exception:
            # Fallback a chat.completions
            resp = client.chat.completions.create(
                model=ANSWER_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=200,
            )
            variants = [
                line.strip()
                for line in resp.choices[0].message.content.strip().split("\n")
                if line.strip()
            ]
            return variants[:fanout]
    except Exception:
        # Fallback simple
        seeds = [
            f"definición {original_query}",
            f"metodología {original_query}",
            f"tabla {original_query}",
            f"fórmula {original_query}",
        ]
        return seeds[:fanout]


