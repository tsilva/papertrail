from pathlib import Path

import fitz
import openpyxl
from PIL import Image

from papertrail.config import ProfileSettings
from papertrail.commands.reconcile import _reconciliation_rules
from papertrail.runtime import Runtime, runtime_from_profile


TEST_COUNTERPARTY_ALIASES = {
    "TESTSHAREDTOLL": "shared-toll",
    "ptTESTSHAREDTOLL": "shared-toll",
    "sharedtoll": "shared-toll",
    "sharedtollportugal": "shared-toll",
    "TESTCOMPANY": "example-company",
    "ptTESTCOMPANY": "example-company",
    "examplecompany": "example-company",
    "examplecompanyunipessoallda": "example-company",
    "examplecompanyunipessoalltda": "example-company",
    "benefits-provider": "benefits-provider",
}

_GENERIC_RULES = [rule.model_dump() for rule in _reconciliation_rules()]
_DEFAULT_SUPPLIER_RULES = [rule for rule in _GENERIC_RULES if rule.get("name") == "supplier-payment"]
_RULES_BEFORE_DEFAULT_SUPPLIER = [
    rule for rule in _GENERIC_RULES if rule.get("name") != "supplier-payment"
]

TEST_RECONCILIATION_RULES = [
    *_RULES_BEFORE_DEFAULT_SUPPLIER,
    {
        "name": "payroll-payment",
        "match_description": ["EMPLOYEE ONE", "EMPLOYEE TWO"],
        "required_types": {
            "bank-anchor": 1,
            "payroll-evidence": 1,
        },
        "shared_types": {"payroll-evidence": None},
        "shared_filters": {
            "payroll-evidence": {
                "document_type": "payroll-salary",
            },
        },
        "expected_page_count": {"bank-anchor": 1},
    },
    {
        "name": "bank-only",
        "match_description": [
            "TRF P/ EXAMPLE COMPANY - BPI",
            "EXAMPLE COMPANY - BPI",
        ],
        "required_types": {"bank-anchor": 1},
        "expected_page_count": {"bank-anchor": 1},
    },
    *_DEFAULT_SUPPLIER_RULES,
]


def make_test_runtime(root: Path) -> Runtime:
    raw = root / "raw"
    processed = root / "processed"
    export = root / "export"
    cache = root / "cache"
    raw.mkdir()
    processed.mkdir()
    export.mkdir()
    cache.mkdir()

    profile = ProfileSettings.model_validate(
        {
            "profile": {"name": "test", "description": "", "tax_number": None},
            "paths": {
                "raw": [str(raw)],
                "processed": str(processed),
                "export": str(export),
            },
            "openrouter": {
                "model_id": "test-model",
                "base_url": "https://example.invalid/api/v1",
                "api_key": "test-key",
            },
            "gmail": {"enabled": False},
            "passwords": {},
            "nif_api": {"enabled": False},
            "reconciliation": {"exclude_prefixes": []},
            "export": {
                "file_mappings": {
                    "enabled": False,
                    "default_prefix": "",
                    "rules": [],
                    "filename_fields": [],
                },
                "merge_rules": [],
                "max_file_size_mb": None,
            },
            "profile_path": root / "profile.yaml",
            "profile_dir": root,
        }
    )
    profile.reconciliation.counterparty_aliases = TEST_COUNTERPARTY_ALIASES
    profile.reconciliation.shared_period_transaction_keywords = {"shared-toll": ["SHAREDTOLL"]}
    profile.reconciliation.shared_period_title_terms = {
        "shared-toll": ["pagamentosdeservicos", "extratorecibo"],
        "$bank": ["comissoes", "manctapacote", "operacaocartoes", "impostodoselo"],
    }
    profile.reconciliation.same_month_shared_rule_names = ["vendor-sharedtoll"]
    profile.reconciliation.rules = TEST_RECONCILIATION_RULES

    return runtime_from_profile(
        profile,
        profiles_dir=root,
        enable_client=False,
        probe_api=False,
    )


def create_pdf(pdf_path: Path, page_texts: list[str]) -> None:
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(pdf_path)
    doc.close()


def create_png(image_path: Path, size: tuple[int, int] = (40, 40), color: str = "white") -> None:
    Image.new("RGB", size, color=color).save(image_path)


def create_millennium_statement(
    xlsx_path: Path,
    account: str = "TEST-ACCOUNT-ALPHA",
    currency: str = "EUR",
    period_start: str = "01/01/2026",
    period_end: str = "31/01/2026",
    transactions: list[dict] | None = None,
) -> None:
    transactions = transactions or [
        {
            "date_posting": "01/01/2026",
            "date_value": "01/01/2026",
            "description": "TEST PURCHASE",
            "amount": -12.34,
            "currency": currency,
            "notes": "",
            "treated": "Nao",
        }
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(row=2, column=3, value=f"{account} - {currency}")
    ws.cell(row=3, column=3, value=period_start)
    ws.cell(row=4, column=3, value=period_end)
    ws.cell(row=8, column=1, value="Data Lancamento")
    ws.cell(row=8, column=2, value="Data Valor")
    ws.cell(row=8, column=3, value="Descricao")
    ws.cell(row=8, column=4, value="Montante")
    ws.cell(row=8, column=5, value="Moeda")
    ws.cell(row=8, column=6, value="Notas")
    ws.cell(row=8, column=7, value="Tratado")

    for index, txn in enumerate(transactions, start=9):
        ws.cell(row=index, column=1, value=txn["date_posting"])
        ws.cell(row=index, column=2, value=txn["date_value"])
        ws.cell(row=index, column=3, value=txn["description"])
        ws.cell(row=index, column=4, value=txn["amount"])
        ws.cell(row=index, column=5, value=txn.get("currency", currency))
        ws.cell(row=index, column=6, value=txn.get("notes", ""))
        ws.cell(row=index, column=7, value=txn.get("treated", "Nao"))

    wb.save(xlsx_path)
    wb.close()


def create_bpi_statement(
    xlsx_path: Path,
    account: str = "TEST-ACCOUNT-BETA",
    currency: str = "EUR",
    transactions: list[dict] | None = None,
) -> None:
    transactions = transactions or [
        {
            "date_posting": "01-01-2026",
            "date_value": "01-01-2026",
            "description": "TEST TRANSFER",
            "amount": 12.34,
            "currency": currency,
        }
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(row=7, column=3, value=f"{account} ({currency})")
    ws.cell(row=18, column=1, value="Data Mov.")
    ws.cell(row=18, column=2, value="Data Valor")
    ws.cell(row=18, column=3, value="Descricao do Movimento")
    ws.cell(row=18, column=4, value="Valor em EUR")

    for index, txn in enumerate(transactions, start=19):
        ws.cell(row=index, column=1, value=txn["date_posting"])
        ws.cell(row=index, column=2, value=txn["date_value"])
        ws.cell(row=index, column=3, value=txn["description"])
        ws.cell(row=index, column=4, value=txn["amount"])

    wb.save(xlsx_path)
    wb.close()
