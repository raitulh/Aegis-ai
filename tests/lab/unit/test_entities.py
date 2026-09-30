"""Entity extraction: identifiers, URLs, redaction, datasets, methods, models, metrics, citations."""

from __future__ import annotations

import pytest

from engines.lab.ingestion import extract_entities, sanitize_url
from engines.lab.ingestion.entities import MetricMention

ABSTRACT = """
We fine-tuned BERT-large and GPT-4 on the CIFAR-10 dataset and evaluated on WMT14 (Smith et al., 2020;
Lee and Kim, 2019). Following Vaswani et al. (2017), we use a Transformer with Low-Rank Adaptation (LoRA),
AdamW and dropout, plus a U-Net decoder. Our model reached an accuracy of 92.3% and F1 = 0.81, with BLEU 34.2
(p < 0.05); we also measured 0.95 AUC. Code: https://github.com/org/repo. Paper: doi:10.1038/nature12373.
Preprint arXiv:2301.01234v2. Contact alice@example.com or bob.smith@lab.example.org.
"""


@pytest.fixture(scope="module")
def entities():
    return extract_entities(ABSTRACT)


def test_identifiers(entities):
    assert entities.dois == ["10.1038/nature12373"]
    assert entities.arxiv_ids == ["2301.01234v2"]
    assert entities.urls == ["https://github.com/org/repo"]


def test_emails_are_counted_never_returned(entities):
    assert entities.emails_redacted_count == 2
    dumped = entities.model_dump_json()
    assert "alice@" not in dumped and "example.com" not in dumped and "@" not in dumped


def test_datasets(entities):
    assert entities.datasets == ["CIFAR-10", "WMT14"]


def test_models(entities):
    assert entities.models == ["BERT-large", "GPT-4"]


def test_methods(entities):
    assert entities.methods == ["Transformer", "LoRA", "AdamW", "dropout", "U-Net"]


def test_metrics(entities):
    assert entities.metrics == [
        MetricMention(name="accuracy", value=92.3, unit="%"),
        MetricMention(name="f1", value=0.81),
        MetricMention(name="bleu", value=34.2),
        MetricMention(name="p", value=0.05, comparator="<"),
        MetricMention(name="auc", value=0.95),
    ]


def test_citations(entities):
    assert entities.citations == ["Smith et al., 2020", "Lee and Kim, 2019", "Vaswani et al., 2017"]


def test_extraction_is_deterministic():
    assert extract_entities(ABSTRACT) == extract_entities(ABSTRACT)


def test_empty_text():
    empty = extract_entities("")
    assert empty.dois == [] and empty.metrics == [] and empty.emails_redacted_count == 0


# ---------------------------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------------------------
def test_doi_trailing_punctuation_parentheses_and_dedupe():
    text = (
        "See 10.1016/S0140-6736(20)30183-5. Also (doi 10.1000/XYZ123), again 10.1000/xyz123; and "
        "https://doi.org/10.5555/abc.def."
    )
    assert extract_entities(text).dois == ["10.1016/s0140-6736(20)30183-5", "10.1000/xyz123", "10.5555/abc.def"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("arXiv:1706.03762", ["1706.03762"]),
        ("see arxiv.org/abs/2106.09685v3 for details", ["2106.09685v3"]),
        ("https://arxiv.org/pdf/2203.02155.pdf", ["2203.02155"]),
        ("arXiv:hep-th/9901001", ["hep-th/9901001"]),
        ("arXiv:math.GT/0309136v2", ["math.GT/0309136v2"]),
        ("arXiv:2313.01234 (invalid month)", []),
        ("the number 2301.01234 alone is not an arXiv id", []),
    ],
)
def test_arxiv_ids(text, expected):
    assert extract_entities(text).arxiv_ids == expected


# ---------------------------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------------------------
def test_urls_http_only_trailing_punctuation_and_dedupe():
    text = (
        "Links: https://example.org/a, http://example.org/b. ftp://files.example.org/x "
        "(https://en.wikipedia.org/wiki/Foo_(bar)) https://example.org/a javascript:alert(1)"
    )
    assert extract_entities(text).urls == [
        "https://example.org/a",
        "http://example.org/b",
        "https://en.wikipedia.org/wiki/Foo_(bar)",
    ]


def test_urls_strip_credentials_and_redact_secrets():
    urls = extract_entities(
        "https://user:hunter2@Example.ORG:8443/data?id=3&token=abc123&X-Amz-Signature=deadbeef#frag"
    ).urls
    assert urls == ["https://example.org:8443/data?id=3&token=REDACTED&X-Amz-Signature=REDACTED#frag"]
    assert "hunter2" not in urls[0]


def test_urls_redact_embedded_emails():
    entities = extract_entities("https://example.org/unsubscribe?email=alice%40example.com&x=1")
    assert "alice" not in entities.urls[0]
    assert "REDACTED" in entities.urls[0]


@pytest.mark.parametrize("raw", ["https://", "https://:80", "http://[::1", "mailto:a@b.co"])
def test_sanitize_url_rejects_invalid(raw):
    assert sanitize_url(raw) is None


def test_sanitize_url_ipv6():
    assert sanitize_url("http://[2001:db8::1]:8080/x") == "http://[2001:db8::1]:8080/x"


# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("an accuracy of 92.3%", [MetricMention(name="accuracy", value=92.3, unit="%")]),
        ("F1-score: 0.77", [MetricMention(name="f1", value=0.77)]),
        ("ROC-AUC = 0.91", [MetricMention(name="auroc", value=0.91)]),
        ("top-1 accuracy was 76.1 percent", [MetricMention(name="top-1 accuracy", value=76.1, unit="%")]),
        ("RMSE of 1.25", [MetricMention(name="rmse", value=1.25)]),
        ("perplexity reached 12.5", [MetricMention(name="perplexity", value=12.5)]),
        ("a 3.2x speedup", []),
        ("speedup of 3.2x", [MetricMention(name="speedup", value=3.2, unit="x")]),
        ("latency of 20 ms", [MetricMention(name="latency", value=20.0, unit="ms")]),
        ("mAP 41.2", [MetricMention(name="map", value=41.2)]),
        ("81.5% top-1 accuracy", [MetricMention(name="top-1 accuracy", value=81.5, unit="%")]),
        ("0.88 F1", [MetricMention(name="f1", value=0.88)]),
        ("p = 0.003", [MetricMention(name="p", value=0.003)]),
        ("p-value of 0.04", [MetricMention(name="p", value=0.04)]),
        ("p ≤ .01", [MetricMention(name="p", value=0.01, comparator="<=")]),
        ("p < 1e-4", [MetricMention(name="p", value=0.0001, comparator="<")]),
        ("p < 3 × 10^-4", [MetricMention(name="p", value=0.0003, comparator="<")]),
        ("p = 3 is not a probability", []),
        ("the map of the region", []),
    ],
)
def test_metric_mentions(text, expected):
    assert extract_entities(text).metrics == expected


def test_duplicate_metric_mentions_are_merged():
    assert len(extract_entities("accuracy of 90% ... accuracy of 90%").metrics) == 1


# ---------------------------------------------------------------------------------------------
# Datasets, models, methods
# ---------------------------------------------------------------------------------------------
def test_dataset_phrases_and_filters():
    text = (
        "We evaluate on the Penn Treebank dataset, the MIMIC-IV database and a new ClinicalNotes corpus. "
        "The training dataset is private. Models were trained on GPUs and pre-trained on ImageNet-21k. "
        "Results on English are shown."
    )
    assert extract_entities(text).datasets == ["Penn Treebank", "MIMIC-IV", "ClinicalNotes", "ImageNet-21k"]


def test_model_patterns():
    text = (
        "Baselines: Llama-2-7B, ViT-B/16, ResNet-50, MobileNetV2, Claude 3 Opus, T5-base, Yi-34B, "
        "and YOLOv5. Phi alone is a letter; Phi-2 is a model. OPT is ambiguous but OPT-175B is not."
    )
    assert extract_entities(text).models == [
        "Llama-2-7B",
        "ViT-B/16",
        "ResNet-50",
        "MobileNetV2",
        "Claude 3 Opus",
        "T5-base",
        "Yi-34B",
        "YOLOv5",
        "Phi-2",
        "OPT-175B",
    ]


def test_model_names_inside_urls_are_ignored():
    assert extract_entities("weights at https://hub.example.org/models/GPT-4-clone").models == []


def test_defined_acronyms_become_methods_unless_metric_or_dataset():
    text = (
        "We use Reinforcement Learning from Human Feedback (RLHF) and Proximal Policy Optimization (PPO). "
        "Mean Squared Error (MSE) is reported on the Penn Treebank (PTB). Large Language Models (LLMs) help."
    )
    methods = extract_entities(text).methods
    assert "RLHF" in methods and "PPO" in methods
    assert "MSE" not in methods and "PTB" not in methods and "LLMs" not in methods


def test_acronym_with_mismatched_initials_is_ignored():
    assert "XYZ" not in extract_entities("a completely unrelated phrase (XYZ)").methods


def test_net_pattern_methods_exclude_models():
    methods = extract_entities("A PointNet++ encoder, a U-Net decoder and a ResNet backbone.").methods
    assert methods == ["PointNet++", "U-Net"]


# ---------------------------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------------------------
def test_citation_patterns_and_false_positives():
    text = (
        "Prior work (e.g., Hochreiter & Schmidhuber, 1997; Goodfellow et al. 2014a) and He et al. [2016] "
        "showed this. In (2020) nothing happened. The LSTM (1997) paper. Published (2021). Table (2019)."
    )
    assert extract_entities(text).citations == [
        "Hochreiter and Schmidhuber, 1997",
        "Goodfellow et al., 2014a",
        "He et al., 2016",
    ]


def test_input_is_bounded():
    text = "accuracy of 90%. " * 10 + "x" * 100
    assert extract_entities(text, max_chars=20).metrics == [MetricMention(name="accuracy", value=90.0, unit="%")]


@pytest.mark.parametrize(
    "adversarial",
    [
        "a" * 200_000,
        "A" * 200_000,
        "A-" * 100_000,
        "a." * 100_000,
        "http://" + "a" * 200_000,
        "x a@" + "b" * 200_000,
        "(" + "Word " * 40_000,
    ],
)
def test_adversarial_input_is_processed_in_linear_time(adversarial):
    import time

    started = time.perf_counter()
    extract_entities(adversarial)
    # quadratic regex backtracking on these inputs took minutes before the patterns were bounded
    assert time.perf_counter() - started < 5.0
