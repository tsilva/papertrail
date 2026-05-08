"""Default reconciliation policy values.

The values in this module preserve the historical behavior. Profiles can
override them through the ``reconciliation`` section.
"""

from __future__ import annotations

DEFAULT_AMOUNT_TOLERANCE = 0.01
DEFAULT_DATE_WINDOW_DAYS = 30

DEFAULT_BANK_GENERATED_DOC_TYPES = (
    "bank-card-transaction",
    "bank-note",
    "bank-transfer",
    "bank-statement",
    "bank-investment",
)
DEFAULT_STATEMENT_BANK_SCOPED_DOC_TYPES = (
    "bank-card-transaction",
    "bank-note",
)
DEFAULT_STATEMENT_BANK_ISSUER_ALIASES = {
    "bancobpi": "bpi",
    "bpi": "bpi",
    "bancocomercialportugues": "millennium-bcp",
    "bcp": "millennium-bcp",
    "millennium": "millennium-bcp",
    "millenniumbcp": "millennium-bcp",
    "millennium-bcp": "millennium-bcp",
}
DEFAULT_BANK_EXPORT_PREFIX = "BNC_"
DEFAULT_SUPPORTING_EXPORT_PREFIXES = ("CMP_", "DIV_")
DEFAULT_SUPPORTING_DOC_TYPE_PATTERNS = (
    "invoice",
    "receipt",
    "invoice-receipt",
    "invoice-credit",
    "invoice-debit",
    "invoice-order",
    "insurance-notice",
    "receipt-reference",
    "receipt-delivery",
)

