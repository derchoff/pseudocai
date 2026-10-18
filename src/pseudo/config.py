"""Configuration lue depuis les variables d'environnement (app settings de la Function)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    """Variable d'environnement, ou la valeur par défaut si elle est absente ou vide."""
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    """Variable d'environnement booléenne (1, true, yes, oui, on)."""
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "oui", "on")


def _int(name: str, default: int) -> int:
    """Variable d'environnement entière."""
    value = _env(name)
    return int(value) if value is not None else default


def _float(name: str, default: float) -> float:
    """Variable d'environnement décimale."""
    value = _env(name)
    return float(value) if value is not None else default


OUTPUT_MODES = ("substitution", "jetons")
HANDWRITING_POLICIES = ("quarantaine", "traiter")
REPORT_DETAILS = ("diagnostic", "standard")


def _output_mode() -> str:
    """PSEUDO_OUTPUT_MODE validé : « substitution » (défaut) ou « jetons »."""
    value = (_env("PSEUDO_OUTPUT_MODE", "substitution") or "").strip().lower()
    if value not in OUTPUT_MODES:
        raise ValueError(f"PSEUDO_OUTPUT_MODE inconnu : {value!r} (attendu : {', '.join(OUTPUT_MODES)})")
    return value


def _handwriting_policy() -> str:
    """HANDWRITING_POLICY validé : « quarantaine » (défaut) ou « traiter »."""
    value = (_env("HANDWRITING_POLICY", "quarantaine") or "").strip().lower()
    if value not in HANDWRITING_POLICIES:
        raise ValueError(f"HANDWRITING_POLICY inconnu : {value!r} (attendu : {', '.join(HANDWRITING_POLICIES)})")
    return value


def _report_detail() -> str:
    """REPORT_DETAIL validé : « diagnostic » (défaut) ou « standard »."""
    value = (_env("REPORT_DETAIL", "diagnostic") or "").strip().lower()
    if value not in REPORT_DETAILS:
        raise ValueError(f"REPORT_DETAIL inconnu : {value!r} (attendu : {', '.join(REPORT_DETAILS)})")
    return value


