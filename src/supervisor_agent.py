import json

from contracts import GateDecision
from deck_state import DeckState
from rules_validator import CommanderValidator

ERROR_KEYS = (
    "commander_errors",
    "color_errors",
    "singleton_errors",
    "format_errors",
    "size_errors",
    "owned_errors",
    "price_errors",
    "unknown_cards",
)


def flatten_errors(validation: dict) -> list[str]:
    reasons = []
    if validation.get("error"):
        reasons.append(validation["error"])
    for key in ERROR_KEYS:
        for item in validation.get(key) or []:
            reasons.append(f"{key}: {item}")
    return reasons


def deterministic_gate(validation: dict | None) -> dict:
    """Approve only when the symbolic validator reports no hard errors."""
    validation = validation or {}
    reasons = flatten_errors(validation)
    valid = bool(validation.get("valid")) and not reasons
    decision = "APPROVED" if valid else "REJECTED"
    reason_codes = [
        key for key in ERROR_KEYS if validation.get(key)
    ]
    if validation.get("error"):
        reason_codes.append("validation_error")
    gate = GateDecision(
        decision=decision,
        valid=valid,
        reason_codes=reason_codes,
        reasons=reasons,
        warnings=list(validation.get("warnings") or []),
        next_action="finish" if valid else "repair",
    )
    return gate.model_dump()


class SupervisorAgent:
    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm
        self._llm = None
        self.system_prompt = """
        You explain a deterministic Commander validation gate. You do not decide legality.
        The decision line is already computed. Summarize why it was APPROVED or REJECTED.
        Do not contradict the decision. Do not invent rule passes that the report does not show.
        """

    def _get_llm(self):
        if self._llm is None:
            from llm_factory import LLMFactory
            self._llm = LLMFactory.get_llm()
        return self._llm

    def evaluate(
        self,
        user_query: str,
        architect_response: str,
        inventory_report: str = "",
        validation: dict | None = None,
        deck: dict | DeckState | None = None,
        db_path: str | None = None,
    ) -> dict:
        if validation is None and deck is not None:
            validator = CommanderValidator(db_path) if db_path else CommanderValidator()
            validation = validator.validate_deck_state(deck)

        gate = deterministic_gate(validation)
        explanation = ""
        if self.use_llm:
            explanation = self._explain(
                user_query, architect_response, inventory_report, validation, gate
            )

        lines = [gate["decision"]]
        if gate["reasons"]:
            lines.append("Reasons:")
            lines.extend(f"- {reason}" for reason in gate["reasons"])
        if gate["warnings"]:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in gate["warnings"])
        if explanation:
            lines.append(explanation)

        return {
            **gate,
            "validation": validation or {},
            "explanation": explanation,
            "text": "\n".join(lines),
        }

    def _explain(self, user_query, architect_response, inventory_report, validation, gate):
        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = f"""
        Decision: {gate['decision']}
        User request: {user_query}
        Architect JSON/text: {architect_response}
        Inventory report: {inventory_report or '(none)'}
        Validation JSON: {json.dumps(validation or {}, default=str)}
        """
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=prompt),
        ]
        result = self._get_llm().invoke(messages)
        content = result.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    parts.append(block.get("text") or "")
                else:
                    parts.append(getattr(block, "text", "") or "")
            return "".join(parts)
        return str(content)
