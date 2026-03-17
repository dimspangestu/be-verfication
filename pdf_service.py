import os
from xhtml2pdf import pisa


def generate_pdf_from_html(html_content: str, output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "wb") as output_file:
        pisa_status = pisa.CreatePDF(html_content, dest=output_file)

    if pisa_status.err:
        raise Exception("Failed to generate PDF from HTML")

    return output_path