DEFAULT_BANK_COUNTERPARTIES = ("bpi", "millennium-bcp")
DEFAULT_COUNTERPARTY_ALIASES = {
    "TESTBANKALPHATAX": "millennium-bcp",
    "ptTESTBANKALPHATAX": "millennium-bcp",
    "bancocomercialportugues": "millennium-bcp",
    "millenniumbcp": "millennium-bcp",
    "millenniumbcpsa": "millennium-bcp",
    "millenniumbcpbancocomercialportugues": "millennium-bcp",
    "millennium": "millennium-bcp",
    "millenniumbanco": "millennium-bcp",
    "bcp": "millennium-bcp",
    "TESTBANKBETATAX": "bpi",
    "ptTESTBANKBETATAX": "bpi",
    "bpi": "bpi",
    "bancobpi": "bpi",
    "bancobpisa": "bpi",
    "TESTSHAREDTOLL": "shared-toll",
    "ptTESTSHAREDTOLL": "shared-toll",
    "sharedtoll": "shared-toll",
    "sharedtollportugal": "shared-toll",
    "google": "google",
    "googlecommerce": "google",
    "googlecommercelimited": "google",
    "googley": "google",
    "ie9825613n": "google",
    "TESTUTILITY": "utility-provider",
    "ptTESTUTILITY": "utility-provider",
    "utility-provider": "utility-provider",
    "utility-providerportugal": "utility-provider",
    "utility-providerportugalcomunicacoespessoais": "utility-provider",
    "TESTINSURER": "insurance-provider",
    "ptTESTINSURER": "insurance-provider",
    "insurance-provider": "insurance-provider",
    "companhiadesegurosinsurance-providerportugal": "insurance-provider",
    "companhiadesegurosinsurance-providerportugalsa": "insurance-provider",
    "ISSUER-TAX-ID": "melo-nadais",
    "ptISSUER-TAX-ID": "melo-nadais",
    "melonadais": "melo-nadais",
    "melonadaisassociados": "melo-nadais",
    "500918880": "fidelidade",
    "pt500918880": "fidelidade",
    "fidelidade": "fidelidade",
    "companhiades": "fidelidade",
    "companhiadessegurosfidelidade": "fidelidade",
    "segurancasocial": "seguranca-social",
    "at": "at",
    "atautoridadetributariaeaduaneira": "at",
    "benefits-provider": "benefits-provider",
    "TESTCOMPANY": "example-company",
    "ptTESTCOMPANY": "example-company",
    "examplecompany": "example-company",
    "examplecompanyunipessoalltda": "example-company",
    "examplecompanyunipessoallda": "example-company",
    "digitalocean": "digitalocean",
    "digitaloceanllc": "digitalocean",
    "eu528002224": "digitalocean",
    "wisdomtree": "wisdomtree",
    "wisdomtreeuklimited": "wisdomtree",
}
DEFAULT_SHARED_PERIOD_TRANSACTION_KEYWORDS = {
    "shared-toll": ("SHAREDTOLL",),
}
DEFAULT_SHARED_PERIOD_TITLE_TERMS = {
    "shared-toll": ("pagamentosdeservicos", "extratorecibo"),
    "$bank": ("comissoes", "manctapacote", "operacaocartoes", "impostodoselo"),
}
DEFAULT_SAME_MONTH_SHARED_RULE_NAMES = ("vendor-sharedtoll",)
DEFAULT_STRICT_STATEMENT_BANKS = ("bpi",)
DEFAULT_SUPPORTING_PAIR_EXEMPT_STATEMENT_BANKS = ("bpi",)
DEFAULT_SHARED_PERIOD_LINK_CATEGORIES = ("supplier-payment",)
DEFAULT_SHARED_PERIOD_SUPPLIER_EVIDENCE_ERROR_EXEMPT_RULE_NAMES = ("supplier-payment",)
DEFAULT_SHARED_PERIOD_BANK_ANCHOR_ERROR_EXEMPT_RULE_NAMES = ("supplier-payment",)
DEFAULT_EVIDENCE_COUNTERPARTY_CATEGORIES = ("supplier-payment", "bank-fee")
DEFAULT_EVIDENCE_COUNTERPARTY_REQUIRED_PATTERN = "invoice"
DEFAULT_LINE_ITEM_CATEGORY_ALIASES = {
    "bank-fee": {
        "prefixes": ("bank-fee",),
        "categories": ("bank-custody-fee",),
    },
    "bank-only": {
        "prefixes": ("bank-transfer",),
        "categories": (),
    },
    "investment": {
        "prefixes": ("stock-",),
        "categories": (),
    },
}
DEFAULT_LINE_ITEM_EXTRACTORS = {
    "bpi_fee_invoice": {
        "document_types": ("invoice",),
        "issuing_parties": ("bpi",),
        "title_terms": (
            "comissoes",
            "titulos",
            "manutencaodecontavalornegocios",
            "contavalornegocios",
        ),
        "maintenance_marker": "MANUTENCAO DE CONTA VALOR NEGOCIOS",
        "custody_marker": "COMISSAO DEPOSITO E REGISTO VALORES MOBILIARIOS",
        "total_debit_marker": "TOTAL A DEBITO",
        "stamp_duty_rate": 0.04,
        "max_stamp_duty": 1.0,
        "maintenance_search_after": 25,
        "custody_total_search_after": 20,
        "custody_total_amount_after": 3,
        "custody_fallback_after": 15,
        "custody_fallback_before": 15,
        "custody_fallback_max_amount": 100,
        "maintenance_category": "bank-fee-maintenance",
        "stamp_duty_category": "bank-fee-stamp-duty",
        "custody_category": "bank-custody-fee",
    },
    "bpi_stock_invoice": {
        "document_types": ("invoice",),
        "issuing_parties": ("bpi",),
        "title_terms": ("comissoes", "titulos"),
        "sale_required_terms": ("VENDA DE", "ACCOES"),
        "order_reference_pattern": r"N[ºO]\s+ORDEM:\s*([A-Z0-9]+)",
        "total_credit_marker": "TOTAL A CREDITO",
        "settlement_offset_days": 1,
        "movement_date_lookback": 25,
        "reference_search_after": 15,
        "category": "stock-sale-bpi",
        "document_type": "bank-stock-sell",
    },
    "bpi_transfer": {
        "document_types": ("bank-note", "bank-transfer"),
        "issuing_parties": ("bpi",),
        "line_pattern": r"\b(?:TRF\s+CR\s+SEPA\+\s+|TRF\s+SEPA\+\s+INST\s+|TRANSFER[ÊE]NCIA\s+RECEBIDA\s+)(\d+)\b",
        "amount_search_before": 8,
        "date_search_after": 5,
        "category": "bank-transfer-sepa",
    },
    "millennium_fee_invoice": {
        "document_types": ("invoice", "invoice-receipt"),
        "issuing_parties": (
            "millenniumbcp",
            "millenniumbancocomercialportugues",
            "bancocomercialportugues",
        ),
        "movement_date_marker": "DATA DO MOVIMENTO",
        "amount_search_after": 5,
        "markers": {
            "CUSTO DE SERVICO INTERNACIONAL": "bank-fee-international-service",
            "COMISSAO REFERENTE": "bank-fee-maintenance",
        },
        "stamp_duty_markers": ("IMPOSTO DO SELO", "IMP. SELO"),
        "stamp_duty_legal_reference": "17.3.4",
        "stamp_duty_category": "bank-fee-stamp-duty",
    },
    "direct_debit": {
        "date_pattern": r"\bDEBITO\s+A\s+PARTIR\s+DE:\s*(\d{2})[/-](\d{2})[/-](20\d{2})\b",
        "auth_pattern": r"\bN[ºO]\s+AUTORIZACAO:\s*([A-Z0-9]+)\b",
        "amount_pattern": r"\bVALOR:[^\d\n]*([\d\s.]+,\d{2})\b",
        "category": "supplier-payment",
        "label": "Direct debit",
    },
    "insurance_notice": {
        "document_types": ("insurance-notice",),
        "period_pattern": r"\bPERIODO\s+DO\s+RECIBO\b.*?(\d{2})[/-](\d{2})[/-](20\d{2})\s+A\s+\d{2}[/-]\d{2}[/-]20\d{2}\b",
        "reference_pattern": r"\bADC\s+([A-Z0-9]+)\b",
        "category": "supplier-payment",
        "label": "Insurance direct debit",
    },
}

