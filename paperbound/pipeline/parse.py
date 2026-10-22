from dataclasses import dataclass, field

import pymupdf

RENDER_ZOOM = 2.0


@dataclass
class ParsedPage:
    page_no: int
    route: str
    text: str
    words: list[dict]
    quality_score: float
    width_pt: float
    height_pt: float
    image_png: bytes = field(repr=False)


def _quality(words: list[dict]) -> float:
    """Share of words containing at least one alphanumeric character."""
    if not words:
        return 0.0
    return sum(any(c.isalnum() for c in w["text"]) for w in words) / len(words)


def parse_pdf(data: bytes) -> list[ParsedPage]:
    """Text-layer parse: per-page text, word bboxes (PDF points), rendered PNG."""
    pages = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for index, page in enumerate(doc):
            words = [
                {"text": w[4], "bbox": [round(v, 2) for v in w[:4]]}
                for w in page.get_text("words")
            ]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM))
            pages.append(
                ParsedPage(
                    page_no=index + 1,
                    route="text_layer",
                    text=page.get_text(),
                    words=words,
                    quality_score=_quality(words),
                    width_pt=page.rect.width,
                    height_pt=page.rect.height,
                    image_png=pix.tobytes("png"),
                )
            )
    return pages