@dataclass(frozen=True)
class Settings:
    """Réglages de l'application, lus dans les variables d'environnement (app settings Azure)."""
    # Stockage
    storage_account_url: str | None  # compte « data » : work, sorties, quarantaine, prompts
    # Compte où arrivent les documents (par défaut le compte « data ») et son container d'entrée.
    source_storage_account_url: str | None
    incoming_container: str  # point d'entrée : les fichiers y sont déposés et y restent
    work_container: str  # fichiers intermédiaires (images préparées), purgés en fin de traitement
    output_container: str
    quarantine_container: str
    config_container: str
    prompts_prefix: str
    delete_source_after_success: bool
    max_file_mb: int

    # Services IA
    docintel_endpoint: str | None
    docintel_model: str
    openai_endpoint: str | None
    openai_deployment: str | None
    openai_api_version: str
    openai_reasoning_effort: str | None
    openai_temperature: float | None
    openai_max_completion_tokens: int
    openai_max_concurrent_calls: int  # par instance de Function
    openai_rate_limit_max_wait: float  # secondes d'attente maximale après des refus 429
    language_endpoint: str | None
    language_pii_categories: str
    language_min_confidence: float

    # Secrets / persistance
    key_vault_url: str | None
    hmac_secret_name: str
    encryption_secret_name: str
    hmac_key_b64: str | None  # surcharge locale (dev uniquement)
    encryption_key_b64: str | None  # surcharge locale (dev uniquement)
    cosmos_endpoint: str | None
    cosmos_database: str
    cosmos_mapping_container: str
    cosmos_trace_container: str
    cosmos_occurrence_container: str

    # Paramètres du pipeline
    max_pages: int
    min_native_chars: int
    image_coverage_ocr_threshold: float
    force_ocr: bool
    ocr_native_pages: bool
    ocr_min_confidence: float
    ocr_render_dpi: int
    llm_chunk_chars: int
    llm_chunk_overlap_lines: int
    llm_min_confidence: float
    verification_rounds: int
    token_length: int
    propagation_min_length: int
    fuzzy_min_score: float
    quarantine_on_unlocated: bool
    prompt_cache_seconds: int

    # Contenu du document publié : "substitution" (valeurs fictives réalistes) ou "jetons"
    output_mode: str

    # Écriture manuscrite détectée par l'OCR : "quarantaine" (revue humaine) ou "traiter"
    handwriting_policy: str
    handwriting_min_confidence: float

    # Rapport : "diagnostic" (détail de chaque étape, sans valeur) ou "standard" (totaux seulement)
    report_detail: str

    # Classification du type de document
    classification_enabled: bool
    classification_min_confidence: float
    classification_secondary_min_confidence: float
    classification_max_chars: int
    unclassified_folder: str
    deposit_timezone: str  # fuseau des dossiers year=/month=/day=/hour= (date de dépose dans incoming/)

    # Images (étape de préparation)
    image_processing_enabled: bool
    image_extensions: tuple[str, ...]
    image_max_pixels: int
    image_min_short_side: int
    image_blur_threshold: float
    image_enhance_for_ocr: bool
    image_render_dpi: int
    image_output_quality: int

    # Mails (.eml) : corps, en-têtes et pièces jointes pseudonymisés, rangés dans un dossier par mail
    mail_processing_enabled: bool
    json_metadata_enabled: bool  # JSON de métadonnées de la GED (<nom du document>.json)
    mail_max_attachments: int
    mail_image_min_side: int  # image d'un mail plus petite sur un côté : retirée (logo, icône)

    @classmethod
    def from_env(cls) -> "Settings":
        """Construit les réglages depuis l'environnement, avec les valeurs par défaut."""
        temperature = _env("OPENAI_TEMPERATURE")
        return cls(
            storage_account_url=_env("STORAGE_ACCOUNT_URL"),
            source_storage_account_url=_env("SOURCE_STORAGE_ACCOUNT_URL", _env("STORAGE_ACCOUNT_URL")),
            incoming_container=_env("INCOMING_CONTAINER", "incoming"),
            work_container=_env("WORK_CONTAINER", "work"),
            output_container=_env("OUTPUT_CONTAINER", "pseudonymised"),
            quarantine_container=_env("QUARANTINE_CONTAINER", "quarantine"),
            config_container=_env("CONFIG_CONTAINER", "config"),
            prompts_prefix=_env("PROMPTS_PREFIX", "prompts/"),
            delete_source_after_success=_bool("DELETE_SOURCE_AFTER_SUCCESS", False),
            max_file_mb=_int("MAX_FILE_MB", 100),
            docintel_endpoint=_env("DOCINTEL_ENDPOINT"),
            docintel_model=_env("DOCINTEL_MODEL", "prebuilt-read"),
            openai_endpoint=_env("OPENAI_ENDPOINT"),
            openai_deployment=_env("OPENAI_DEPLOYMENT"),
            openai_api_version=_env("OPENAI_API_VERSION", "2025-04-01-preview"),
            openai_reasoning_effort=_env("OPENAI_REASONING_EFFORT"),
            openai_temperature=float(temperature) if temperature is not None else None,
            openai_max_completion_tokens=_int("OPENAI_MAX_COMPLETION_TOKENS", 16000),
            openai_max_concurrent_calls=_int("OPENAI_MAX_CONCURRENT_CALLS", 2),
            openai_rate_limit_max_wait=_float("OPENAI_RATE_LIMIT_MAX_WAIT_SECONDS", 300),
            language_endpoint=_env("LANGUAGE_ENDPOINT"),
            language_pii_categories=_env(
                "LANGUAGE_PII_CATEGORIES",
                "Person:PERSONNE,PhoneNumber:TELEPHONE,Email:EMAIL,Address:ADRESSE,"
                "FRSocialSecurityNumber:NIR,DateOfBirth:DATE_NAISSANCE",
            ),
            language_min_confidence=_float("LANGUAGE_MIN_CONFIDENCE", 0.6),
            key_vault_url=_env("KEY_VAULT_URL"),
            hmac_secret_name=_env("HMAC_SECRET_NAME", "pseudo-hmac-key"),
            encryption_secret_name=_env("ENCRYPTION_SECRET_NAME", "pseudo-encryption-key"),
            hmac_key_b64=_env("PSEUDO_HMAC_KEY_B64"),
            encryption_key_b64=_env("PSEUDO_ENCRYPTION_KEY_B64"),
            cosmos_endpoint=_env("COSMOS_ENDPOINT"),
            cosmos_database=_env("COSMOS_DATABASE", "pseudonymisation"),
            cosmos_mapping_container=_env("COSMOS_MAPPING_CONTAINER", "mappings"),
            cosmos_trace_container=_env("COSMOS_TRACE_CONTAINER", "traces"),
            cosmos_occurrence_container=_env("COSMOS_OCCURRENCE_CONTAINER", "occurrences"),
            max_pages=_int("MAX_PAGES", 200),
            min_native_chars=_int("MIN_NATIVE_CHARS", 30),
            image_coverage_ocr_threshold=_float("IMAGE_COVERAGE_OCR_THRESHOLD", 0.6),
            force_ocr=_bool("FORCE_OCR", False),
            ocr_native_pages=_bool("OCR_NATIVE_PAGES", True),
            ocr_min_confidence=_float("OCR_MIN_CONFIDENCE", 0.75),
            ocr_render_dpi=_int("OCR_RENDER_DPI", 200),
            llm_chunk_chars=_int("LLM_CHUNK_CHARS", 12000),
            llm_chunk_overlap_lines=_int("LLM_CHUNK_OVERLAP_LINES", 3),
            llm_min_confidence=_float("LLM_MIN_CONFIDENCE", 0.3),
            verification_rounds=_int("VERIFICATION_ROUNDS", 2),
            token_length=_int("TOKEN_LENGTH", 10),
            propagation_min_length=_int("PROPAGATION_MIN_LENGTH", 3),
            fuzzy_min_score=_float("FUZZY_MIN_SCORE", 85.0),
            quarantine_on_unlocated=_bool("QUARANTINE_ON_UNLOCATED", True),
            prompt_cache_seconds=_int("PROMPT_CACHE_SECONDS", 300),
            output_mode=_output_mode(),
            handwriting_policy=_handwriting_policy(),
            handwriting_min_confidence=_float("HANDWRITING_MIN_CONFIDENCE", 0.5),
            report_detail=_report_detail(),
            classification_enabled=_bool("CLASSIFICATION_ENABLED", True),
            classification_min_confidence=_float("CLASSIFICATION_MIN_CONFIDENCE", 0.6),
            classification_secondary_min_confidence=_float("CLASSIFICATION_SECONDARY_MIN_CONFIDENCE", 0.3),
            classification_max_chars=_int("CLASSIFICATION_MAX_CHARS", 6000),
            unclassified_folder=_env("UNCLASSIFIED_FOLDER", "non-classe"),
            deposit_timezone=_env("DEPOSIT_TIMEZONE", "Europe/Paris"),
            image_processing_enabled=_bool("IMAGE_PROCESSING_ENABLED", False),
            image_extensions=tuple(
                e.strip().lower()
                for e in _env("IMAGE_EXTENSIONS", ".jpg,.jpeg,.png,.heic,.heif,.tif,.tiff,.webp,.bmp").split(",")
                if e.strip()
            ),
            image_max_pixels=_int("IMAGE_MAX_PIXELS", 50_000_000),
            image_min_short_side=_int("IMAGE_MIN_SHORT_SIDE", 1000),
            image_blur_threshold=_float("IMAGE_BLUR_THRESHOLD", 60.0),
            image_enhance_for_ocr=_bool("IMAGE_ENHANCE_FOR_OCR", True),
            image_render_dpi=_int("IMAGE_RENDER_DPI", 300),
            image_output_quality=_int("IMAGE_OUTPUT_QUALITY", 90),
            mail_processing_enabled=_bool("MAIL_PROCESSING_ENABLED", False),
            json_metadata_enabled=_bool("JSON_METADATA_ENABLED", False),
            mail_max_attachments=_int("MAIL_MAX_ATTACHMENTS", 20),
            mail_image_min_side=_int("MAIL_IMAGE_MIN_SIDE", 300),
        )
