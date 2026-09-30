"""Deterministic scientific entity extraction (regex + gazetteers; no model calls).

Extracts DOIs, arXiv ids, http(s) URLs (credentials stripped, secret-looking query values redacted),
datasets, methods, models, metric mentions (``accuracy of 92.3%``, ``F1 = 0.81``, ``BLEU 34.2``,
``p < 0.05``) and author–year citations. Email addresses are counted but never returned. Name-based
extractors run on the text with URLs and emails masked out so identifiers inside links do not leak into
the dataset/model/method lists. Output lists keep first-occurrence order and are capped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

ENTITY_ENGINE_VERSION = "entities-1.0.0"
MAX_ENTITY_TEXT_CHARS = 2_000_000
MAX_PER_KIND = 200
MAX_URL_CHARS = 2000


class MetricMention(BaseModel):
    name: str
    value: float
    unit: str | None = None
    comparator: str = "="


class ExtractedEntities(BaseModel):
    dois: list[str] = Field(default_factory=list)
    arxiv_ids: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    emails_redacted_count: int = 0
    datasets: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    metrics: list[MetricMention] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    engine_version: str = ENTITY_ENGINE_VERSION


# =============================================================================================
# Helpers
# =============================================================================================
def _dedupe(values: Iterable[str], *, casefold: bool = True, limit: int = MAX_PER_KIND) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.casefold() if casefold else value
        if value and key not in seen:
            seen.add(key)
            out.append(value)
            if len(out) >= limit:
                break
    return out


_CLOSERS = {")": "(", "]": "[", "}": "{"}


def _strip_trailing(value: str) -> str:
    """Remove trailing punctuation and unbalanced closing brackets (common when ids end a sentence)."""
    while value:
        last = value[-1]
        opener = _CLOSERS.get(last)
        unbalanced = opener is not None and value.count(opener) < value.count(last)
        if last not in ".,;:!?'\"*>" and not unbalanced:
            break
        value = value[:-1]
    return value


def _alternation(terms: Iterable[str]) -> str:
    return "|".join(re.escape(term) for term in sorted(set(terms), key=lambda t: (-len(t), t)))


# =============================================================================================
# Identifiers
# =============================================================================================
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+")
_ARXIV_NEW = r"\d{4}\.\d{4,5}(?:v\d+)?"
_ARXIV_OLD = r"[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?"
_ARXIV_RE = re.compile(
    rf"(?:\barXiv\s*:?\s*|arxiv\.org/(?:abs|pdf)/)(?P<id>{_ARXIV_NEW}|{_ARXIV_OLD})(?![\d])", re.IGNORECASE
)
_URL_RE = re.compile(r"\bhttps?://[^\s<>\"'`{}|\\^\[\]]+", re.IGNORECASE)
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}\b"
)
SENSITIVE_QUERY_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "api-key",
        "key",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "pwd",
        "sig",
        "signature",
        "auth",
        "authorization",
        "session",
        "sessionid",
        "code",
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "x-goog-signature",
        "x-goog-credential",
    }
)


def _valid_arxiv(identifier: str) -> bool:
    if "/" in identifier:
        return True
    month = int(identifier[2:4])
    return 1 <= month <= 12


def sanitize_url(raw: str) -> str | None:
    """Return a safe http(s) URL: userinfo removed, secret-looking query values redacted; None if invalid."""
    url = _strip_trailing(raw)[:MAX_URL_CHARS]
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    netloc = hostname if port is None else f"{hostname}:{port}"
    if ":" in hostname:  # IPv6 literal
        netloc = f"[{hostname}]" if port is None else f"[{hostname}]:{port}"
    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        if any(key.lower() in SENSITIVE_QUERY_KEYS for key, _ in pairs):
            query = urlencode(
                [(key, "REDACTED" if key.lower() in SENSITIVE_QUERY_KEYS else value) for key, value in pairs]
            )
    path, fragment = _redact_emails(parts.path), _redact_emails(parts.fragment)
    return urlunsplit((parts.scheme.lower(), netloc, path, _redact_emails(query), fragment))


_ENCODED_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}(?:@|%40)[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
    r"\.[A-Za-z]{2,24}",
    re.IGNORECASE,
)


def _redact_emails(value: str) -> str:
    return _ENCODED_EMAIL_RE.sub("REDACTED", value) if value else value


# =============================================================================================
# Gazetteers
# =============================================================================================
KNOWN_DATASETS: tuple[str, ...] = (
    "ImageNet",
    "ImageNet-1k",
    "ImageNet-21k",
    "CIFAR-10",
    "CIFAR-100",
    "MNIST",
    "Fashion-MNIST",
    "EMNIST",
    "SVHN",
    "STL-10",
    "CelebA",
    "LSUN",
    "COCO",
    "MS COCO",
    "MS-COCO",
    "Pascal VOC",
    "PASCAL VOC",
    "ADE20K",
    "Cityscapes",
    "KITTI",
    "nuScenes",
    "GLUE",
    "SuperGLUE",
    "SQuAD",
    "MNLI",
    "MultiNLI",
    "SNLI",
    "SST-2",
    "QNLI",
    "QQP",
    "CoLA",
    "MRPC",
    "STS-B",
    "IMDb",
    "AG News",
    "WikiText-2",
    "WikiText-103",
    "Penn Treebank",
    "PTB",
    "C4",
    "The Pile",
    "OpenWebText",
    "BookCorpus",
    "Common Crawl",
    "LAION-400M",
    "LAION-5B",
    "MMLU",
    "HellaSwag",
    "GSM8K",
    "HumanEval",
    "MBPP",
    "BIG-bench",
    "TriviaQA",
    "Natural Questions",
    "HotpotQA",
    "WMT14",
    "WMT16",
    "WMT19",
    "LibriSpeech",
    "Common Voice",
    "VoxCeleb",
    "AudioSet",
    "MIMIC-III",
    "MIMIC-IV",
    "UK Biobank",
    "PubMedQA",
    "BioASQ",
    "Cora",
    "Citeseer",
    "QM9",
    "ZINC",
    "Kinetics-400",
    "UCF101",
    "HMDB51",
    "MovieLens",
)
METHOD_TERMS: tuple[str, ...] = (
    "dropout",
    "batch normalization",
    "layer normalization",
    "weight decay",
    "data augmentation",
    "knowledge distillation",
    "contrastive learning",
    "transfer learning",
    "fine-tuning",
    "self-attention",
    "multi-head attention",
    "gradient boosting",
    "random forest",
    "logistic regression",
    "linear regression",
    "support vector machine",
    "k-means",
    "principal component analysis",
    "cross-validation",
    "bayesian optimization",
    "beam search",
    "early stopping",
    "label smoothing",
    "mixup",
    "reinforcement learning",
    "curriculum learning",
    "federated learning",
    "active learning",
    "retrieval-augmented generation",
    "chain-of-thought",
    "in-context learning",
    "instruction tuning",
    "stochastic gradient descent",
    "gradient descent",
    "markov chain monte carlo",
    "monte carlo",
    "bootstrap",
    "t-test",
    "mann-whitney",
    "wilcoxon",
    "anova",
)
METHOD_ACRONYMS: tuple[str, ...] = (
    "Adam",
    "AdamW",
    "SGD",
    "RMSProp",
    "LoRA",
    "QLoRA",
    "RLHF",
    "DPO",
    "PPO",
    "TRPO",
    "DQN",
    "GAN",
    "VAE",
    "CNN",
    "RNN",
    "LSTM",
    "GRU",
    "MLP",
    "Transformer",
    "BatchNorm",
    "LayerNorm",
    "PCA",
    "t-SNE",
    "UMAP",
    "SVM",
    "XGBoost",
    "LightGBM",
    "CatBoost",
    "RAG",
    "MCMC",
    "k-NN",
    "KNN",
    "BPE",
    "SMOTE",
    "DDPM",
    "DDIM",
    "NeRF",
    "GNN",
    "GCN",
    "GAT",
    "ReLU",
    "GELU",
)
_MODEL_FAMILIES: tuple[str, ...] = (
    "GPT",
    "ChatGPT",
    "InstructGPT",
    "GPT-J",
    "GPT-NeoX",
    "BERT",
    "RoBERTa",
    "DeBERTa",
    "DistilBERT",
    "ALBERT",
    "ELECTRA",
    "XLNet",
    "XLM-R",
    "T5",
    "mT5",
    "Flan-T5",
    "BART",
    "LLaMA",
    "Llama",
    "Code Llama",
    "CodeLlama",
    "Mistral",
    "Mixtral",
    "Falcon",
    "PaLM",
    "Gemini",
    "Gemma",
    "Claude",
    "Qwen",
    "Phi",
    "OPT",
    "BLOOM",
    "Pythia",
    "Vicuna",
    "Alpaca",
    "StarCoder",
    "Codex",
    "Chinchilla",
    "Gopher",
    "ResNet",
    "ResNeXt",
    "Wide ResNet",
    "VGG",
    "ViT",
    "DeiT",
    "Swin",
    "ConvNeXt",
    "EfficientNet",
    "MobileNet",
    "DenseNet",
    "AlexNet",
    "Inception",
    "GoogLeNet",
    "YOLO",
    "DETR",
    "CLIP",
    "BLIP",
    "DALL-E",
    "Stable Diffusion",
    "Whisper",
    "wav2vec",
    "HuBERT",
    "AlphaFold",
    "ESM",
)
#: Families that are ordinary words/acronyms on their own and only count with a version/size suffix.
_SUFFIX_REQUIRED = frozenset({"Phi", "OPT", "ESM", "Swin", "Inception", "Falcon", "Mistral", "Gemini", "Whisper"})
GENERIC_ACRONYMS = frozenset(
    {
        "LLM",
        "LLMs",
        "AI",
        "ML",
        "NLP",
        "CV",
        "API",
        "APIs",
        "GPU",
        "GPUs",
        "CPU",
        "CPUs",
        "TPU",
        "TPUs",
        "USA",
        "UK",
        "EU",
        "PDF",
        "URL",
        "DOI",
        "FAQ",
        "ID",
        "IDs",
        "SOTA",
    }
)
_NOT_DATASET_NAMES = frozenset(
    {
        "The",
        "This",
        "That",
        "These",
        "Those",
        "Our",
        "Their",
        "Its",
        "A",
        "An",
        "Each",
        "Every",
        "Same",
        "Training",
        "Test",
        "Testing",
        "Validation",
        "Full",
        "Original",
        "New",
        "Public",
        "Large",
        "Small",
        "Synthetic",
        "Benchmark",
        "Real",
        "Private",
        "Standard",
        "Existing",
        "Proposed",
        "Entire",
        "Whole",
        "Clean",
        "Raw",
        "Final",
        "Other",
        "Both",
        "All",
        "Two",
        "Three",
        "Several",
        "Multiple",
        "Different",
        "Such",
        "Held-out",
    }
)
_HARDWARE = frozenset({"GPU", "GPUs", "TPU", "TPUs", "CPU", "CPUs", "A100", "V100", "H100", "T4", "RTX", "NVIDIA"})

_DATASET_KNOWN_RE = re.compile(rf"(?<![\w-])(?:{_alternation(KNOWN_DATASETS)})(?![\w-])")
_DATASET_PHRASE_RE = re.compile(
    r"(?<![\w-])(?P<name>[A-Z][\w-]{0,40}(?:[ \t]{1,3}[A-Z0-9][\w-]{0,40}){0,3})[ \t]{1,3}"
    r"(?:dataset|data set|corpus|benchmark|database)s?\b"
)
_DATASET_ON_RE = re.compile(
    r"\b(?:trained|evaluated|tested|fine-?tuned|pre-?trained|benchmarked|experiments?|results)\s+on\s+(?:the\s+)?"
    r"(?P<name>[A-Z][A-Za-z0-9]{0,40}(?:-[A-Za-z0-9]{1,20}){0,4})(?![\w-])"
)
_METHOD_TERM_RE = re.compile(rf"(?<![\w-])(?:{_alternation(METHOD_TERMS)})(?![\w-])", re.IGNORECASE)
_METHOD_ACRONYM_RE = re.compile(rf"(?<![\w-])(?:{_alternation(METHOD_ACRONYMS)})(?![\w-])")
_NET_RE = re.compile(r"(?<![\w-])[A-Z][A-Za-z0-9]{0,40}-?Net(?:\+\+)?(?![\w-])")
_ACRONYM_DEF_RE = re.compile(
    r"(?<![A-Za-z-])(?P<exp>[A-Za-z][A-Za-z-]{0,39}(?:[ \t\n]{1,3}[A-Za-z][A-Za-z-]{0,39}){1,7})[ \t]*"
    r"\((?P<acr>[A-Z][A-Za-z0-9-]{1,11})\)"
)
_ACRONYM_SKIP_WORDS = frozenset({"of", "the", "and", "for", "from", "with", "to", "in", "on", "via", "by", "a", "an"})
_MODEL_SUFFIX = (
    r"(?:[- ]?(?:\d+(?:\.\d+)*[A-Za-z]{0,2}|[vV]\d+(?:\.\d+)*|[BLHS]/\d+|"
    r"(?i:base|large|small|tiny|xl|xxl|mini|nano|turbo|instruct|chat|pro|ultra|flash|opus|sonnet|haiku))\b){0,3}"
)
_MODEL_RE = re.compile(rf"(?<![\w-])(?P<family>{_alternation(_MODEL_FAMILIES)})(?P<suffix>{_MODEL_SUFFIX})(?!\w)")
_MODEL_SIZE_RE = re.compile(
    r"(?<![\w-])[A-Z][A-Za-z0-9]{0,30}(?:-[A-Za-z0-9]{1,20}){0,4}-\d{1,4}(?:\.\d{1,2})?[BM](?![\w-])"
)

# =============================================================================================
# Metrics
# =============================================================================================
_METRIC_NAMES = (
    r"top-1 accuracy|top-5 accuracy|balanced accuracy|exact match|error rate|inception score|log[- ]loss|"
    r"cross[- ]entropy|macro[- ]f1|micro[- ]f1|f1[- ]score|auc-roc|roc[- ]auc|auroc|auprc|"
    r"spearman(?:'s)?(?: rho| correlation)?|pearson(?:'s)?(?: r| correlation)?|kendall(?:'s)? tau|"
    r"cohen'?s d|cohen'?s kappa|effect size|rouge-(?:l|1|2|lsum)|bleu(?:-\d)?|pass@\d+|ndcg(?:@\d+)?|"
    r"mrr(?:@\d+)?|recall@\d+|precision@\d+|hits@\d+|top-1|top-5|accuracy|precision|recall|f1|auc|"
    r"(?-i:mAP)|rouge|meteor|chrf|perplexity|ppl|rmse|mse|mae|mape|r\^2|r²|r2|loss|wer|cer|miou|iou|dice|"
    r"(?-i:EM)|fid|psnr|ssim|lpips|speedup|latency|throughput"
)
_VALUE = r"(?P<value>[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?)"
_UNIT = r"(?P<unit>%|percent\b|pp\b|points\b|ms\b|sec\b|seconds\b|s\b|x\b|×|dB\b|tokens/s\b)?"
_CONNECTOR = (
    r"(?:\s+on\s+(?:the\s+)?[\w-]+(?:\s+(?:set|split))?)?\s*(?:\([^()\n]{0,20}\)\s*)?"
    r"(?:(?:of|=|:|was|is|are|reached|reaches|achieves?|achieved|at|scores?|improved to|increased to|"
    r"decreased to|drops? to|≈|~)\s*)?"
)
_METRIC_NAME_FIRST_RE = re.compile(
    rf"(?<![\w-])(?P<name>{_METRIC_NAMES})(?![\w-]){_CONNECTOR}{_VALUE}\s?{_UNIT}", re.IGNORECASE
)
_METRIC_VALUE_FIRST_RE = re.compile(
    rf"(?<![\w.-]){_VALUE}\s?(?P<unit>%|percent\b)?\s+(?P<name>top-1 accuracy|top-5 accuracy|accuracy|precision|recall|"
    r"f1(?:[- ]score)?|auc|auroc|bleu|rouge-l|rouge|(?-i:mAP)|perplexity|wer|cer|miou|exact match)(?![\w-])",
    re.IGNORECASE,
)
_P_VALUE_RE = re.compile(
    r"(?<![\w])p(?:[- ]?values?)?\s*(?P<cmp><=|>=|≤|≥|<|>|=|of|was|is)\s*"
    r"(?P<mant>\d*\.?\d+(?:[eE][-−+]?\d+)?)(?:\s*[x×]\s*10\s*\^?\s*(?P<exp>[-−+]?\d+))?",
)
_METRIC_ALIASES: dict[str, str] = {
    "f1 score": "f1",
    "f1-score": "f1",
    "auc-roc": "auroc",
    "roc-auc": "auroc",
    "roc auc": "auroc",
    "r^2": "r2",
    "r²": "r2",
    "top-1": "top-1 accuracy",
    "top-5": "top-5 accuracy",
    "log-loss": "log loss",
    "cross-entropy": "cross entropy",
    "ppl": "perplexity",
    "em": "exact match",
    "macro-f1": "macro f1",
    "micro-f1": "micro f1",
}
_UNIT_ALIASES = {"percent": "%", "sec": "s", "seconds": "s", "×": "x"}
_CMP_ALIASES = {"≤": "<=", "≥": ">=", "of": "=", "was": "=", "is": "="}


def _metric_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw.strip().lower())
    name = _METRIC_ALIASES.get(name, name)
    if name.startswith(("spearman", "pearson", "kendall")):
        return name.split("'")[0].split(" ")[0]
    if name.startswith("cohen"):
        return "cohens " + name.rsplit(" ", 1)[-1]
    return name


def _metrics(prose: str) -> list[MetricMention]:
    found: list[tuple[int, MetricMention]] = []
    for match in _METRIC_NAME_FIRST_RE.finditer(prose):
        unit = match.group("unit")
        found.append(
            (
                match.start(),
                MetricMention(
                    name=_metric_name(match.group("name")),
                    value=float(match.group("value")),
                    unit=_UNIT_ALIASES.get(unit.lower(), unit.lower()) if unit else None,
                ),
            )
        )
    for match in _METRIC_VALUE_FIRST_RE.finditer(prose):
        unit = match.group("unit")
        found.append(
            (
                match.start(),
                MetricMention(
                    name=_metric_name(match.group("name")),
                    value=float(match.group("value")),
                    unit=_UNIT_ALIASES.get(unit.lower(), unit.lower()) if unit else None,
                ),
            )
        )
    for match in _P_VALUE_RE.finditer(prose):
        mantissa = match.group("mant").replace("−", "-")
        exponent = match.group("exp")
        value = float(f"{mantissa}e{int(exponent.replace('−', '-'))}") if exponent else float(mantissa)
        if not 0.0 <= value <= 1.0:
            continue
        cmp_raw = match.group("cmp")
        found.append(
            (
                match.start(),
                MetricMention(name="p", value=value, unit=None, comparator=_CMP_ALIASES.get(cmp_raw, cmp_raw)),
            )
        )
    found.sort(key=lambda item: item[0])
    seen: set[tuple[str, float, str | None, str]] = set()
    out: list[MetricMention] = []
    for _, mention in found:
        key = (mention.name, mention.value, mention.unit, mention.comparator)
        if key not in seen:
            seen.add(key)
            out.append(mention)
            if len(out) >= MAX_PER_KIND:
                break
    return out


# =============================================================================================
# Names
# =============================================================================================
def _models(prose: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for match in _MODEL_RE.finditer(prose):
        family, suffix = match.group("family"), match.group("suffix")
        if family in _SUFFIX_REQUIRED and not suffix:
            continue
        found.append((match.start(), (family + suffix).strip()))
    for match in _MODEL_SIZE_RE.finditer(prose):
        found.append((match.start(), match.group(0)))
    found.sort(key=lambda item: item[0])
    return _dedupe(name for _, name in found)


def _initials(expansion: str) -> str:
    words = [w for w in re.split(r"[\s-]+", expansion) if w and w.lower() not in _ACRONYM_SKIP_WORDS]
    return "".join(word[0].upper() for word in words)


def _defined_acronyms(prose: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for match in _ACRONYM_DEF_RE.finditer(prose):
        acronym = match.group("acr")
        letters = "".join(ch for ch in acronym if ch.isupper())
        if len(letters) < 2:
            continue
        initials = _initials(match.group("exp"))
        if initials.endswith(letters):
            out.append((match.start("acr"), acronym))
    return out


def _is_metric_word(name: str) -> bool:
    return bool(re.fullmatch(_METRIC_NAMES, name, re.IGNORECASE))


def _datasets(prose: str, models: set[str]) -> list[str]:
    known_lower = {name.casefold() for name in KNOWN_DATASETS}
    found: list[tuple[int, str]] = [(m.start(), m.group(0)) for m in _DATASET_KNOWN_RE.finditer(prose)]
    for match in _DATASET_PHRASE_RE.finditer(prose):
        tokens = match.group("name").split()
        while tokens and tokens[0] in _NOT_DATASET_NAMES:
            tokens.pop(0)
        name = " ".join(tokens)
        if name and name.casefold() not in models and name not in _HARDWARE and name not in GENERIC_ACRONYMS:
            found.append((match.start("name") + match.group("name").find(tokens[0]), name))
    for match in _DATASET_ON_RE.finditer(prose):
        name = match.group("name")
        datasetish = any(ch.isdigit() for ch in name) or sum(ch.isupper() for ch in name) >= 2
        if (
            datasetish
            and name.casefold() not in models
            and name not in _HARDWARE
            and name not in GENERIC_ACRONYMS
            and name not in _NOT_DATASET_NAMES
        ) or name.casefold() in known_lower:
            found.append((match.start("name"), name))
    found.sort(key=lambda item: item[0])
    return _dedupe(name for _, name in found)


def _methods(prose: str, models: set[str], datasets: set[str]) -> list[str]:
    found: list[tuple[int, str]] = []
    found.extend((m.start(), m.group(0).lower()) for m in _METHOD_TERM_RE.finditer(prose))
    found.extend((m.start(), m.group(0)) for m in _METHOD_ACRONYM_RE.finditer(prose))
    found.extend((m.start(), m.group(0)) for m in _NET_RE.finditer(prose))
    found.extend(_defined_acronyms(prose))
    found.sort(key=lambda item: item[0])
    out: list[str] = []
    for _, name in found:
        folded = name.casefold()
        if folded in models or folded in datasets or name in GENERIC_ACRONYMS or _is_metric_word(name):
            continue
        if any(model.startswith(folded) for model in models):
            continue
        out.append(name)
    return _dedupe(out)


_AUTHOR = r"[A-Z][A-Za-z'’-]{1,40}(?:\s{1,3}(?:et\s{1,3}al\.?|and\s{1,3}[A-Z][A-Za-z'’-]{1,40}|&\s{1,3}[A-Z][A-Za-z'’-]{1,40}))?"
_YEAR = r"(?:19|20)\d{2}[a-z]?"
_PAREN_RE = re.compile(r"\((?P<body>[^()]{4,300})\)")
_PAREN_PART_RE = re.compile(
    rf"^\s*(?:see\s+|e\.g\.,?\s+|cf\.\s+|i\.e\.,?\s+)?(?P<authors>{_AUTHOR}),?\s+(?P<year>{_YEAR})\s*$"
)
_NARRATIVE_RE = re.compile(rf"(?<![\w-])(?P<authors>{_AUTHOR})\s+[(\[](?P<year>{_YEAR})[)\]]")
_NOT_AUTHORS = frozenset(
    {
        "In",
        "On",
        "At",
        "The",
        "This",
        "Since",
        "By",
        "From",
        "For",
        "And",
        "Of",
        "To",
        "After",
        "Before",
        "During",
        "Until",
        "Published",
        "Released",
        "Version",
        "Table",
        "Figure",
        "Fig",
        "Section",
        "Eq",
        "Equation",
        "Year",
        "Updated",
        "Copyright",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    }
)


def _citation(authors: str, year: str) -> str | None:
    authors = re.sub(r"\s+", " ", authors.strip())
    surname = authors.split(" ")[0]
    if surname in _NOT_AUTHORS or not any(ch.islower() for ch in surname):
        return None
    authors = re.sub(r"\bet al\b\.?", "et al.", authors).replace(" & ", " and ")
    return f"{authors}, {year}"


def _citations(prose: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for match in _PAREN_RE.finditer(prose):
        offset = match.start("body")
        for part in match.group("body").split(";"):
            cite = _PAREN_PART_RE.match(" ".join(part.split()))
            if cite:
                value = _citation(cite.group("authors"), cite.group("year"))
                if value:
                    found.append((offset, value))
            offset += len(part) + 1
    for match in _NARRATIVE_RE.finditer(prose):
        value = _citation(match.group("authors"), match.group("year"))
        if value:
            found.append((match.start(), value))
    found.sort(key=lambda item: item[0])
    return _dedupe(value for _, value in found)


def extract_entities(text: str, *, max_chars: int = MAX_ENTITY_TEXT_CHARS) -> ExtractedEntities:
    """Extract scientific entities from (untrusted) text. Deterministic; never returns email addresses."""
    text = text[:max_chars]
    urls: list[str] = []
    for match in _URL_RE.finditer(text):
        safe = sanitize_url(match.group(0))
        if safe:
            urls.append(safe)
    without_urls = _URL_RE.sub(" ", text)
    emails = {match.group(0).lower() for match in _EMAIL_RE.finditer(without_urls)}
    prose = _EMAIL_RE.sub(" ", without_urls)
    identifier_text = prose + "\n" + "\n".join(urls)
    dois = [_strip_trailing(m.group(0)).lower() for m in _DOI_RE.finditer(identifier_text)]
    arxiv = [m.group("id") for m in _ARXIV_RE.finditer(identifier_text) if _valid_arxiv(m.group("id"))]
    models = _models(prose)
    model_keys = {name.casefold() for name in models}
    datasets = _datasets(prose, model_keys)
    methods = _methods(prose, model_keys, {name.casefold() for name in datasets})
    return ExtractedEntities(
        dois=_dedupe(doi for doi in dois if "/" in doi and len(doi) > 8),
        arxiv_ids=_dedupe(arxiv),
        urls=_dedupe(urls, casefold=False),
        emails_redacted_count=len(emails),
        datasets=datasets,
        methods=methods,
        metrics=_metrics(prose),
        models=models,
        citations=_citations(prose),
    )
