"""Mock hook for T4 (checks_headers.py): extra response-header profiles selectable via the existing `vendor` knob.

  /s/good:vendor=bedrock/v1          x-amzn-requestid + x-amzn-bedrock-*              (AWS Bedrock channel)
  /s/good:vendor=vertex/v1           x-goog-request-id + x-vertex-ai-*                (Google Vertex channel)
  /s/good:vendor=azure/v1            x-ms-region + apim-request-id + x-ms-deployment-name + azureml-model-session
  /s/good:vendor=bedrock_openai/v1   Bedrock headers + openai-organization            (contradiction for claim=bedrock)
  /s/good:vendor=bedrock_anthropic/v1  Bedrock headers + anthropic-ratelimit-*        (contradiction for claim=bedrock)
  /s/good:vendor=vertex_amzn/v1      Vertex headers + x-amzn-requestid                (contradiction for claim=vertex)
  /s/good:vendor=azure_goog/v1       Azure headers + x-goog-request-id                (contradiction for claim=azure)
  /s/good:vendor=anthropic_amzn/v1   Anthropic headers + x-amzn-requestid             (contradiction for claim=official + vendor anthropic)
  /s/good:vendor=moonshot_amzn/v1    Moonshot headers + x-amzn-requestid              (contradiction for claim=official + moonshot prefix)
Plain scenarios also exist: hdr_bedrock, hdr_vertex, hdr_azure.
This module never overrides an answer (answer() returns None); it only registers header profiles/scenarios.
"""
import uuid

import mock_llm

_P = mock_llm.VENDOR_HEADER_PROFILES


def _rid():
    return uuid.uuid4().hex


def _bedrock():
    return {"x-amzn-requestid": str(uuid.uuid4()), "x-amzn-bedrock-invocation-latency": "321", "x-amzn-bedrock-input-token-count": "12"}


def _vertex():
    return {"x-goog-request-id": _rid(), "x-vertex-ai-llm-request-type": "shared"}


def _azure():
    return {"x-ms-region": "East US", "apim-request-id": str(uuid.uuid4()), "x-ms-deployment-name": "gpt-mock",
            "azureml-model-session": "d001-mock"}


def _mix(*fns, **extra):
    def f():
        h = {}
        for fn in fns:
            h.update(fn())
        h.update({k: (v() if callable(v) else v) for k, v in extra.items()})
        return h
    return f


_P.update({
    "bedrock": _bedrock,
    "vertex": _vertex,
    "azure": _azure,
    "bedrock_openai": _mix(_bedrock, **{"openai-organization": "org-mock"}),
    "bedrock_anthropic": _mix(_bedrock, **{"anthropic-ratelimit-requests-limit": "1000"}),
    "vertex_amzn": _mix(_vertex, **{"x-amzn-requestid": lambda: str(uuid.uuid4())}),
    "azure_goog": _mix(_azure, **{"x-goog-request-id": _rid}),
    "anthropic_amzn": _mix(_P["anthropic"], **{"x-amzn-requestid": lambda: str(uuid.uuid4())}),
    "moonshot_amzn": _mix(_P["moonshot"], **{"x-amzn-requestid": lambda: str(uuid.uuid4())}),
})

SCENARIOS = {"hdr_bedrock": {"vendor": "bedrock"}, "hdr_vertex": {"vendor": "vertex"}, "hdr_azure": {"vendor": "azure"}}


def answer(ctx, user_text):
    return None
