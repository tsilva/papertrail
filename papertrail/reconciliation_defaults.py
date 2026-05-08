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
