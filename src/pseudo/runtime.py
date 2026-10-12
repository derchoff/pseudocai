"""Construction des clients Azure (identité managée) et des services du pipeline."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import urlparse

from .config import Settings
from .detection.language_pii import LanguagePiiDetector, parse_category_map
from .detection.llm import (
    AzureOpenAIJsonAgent,
    ClassificationAgent,
    DetectionAgent,
    EntityAgent,
    VerificationAgent,
)
from .extraction import DocumentIntelligenceOcr
from .mapping_store import CosmosMappingStore, MappingStore, NullMappingStore
from .outputs import SimpleStorageContext
from .pipeline import Services
from .prompts import BlobPromptSource, LocalPromptSource, PromptRepository, PromptSource
from .pseudonymizer import Pseudonymizer, ValueCipher
from .surrogates import SurrogateFactory

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


@dataclass
class Runtime:
    """Tout ce qu'il faut pour traiter un job : réglages, services, stockage des correspondances, Blob Storage."""
    settings: Settings
    services: Services
    mapping_store: MappingStore
    blob_service: object | None
    # Client du compte d'origine, s'il diffère du compte « data » (sinon None : même compte).
    source_blob_service: object | None = None


def credential():
    """Identité Azure (identité managée sur Azure, compte az login en local)."""
    from azure.identity import DefaultAzureCredential

    # Sur Azure, AZURE_CLIENT_ID désigne l'identité managée affectée par l'utilisateur.
    return DefaultAzureCredential()


def load_keys(settings: Settings, cred) -> tuple[bytes, bytes]:
    """Clés HMAC et AES : variables locales si présentes, sinon secrets Key Vault."""
    if settings.hmac_key_b64 and settings.encryption_key_b64:
        return base64.b64decode(settings.hmac_key_b64), base64.b64decode(settings.encryption_key_b64)
    if not settings.key_vault_url:
        raise RuntimeError("KEY_VAULT_URL (ou PSEUDO_HMAC_KEY_B64/PSEUDO_ENCRYPTION_KEY_B64 en local) requis")
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=settings.key_vault_url, credential=cred)
    hmac_key = base64.b64decode(client.get_secret(settings.hmac_secret_name).value)
    enc_key = base64.b64decode(client.get_secret(settings.encryption_secret_name).value)
    return hmac_key, enc_key


def _blob_service(settings: Settings, cred):
    """Client Blob Storage, ou None sans STORAGE_ACCOUNT_URL."""
    if not settings.storage_account_url:
        return None
    from azure.storage.blob import BlobServiceClient

    return BlobServiceClient(settings.storage_account_url, credential=cred)


def _source_blob_service(settings: Settings, cred):
    """Client du compte d'origine, ou None s'il s'agit du compte « data »."""
    url = settings.source_storage_account_url
    if not url or _host(url) == _host(settings.storage_account_url):
        return None
    from azure.storage.blob import BlobServiceClient

    return BlobServiceClient(url, credential=cred)


def _host(url: str | None) -> str:
    """Nom d'hôte d'une URL de compte, en minuscules (ex. stdata.blob.core.windows.net)."""
    return (urlparse(url or "").hostname or "").lower()


def _mapping_store(settings: Settings, cred) -> MappingStore:
    """Stockage Cosmos DB, ou stockage vide sans COSMOS_ENDPOINT."""
    if not settings.cosmos_endpoint:
        return NullMappingStore()
    from azure.cosmos import CosmosClient

    database = CosmosClient(settings.cosmos_endpoint, credential=cred).get_database_client(settings.cosmos_database)
    return CosmosMappingStore(
        database,
        settings.cosmos_mapping_container,
        settings.cosmos_trace_container,
        settings.cosmos_occurrence_container,
    )


def build_storage_context(settings: Settings) -> SimpleStorageContext:
    """Contexte léger (stockage + traces) pour le routage et la préparation des images :
    ni Azure OpenAI, ni Document Intelligence, ni clés de pseudonymisation."""
    cred = credential()
    blob_service = _blob_service(settings, cred)
    if blob_service is None:
        raise RuntimeError("STORAGE_ACCOUNT_URL requis")
    return SimpleStorageContext(
        settings=settings,
        blob_service=blob_service,
        mapping_store=_mapping_store(settings, cred),
        source_blob_service=_source_blob_service(settings, cred),
    )


