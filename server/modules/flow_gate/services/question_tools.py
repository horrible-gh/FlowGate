"""API-model Q&A tools use the same token-bound HTTP contracts as CLI workers."""
import json
from modules.flow_gate.db import question_items

SEARCH = "search_question_answers"
ANSWER = "answer_question"
ESCALATE = "escalate_question"


def responder_item(run):
    if not run.get("run_id") or run.get("completion_oracle") is None:
        return None
    rows = question_items.list_by_responder_run(run["run_id"])
    return rows[0]["id"] if len(rows) == 1 else None


def definitions(item_id=None):
    def tool(name, description, properties, required, completion=False):
        return {"name": name, "description": description, "completion": completion,
                "schema": {"type": "object", "properties": properties, "required": required,
                           "additionalProperties": False}}
    result = [tool(SEARCH, "Search past answered questions in the current document's authorized project. "
                   "Check existing answers before asking or answering again.",
                   {"q": {"type": "string", "minLength": 1, "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "before_id": {"type": "integer", "minimum": 1}}, ["q"])]
    if item_id is not None:
        result += [tool(ANSWER, "Answer only the question assigned to this responder.",
                        {"body": {"type": "string"}, "selected_option_ids": {
                            "type": "array", "items": {"type": "string"}}}, [], True),
                   tool(ESCALATE, "Hand this question to the user. Explain why the user's question policy "
                        "requires a human decision, with the relevant choices.",
                        {"reason": {"type": "string", "minLength": 1}}, ["reason"], True)]
    return result


def search(run, raw_token, data):
    from modules.flow_gate.api.v1.q_tapi_routes import search_answers
    from modules.flow_gate.services.ai_invoke.worker import _BearerOnlyRequest
    from fastapi.responses import JSONResponse
    response = search_answers(_BearerOnlyRequest(raw_token), run["doc_ref"], data["q"],
                              data.get("limit", 10), data.get("before_id"))
    if isinstance(response, JSONResponse):
        return response.status_code, json.loads(response.body)
    return 200, response
