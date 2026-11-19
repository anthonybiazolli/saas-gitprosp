from __future__ import annotations

from decimal import Decimal
from typing import Any

from fpdf import FPDF

from app.models.crm import Company, Contact
from app.models.proposals import BillingType, CommercialProposal


def safe_pdf_text(value: Any) -> str:
    """
    Normaliza strings UTF-8 para Latin-1 seguro nas fontes padrão do FPDF,
    evitando falhas com aspas tipográficas, travessões ou emojis.
    """
    if value is None:
        return ""
    text = str(value)
    replacements = {
        "\u2014": "-",
        "\u2013": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def format_brl(amount: Decimal | float | int) -> str:
    val = Decimal(str(amount)).quantize(Decimal("0.01"))
    formatted = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


class ExecutiveProposalPDF(FPDF):
    def __init__(self, issuer_name: str, proposal_code: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.issuer_name = safe_pdf_text(issuer_name)
        self.proposal_code = safe_pdf_text(proposal_code)
        self.set_auto_page_break(auto=True, margin=18)

    def header(self) -> None:
        self.set_fill_color(15, 23, 42)  # Slate 900
        self.rect(0, 0, 210, 28, style="F")

        self.set_xy(14, 7)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(255, 255, 255)
        self.cell(120, 7, self.issuer_name, ln=False)

        self.set_font("Helvetica", "B", 11)
        self.set_text_color(56, 189, 248)  # Sky 400
        self.cell(62, 7, f"PROPOSTA {self.proposal_code}", align="R", ln=True)

        self.set_xy(14, 15)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(203, 213, 225)
        self.cell(182, 6, "PROPOSTA COMERCIAL EXECUTIVA & ESCOPO TECNICO", ln=True)
        self.ln(8)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(100, 116, 139)
        self.cell(
            0,
            8,
            f"{self.issuer_name} | Documento Comercial {self.proposal_code} | Pagina {self.page_no()}/{{nb}}",
            align="C",
        )


def generate_proposal_pdf_bytes(
    proposal: CommercialProposal,
    issuer_name: str,
    company: Company | None = None,
    contact: Contact | None = None,
    public_acceptance_url: str | None = None,
) -> bytes:
    pdf = ExecutiveProposalPDF(issuer_name=issuer_name, proposal_code=proposal.proposal_code)
    pdf.alias_nb_pages()
    pdf.add_page()

    # Título da Proposta
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(15, 23, 42)
    pdf.multi_cell(0, 7, safe_pdf_text(proposal.title))
    pdf.ln(2)

    # Caixa de Identificação do Cliente e Condições Gerais
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(226, 232, 240)
    box_top = pdf.get_y()
    pdf.rect(14, box_top, 182, 28, style="DF")

    client_name = (
        (company.trade_name or company.legal_name)
        if company
        else (contact.full_name if contact else "Cliente B2B")
    )
    client_cnpj = company.cnpj if (company and company.cnpj) else "Nao informado"
    contact_line = (
        f"{contact.full_name} ({contact.job_title or 'Contato Comercial'})"
        if contact
        else "A/C Diretoria Comercial"
    )
    created_str = proposal.created_at.strftime("%d/%m/%Y") if proposal.created_at else "-"
    valid_str = proposal.valid_until.strftime("%d/%m/%Y") if proposal.valid_until else "-"

    pdf.set_xy(18, box_top + 3)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(105, 5, safe_pdf_text(f"Cliente: {client_name}"), ln=False)
    pdf.cell(70, 5, safe_pdf_text(f"Emissao: {created_str} | Validade: {valid_str}"), align="R", ln=True)

    pdf.set_x(18)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(105, 5, safe_pdf_text(f"CNPJ: {client_cnpj}"), ln=False)
    pdf.cell(70, 5, safe_pdf_text(f"Prazo Contratual: {proposal.contract_months} meses"), align="R", ln=True)

    pdf.set_x(18)
    pdf.cell(105, 5, safe_pdf_text(f"Responsavel: {contact_line}"), ln=False)
    pdf.cell(70, 5, safe_pdf_text(f"Status: {proposal.status.value.upper()}"), align="R", ln=True)

    if contact and (contact.email or contact.whatsapp_e164):
        pdf.set_x(18)
        pdf.cell(
            175,
            5,
            safe_pdf_text(f"Contato: {contact.email or '-'} | WhatsApp: {contact.whatsapp_e164 or '-'}"),
            ln=True,
        )

    pdf.set_y(box_top + 33)

    # Resumo Executivo
    if proposal.executive_summary:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 6, "1. Resumo Executivo & Escopo", ln=True)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(51, 65, 85)
        pdf.multi_cell(0, 5, safe_pdf_text(proposal.executive_summary))
        pdf.ln(4)

    # Tabela de Itens Comerciais
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6, "2. Investimento & Composicao Comercial", ln=True)
    pdf.ln(1)

    col_widths = [84, 28, 16, 26, 28]
    headers = ["Item / Solucao", "Modalidade", "Qtd", "Valor Unit.", "Subtotal"]

    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8.5)
    for w, h_text in zip(col_widths, headers):
        align = "R" if h_text in ("Qtd", "Valor Unit.", "Subtotal") else "L"
        pdf.cell(w, 7, h_text, border=1, align=align, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8.5)
    for idx, item in enumerate(proposal.items):
        fill = idx % 2 == 1
        pdf.set_fill_color(248, 250, 252)
        pdf.set_text_color(15, 23, 42)

        mod_label = "Mensal (MRR)" if item.billing_type == BillingType.RECURRING else "Unico (Setup)"
        qty_str = f"{Decimal(str(item.quantity)).normalize():f}"
        unit_str = format_brl(item.unit_price)
        sub_str = format_brl(item.line_total)
        title_str = safe_pdf_text(item.name)[:46]

        pdf.cell(col_widths[0], 7, title_str, border=1, fill=fill)
        pdf.cell(col_widths[1], 7, mod_label, border=1, fill=fill)
        pdf.cell(col_widths[2], 7, qty_str, border=1, align="R", fill=fill)
        pdf.cell(col_widths[3], 7, unit_str, border=1, align="R", fill=fill)
        pdf.cell(col_widths[4], 7, sub_str, border=1, align="R", fill=fill)
        pdf.ln()

        if item.description:
            pdf.set_font("Helvetica", "I", 7.8)
            pdf.set_text_color(100, 116, 139)
            pdf.multi_cell(sum(col_widths), 4.5, safe_pdf_text(f"   {item.description}"), border="LRB")
            pdf.set_font("Helvetica", "", 8.5)

    pdf.ln(4)

    # Quadro Consolidado de Totais (MRR, Setup, Desconto, TCV)
    summary_x = 106
    summary_w = 90
    pdf.set_x(summary_x)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(52, 6, "Subtotal Mensal (MRR):", border=0)
    pdf.cell(38, 6, format_brl(proposal.subtotal_mrr), align="R", ln=True)

    pdf.set_x(summary_x)
    pdf.cell(52, 6, "Implantacao / Setup (Unico):", border=0)
    pdf.cell(38, 6, format_brl(proposal.subtotal_one_time), align="R", ln=True)

    if Decimal(str(proposal.discount_amount)) > 0:
        pdf.set_x(summary_x)
        pdf.set_text_color(16, 185, 129)
        pdf.cell(52, 6, f"Desconto Comercial ({proposal.discount_percent}%):", border=0)
        pdf.cell(38, 6, f"- {format_brl(proposal.discount_amount)}", align="R", ln=True)

    pdf.set_x(summary_x)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(52, 7, "Total Mensal Liquido (MRR):", border="T")
    pdf.cell(38, 7, format_brl(proposal.total_mrr), border="T", align="R", ln=True)

    pdf.set_x(summary_x)
    pdf.set_fill_color(15, 23, 42)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(52, 8, f"Valor Global ({proposal.contract_months}m):", fill=True)
    pdf.cell(38, 8, format_brl(proposal.total_contract_value), fill=True, align="R", ln=True)

    pdf.ln(6)

    # Termos e Aceite Digital
    terms_text = proposal.terms_and_conditions or (
        "1. Faturamento mensal recorrente com vencimento 10 dias apos ativacao tecnica.\n"
        "2. Garantia de nivel de servico (SLA) e suporte tecnico especializado conforme contrato padrao.\n"
        "3. Esta proposta pode ser aprovada eletronicamente atraves do link de aceite digital abaixo."
    )
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6, "3. Condicoes Comerciais & Aceite Digital", ln=True)
    pdf.set_font("Helvetica", "", 8.8)
    pdf.set_text_color(71, 85, 105)
    pdf.multi_cell(0, 4.8, safe_pdf_text(terms_text))

    if public_acceptance_url:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 8.8)
        pdf.set_text_color(2, 132, 199)
        pdf.multi_cell(0, 5, safe_pdf_text(f"Link para Aprovacao Digital: {public_acceptance_url}"))

    raw_output = pdf.output()
    return bytes(raw_output) if isinstance(raw_output, (bytes, bytearray)) else raw_output.encode("latin-1")