def build_runtime(
    settings: Settings,
    *,
    prompts_dir: str | None = None,
    mapping_store: MappingStore | None = None,
    enable_language: bool = True,
    enable_verification: bool = True,
) -> Runtime:
    """Crée les clients Azure, charge les prompts et les clés, et assemble les services.

    Échoue tout de suite si un référentiel Markdown est invalide.
    """
    cred = credential()

    blob_service = _blob_service(settings, cred)

    source: PromptSource
    if prompts_dir:
        source = LocalPromptSource(prompts_dir)
    else:
        if blob_service is None:
            raise RuntimeError("STORAGE_ACCOUNT_URL requis pour lire les prompts")
        source = BlobPromptSource(
            blob_service.get_container_client(settings.config_container),
            settings.prompts_prefix,
            settings.prompt_cache_seconds,
        )
    prompts = PromptRepository(source)
    # Échec immédiat (et visible dans les journaux) si un référentiel Markdown est invalide.
    prompts.detection()
    prompts.verification()
    if settings.classification_enabled:
        prompts.classification()

    # Azure OpenAI ----------------------------------------------------------------------------
    from azure.identity import get_bearer_token_provider
    from openai import AzureOpenAI

    if not (settings.openai_endpoint and settings.openai_deployment):
        raise RuntimeError("OPENAI_ENDPOINT et OPENAI_DEPLOYMENT requis")
    openai_client = AzureOpenAI(
        azure_endpoint=settings.openai_endpoint,
        azure_ad_token_provider=get_bearer_token_provider(cred, COGNITIVE_SCOPE),
        api_version=settings.openai_api_version,
        max_retries=6,
        timeout=180,
    )
    llm = AzureOpenAIJsonAgent(
        openai_client,
        settings.openai_deployment,
        reasoning_effort=settings.openai_reasoning_effort,
        temperature=settings.openai_temperature,
        max_completion_tokens=settings.openai_max_completion_tokens,
    )
    agent_kwargs = dict(
        chunk_chars=settings.llm_chunk_chars,
        overlap_lines=settings.llm_chunk_overlap_lines,
        min_confidence=settings.llm_min_confidence,
    )
    detector = DetectionAgent(EntityAgent(llm, prompts.detection, source="llm", **agent_kwargs))
    verifier = (
        VerificationAgent(EntityAgent(llm, prompts.verification, source="verifier", **agent_kwargs))
        if enable_verification and settings.verification_rounds > 0
        else None
    )

    classifier = (
        ClassificationAgent(llm, prompts.classification, max_chars=settings.classification_max_chars)
        if settings.classification_enabled
        else None
    )

    # Document Intelligence (OCR des pages scannées) -----------------------------------------
    ocr = None
    if settings.docintel_endpoint:
        from azure.ai.documentintelligence import DocumentIntelligenceClient

        ocr = DocumentIntelligenceOcr(
            DocumentIntelligenceClient(settings.docintel_endpoint, cred),
            settings.docintel_model,
            handwriting_min_confidence=settings.handwriting_min_confidence,
        )

    # Azure AI Language (filet de sécurité) ---------------------------------------------------
    language = None
    if enable_language and settings.language_endpoint:
        from azure.ai.textanalytics import TextAnalyticsClient

        language = LanguagePiiDetector(
            TextAnalyticsClient(settings.language_endpoint, cred),
            parse_category_map(settings.language_pii_categories),
            settings.language_min_confidence,
        )

    hmac_key, enc_key = load_keys(settings, cred)
    services = Services(
        detector=detector,
        verifier=verifier,
        ocr=ocr,
        language=language,
        pseudonymizer=Pseudonymizer(hmac_key, settings.token_length),
        cipher=ValueCipher(enc_key),
        fields=prompts.catalog,
        classifier=classifier,
        surrogates=SurrogateFactory(hmac_key),
    )

    if mapping_store is None:
        mapping_store = _mapping_store(settings, cred)

    return Runtime(
        settings=settings,
        services=services,
        mapping_store=mapping_store,
        blob_service=blob_service,
        source_blob_service=_source_blob_service(settings, cred),
    )