DEFAULT_RECONCILIATION_RULES = (
    {
        "name": "investment",
        "match_description": ["TRANSFERENCIA A CREDITO LIS"],
        "required_types": {"bank-stock-sell": [1, None]},
        "expected_page_count": {"bank-stock-sell": [1, 2]},
    },
    {
        "name": "investment",
        "match_description": ["TRANSFERENCIA A DEBITO LIS"],
        "required_types": {"investment-evidence": [1, None]},
        "shared_types": {"investment-evidence": "bpi"},
        "shared_filters": {"investment-evidence": {"document_type": "bank-investment"}},
        "expected_page_count": {"investment-evidence": [1, 2]},
    },
    {
        "name": "loan-payment",
        "match_description": ["CONCESS CRED EMPR"],
        "required_types": {
            "bank-anchor": [1, None],
            "contract-evidence": [1, None],
        },
        "expected_page_count": {"bank-anchor": 1},
    },
    {
        "name": "loan-payment",
        "match_description": ["IMP ABERT CRED EMPRES"],
        "required_types": {"bank-anchor": 1, "supplier-evidence": 1},
        "expected_page_count": {"bank-anchor": 1, "supplier-evidence": 1},
    },
    {
        "name": "loan-payment",
        "match_description": ["PAGAMENT EMPRESTIMO"],
        "required_types": {"bank-anchor": 1, "supplier-evidence": 1},
        "shared_types": {"supplier-evidence": "millennium-bcp"},
        "shared_filters": {"supplier-evidence": {"document_title": "Pagamento de Prestação"}},
        "expected_page_count": {"bank-anchor": 1, "supplier-evidence": 1},
    },
    {
        "name": "tax-payment",
        "match_description": ["IGCP", "PAG.DUC"],
        "required_types": {"bank-anchor": 1, "tax-evidence": 1},
        "expected_page_count": {"bank-anchor": 1},
    },
    {
        "name": "payroll-payment",
        "match_description": ["TAXA SOCIAL UNICA"],
        "required_types": {"bank-anchor": 1, "payroll-evidence": 1},
        "expected_page_count": {"bank-anchor": 1},
    },
    {
        "name": "payroll-payment",
        "match_description": ["EMPLOYEE ONE", "EMPLOYEE TWO"],
        "required_types": {"bank-anchor": 1, "payroll-evidence": 1},
        "shared_types": {"payroll-evidence": None},
        "shared_filters": {"payroll-evidence": {"document_type": "payroll-salary"}},
        "expected_page_count": {"bank-anchor": 1},
    },
    {
        "name": "bank-fee",
        "match_description": [
            "CUSTO DE SERVICO INTERNACIONAL",
            "MANUTENCAO DE CONTA VALOR NEGOCIOS",
            "PACOTE M EMPRESA",
            "TITULOS CUSTODIA",
            "OPERACOES COM TITULOS",
            "IMPOSTO DO SELO",
            "IMPOSTO SELO",
            "IMPOSTO DE SELO",
            "COMISSAO",
            "COMISSÃO",
        ],
        "required_types": {"supplier-evidence": [1, None], "bank-anchor": [0, 1]},
        "expected_page_count": {"supplier-evidence": [1, 2], "bank-anchor": 1},
    },
    {
        "name": "bank-only",
        "match_description": ["TRF SEPA+"],
        "required_types": {"bank-anchor": [1, None]},
        "expected_page_count": {},
    },
    {
        "name": "bank-only",
        "match_description": [
            "TRF P/ EXAMPLE COMPANY - BPI",
            "TRANSFERENCIA RECEBIDA",
            "TRANSFERÊNCIA RECEBIDA",
            "EXAMPLE COMPANY - BPI",
        ],
        "required_types": {"bank-anchor": 1},
        "expected_page_count": {"bank-anchor": 1},
    },
    {
        "name": "bank-only",
        "direction": "credit",
        "required_types": {"bank-anchor": [1, None]},
        "expected_page_count": {"bank-anchor": 1},
    },
    {
        "name": "supplier-payment",
        "direction": "debit",
        "required_types": {"bank-anchor": 1, "supplier-evidence": [1, None]},
        "expected_page_count": {"bank-anchor": 1},
    },
)
