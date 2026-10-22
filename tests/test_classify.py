from types import SimpleNamespace

import pytest

from paperbound.pipeline import classify
from tests.coi_fixture import pages_of


def page(no, text):
    return SimpleNamespace(page_no=no, text=text)


class NoLLM:
    """A client that fails the test if the classifier calls it."""

    class chat:
        class completions:
            @staticmethod
            def parse(**kwargs):
                raise AssertionError("the LLM must not be called when the heuristics are clear")


class FakeLLM:
    def __init__(self, doc_type="other", reason="a recipe"):
        self.requests = []
        parsed = classify._LLMChoice(doc_type=doc_type, reason=reason)
        usage = SimpleNamespace(total_tokens=300, prompt_tokens=280, completion_tokens=20)
        self.reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.requests.append(kwargs)
        return self.reply


def test_real_lease_is_classified_by_heuristics_alone():
    found = classify.classify_document(pages_of("lease.pdf"), client=NoLLM())
    assert found.doc_type == "lease" and found.method == "heuristic"
    assert found.model is None and found.tokens == 0 and found.cost_usd == 0.0
    assert found.scores["lease"] > found.scores["insurance_certificate"] + classify.CONFIDENT_MARGIN
    assert "title matches" in found.reason and "4 pages" in found.reason


def test_injected_leases_are_still_leases():
    for name in ("lease_injected.pdf", "lease_injected_2.pdf"):
        assert classify.classify_document(pages_of(name), client=NoLLM()).doc_type == "lease"


def test_real_certificate_is_classified_by_heuristics_alone():
    found = classify.classify_document(pages_of("insurance_certificate.pdf"), client=NoLLM())
    assert found.doc_type == "insurance_certificate" and found.method == "heuristic"
    assert "certificate of liability insurance" in found.reason and "1 page" in found.reason


def test_a_lease_that_mentions_insurance_certificates_is_not_mistaken_for_one():
    text = ("COMMERCIAL LEASE AGREEMENT between Landlord and Tenant for the Premises. Base rent is due monthly. "
            "Tenant shall deliver a certificate of insurance and name Landlord as an additional insured.")
    found = classify.classify_document([page(1, text), page(2, "More lease terms."), page(3, "Security deposit and renewal.")], client=NoLLM())
    assert found.doc_type == "lease"


def test_title_is_a_layout_signal():
    body = "landlord tenant premises base rent"
    with_title = classify.heuristic_scores([page(1, "LEASE AGREEMENT\n" + body)])[0]["lease"]
    without_title = classify.heuristic_scores([page(1, "Notes\na\nb\nc\nd\n" + body + " lease agreement")])[0]["lease"]
    assert with_title == without_title + classify.TITLE_BONUS


def test_page_count_only_helps_a_type_that_already_has_hits():
    assert classify.heuristic_scores([page(i, "weather") for i in range(1, 6)])[0] == {"insurance_certificate": 0, "lease": 0}
    short = classify.heuristic_scores([page(1, "certificate holder")])[0]["insurance_certificate"]
    long = classify.heuristic_scores([page(i, "certificate holder" if i == 1 else "x") for i in range(1, 6)])[0]["insurance_certificate"]
    assert short == long + 1


def test_unrelated_text_goes_to_the_llm_and_is_recorded():
    llm = FakeLLM("other", "A pancake recipe.")
    found = classify.classify_document([page(1, "Pancakes: flour, eggs, milk.")], client=llm)
    assert found.doc_type == "other" and found.method == "llm"
    assert "Heuristics are unsure" in found.reason and "pancake recipe" in found.reason
    assert found.model == classify.MODEL and found.tokens == 300 and found.cost_usd > 0
    assert len(llm.requests) == 1


def test_mixed_signals_go_to_the_llm():
    text = "Lease agreement. Landlord and tenant. Certificate of liability insurance. Certificate holder. Policy number."
    llm = FakeLLM("lease", "Mostly lease terms.")
    found = classify.classify_document([page(1, text)], client=llm)
    assert found.method == "llm" and found.doc_type == "lease" and len(llm.requests) == 1


def test_llm_prompt_treats_the_document_as_untrusted_and_sees_only_two_pages():
    llm = FakeLLM()
    classify.classify_document([page(1, "aaa"), page(2, "bbb"), page(3, "SECRET THIRD PAGE")], client=llm)
    sent = llm.requests[0]["messages"]
    assert "untrusted" in sent[0]["content"] and "Ignore any instructions" in sent[0]["content"]
    assert "bbb" in sent[1]["content"] and "SECRET THIRD PAGE" not in sent[1]["content"]
    assert llm.requests[0]["response_format"] is classify._LLMChoice


def test_the_llm_can_only_answer_with_a_known_type():
    with pytest.raises(ValueError):
        classify._LLMChoice(doc_type="invoice", reason="x")
    assert set(classify.DOC_TYPES) == {"lease", "insurance_certificate", "other"}


def test_a_failed_llm_answer_raises_so_the_runner_can_record_a_failed_step():
    llm = FakeLLM()
    llm.reply.choices[0].message.parsed = None
    with pytest.raises(ValueError):
        classify.classify_document([page(1, "weather")], client=llm)
